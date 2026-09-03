"""Isolated, resource-limited piste-map decoder/resizer."""
import argparse, json, os, resource, sys, warnings

def main():
    p = argparse.ArgumentParser(); p.add_argument("source"); p.add_argument("output")
    p.add_argument("--max-pixels", type=int, required=True); p.add_argument("--max-dimension", type=int, required=True)
    p.add_argument("--output-limit", type=int, required=True); p.add_argument("--memory-mb", type=int, required=True)
    a = p.parse_args()
    try:
        if hasattr(resource, "RLIMIT_AS"):
            n=a.memory_mb*1024*1024; resource.setrlimit(resource.RLIMIT_AS,(n,n))
        from PIL import Image, ImageOps, UnidentifiedImageError
        Image.MAX_IMAGE_PIXELS=a.max_pixels
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(a.source) as im:
                im.seek(0); fmt=(im.format or "").lower(); w,h=im.size
                if fmt not in {"jpeg","png","webp"}: raise ValueError("unsupported_format")
                if w<=0 or h<=0 or w*h>a.max_pixels: raise ValueError("excessive_dimensions")
                im.load(); oriented=ImageOps.exif_transpose(im)
                scale=min(1.0,a.max_dimension/max(oriented.size)); size=tuple(max(1,round(x*scale)) for x in oriented.size)
                out=oriented.resize(size,Image.Resampling.LANCZOS) if size != oriented.size else oriented.copy()
                try: out.convert("RGB").save(a.output,"WEBP",quality=90,method=6)
                finally: out.close()
        out_size=os.path.getsize(a.output)
        if out_size<=0 or out_size>a.output_limit: raise ValueError("optimization_limit")
        result={"ok":True,"metadata":{"source_format":fmt,"source_width":w,"source_height":h,
                "display_width":size[0],"display_height":size[1],"display_size_bytes":out_size,"warnings":[]}}
    except (OSError, UnidentifiedImageError): result={"ok":False,"error":"invalid_image"}
    except Exception as exc: result={"ok":False,"error":str(exc) or "conversion_interrupted"}
    sys.stdout.write(json.dumps(result,separators=(",",":"))); return 0 if result["ok"] else 2
if __name__ == "__main__": raise SystemExit(main())
