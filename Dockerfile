# syntax=docker/dockerfile:1.7
# ============================================================
# Stage 1: Build the React panel
# ============================================================
FROM node:20-alpine AS frontend

WORKDIR /frontend

COPY web-panel/package.json web-panel/package-lock.json ./
RUN npm ci

COPY web-panel/ ./
RUN npm run build

# ============================================================
# Stage 2: Compile wheels for all Python dependencies
# ============================================================
FROM python:3.12-slim AS builder

WORKDIR /app

ENV TMPDIR=/data/tmp \
    PIP_CACHE_DIR=/data/pip-cache

RUN mkdir -p /data/tmp /data/pip-cache /data/wheels \
    /data/apt-cache/archives/partial /data/apt-lists/partial \
    && apt-get \
        -o Dir::Cache::archives=/data/apt-cache/archives \
        -o Dir::State::lists=/data/apt-lists \
        update \
    && apt-get \
        -o Dir::Cache::archives=/data/apt-cache/archives \
        -o Dir::State::lists=/data/apt-lists \
        install -y --no-install-recommends \
    gcc \
    libffi-dev \
    libssl-dev \
    && rm -rf /data/apt-lists/* /data/apt-cache/archives/*.deb

COPY requirements ./requirements
RUN pip wheel --cache-dir=/data/pip-cache --wheel-dir=/data/wheels -r requirements/web.txt

# ============================================================
# Stage 3: Runtime - Web service
# ============================================================
FROM python:3.12-slim

WORKDIR /app

ENV HOME=/home/appuser \
    XDG_CONFIG_HOME=/home/appuser/.config \
    XDG_CACHE_HOME=/home/appuser/.cache

ARG INSTALL_CHROMIUM=true
RUN mkdir -p /data/apt-cache/archives/partial /data/apt-lists/partial \
    && apt-get \
        -o Dir::Cache::archives=/data/apt-cache/archives \
        -o Dir::State::lists=/data/apt-lists \
        update \
    && apt-get \
        -o Dir::Cache::archives=/data/apt-cache/archives \
        -o Dir::State::lists=/data/apt-lists \
        install -y --no-install-recommends \
    ca-certificates \
    && if [ "$INSTALL_CHROMIUM" = "true" ]; then \
        apt-get \
            -o Dir::Cache::archives=/data/apt-cache/archives \
            -o Dir::State::lists=/data/apt-lists \
            install -y --no-install-recommends chromium; \
    fi \
    && rm -rf /data/apt-lists/* /data/apt-cache/archives/*.deb

RUN --mount=type=bind,from=builder,source=/data/wheels,target=/wheels,readonly \
    pip install --no-cache-dir /wheels/*.whl

COPY app/ ./app/
COPY --from=frontend /static/ ./web-panel/dist/

RUN groupadd -r appuser && useradd -r -m -d /home/appuser -g appuser appuser \
    && mkdir -p /app/output /home/appuser/.config /home/appuser/.cache \
    && chown -R appuser:appuser /app /home/appuser
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import httpx; r=httpx.get('http://localhost:8000/api/health'); r.raise_for_status(); payload=r.json(); assert payload['data']['status'] == 'healthy'; assert payload['data']['services']['browser'] == 'available'" || exit 1

CMD ["uvicorn", "app.web.main:app", "--host", "0.0.0.0", "--port", "8000"]
