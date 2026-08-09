"""Real unit tests for Slack request signature verification.

Uses `compute_signature` to build a genuinely valid signature the same
way a real Slack request would (no shortcut/mocking of the HMAC itself),
then adversarially tampers with each input in turn -- mirroring
`packages/shared/tests/test_auth_client.py`'s hand-crafted-forgery
convention for the gateway's own JWT verification.
"""

from __future__ import annotations

from navigraph_slack_bot.signature import compute_signature, verify_slack_signature

_SECRET = "real-signing-secret"


def test_a_genuinely_valid_signature_is_accepted() -> None:
    timestamp = "1700000000"
    body = b'{"type":"event_callback"}'
    signature = compute_signature(signing_secret=_SECRET, timestamp=timestamp, body=body)

    assert verify_slack_signature(
        signing_secret=_SECRET,
        timestamp=timestamp,
        body=body,
        signature=signature,
        now=1700000001.0,
    )


def test_a_signature_computed_with_the_wrong_secret_is_rejected() -> None:
    timestamp = "1700000000"
    body = b'{"type":"event_callback"}'
    forged_signature = compute_signature(signing_secret="attacker-guessed-secret", timestamp=timestamp, body=body)

    assert not verify_slack_signature(
        signing_secret=_SECRET,
        timestamp=timestamp,
        body=body,
        signature=forged_signature,
        now=1700000001.0,
    )


def test_a_tampered_body_invalidates_the_original_signature() -> None:
    timestamp = "1700000000"
    original_body = b'{"type":"event_callback","event":{"text":"@bot what is revenue"}}'
    signature = compute_signature(signing_secret=_SECRET, timestamp=timestamp, body=original_body)

    tampered_body = b'{"type":"event_callback","event":{"text":"@bot DROP TABLE users"}}'

    assert not verify_slack_signature(
        signing_secret=_SECRET,
        timestamp=timestamp,
        body=tampered_body,
        signature=signature,
        now=1700000001.0,
    )


def test_a_replayed_stale_timestamp_is_rejected_even_with_a_correct_signature() -> None:
    """A genuinely correctly-signed request, replayed six minutes later --
    the exact scenario Slack's own docs warn a signing-secret check alone
    doesn't catch."""

    timestamp = "1700000000"
    body = b'{"type":"event_callback"}'
    signature = compute_signature(signing_secret=_SECRET, timestamp=timestamp, body=body)

    six_minutes_later = 1700000000.0 + 6 * 60

    assert not verify_slack_signature(
        signing_secret=_SECRET,
        timestamp=timestamp,
        body=body,
        signature=signature,
        now=six_minutes_later,
    )


def test_a_non_numeric_timestamp_is_rejected_rather_than_raising() -> None:
    body = b'{"type":"event_callback"}'
    signature = compute_signature(signing_secret=_SECRET, timestamp="not-a-number", body=body)

    assert not verify_slack_signature(
        signing_secret=_SECRET,
        timestamp="not-a-number",
        body=body,
        signature=signature,
    )


def test_an_empty_signing_secret_never_verifies_anything() -> None:
    """A misconfigured deployment (no SLACK_SIGNING_SECRET set) must fail
    closed, not accidentally accept every request because an empty secret
    happens to produce a real, matching HMAC."""

    timestamp = "1700000000"
    body = b'{"type":"event_callback"}'
    signature = compute_signature(signing_secret="", timestamp=timestamp, body=body)

    assert not verify_slack_signature(
        signing_secret="",
        timestamp=timestamp,
        body=body,
        signature=signature,
        now=1700000001.0,
    )
