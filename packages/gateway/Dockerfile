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

# Install local workspace dependencies in dependency order, then the
# service itself.
COPY shared /app/shared
RUN pip install --no-cache-dir /app/shared

# Phase 4 of the configurable-platform build plan added navigraph-metadata-
# catalog as a real gateway dependency (TenantVerifierResolver's live
# per-tenant lookup) -- connector_sdk is metadata_catalog's own dependency
# and must be installed before it for the same reason agent_runtime's
# Dockerfile already documents.
COPY connector_sdk /app/connector_sdk
RUN pip install --no-cache-dir /app/connector_sdk

COPY metadata_catalog /app/metadata_catalog
RUN pip install --no-cache-dir /app/metadata_catalog

COPY gateway /app/gateway
RUN pip install --no-cache-dir /app/gateway

EXPOSE 8000

CMD ["uvicorn", "navigraph_gateway.main:app", "--host", "0.0.0.0", "--port", "8000"]
