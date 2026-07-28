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

# Install the local workspace dependency first, then the service itself.
COPY shared /app/shared
RUN pip install --no-cache-dir /app/shared

COPY agent_runtime /app/agent_runtime
RUN pip install --no-cache-dir /app/agent_runtime

EXPOSE 8001

CMD ["uvicorn", "navigraph_agents.main:app", "--host", "0.0.0.0", "--port", "8001"]
