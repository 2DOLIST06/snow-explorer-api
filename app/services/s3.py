"""Single S3 client/configuration shared by browser and server-side uploads."""
import os
from urllib.parse import quote

import boto3


def setting(name, legacy_name=None):
    value = os.getenv(name)
    if value is None and legacy_name:
        value = os.getenv(legacy_name)
    return (value or "").strip()


def client():
    return boto3.client("s3", aws_access_key_id=setting("AWS_ACCESS_KEY_ID"),
                        aws_secret_access_key=setting("AWS_SECRET_ACCESS_KEY"),
                        region_name=setting("AWS_REGION"))


def bucket(): return setting("AWS_S3_BUCKET", "AWS_BUCKET_NAME")


def public_url(key):
    base = setting("AWS_S3_PUBLIC_URL").rstrip("/") or f"https://{bucket()}.s3.{setting('AWS_REGION')}.amazonaws.com"
    return f"{base}/{quote(key)}"


def put_webp(key, content):
    client().put_object(Bucket=bucket(), Key=key, Body=content, ContentType="image/webp",
                        CacheControl="public,max-age=31536000,immutable")
    return public_url(key)
