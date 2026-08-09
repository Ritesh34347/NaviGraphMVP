"""Real Slack request signature verification.

Implements Slack's documented signing algorithm exactly
(https://api.slack.com/authentication/verifying-requests-from-slack):
`basestring = f"v0:{timestamp}:{body}"`, HMAC-SHA256 with the app's
signing secret, hex-digested, prefixed `v0=`. Every real request Slack
sends carries `X-Slack-Signature` and `X-Slack-Request-Timestamp`
headers computed this way -- `main.py` rejects any request whose
signature doesn't match, or whose timestamp is stale, BEFORE looking at
the body at all, mirroring `navigraph_shared.auth`'s established
"verify first, trust nothing else about the request until you have"
discipline for the gateway's own bearer-token verification.
"""

from __future__ import annotations

import hashlib
import hmac
import time

# Slack's own documented replay-window recommendation.
_MAX_TIMESTAMP_AGE_SECONDS = 60 * 5


def compute_signature(*, signing_secret: str, timestamp: str, body: bytes) -> str:
    """Compute the `v0=<hex>` signature Slack expects for a request with
    this exact `timestamp` and raw `body`. Exposed (not just used
    internally) so tests can construct a genuinely valid signature the
    same way a real Slack request would, rather than hand-waving one."""

    basestring = f"v0:{timestamp}:".encode() + body
    digest = hmac.new(signing_secret.encode("utf-8"), basestring, hashlib.sha256).hexdigest()
    return f"v0={digest}"


def verify_slack_signature(
    *,
    signing_secret: str,
    timestamp: str,
    body: bytes,
    signature: str,
    now: float | None = None,
) -> bool:
    """Return True only if `signature` is a genuine Slack signature for
    this exact `(timestamp, body)` pair, computed with `signing_secret`,
    AND `timestamp` is recent enough to not be a replayed old request.

    `now` is injectable so tests can exercise the staleness check
    deterministically without depending on wall-clock timing; real
    callers should never pass it (defaults to `time.time()`).
    """

    if not signing_secret:
        return False

    try:
        timestamp_value = int(timestamp)
    except ValueError:
        return False

    current_time = now if now is not None else time.time()
    if abs(current_time - timestamp_value) > _MAX_TIMESTAMP_AGE_SECONDS:
        return False

    expected = compute_signature(signing_secret=signing_secret, timestamp=timestamp, body=body)
    # `hmac.compare_digest` -- constant-time, not `==` -- exactly the same
    # timing-attack-resistant comparison this codebase already uses for
    # bearer-token/JWT-adjacent checks elsewhere.
    return hmac.compare_digest(expected, signature)
