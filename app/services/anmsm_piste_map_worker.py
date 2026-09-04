"""Resource-limited decoder executed in a separate Python interpreter."""
import argparse
import json
import os
import resource
import sys
import tempfile
import warnings


def _log(stage, status, **fields):
    """Keep diagnostics off stdout, which is reserved for the result protocol."""
    message = {"event": "anmsm_piste_map_conversion", "stage": stage,
               "status": status, "pid": os.getpid(), **fields}
    print(json.dumps(message, separators=(",", ":")), file=sys.stderr, flush=True)


def _pdf_first_page(source, max_dimension, max_pixels):
    """Rasterize one bounded PDF page without retaining duplicate rasters."""
    import math
    import pypdfium2 as pdfium

    document = page = bitmap = rendered = None
    try:
        _log("pdf_open", "before")
        try:
            document = pdfium.PdfDocument(source)
        except Exception as exc:
            raise ValueError("invalid_pdf") from exc
        if len(document) < 1:
            raise ValueError("invalid_pdf")

        page = document[0]
        width, height = map(float, page.get_size())
        if width <= 0 or height <= 0:
            raise ValueError("invalid_pdf")
        _log("pdf_open", "after", page_count=len(document))
        _log("scale_calculation", "before")
        scale = min(max_dimension / max(width, height),
                    (max_pixels / (width * height)) ** .5)
        while (math.ceil(width * scale) > max_dimension or
               math.ceil(height * scale) > max_dimension or
               math.ceil(width * scale) * math.ceil(height * scale) > max_pixels):
            scale *= .999
        if scale <= 0:
            raise ValueError("excessive_dimensions")
        _log("scale_calculation", "after", scale=scale)

        _log("rasterization", "before")
        bitmap = page.render(scale=scale, rotation=0)
        # The bitmap no longer depends on the page. Release PDF structures before
        # asking Pillow for its independent RGB allocation.
        page.close(); page = None
        document.close(); document = None
        rendered = bitmap.to_pil()
        image = rendered.convert("RGB")  # one independent copy, never copy() again
        rendered.close(); rendered = None
        bitmap.close(); bitmap = None
        _log("rasterization", "after", width=image.width, height=image.height)
        if (image.width <= 0 or image.height <= 0 or
                max(image.size) > max_dimension or
                image.width * image.height > max_pixels):
            image.close()
            raise ValueError("excessive_dimensions")
        return image, (int(round(width)), int(round(height)))
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("pdf_conversion_failed") from exc
    finally:
        if rendered is not None: rendered.close()
        if bitmap is not None: bitmap.close()
        if page is not None: page.close()
        if document is not None: document.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source")
    parser.add_argument("--output-directory", required=True)
    parser.add_argument("--max-pixels", type=int, required=True)
    parser.add_argument("--max-dimension", type=int, required=True)
    parser.add_argument("--output-limit", type=int, required=True)
    parser.add_argument("--memory-mb", type=int, required=True)
    parser.add_argument("--quality", type=int, required=True)
    parser.add_argument("--source-format", choices=("jpeg", "png", "webp", "pdf"))
    args = parser.parse_args()
    output_path = None
    try:
        if hasattr(resource, "RLIMIT_AS"):
            limit = args.memory_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
        from PIL import Image, ImageOps, UnidentifiedImageError
        Image.MAX_IMAGE_PIXELS = args.max_pixels
        with open(args.source, "rb") as source:
            signature = source.read(5)
        is_pdf = args.source_format == "pdf" or signature == b"%PDF-"
        if args.source_format == "pdf" and signature != b"%PDF-":
            raise ValueError("invalid_pdf")
        if is_pdf:
            image, _pdf_page_size = _pdf_first_page(
                args.source, args.max_dimension, args.max_pixels)
            source_format = "pdf"
            source_size = image.size
        else:
            image = Image.open(args.source)
            source_format = (image.format or "").lower()
            if source_format not in {"jpeg", "png", "webp"}:
                raise ValueError("unsupported_format")
            source_size = image.size
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                source_width, source_height = source_size
                if (source_width <= 0 or source_height <= 0 or
                        source_width * source_height > args.max_pixels):
                    raise ValueError("excessive_dimensions")
                image.load()
                if is_pdf:
                    output = image
                else:
                    oriented = ImageOps.exif_transpose(image)
                    scale = min(1.0, args.max_dimension / max(oriented.size),
                                (args.max_pixels / (oriented.width * oriented.height)) ** .5)
                    size = tuple(max(1, int(value * scale)) for value in oriented.size)
                    output = (oriented.resize(size, Image.Resampling.LANCZOS)
                              if size != oriented.size else oriented)
                size = output.size
                handle = tempfile.NamedTemporaryFile(
                    prefix="anmsm-map-display-", suffix=".webp",
                    dir=args.output_directory, delete=False)
                output_path = handle.name; handle.close()
                _log("webp_encoding", "before", width=size[0], height=size[1])
                output.save(output_path, "WEBP", quality=args.quality, method=6)
                _log("webp_encoding", "after")
                if not is_pdf and output is not image:
                    output.close()
                if not is_pdf and 'oriented' in locals() and oriented is not image and oriented is not output:
                    oriented.close()
        finally:
            image.close()
        output_size = os.path.getsize(output_path)
        if output_size <= 0 or output_size > args.output_limit:
            raise ValueError("optimization_limit")
        # stdout is deliberately a tiny metadata-only protocol. File contents
        # never cross the process boundary.
        result = {"ok": True, "path": output_path, "width": size[0],
                  "height": size[1], "size": output_size, "format": "webp"}
    except MemoryError:
        result = {"ok": False, "error": "conversion_memory_limit"}
    except (OSError, UnidentifiedImageError):
        result = {"ok": False, "error": "invalid_pdf" if 'is_pdf' in locals() and is_pdf else "invalid_image"}
    except Exception as exc:
        result = {"ok": False, "error": str(exc) or "conversion_interrupted"}
    if not result["ok"] and output_path:
        try: os.unlink(output_path)
        except FileNotFoundError: pass
    print(json.dumps(result, separators=(",", ":")), flush=True)
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
