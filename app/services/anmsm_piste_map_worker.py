"""Isolated, resource-limited image/PDF decoder. Never import this in Gunicorn."""
import argparse
import json
import os
import resource
import sys
import warnings


def _pdf_first_page(source, max_pages, max_dimension, max_pixels):
    """Rasterize one bounded PDF page with the pip-installed PyMuPDF wheel."""
    import pymupdf

    document = None
    try:
        try:
            document = pymupdf.open(source)
        except Exception as exc:
            raise ValueError("invalid_pdf") from exc
        if document.needs_pass or document.page_count < 1:
            raise ValueError("invalid_pdf")
        if document.page_count > max_pages:
            raise ValueError("pdf_page_limit")
        try:
            page = document.load_page(0)
            width, height = float(page.rect.width), float(page.rect.height)
            if width <= 0 or height <= 0:
                raise ValueError("invalid_pdf")
            scale = min(max_dimension / max(width, height),
                        (max_pixels / (width * height)) ** .5)
            if scale <= 0:
                raise ValueError("excessive_dimensions")
            pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale),
                                     colorspace=pymupdf.csRGB, alpha=False)
            if pixmap.width <= 0 or pixmap.height <= 0 or pixmap.width * pixmap.height > max_pixels:
                raise ValueError("excessive_dimensions")
            return pixmap.width, pixmap.height, bytes(pixmap.samples)
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("pdf_conversion_failed") from exc
    finally:
        if document is not None:
            document.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source"); parser.add_argument("output")
    parser.add_argument("--max-pixels", type=int, required=True)
    parser.add_argument("--max-dimension", type=int, required=True)
    parser.add_argument("--output-limit", type=int, required=True)
    parser.add_argument("--memory-mb", type=int, required=True)
    parser.add_argument("--max-pages", type=int, required=True)
    parser.add_argument("--source-format", choices=("jpeg", "png", "webp", "pdf"))
    args = parser.parse_args()
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
            width, height, samples = _pdf_first_page(
                args.source, args.max_pages, args.max_dimension, args.max_pixels)
            image = Image.frombytes("RGB", (width, height), samples)
            source_format = "pdf"
        else:
            image = Image.open(args.source)
            source_format = (image.format or "").lower()
            if source_format not in {"jpeg", "png", "webp"}:
                raise ValueError("unsupported_format")
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                source_width, source_height = image.size
                if (source_width <= 0 or source_height <= 0 or
                        source_width * source_height > args.max_pixels):
                    raise ValueError("excessive_dimensions")
                image.load()
                oriented = ImageOps.exif_transpose(image)
                scale = min(1.0, args.max_dimension / max(oriented.size))
                size = tuple(max(1, round(value * scale)) for value in oriented.size)
                output = (oriented.resize(size, Image.Resampling.LANCZOS)
                          if size != oriented.size else oriented.copy())
                try:
                    output.convert("RGB").save(args.output, "WEBP", quality=90, method=6)
                finally:
                    output.close()
                    if oriented is not image:
                        oriented.close()
        finally:
            image.close()
        output_size = os.path.getsize(args.output)
        if output_size <= 0 or output_size > args.output_limit:
            raise ValueError("optimization_limit")
        result = {"ok": True, "metadata": {
            "source_format": source_format,
            "source_width": source_width, "source_height": source_height,
            "display_width": size[0], "display_height": size[1],
            "display_size_bytes": output_size, "warnings": [],
        }}
    except (OSError, UnidentifiedImageError):
        result = {"ok": False, "error": "invalid_pdf" if 'is_pdf' in locals() and is_pdf else "invalid_image"}
    except Exception as exc:
        result = {"ok": False, "error": str(exc) or "conversion_interrupted"}
    sys.stdout.write(json.dumps(result, separators=(",", ":")))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
