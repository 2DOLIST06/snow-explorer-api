"""Official MCP Streamable HTTP, isolated Bearer auth, and Flask ASGI coexistence."""
from contextlib import asynccontextmanager
import hmac
import json
import os
from functools import partial

import anyio
from a2wsgi import WSGIMiddleware
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from app.datetime_utils import utcnow
from app.services.station_ops.candidates import MAX_BODY_BYTES
from .tools import invoke, logger, tool_definitions

MCP_PATH = '/mcp/station-ops'


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


class Guard:
    """Authenticate before SDK dispatch or business connection; bound streamed bodies."""
    def __init__(self, app, flask_app):
        self.app, self.flask_app = app, flask_app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or not (scope['path'] == '/mcp' or scope['path'].startswith('/mcp/')):
            return await self.app(scope, receive, send)
        async def safe_send(message):
            if message['type'] == 'http.response.start':
                message['headers'] = list(message.get('headers', [])) + [(b'cache-control', b'no-store')]
            await send(message)
        async def refuse(code, status, message):
            logger.info(json.dumps({'event': 'station_ops_mcp', 'timestamp': utcnow().isoformat(),
                                   'tool': 'transport', 'result': 'refused', 'candidate_count': 0,
                                   'execution_id': None, 'code': code}))
            headers = {'WWW-Authenticate': 'Bearer'} if status == 401 else None
            await JSONResponse({'code': code, 'message': message}, status_code=status, headers=headers)(scope, receive, safe_send)
        expected = os.environ.get('STATION_OPS_MCP_TOKEN', '')
        if not expected.strip():
            return await refuse('station_ops_mcp_unavailable', 503, 'Station Ops MCP is unavailable on this environment.')
        if scope.get('scheme') != 'https' and not self.flask_app.testing:
            return await refuse('https_required', 403, 'Station Ops MCP requires HTTPS.')
        headers = [v for k, v in scope.get('headers', []) if k.lower() == b'authorization']
        if len(headers) != 1:
            return await refuse('unauthorized', 401, 'A valid Bearer token is required.')
        parts = headers[0].split(b' ')
        if len(parts) != 2 or parts[0].lower() != b'bearer' or not parts[1] or not hmac.compare_digest(parts[1], expected.encode('utf-8')):
            return await refuse('unauthorized', 401, 'A valid Bearer token is required.')
        if scope.get('query_string'):
            return await refuse('mcp_query_not_allowed', 400, 'MCP credentials and arguments must not be sent in query strings.')
        if scope['path'] != MCP_PATH:
            return await refuse('not_found', 404, 'Unknown MCP endpoint.')
        if scope.get('method') == 'POST':
            body = bytearray()
            while True:
                chunk = await receive()
                if chunk['type'] == 'http.disconnect':
                    return
                body.extend(chunk.get('body', b''))
                if len(body) > MAX_BODY_BYTES:
                    return await refuse('payload_too_large', 413, 'MCP request exceeds 16 MiB.')
                if not chunk.get('more_body', False):
                    break
            try:
                # SDK remains responsible for JSON-RPC/protocol parsing. This
                # guard only enforces the same strict JSON and size boundary as admin.
                json.loads(body, object_pairs_hook=_unique_object,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Non-finite number')),
                           parse_float=_finite_float)
            except (ValueError, UnicodeError, RecursionError):
                return await refuse('invalid_json', 400, 'Malformed, duplicate-key or non-finite JSON.')
            delivered = False
            async def replay():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {'type': 'http.request', 'body': bytes(body), 'more_body': False}
                return await receive()
            return await self.app(scope, replay, safe_send)
        return await self.app(scope, receive, safe_send)


def _finite_float(value):
    import math
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError('Non-finite number')
    return parsed


class Transport:
    def __init__(self, manager):
        self.manager = manager

    async def __call__(self, scope, receive, send):
        await self.manager.handle_request(scope, receive, send)


def create_mcp_application(flask_app):
    """One lifespan-managed official SDK manager, with Flask served unchanged."""
    for handler in flask_app.logger.handlers:
        if handler not in logger.handlers:
            logger.addHandler(handler)
    server = Server('Snow Explorer Content Ops', version='1.0',
                    instructions='Research is external. Use scan, validate, compare, review, explicit user decisions, dry-run, then optional commit. Never approve operations automatically.')
    # One MCP business worker plus two Flask workers fits the existing default
    # Peewee pool of three. No business transaction is opened by this wrapper.
    limiter = anyio.CapacityLimiter(1)
    @server.list_tools()
    async def list_tools():
        return tool_definitions()
    @server.call_tool(validate_input=False)
    async def call_tool(name, arguments):
        # Validate ourselves to return structured, sanitized errors and retain 413.
        return await anyio.to_thread.run_sync(partial(invoke, flask_app, name, arguments), limiter=limiter)
    allowed = [host.strip() for host in os.environ.get('STATION_OPS_MCP_ALLOWED_HOSTS',
              'snow-explorer-api-3.onrender.com,localhost,localhost:*,127.0.0.1,127.0.0.1:*').split(',') if host.strip()]
    manager = StreamableHTTPSessionManager(server, stateless=True, json_response=True,
        max_request_body_size=MAX_BODY_BYTES,
        security_settings=TransportSecuritySettings(enable_dns_rebinding_protection=True,
            allowed_hosts=allowed, allowed_origins=['https://' + host for host in allowed]))
    @asynccontextmanager
    async def lifespan(app):
        async with manager.run():
            yield
    app = Starlette(routes=[Route(MCP_PATH, endpoint=Transport(manager)),
                           Mount('/', app=WSGIMiddleware(flask_app, workers=2))], lifespan=lifespan)
    return Guard(app, flask_app)
