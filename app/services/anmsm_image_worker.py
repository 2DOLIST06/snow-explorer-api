"""Resource-limited ANMSM logo decoder; stdout is one small JSON object."""
import argparse
from collections import deque
import json
import os
import resource
import sys
import warnings


PROBE_SIZE = 1024
CONTENT_FRACTION = 0.88


class ConversionError(ValueError):
    """An expected, safe-to-report conversion failure."""


def _limit(memory_mb):
    if hasattr(resource, "RLIMIT_AS"):
        limit = memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))


def _scaled_box(box, probe_size, image_size, padding=True):
    """Map a conservative detection box from a small probe to the source."""
    pw, ph = probe_size
    width, height = image_size
    left, top, right, bottom = box
    # One probe pixel plus 0.5% of the detected content is deliberately kept.
    pad = max(1, round(max(right - left, bottom - top) * .005)) if padding else 0
    left, top = max(0, left - pad), max(0, top - pad)
    right, bottom = min(pw, right + pad), min(ph, bottom + pad)
    return (max(0, left * width // pw), max(0, top * height // ph),
            min(width, (right * width + pw - 1) // pw),
            min(height, (bottom * height + ph - 1) // ph))


def _alpha_box(probe):
    alpha = probe.getchannel("A")
    try:
        # Treat tiny alpha values as compression/conversion noise.
        mask = alpha.point(lambda value: 255 if value >= 8 else 0)
        try:
            return mask.getbbox()
        finally:
            mask.close()
    finally:
        alpha.close()


def _edge_white_box(probe):
    """Return content bounds after flood-filling only near-white edge pixels."""
    rgb = probe.convert("RGB")
    try:
        width, height = rgb.size
        pixels = rgb.load()
        white = bytearray(width * height)
        queue = deque()

        def eligible(x, y):
            red, green, blue = pixels[x, y]
            # The low channel and channel spread jointly tolerate JPEG noise,
            # but avoid considering saturated pale brand colours as paper.
            return min(red, green, blue) >= 238 and max(red, green, blue) - min(red, green, blue) <= 14

        def add(x, y):
            index = y * width + x
            if not white[index] and eligible(x, y):
                white[index] = 1
                queue.append((x, y))

        for x in range(width):
            add(x, 0); add(x, height - 1)
        for y in range(height):
            add(0, y); add(width - 1, y)
        while queue:
            x, y = queue.popleft()
            if x: add(x - 1, y)
            if x + 1 < width: add(x + 1, y)
            if y: add(x, y - 1)
            if y + 1 < height: add(x, y + 1)

        background = sum(white)
        # Do not crop uncertain backgrounds or tiny edge decorations.
        if background < width * height * .08:
            return None
        left, top, right, bottom = width, height, -1, -1
        for y in range(height):
            row = y * width
            for x in range(width):
                if not white[row + x]:
                    left, top = min(left, x), min(top, y)
                    right, bottom = max(right, x), max(bottom, y)
        if right < left:
            raise ConversionError("empty_image")
        box = left, top, right + 1, bottom + 1
        if (box[2] - box[0]) * (box[3] - box[1]) < width * height * .002:
            return None
        return box
    finally:
        rgb.close()


def _content_box(image):
    width, height = image.size
    factor = min(1.0, PROBE_SIZE / max(width, height))
    probe_size = max(1, round(width * factor)), max(1, round(height * factor))
    probe = image.resize(probe_size, Image.Resampling.BOX, reducing_gap=3.0)
    try:
        alpha = probe.getchannel("A")
        alpha_extrema = alpha.getextrema()
        alpha.close()
        if alpha_extrema[0] < 255:
            box = _alpha_box(probe)
            if not box:
                raise ConversionError("empty_image")
        else:
            box = _edge_white_box(probe)
            if box is None:
                return (0, 0, width, height)
        return _scaled_box(box, probe.size, image.size)
    finally:
        probe.close()


def convert(source, output, max_pixels, output_size, output_limit,
            max_width=16_000, max_height=16_000):
    from PIL import Image, UnidentifiedImageError
    globals()["Image"] = Image  # helpers remain import-light until limits are active
    Image.MAX_IMAGE_PIXELS = max_pixels
    image = oriented = rgba = resized = canvas = None
    try:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                image = Image.open(source)
                image.seek(0)  # GIF: explicitly decode the first frame only.
                source_format = image.format
                source_width, source_height = image.size
                if source_format not in {"JPEG", "PNG", "GIF"}:
                    raise ConversionError("unsupported_format")
                if source_width <= 0 or source_height <= 0:
                    raise ConversionError("unreadable_dimensions")
                if (source_width > max_width or source_height > max_height or
                        source_width * source_height > max_pixels):
                    raise ConversionError("excessive_dimensions")
                # JPEG draft asks libjpeg to decode at 1/2, 1/4 or 1/8 size.
                # PNG has no reduced decoder in Pillow; only this child holds its
                # full raster and RLIMIT_AS bounds that allocation.
                if source_format == "JPEG":
                    image.draft("RGB", (PROBE_SIZE, PROBE_SIZE))
                image.load()
        except UnidentifiedImageError as exc:
            raise ConversionError("decode_failed") from exc
        except Image.DecompressionBombError as exc:
            raise ConversionError("excessive_dimensions") from exc
        except (OSError, SyntaxError) as exc:
            raise ConversionError("decode_failed") from exc

        orientation = image.getexif().get(0x0112, 1)
        transpose = {
            2: Image.Transpose.FLIP_LEFT_RIGHT, 3: Image.Transpose.ROTATE_180,
            4: Image.Transpose.FLIP_TOP_BOTTOM, 5: Image.Transpose.TRANSPOSE,
            6: Image.Transpose.ROTATE_270, 7: Image.Transpose.TRANSVERSE,
            8: Image.Transpose.ROTATE_90,
        }.get(orientation)
        # Do not call ImageOps.exif_transpose for the common orientation=1:
        # that helper copies the entire raster even when no transpose is due.
        oriented = image.transpose(transpose) if transpose is not None else image
        rgba = oriented if oriented.mode == "RGBA" else oriented.convert("RGBA")
        box = _content_box(rgba)
        content_width, content_height = box[2] - box[0], box[3] - box[1]
        if content_width <= 0 or content_height <= 0:
            raise ConversionError("empty_image")
        maximum = round(output_size * CONTENT_FRACTION)
        scale = min(maximum / content_width, maximum / content_height)
        width, height = max(1, round(content_width * scale)), max(1, round(content_height * scale))
        resized = rgba.resize((width, height), Image.Resampling.LANCZOS, box=box, reducing_gap=3.0)
        canvas = Image.new("RGBA", (output_size, output_size), (0, 0, 0, 0))
        canvas.alpha_composite(resized, ((output_size - width) // 2, (output_size - height) // 2))
        for quality in (82, 72, 62, 52, 42, 32, 22):
            try:
                canvas.save(output, "WEBP", quality=quality, method=6)
            except OSError as exc:
                raise ConversionError("webp_encode_failed") from exc
            size = os.path.getsize(output)
            if 0 < size <= output_limit:
                break
        else:
            raise ConversionError("webp_encode_failed")

        ratio = content_width / content_height
        warning_codes = []
        if max(source_width, source_height) < 256: warning_codes.append("low_resolution")
        if ratio > 6 or ratio < 1 / 6: warning_codes.append("extreme_aspect_ratio")
        return {"source_format": source_format.lower(), "source_width": source_width,
                "source_height": source_height, "content_width": content_width,
                "content_height": content_height, "aspect_ratio": ratio,
                "visual_occupancy_width": width / output_size,
                "visual_occupancy_height": height / output_size,
                "optimized_width": output_size, "optimized_height": output_size,
                "optimized_size_bytes": size, "warnings": warning_codes}
    except MemoryError as exc:
        raise ConversionError("memory_limit_exceeded") from exc
    finally:
        for item in (canvas, resized, rgba if rgba is not oriented else None,
                     oriented if oriented is not image else None, image):
            if item is not None:
                item.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source"); parser.add_argument("output")
    parser.add_argument("--max-pixels", type=int, required=True)
    parser.add_argument("--max-width", type=int, required=True)
    parser.add_argument("--max-height", type=int, required=True)
    parser.add_argument("--size", type=int, required=True)
    parser.add_argument("--output-limit", type=int, required=True)
    parser.add_argument("--memory-mb", type=int, required=True)
    args = parser.parse_args()
    try:
        _limit(args.memory_mb)
        metadata = convert(args.source, args.output, args.max_pixels, args.size,
                           args.output_limit, args.max_width, args.max_height)
        result, status = {"ok": True, "metadata": metadata}, 0
    except ConversionError as exc:
        result, status = {"ok": False, "error": str(exc)}, 2
    except Exception as exc:
        result, status = {"ok": False, "error": "conversion_interrupted",
                          "detail": type(exc).__name__}, 3
    sys.stdout.write(json.dumps(result, separators=(",", ":"))); sys.stdout.flush()
    return status


if __name__ == "__main__":
    raise SystemExit(main())
