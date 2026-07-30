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

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libffi-dev \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip wheel --no-cache-dir --wheel-dir=/wheels -r requirements.txt

# ============================================================
# Stage 3: Runtime - Web service
# ============================================================
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    chromium \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /wheels /wheels
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
