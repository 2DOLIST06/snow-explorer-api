"""Isolated, resource-limited image/PDF decoder. Never import this in Gunicorn."""
import argparse, json, os, resource, subprocess, sys, tempfile, warnings

def main():
    p = argparse.ArgumentParser(); p.add_argument("source"); p.add_argument("output")
    p.add_argument("--max-pixels", type=int, required=True); p.add_argument("--max-dimension", type=int, required=True)
    p.add_argument("--output-limit", type=int, required=True); p.add_argument("--memory-mb", type=int, required=True)
    p.add_argument("--max-pages", type=int, required=True)
    p.add_argument("--source-format", choices=("jpeg","png","webp","pdf"))
    a = p.parse_args()
    try:
        if hasattr(resource, "RLIMIT_AS"):
            n=a.memory_mb*1024*1024; resource.setrlimit(resource.RLIMIT_AS,(n,n))
        from PIL import Image, ImageOps, UnidentifiedImageError
        Image.MAX_IMAGE_PIXELS=a.max_pixels
        raster=None
        with open(a.source,"rb") as source: signature=source.read(5)
        is_pdf=a.source_format=="pdf" or signature==b"%PDF-"
        if a.source_format=="pdf" and signature!=b"%PDF-": raise ValueError("invalid_pdf")
        if is_pdf:
            info=subprocess.run(["pdfinfo",a.source],capture_output=True,text=True,check=False)
            if info.returncode: raise ValueError("invalid_pdf")
            pages=next((line.split(":",1)[1].strip() for line in info.stdout.splitlines() if line.startswith("Pages:")),"")
            if not pages.isdigit() or int(pages)<1: raise ValueError("invalid_pdf")
            if int(pages)>a.max_pages: raise ValueError("pdf_page_limit")
            directory=tempfile.mkdtemp(prefix="anmsm-pdf-"); prefix=os.path.join(directory,"page")
            rendered=subprocess.run(["pdftoppm","-f","1","-l","1","-singlefile","-png","-scale-to",str(a.max_dimension),a.source,prefix],capture_output=True,check=False)
            raster=prefix+".png"
            if rendered.returncode or not os.path.isfile(raster): raise ValueError("pdf_conversion_failed")
            image_source=raster
        else: image_source=a.source
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(image_source) as im:
                im.seek(0); fmt=(im.format or "").lower(); w,h=im.size
                if (not is_pdf and fmt not in {"jpeg","png","webp"}) or (is_pdf and fmt!="png"): raise ValueError("unsupported_format")
                if w<=0 or h<=0 or w*h>a.max_pixels: raise ValueError("excessive_dimensions")
                im.load(); oriented=ImageOps.exif_transpose(im)
                scale=min(1.0,a.max_dimension/max(oriented.size)); size=tuple(max(1,round(x*scale)) for x in oriented.size)
                out=oriented.resize(size,Image.Resampling.LANCZOS) if size != oriented.size else oriented.copy()
                try: out.convert("RGB").save(a.output,"WEBP",quality=90,method=6)
                finally: out.close()
        out_size=os.path.getsize(a.output)
        if out_size<=0 or out_size>a.output_limit: raise ValueError("optimization_limit")
        result={"ok":True,"metadata":{"source_format":"pdf" if is_pdf else fmt,"source_width":w,"source_height":h,
                "display_width":size[0],"display_height":size[1],"display_size_bytes":out_size,"warnings":[]}}
    except (OSError, UnidentifiedImageError): result={"ok":False,"error":"invalid_pdf" if 'is_pdf' in locals() and is_pdf else "invalid_image"}
    except Exception as exc: result={"ok":False,"error":str(exc) or "conversion_interrupted"}
    finally:
        if 'raster' in locals() and raster:
            try: os.unlink(raster)
            except FileNotFoundError: pass
            try: os.rmdir(os.path.dirname(raster))
            except OSError: pass
    sys.stdout.write(json.dumps(result,separators=(",",":"))); return 0 if result["ok"] else 2
if __name__ == "__main__": raise SystemExit(main())
