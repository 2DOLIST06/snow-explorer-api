FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends poppler-utils && rm -rf /var/lib/apt/lists/*
RUN command -v pdfinfo && command -v pdftoppm && pdfinfo -v && pdftoppm -v
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["sh", "-c", "command -v pdfinfo && command -v pdftoppm && exec gunicorn -w 2 -b 0.0.0.0:${PORT:-5001} --timeout 90 app.main:app"]
