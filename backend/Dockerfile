FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Tesseract enables the OCR fallback for scanned PDF pages (prototype capability).
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

# Dependencies first so code edits don't invalidate the dependency layer.
COPY pyproject.toml ./
RUN mkdir app && touch app/__init__.py \
    && pip install . \
    && rm -rf app

COPY app ./app
COPY scripts ./scripts
COPY prompts ./prompts
COPY migrations ./migrations
COPY alembic.ini docker-entrypoint.sh ./
RUN pip install --no-deps . && chmod +x docker-entrypoint.sh

RUN useradd --create-home --uid 10001 regtrace \
    && mkdir -p /data/uploads /data/models /data/corpus \
    && chown -R regtrace /data
USER regtrace

ENV TESSDATA_PREFIX=/usr/share/tesseract-ocr/5/tessdata

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=60s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health/live', timeout=2).status == 200 else 1)"

ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["uvicorn", "--factory", "app.main:create_app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
