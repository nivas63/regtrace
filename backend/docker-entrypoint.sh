#!/bin/sh
# Apply migrations as the owner role, warm the local models, then start the app.
set -e

echo "[entrypoint] applying database migrations"
alembic upgrade head

if [ "${WARM_MODELS:-true}" = "true" ] && [ "${EMBEDDING_BACKEND:-fastembed}" = "fastembed" ]; then
  echo "[entrypoint] warming local embedding/reranker models (first run downloads ~0.6 GB)"
  python - <<'PY'
import asyncio
from app.config import get_settings
from app.retrieval.embeddings import build_embedder, build_reranker

async def warm() -> None:
    settings = get_settings()
    await build_embedder(settings).embed_query("warm up")
    await build_reranker(settings).score("warm up", ["warm up passage"])

asyncio.run(warm())
PY
fi

exec "$@"
