"""Resource-limited ANMSM logo decoder; stdout is one small JSON object."""
import argparse
from collections import deque
import json
import os
import resource
import struct
import sys
import warnings
import zlib


PROBE_SIZE = 1024
PNG_DECODE_SIZE = 2048
CONTENT_FRACTION = 0.88


class ConversionError(ValueError):
    """An expected, safe-to-report conversion failure."""


def _limit(memory_mb):
    if hasattr(resource, "RLIMIT_AS"):
        limit = memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))


def _png_chunks(source):
    """Read and CRC-check a PNG without asking Pillow to allocate its raster."""
    with open(source, "rb") as stream:
        if stream.read(8) != b"\x89PNG\r\n\x1a\n":
            raise ConversionError("decode_failed")
        while True:
            header = stream.read(8)
            if len(header) != 8:
                raise ConversionError("decode_failed")
            length, kind = struct.unpack(">I4s", header)
            if length > 10 * 1024 * 1024:
                raise ConversionError("decode_failed")
            data, checksum = stream.read(length), stream.read(4)
            if len(data) != length or len(checksum) != 4:
                raise ConversionError("decode_failed")
            if zlib.crc32(kind + data) & 0xffffffff != struct.unpack(">I", checksum)[0]:
                raise ConversionError("decode_failed")
            yield kind, data
            if kind == b"IEND":
                return


def _paeth(left, above, upper_left):
    estimate = left + above - upper_left
    distances = abs(estimate - left), abs(estimate - above), abs(estimate - upper_left)
    return (left, above, upper_left)[distances.index(min(distances))]


def _load_png_reduced(source, Image):
    """Decode a non-interlaced 8-bit PNG row-by-row into a bounded RGBA raster."""
    width = height = colour_type = interlace = None
    palette = transparency = None
    compressed = bytearray()
    for kind, data in _png_chunks(source):
        if kind == b"IHDR":
            if len(data) != 13:
                raise ConversionError("decode_failed")
            width, height, depth, colour_type, compression, filtering, interlace = struct.unpack(">IIBBBBB", data)
            if depth != 8 or compression or filtering:
                raise ConversionError("png_reduced_decode_unsupported")
        elif kind == b"PLTE": palette = data
        elif kind == b"tRNS": transparency = data
        elif kind == b"IDAT": compressed.extend(data)
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(colour_type)
    if not width or not height or channels is None or interlace:
        raise ConversionError("png_reduced_decode_unsupported")
    target_scale = min(1.0, PNG_DECODE_SIZE / max(width, height))
    target_width = max(1, round(width * target_scale))
    target_height = max(1, round(height * target_scale))
    selected_x = [min(width - 1, (x * width + width // 2) // target_width)
                  for x in range(target_width)]
    selected_y = {min(height - 1, (y * height + height // 2) // target_height): y
                  for y in range(target_height)}
    output = bytearray(target_width * target_height * 4)
    row_bytes = width * channels
    decoder = zlib.decompressobj()
    pending = bytearray()
    compressed_offset = 0

    def next_row():
        nonlocal compressed_offset
        required = row_bytes + 1
        try:
            while len(pending) < required:
                if decoder.unconsumed_tail:
                    chunk = decoder.unconsumed_tail
                elif compressed_offset < len(compressed):
                    chunk = compressed[compressed_offset:compressed_offset + 64 * 1024]
                    compressed_offset += len(chunk)
                else:
                    break
                pending.extend(decoder.decompress(chunk, required - len(pending)))
        except zlib.error as exc:
            raise ConversionError("decode_failed") from exc
        if len(pending) < required:
            raise ConversionError("decode_failed")
        row = bytes(pending[:required])
        del pending[:required]
        return row

    previous = bytearray(row_bytes)
    for y in range(height):
        encoded = next_row()
        filter_type = encoded[0]
        scanline = bytearray(encoded[1:])
        if filter_type > 4:
            raise ConversionError("decode_failed")
        if filter_type:
            for index in range(row_bytes):
                left = scanline[index - channels] if index >= channels else 0
                above = previous[index]
                upper_left = previous[index - channels] if index >= channels else 0
                predictor = (left, above, (left + above) // 2,
                             _paeth(left, above, upper_left))[filter_type - 1]
                scanline[index] = (scanline[index] + predictor) & 255
        output_y = selected_y.get(y)
        if output_y is not None:
            for output_x, source_x in enumerate(selected_x):
                index = source_x * channels
                if colour_type == 6:
                    pixel = scanline[index:index + 4]
                elif colour_type == 2:
                    pixel = scanline[index:index + 3] + b"\xff"
                elif colour_type == 4:
                    pixel = bytes((scanline[index],) * 3 + (scanline[index + 1],))
                elif colour_type == 0:
                    pixel = bytes((scanline[index],) * 3 + (255,))
                else:
                    palette_index = scanline[index]
                    base = palette_index * 3
                    if palette is None or base + 3 > len(palette):
                        raise ConversionError("decode_failed")
                    alpha = transparency[palette_index] if transparency and palette_index < len(transparency) else 255
                    pixel = palette[base:base + 3] + bytes((alpha,))
                destination = (output_y * target_width + output_x) * 4
                output[destination:destination + 4] = pixel
        previous = scanline
    return Image.frombytes("RGBA", (target_width, target_height), bytes(output)), width, height


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
                reduced_png = source_format == "PNG" and max(source_width, source_height) > PNG_DECODE_SIZE
                if source_format == "JPEG":
                    image.draft("RGB", (PROBE_SIZE, PROBE_SIZE))
                if not reduced_png:
                    image.load()
        except UnidentifiedImageError as exc:
            raise ConversionError("decode_failed") from exc
        except Image.DecompressionBombError as exc:
            raise ConversionError("excessive_dimensions") from exc
        except (OSError, SyntaxError) as exc:
            raise ConversionError("decode_failed") from exc

        if reduced_png:
            image.close()
            image, checked_width, checked_height = _load_png_reduced(source, Image)
            if (checked_width, checked_height) != (source_width, source_height):
                raise ConversionError("decode_failed")

        orientation = image.getexif().get(0x0112, 1) if source_format != "PNG" else 1
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
        result, status = {"ok": False, "error": "worker_internal_error",
                          "detail": type(exc).__name__}, 3
    sys.stdout.write(json.dumps(result, separators=(",", ":"))); sys.stdout.flush()
    return status


if __name__ == "__main__":
    raise SystemExit(main())
