# ============================================================
# Stage 1: Build the React panel
# ============================================================
FROM node:20-alpine AS frontend

WORKDIR /frontend

ARG NPM_REGISTRY=https://registry.npmmirror.com

COPY web-panel/package.json web-panel/package-lock.json ./
RUN npm ci --registry=${NPM_REGISTRY}

COPY web-panel/ ./
RUN npm run build

# ============================================================
# Stage 2: Compile wheels for all Python dependencies
# ============================================================
FROM python:3.12-slim AS builder

WORKDIR /app

ARG DEBIAN_MIRROR=https://mirrors.tuna.tsinghua.edu.cn/debian
ARG DEBIAN_SECURITY_MIRROR=https://mirrors.tuna.tsinghua.edu.cn/debian-security
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ENV TMPDIR=/data/tmp \
    PIP_CACHE_DIR=/data/pip-cache \
    PIP_INDEX_URL=${PIP_INDEX_URL} \
    PIP_DEFAULT_TIMEOUT=300

RUN mkdir -p /data/tmp /data/pip-cache /data/wheels \
    /data/apt-cache/archives/partial /data/apt-lists/partial \
    && sed -i \
        -e "s|https\\?://deb.debian.org/debian-security|${DEBIAN_SECURITY_MIRROR}|g" \
        -e "s|https\\?://deb.debian.org/debian|${DEBIAN_MIRROR}|g" \
        /etc/apt/sources.list.d/debian.sources \
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

COPY requirements.txt .
RUN --mount=type=cache,target=/data/pip-cache \
    pip wheel --retries 10 --cache-dir=/data/pip-cache --wheel-dir=/data/wheels -r requirements.txt

# ============================================================
# Stage 3: Runtime - Web service
# ============================================================
FROM python:3.12-slim

WORKDIR /app

ARG DEBIAN_MIRROR=https://mirrors.tuna.tsinghua.edu.cn/debian
ARG DEBIAN_SECURITY_MIRROR=https://mirrors.tuna.tsinghua.edu.cn/debian-security
ARG INSTALL_CHROMIUM=false
RUN mkdir -p /data/apt-cache/archives/partial /data/apt-lists/partial \
    && sed -i \
        -e "s|https\\?://deb.debian.org/debian-security|${DEBIAN_SECURITY_MIRROR}|g" \
        -e "s|https\\?://deb.debian.org/debian|${DEBIAN_MIRROR}|g" \
        /etc/apt/sources.list.d/debian.sources \
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

COPY --from=builder /data/wheels /wheels
RUN pip install --no-cache-dir /wheels/*.whl && rm -rf /wheels

COPY app/ ./app/
COPY --from=frontend /static/ ./web-panel/dist/

RUN groupadd -r appuser && useradd -r -g appuser appuser \
    && mkdir -p /app/output \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import httpx; r=httpx.get('http://localhost:8000/api/health'); r.raise_for_status(); assert r.json()['data']['status'] == 'healthy'" || exit 1

CMD ["uvicorn", "app.web.main:app", "--host", "0.0.0.0", "--port", "8000"]
