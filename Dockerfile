FROM python:3.12.10-slim-bookworm AS builder

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir .

FROM python:3.12.10-slim-bookworm AS api

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080
WORKDIR /app
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
RUN useradd --create-home --uid 10001 appuser
USER appuser
EXPOSE 8080
CMD ["uvicorn", "climatelens.api:app", "--host", "0.0.0.0", "--port", "8080"]
