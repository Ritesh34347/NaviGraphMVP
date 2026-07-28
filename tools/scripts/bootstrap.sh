#!/usr/bin/env bash
# Bootstrap the local NaviGraph dev environment: copy the env template if
# needed, then bring up the full docker-compose stack.
#
# Usage: tools/scripts/bootstrap.sh
# Can be run from anywhere -- it resolves paths relative to the repo root.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

ENV_EXAMPLE="${REPO_ROOT}/infra/.env.example"
ENV_FILE="${REPO_ROOT}/infra/.env"
COMPOSE_FILE="${REPO_ROOT}/infra/docker-compose.yml"

echo "==> NaviGraph bootstrap"
echo "    repo root: ${REPO_ROOT}"

if [[ ! -f "${ENV_FILE}" ]]; then
  if [[ -f "${ENV_EXAMPLE}" ]]; then
    echo "==> infra/.env not found -- copying from infra/.env.example"
    cp "${ENV_EXAMPLE}" "${ENV_FILE}"
  else
    echo "!! infra/.env.example not found at ${ENV_EXAMPLE} -- cannot create infra/.env." >&2
    echo "!! This is owned by the infra workstream; create it (or infra/.env directly) before continuing." >&2
    exit 1
  fi
else
  echo "==> infra/.env already exists -- leaving it untouched"
fi

if [[ ! -f "${COMPOSE_FILE}" ]]; then
  echo "!! infra/docker-compose.yml not found at ${COMPOSE_FILE}." >&2
  echo "!! This is owned by the infra workstream and must exist before bootstrap can bring up the stack." >&2
  exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "!! docker is not installed or not on PATH. Install Docker Desktop (or the docker CLI) and retry." >&2
  exit 1
fi

echo "==> Building and starting the full stack (docker compose up -d --build)"
docker compose -f "${COMPOSE_FILE}" up -d --build

echo ""
echo "==> Stack is starting in the background. Containers take a bit to become healthy"
echo "    (Neo4j and Trino in particular can take 30-60s on first boot)."
echo ""
echo "    Once everything reports healthy, run the smoke test:"
echo ""
echo "        tools/scripts/smoke-test.sh"
echo ""
echo "    To watch container status in the meantime:"
echo ""
echo "        docker compose -f infra/docker-compose.yml ps"
echo ""
