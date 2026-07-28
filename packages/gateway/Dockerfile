# NaviGraph gateway service image.
#
# Build context MUST be the `packages/` directory (not `packages/gateway/`),
# since this image needs the sibling `shared` package too, e.g.:
#   docker build -f packages/gateway/Dockerfile -t navigraph-gateway packages/
# This is exactly what infra/docker-compose.yml's build context for this
# service is expected to be set to.

FROM python:3.11-slim AS base

WORKDIR /app

# System deps for building any wheels that need compilation (kept minimal).
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install the local workspace dependency first, then the service itself.
COPY shared /app/shared
RUN pip install --no-cache-dir /app/shared

COPY gateway /app/gateway
RUN pip install --no-cache-dir /app/gateway

EXPOSE 8000

CMD ["uvicorn", "navigraph_gateway.main:app", "--host", "0.0.0.0", "--port", "8000"]
