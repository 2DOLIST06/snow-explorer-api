"""Single S3 client/configuration shared by browser and server-side uploads."""
import os
from urllib.parse import quote

import boto3
from botocore.config import Config


def setting(name, legacy_name=None):
    value = os.getenv(name)
    if value is None and legacy_name:
        value = os.getenv(legacy_name)
    return (value or "").strip()


def client():
    return boto3.client("s3", aws_access_key_id=setting("AWS_ACCESS_KEY_ID"),
                        aws_secret_access_key=setting("AWS_SECRET_ACCESS_KEY"),
                        region_name=setting("AWS_REGION"),
                        config=Config(
                            connect_timeout=float(setting("AWS_S3_CONNECT_TIMEOUT") or 3),
                            read_timeout=float(setting("AWS_S3_READ_TIMEOUT") or 10),
                            retries={
                                "max_attempts": int(setting("AWS_S3_MAX_ATTEMPTS") or 2),
                                "mode": "standard",
                            },
                        ))


def bucket(): return setting("AWS_S3_BUCKET", "AWS_BUCKET_NAME")


def public_url(key):
    base = setting("AWS_S3_PUBLIC_URL").rstrip("/") or f"https://{bucket()}.s3.{setting('AWS_REGION')}.amazonaws.com"
    return f"{base}/{quote(key)}"


def put_webp(key, content):
    client().put_object(Bucket=bucket(), Key=key, Body=content, ContentType="image/webp",
                        CacheControl="public,max-age=31536000,immutable")
    return public_url(key)


def put_file(key, path, content_type):
    """Upload a prepared immutable object without reading it into application RAM."""
    with open(path, "rb") as body:
        client().upload_fileobj(body, bucket(), key, ExtraArgs={
            "ContentType": content_type, "CacheControl": "public,max-age=31536000,immutable",
        })
    return public_url(key)


def download_file(key, path, maximum_size):
    """Download a bounded private object to disk without buffering it in RAM."""
    remote = client()
    metadata = remote.head_object(Bucket=bucket(), Key=key)
    size = int(metadata.get("ContentLength") or 0)
    if size <= 0 or size > maximum_size:
        raise ValueError("stored_object_too_large")
    with open(path, "wb") as body:
        remote.download_fileobj(bucket(), key, body)
    actual_size = os.path.getsize(path)
    if actual_size != size or actual_size > maximum_size:
        raise ValueError("stored_object_size_mismatch")
    return actual_size


def validate_object(key):
    metadata = client().head_object(Bucket=bucket(), Key=key)
    return int(metadata.get("ContentLength") or 0) > 0


def preview_url(key, expires_in=900):
    """Return a browser-readable URL without ever persisting a signature."""
    if setting("AWS_S3_PRIVATE").lower() in {"1", "true", "yes", "on"}:
        return client().generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket(), "Key": key},
            ExpiresIn=expires_in,
        )
    return public_url(key)


def validate_webp(key, maximum_size=50 * 1024):
    """Validate candidate metadata in S3 without downloading the object."""
    metadata = client().head_object(Bucket=bucket(), Key=key)
    content_type = (metadata.get("ContentType") or "").split(";", 1)[0].lower()
    size = int(metadata.get("ContentLength") or 0)
    return content_type == "image/webp" and 0 < size <= maximum_size
