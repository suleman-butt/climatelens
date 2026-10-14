FROM python:3.12.10-slim-bookworm AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
WORKDIR /app

# Dependencies first, hash-checked against the lock: this layer is reused until
# requirements.lock changes, so code-only changes rebuild in seconds.
COPY requirements.lock ./
RUN pip install --require-hashes -r requirements.lock

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-deps .

FROM python:3.12.10-slim-bookworm AS api

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH \
    PORT=8080 \
    CLIMATELENS_DATA_DIR=/app/data \
    CLIMATELENS_MODEL_DIR=/app/models
# LightGBM needs the OpenMP runtime, which slim images do not ship.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*
RUN useradd --create-home --uid 10001 appuser
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
RUN mkdir -p /app/data /app/models && chown -R appuser:appuser /app
USER appuser
EXPOSE 8080
# Cloud Run injects $PORT; the shell form lets it through, exec keeps uvicorn PID 1.
CMD ["sh", "-c", "exec uvicorn climatelens.api:app --host 0.0.0.0 --port ${PORT}"]
