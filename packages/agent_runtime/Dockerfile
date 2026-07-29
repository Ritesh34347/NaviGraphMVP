# NaviGraph agent-runtime service image.
#
# Build context MUST be the `packages/` directory (not `packages/agent_runtime/`),
# since this image needs the sibling `shared` package too, e.g.:
#   docker build -f packages/agent_runtime/Dockerfile -t navigraph-agent-runtime packages/
# This is exactly what infra/docker-compose.yml's build context for this
# service is expected to be set to.

FROM python:3.11-slim AS base

WORKDIR /app

# System deps for building any wheels that need compilation (kept minimal).
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install local workspace dependencies in dependency order, then the
# service itself. Phase 4 added navigraph-metadata-catalog and
# navigraph-knowledge-graph as real dependencies (the new Understanding
# agents import them directly) -- connector_sdk is metadata_catalog's own
# dependency and must be installed before it for the same reason. Phase 5
# added navigraph-federation (the Data Federation agent's Trino-route
# dependency) -- it has no dependency on the others, so its position here
# only needs to be before agent_runtime's own install.
COPY shared /app/shared
RUN pip install --no-cache-dir /app/shared

COPY connector_sdk /app/connector_sdk
RUN pip install --no-cache-dir /app/connector_sdk

COPY metadata_catalog /app/metadata_catalog
RUN pip install --no-cache-dir /app/metadata_catalog

COPY knowledge_graph /app/knowledge_graph
RUN pip install --no-cache-dir /app/knowledge_graph

COPY federation /app/federation
RUN pip install --no-cache-dir /app/federation

COPY agent_runtime /app/agent_runtime
RUN pip install --no-cache-dir /app/agent_runtime

EXPOSE 8001

CMD ["uvicorn", "navigraph_agents.main:app", "--host", "0.0.0.0", "--port", "8001"]
