"""ASGI production entry point: official MCP alongside the existing Flask app."""
from app.main import app as flask_app
from app.mcp.http import create_mcp_application

app = create_mcp_application(flask_app)
