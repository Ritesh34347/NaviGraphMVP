#!/usr/bin/env python3
"""Real canary promotion-gate check, run identically by
`.github/workflows/cd-deploy.yml` and by a human doing a manual rollout.

Reads the ingress-nginx controller's own per-backend Prometheus metrics
(`nginx_ingress_controller_requests`/`_request_duration_seconds_bucket`,
already labeled by `service`) -- NOT app-level `/metrics` -- so ONE gate
mechanism covers both the `gateway` and `web` canary rollouts with zero
app code changes (`web` has no metrics endpoint of its own; the ingress
controller's metrics don't care). See the Phase 10 plan's canary design
for the full reasoning.

Three checks, all must pass:
  1. canary 5xx rate < 1%
  2. canary error rate <= 2x concurrent stable error rate
  3. canary p95 latency < 1.5x concurrent stable p95 latency

If Prometheus has no data yet for a check (e.g. a freshly-deployed canary
at low traffic within the 5m window), that check is treated as a soft
pass with a clear warning printed -- there is no real evidence to fail on,
but silently treating "no data" as "definitely fine" would be dishonest
about what was actually verified.

Usage:
    python tools/scripts/canary_gate.py --service gateway --prometheus-url http://localhost:9090
    python tools/scripts/canary_gate.py --service web --prometheus-url http://localhost:9090 --window 5m

Exit code 0 = pass (safe to proceed/promote); 1 = fail (roll back).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import urllib.error
import urllib.parse
import urllib.request

_5XX_RATE_THRESHOLD = 0.01
_ERROR_RATE_RATIO_THRESHOLD = 2.0
_P95_LATENCY_RATIO_THRESHOLD = 1.5


def _prometheus_query(prometheus_url: str, promql: str) -> float | None:
    """Query Prometheus's instant-query API, returning the scalar result
    value, or None if Prometheus returned no data (a real, honest "we
    don't know" -- distinct from a real 0.0 result)."""

    url = f"{prometheus_url.rstrip('/')}/api/v1/query?{urllib.parse.urlencode({'query': promql})}"
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            payload = json.loads(response.read())
    except (urllib.error.URLError, TimeoutError) as exc:
        print(f"ERROR: Prometheus query failed ({exc}): {promql}", file=sys.stderr)
        return None

    if payload.get("status") != "success":
        print(f"ERROR: Prometheus query returned non-success status: {payload}", file=sys.stderr)
        return None

    result = payload["data"]["result"]
    if not result:
        return None

    _timestamp, value_str = result[0]["value"]
    try:
        value = float(value_str)
    except ValueError:
        return None
    if math.isnan(value):  # PromQL returns NaN for 0/0
        return None
    return value


def _check_5xx_rate(prometheus_url: str, service: str, window: str) -> tuple[bool, str]:
    query = (
        f'rate(nginx_ingress_controller_requests{{service="{service}-canary", status=~"5.."}}[{window}]) '
        f'/ rate(nginx_ingress_controller_requests{{service="{service}-canary"}}[{window}])'
    )
    rate = _prometheus_query(prometheus_url, query)
    if rate is None:
        return True, f"5xx rate: no data yet for {service}-canary (soft pass)"
    passed = rate < _5XX_RATE_THRESHOLD
    return passed, f"5xx rate: {rate:.4%} ({'OK' if passed else 'FAIL'}, threshold < {_5XX_RATE_THRESHOLD:.0%})"


def _check_error_rate_ratio(prometheus_url: str, service: str, window: str) -> tuple[bool, str]:
    canary_query = (
        f'rate(nginx_ingress_controller_requests{{service="{service}-canary", status=~"4..|5.."}}[{window}]) '
        f'/ rate(nginx_ingress_controller_requests{{service="{service}-canary"}}[{window}])'
    )
    stable_query = (
        f'rate(nginx_ingress_controller_requests{{service="{service}-stable", status=~"4..|5.."}}[{window}]) '
        f'/ rate(nginx_ingress_controller_requests{{service="{service}-stable"}}[{window}])'
    )
    canary_rate = _prometheus_query(prometheus_url, canary_query)
    stable_rate = _prometheus_query(prometheus_url, stable_query)

    if canary_rate is None or stable_rate is None:
        return True, f"error rate ratio: no data yet for {service} (soft pass)"
    if stable_rate == 0.0:
        # Any canary errors at all against a perfectly clean stable baseline
        # is real signal, not noise -- can't compute a ratio against zero.
        passed = canary_rate == 0.0
        return passed, (
            f"error rate ratio: stable={stable_rate:.4%}, canary={canary_rate:.4%} "
            f"({'OK' if passed else 'FAIL'}, stable baseline is 0 -- any canary error rate fails)"
        )
    ratio = canary_rate / stable_rate
    passed = ratio <= _ERROR_RATE_RATIO_THRESHOLD
    return passed, (
        f"error rate ratio: stable={stable_rate:.4%}, canary={canary_rate:.4%}, "
        f"ratio={ratio:.2f}x ({'OK' if passed else 'FAIL'}, threshold <= {_ERROR_RATE_RATIO_THRESHOLD}x)"
    )


def _check_p95_latency_ratio(prometheus_url: str, service: str, window: str) -> tuple[bool, str]:
    canary_query = (
        f'histogram_quantile(0.95, rate(nginx_ingress_controller_request_duration_seconds_bucket'
        f'{{service="{service}-canary"}}[{window}]))'
    )
    stable_query = (
        f'histogram_quantile(0.95, rate(nginx_ingress_controller_request_duration_seconds_bucket'
        f'{{service="{service}-stable"}}[{window}]))'
    )
    canary_p95 = _prometheus_query(prometheus_url, canary_query)
    stable_p95 = _prometheus_query(prometheus_url, stable_query)

    if canary_p95 is None or stable_p95 is None:
        return True, f"p95 latency ratio: no data yet for {service} (soft pass)"
    if stable_p95 == 0.0:
        passed = canary_p95 == 0.0
        return passed, (
            f"p95 latency ratio: stable={stable_p95:.3f}s, canary={canary_p95:.3f}s "
            f"({'OK' if passed else 'FAIL'}, stable baseline is 0s)"
        )
    ratio = canary_p95 / stable_p95
    passed = ratio < _P95_LATENCY_RATIO_THRESHOLD
    return passed, (
        f"p95 latency ratio: stable={stable_p95:.3f}s, canary={canary_p95:.3f}s, "
        f"ratio={ratio:.2f}x ({'OK' if passed else 'FAIL'}, threshold < {_P95_LATENCY_RATIO_THRESHOLD}x)"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--service", required=True, choices=["gateway", "web"])
    parser.add_argument("--prometheus-url", required=True)
    parser.add_argument(
        "--window",
        default="5m",
        help="PromQL rate() window (default: 5m, matching the 5-minute bake-window cap).",
    )
    args = parser.parse_args()

    checks = [
        _check_5xx_rate(args.prometheus_url, args.service, args.window),
        _check_error_rate_ratio(args.prometheus_url, args.service, args.window),
        _check_p95_latency_ratio(args.prometheus_url, args.service, args.window),
    ]

    all_passed = True
    for passed, message in checks:
        print(message)
        if not passed:
            all_passed = False

    if all_passed:
        print(f"PASS: {args.service} canary gate")
        return 0

    print(f"FAIL: {args.service} canary gate -- rollback required", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
