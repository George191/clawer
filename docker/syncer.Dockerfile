# syntax=docker/dockerfile:1.7
# ============================================================
# Stage 1: Build — compile wheels for all dependencies
# ============================================================
FROM python:3.12-slim AS builder

WORKDIR /app

ENV TMPDIR=/data/tmp \
    PIP_CACHE_DIR=/data/pip-cache \
    PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
    PIP_TRUSTED_HOST=pypi.tuna.tsinghua.edu.cn

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
RUN pip wheel --cache-dir=/data/pip-cache --wheel-dir=/data/wheels -r requirements/syncer.txt

# ============================================================
# Stage 2: Runtime — Syncer Service
# ============================================================
FROM python:3.12-slim

WORKDIR /app

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
    && rm -rf /data/apt-lists/* /data/apt-cache/archives/*.deb

RUN --mount=type=bind,from=builder,source=/data/wheels,target=/wheels,readonly \
    pip install --no-cache-dir /wheels/*.whl

COPY app/ ./app/

RUN groupadd -r appuser && useradd -r -g appuser appuser \
    && chown -R appuser:appuser /app
USER appuser

CMD ["python", "-m", "app.syncer.main"]
