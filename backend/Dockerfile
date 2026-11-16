# API and worker image. Build from the repo root (it needs db/migrations):
#   docker build -f backend/Dockerfile -t agentic-rag-api .
# API:    uvicorn app.main:app (the default command; applies pending migrations on startup)
# Worker: python -m app.worker  (also applies pending migrations first)
FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.10.8 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1 FASTEMBED_CACHE_PATH=/opt/fastembed

WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
# Bake the embedding model into the image so containers start without downloading it.
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5')"

COPY db /app/db
COPY backend/app ./app

RUN useradd --system --uid 10001 --home-dir /app app
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health', timeout=4)" || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
