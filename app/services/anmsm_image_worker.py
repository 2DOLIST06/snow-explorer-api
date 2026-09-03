"""Sandboxed ANMSM image decoder.  Its stdout is a single JSON object."""
import argparse
import json
import os
import resource
import sys
import warnings


def _limit(memory_mb):
    if hasattr(resource, "RLIMIT_AS"):
        limit = memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))


def convert(source, output, max_pixels, output_size, output_limit):
    from PIL import Image, ImageOps, UnidentifiedImageError
    Image.MAX_IMAGE_PIXELS = max_pixels
    image = converted = cropped = None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            image = Image.open(source)
            image.seek(0)
            source_format = image.format
            source_width, source_height = image.size
            if source_format not in {"JPEG", "PNG", "GIF"}:
                raise ValueError("unsupported_format")
            if source_width <= 0 or source_height <= 0 or source_width * source_height > max_pixels:
                raise ValueError("excessive_dimensions")
            image.load()
        converted = ImageOps.exif_transpose(image).convert("RGBA")
        alpha_box = converted.getchannel("A").getbbox()
        if not alpha_box:
            raise ValueError("empty_image")
        cropped = converted.crop(alpha_box)
        content_width, content_height = cropped.size
        scale, quality = min(1.0, output_size/content_width, output_size/content_height), 82
        while True:
            width, height = max(1, round(content_width*scale)), max(1, round(content_height*scale))
            resized = cropped.resize((width, height), Image.Resampling.LANCZOS) if cropped.size != (width, height) else cropped
            canvas = Image.new("RGBA", (output_size, output_size), (0, 0, 0, 0))
            try:
                canvas.alpha_composite(resized, ((output_size-width)//2, (output_size-height)//2))
                canvas.save(output, "WEBP", quality=quality, method=6)
            finally:
                canvas.close()
                if resized is not cropped: resized.close()
            size = os.path.getsize(output)
            if 0 < size <= output_limit: break
            if quality > 48: quality -= 6
            elif scale > .35: scale *= .88; quality = 70
            else: raise ValueError("optimization_limit")
        ratio = content_width/content_height
        warning_codes = []
        if max(source_width, source_height) < 256: warning_codes.append("low_resolution")
        if ratio > 6 or ratio < 1/6: warning_codes.append("extreme_aspect_ratio")
        if width/output_size < .2 or height/output_size < .2: warning_codes.append("low_visual_occupancy")
        return {"source_format": source_format.lower(), "source_width": source_width,
                "source_height": source_height, "content_width": content_width,
                "content_height": content_height, "aspect_ratio": ratio,
                "visual_occupancy_width": width/output_size,
                "visual_occupancy_height": height/output_size,
                "optimized_width": output_size, "optimized_height": output_size,
                "optimized_size_bytes": size, "warnings": warning_codes}
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("invalid_image") from exc
    finally:
        if cropped is not None: cropped.close()
        if converted is not None: converted.close()
        if image is not None: image.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source"); parser.add_argument("output")
    parser.add_argument("--max-pixels", type=int, required=True)
    parser.add_argument("--size", type=int, required=True)
    parser.add_argument("--output-limit", type=int, required=True)
    parser.add_argument("--memory-mb", type=int, required=True)
    args = parser.parse_args()
    try:
        _limit(args.memory_mb)
        result = {"ok": True, "metadata": convert(args.source, args.output, args.max_pixels,
                                                    args.size, args.output_limit)}
    except Exception as exc:
        result = {"ok": False, "error": str(exc) or "conversion_interrupted"}
    sys.stdout.write(json.dumps(result, separators=(",", ":"))); sys.stdout.flush()
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
