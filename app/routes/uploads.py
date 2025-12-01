from flask import Blueprint, request, jsonify
import boto3, os, uuid, mimetypes

bp_uploads = Blueprint("uploads", __name__)

def _s3():
    return boto3.client(
        "s3",
        aws_access_key_id=(os.getenv("AWS_ACCESS_KEY_ID") or "").strip(),
        aws_secret_access_key=(os.getenv("AWS_SECRET_ACCESS_KEY") or "").strip(),
        region_name=(os.getenv("AWS_REGION") or "").strip(),
    )

@bp_uploads.route("/api/s3/presign", methods=["POST"])
def presign():
    data = request.get_json(force=True) or {}
    filename = data.get("filename")
    if not filename:
        return jsonify({"error": "missing filename"}), 400

    # clé unique côté S3
    ext = os.path.splitext(filename)[1]
    key = f"uploads/{uuid.uuid4().hex}{ext}"

    # URL pré-signée SANS ACL et SANS ContentType (évite les 403 de signature)
    url = _s3().generate_presigned_url(
        ClientMethod="put_object",
        Params={
            "Bucket": (os.getenv("AWS_BUCKET_NAME") or "").strip(),
            "Key": key
        },
        ExpiresIn=3600,
    )

    public_url = f"https://{(os.getenv('AWS_BUCKET_NAME') or '').strip()}.s3.amazonaws.com/{key}"
    return jsonify({"uploadUrl": url, "publicUrl": public_url})