# ─────────────────────────────────────────────────────────────────────────────
# StrmVert — single-container image (pure Python, no Node build step).
#   docker compose up --build
# The image version comes from the top-level VERSION file (via --build-arg in CI).
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /app

# ---- builder: resolve + install dependencies (and the app) into /install ----
FROM base AS builder
COPY pyproject.toml VERSION ./
COPY app ./app
RUN pip install --prefix=/install .

# ---- runtime ---------------------------------------------------------------
FROM base AS runtime
ARG VERSION=0.0.0
ENV DATA_DIR=/app/data \
    MEDIA_ROOT=/VODS \
    STRMVERT_VERSION=$VERSION
# The app package (with its templates/ and static/ package-data) is installed here.
COPY --from=builder /install /usr/local
COPY VERSION ./VERSION
RUN mkdir -p /app/data /VODS
# Runs as root by default so the bind-mounted ./data and your /VODS library are
# always writable (same convention as dispatcharr / teamarr / most *arr images).
# To run as a specific user instead, set `user: "1000:1000"` in docker-compose.yml
# and make sure that uid owns ./data and MEDIA_ROOT on the host.
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz').status==200 else 1)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
