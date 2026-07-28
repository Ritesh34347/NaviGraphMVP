#!/usr/bin/env bash
# Smoke-test the local NaviGraph docker-compose stack: curl every service's
# health endpoint and fail loudly (non-zero exit) if any of them doesn't
# return a 2xx. Prints a summary line at the end if everything passed.
#
# Usage: tools/scripts/smoke-test.sh

set -uo pipefail

FAILURES=0

# name|url
CHECKS=(
  "gateway|http://localhost:8000/healthz"
  "agent-runtime|http://localhost:8001/healthz"
  "grafana|http://localhost:3001"
  "prometheus|http://localhost:9090/-/healthy"
  "opa|http://localhost:8181/health"
  "trino-coordinator|http://localhost:8080/v1/info"
)

echo "==> Running NaviGraph smoke test against ${#CHECKS[@]} endpoints"
echo ""

for check in "${CHECKS[@]}"; do
  name="${check%%|*}"
  url="${check##*|}"

  printf "  %-20s %-45s " "${name}" "${url}"

  http_code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "${url}" || echo "000")"

  if [[ "${http_code}" =~ ^2[0-9][0-9]$ ]]; then
    echo "OK (${http_code})"
  else
    echo "FAIL (${http_code})"
    FAILURES=$((FAILURES + 1))
  fi
done

echo ""

if [[ "${FAILURES}" -gt 0 ]]; then
  echo "!! smoke test FAILED: ${FAILURES}/${#CHECKS[@]} endpoint(s) did not return 2xx." >&2
  echo "!! Check 'docker compose -f infra/docker-compose.yml ps' and container logs." >&2
  exit 1
fi

echo "==> smoke test PASSED: all ${#CHECKS[@]} endpoints returned 2xx."
exit 0
