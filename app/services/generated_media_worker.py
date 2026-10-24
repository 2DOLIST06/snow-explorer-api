"""Validate an embedded WebP in an isolated process without changing its bytes."""
import json
import resource
import sys


def main():
    try:
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
        from PIL import Image
        Image.MAX_IMAGE_PIXELS = 1600 * 1600
        with Image.open(sys.argv[1]) as image:
            if image.format != "WEBP" or getattr(image, "n_frames", 1) != 1 or max(image.size) > 1600:
                raise ValueError("invalid_generated_webp")
            image.load()
            result = {"ok": True, "width": image.width, "height": image.height}
    except Exception:
        result = {"ok": False, "error": "invalid_generated_webp"}
    print(json.dumps(result))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
