import logging
import mimetypes
import os
import uuid
from urllib.parse import quote

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from flask import Blueprint, jsonify, request

from app.services.admin_auth import admin_required

bp_uploads = Blueprint("uploads", __name__)
logger = logging.getLogger("uploads.s3")


def _setting(name, legacy_name=None):
    value = os.getenv(name)
    if value is None and legacy_name:
        value = os.getenv(legacy_name)
    return (value or "").strip()


def _s3():
    return boto3.client(
        "s3",
        aws_access_key_id=_setting("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=_setting("AWS_SECRET_ACCESS_KEY"),
        region_name=_setting("AWS_REGION"),
    )


def _error(message, status):
    return jsonify({"error": "presign_failed", "message": message}), status


@bp_uploads.route("/api/s3/presign", methods=["POST", "OPTIONS"])
@admin_required
def presign():
    if request.method == "OPTIONS":
        logger.info("S3 presign OPTIONS received origin=%s", request.headers.get("Origin", ""))
        return "", 204

    logger.info("S3 presign POST received origin=%s", request.headers.get("Origin", ""))
    if not request.is_json:
        logger.warning("S3 presign invalid content type")
        return _error("Content-Type application/json is required", 400)
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        logger.warning("S3 presign invalid JSON payload")
        return _error("A valid JSON object is required", 400)

    filename = data.get("filename")
    content_type = data.get("content_type")
    if not isinstance(filename, str) or not filename.strip():
        logger.warning("S3 presign payload missing filename")
        return _error("filename is required", 400)
    if not isinstance(content_type, str) or not content_type.strip():
        logger.warning("S3 presign payload missing content_type")
        return _error("content_type is required", 400)
    filename = os.path.basename(filename.strip())
    content_type = content_type.strip().lower()
    guessed_type = mimetypes.guess_type(filename)[0]
    if guessed_type and guessed_type != content_type:
        logger.warning("S3 presign MIME mismatch filename_extension=%s", os.path.splitext(filename)[1])
        return _error("content_type does not match the filename extension", 400)

    bucket = _setting("AWS_S3_BUCKET", "AWS_BUCKET_NAME")
    region = _setting("AWS_REGION")
    missing = [name for name, value in (
        ("AWS_ACCESS_KEY_ID", _setting("AWS_ACCESS_KEY_ID")),
        ("AWS_SECRET_ACCESS_KEY", _setting("AWS_SECRET_ACCESS_KEY")),
        ("AWS_REGION", region),
        ("AWS_S3_BUCKET", bucket),
    ) if not value]
    if missing:
        logger.error("S3 presign configuration missing variables=%s", ",".join(missing))
        return _error("S3 upload service is not configured", 500)

    # clé unique côté S3
    ext = os.path.splitext(filename)[1]
    key = f"uploads/{uuid.uuid4().hex}{ext}"

    try:
        url = _s3().generate_presigned_url(
            ClientMethod="put_object",
            Params={"Bucket": bucket, "Key": key, "ContentType": content_type},
            ExpiresIn=3600,
        )
    except (BotoCoreError, ClientError, ValueError) as exc:
        logger.exception("S3 presign boto3 failure exception_type=%s", type(exc).__name__)
        return _error("Unable to create the S3 upload URL", 500)

    public_base_url = _setting("AWS_S3_PUBLIC_URL").rstrip("/")
    if not public_base_url:
        public_base_url = f"https://{bucket}.s3.{region}.amazonaws.com"
    public_url = f"{public_base_url}/{quote(key)}"
    logger.info("S3 presign response generated key_prefix=uploads/ content_type=%s", content_type)
    return jsonify({
        "uploadUrl": url,
        "publicUrl": public_url,
        "contentType": content_type,
    })
