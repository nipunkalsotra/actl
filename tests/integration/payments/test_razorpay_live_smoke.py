"""§28 P5 instruction 8: opt-in real Razorpay test-mode smoke test.

This is the one test in the whole suite permitted to reach the real
network (api.razorpay.com). It must never run merely because real-looking
credentials happen to be present in the environment -- test isolation
correction (post-P5 review): running only on real credentials was found
to be an insufficient gate, since a developer's `.env` legitimately
carries real test-mode credentials for other purposes (manual demos, the
`provider-smoke` CLI command), and any accidental broadening of what this
suite collects would then make it fire without anyone asking for it.

Skipped unless ALL of the following hold:
  - RUN_RAZORPAY_LIVE_SMOKE=1 is set explicitly (the actual gate --
    credentials alone are never sufficient);
  - PAYMENT_PROVIDER=razorpay (or its default, which already is
    "razorpay" -- simulator-configured environments never run this);
  - RAZORPAY_KEY_ID is a real (non-placeholder) rzp_test_... key;
  - RAZORPAY_KEY_SECRET is a real (non-placeholder) secret.

Also marked `@pytest.mark.real_provider` so it can be identified or
filtered independently of the skip condition (e.g. `pytest -m
real_provider` to target only this class of test, or `-m "not
real_provider"` as an extra layer of certainty on top of the skip itself).
Normal `make test` never reaches this directory at all (it runs only
tests/unit, tests/property, tests/architecture); CI (.github/workflows/ci.yml)
runs the same restricted set and never sets RUN_RAZORPAY_LIVE_SMOKE, so it
is fully offline regardless of this test's existence.

Creates exactly one real test-mode Order (a unique receipt/idempotency key
per run) and nothing else -- no capture, no payment, no refund, matching
§28 P5's "no real payment/capture unless the architecture explicitly
requires it" (§15.4's Checkout signature step needs a payer, which this
offline test has no way to simulate against the live API). Prints only the
order id -- never a key, secret, or signature. The adapter's own explicit
timeout (settings.provider_timeout_s) applies as-is; this test does not
wrap the call in any retry -- a timeout should fail loudly and clearly,
not be silently retried and mask real latency/connectivity problems.

Run explicitly with:

    RUN_RAZORPAY_LIVE_SMOKE=1 PAYMENT_PROVIDER=razorpay \\
    RAZORPAY_KEY_ID=rzp_test_xxxxxxxxxxxxx \\
    RAZORPAY_KEY_SECRET=xxxxxxxxxxxxxxxxxxxxxxxx \\
    uv run pytest tests/integration/payments/test_razorpay_live_smoke.py -v -s -m real_provider
"""

from __future__ import annotations

import os

import pytest

from actl.config import settings
from actl.infrastructure.providers.razorpay.adapter import RazorpayAdapter
from actl.platform.ids import new_id

_RUN_FLAG = "RUN_RAZORPAY_LIVE_SMOKE"

# Two known-fake forms: config.py's own Settings default, and the literal
# template text in .env.example (`rzp_test_xxxxxxxxxxxxx` /
# `xxxxxxxxxxxxxxxxxxxxxxxx`) -- a `.env` created by copying that file
# verbatim without filling it in is the single most likely accident this
# guard needs to catch, not just config.py's own default.
_PLACEHOLDER_KEY_IDS = {"rzp_test_placeholder000000", "rzp_test_xxxxxxxxxxxxx"}
_PLACEHOLDER_KEY_SECRETS = {"test_secret_placeholder", "xxxxxxxxxxxxxxxxxxxxxxxx"}


def _looks_like_a_real_credential(value: str) -> bool:
    """Beyond the two known template strings: reject anything that is
    mostly the literal filler character 'x' (case-insensitive) -- the
    template's own pattern -- since a user might tweak the exact string
    without actually supplying a working credential."""
    if not value:
        return False
    filler_ratio = value.lower().count("x") / len(value)
    return filler_ratio < 0.5


def _has_real_test_credentials() -> bool:
    return (
        settings.payment_provider == "razorpay"
        and settings.razorpay_key_id.startswith("rzp_test_")
        and settings.razorpay_key_id not in _PLACEHOLDER_KEY_IDS
        and settings.razorpay_key_secret not in _PLACEHOLDER_KEY_SECRETS
        and _looks_like_a_real_credential(settings.razorpay_key_id)
        and _looks_like_a_real_credential(settings.razorpay_key_secret)
    )


_run_flag_set = os.environ.get(_RUN_FLAG) == "1"
_should_run = _run_flag_set and _has_real_test_credentials()

_skip_reason = (
    f"opt-in only: this test never runs merely because credentials are "
    f"present. Set {_RUN_FLAG}=1 AND PAYMENT_PROVIDER=razorpay AND a real "
    f"(non-placeholder) RAZORPAY_KEY_ID (rzp_test_...) AND "
    f"RAZORPAY_KEY_SECRET to run it -- it makes a real network call to "
    f"api.razorpay.com."
)


@pytest.mark.real_provider
@pytest.mark.skipif(not _should_run, reason=_skip_reason)
async def test_creates_one_real_test_mode_order() -> None:
    # Redundant, explicit fail-closed checks on top of the module-level
    # skip, RazorpayAdapter's own __init__ guard, and config.py's startup
    # guard -- four independent places refuse a live key or a missing
    # opt-in before this test could ever move real money or touch the
    # network unasked.
    assert _run_flag_set, f"refusing to run without {_RUN_FLAG}=1"
    assert settings.payment_provider == "razorpay", "refusing to run: PAYMENT_PROVIDER != razorpay"
    assert settings.razorpay_key_id.startswith("rzp_test_"), (
        "refusing to run against a non-test-mode key"
    )

    adapter = RazorpayAdapter(
        key_id=settings.razorpay_key_id,
        key_secret=settings.razorpay_key_secret,
        webhook_secret=settings.razorpay_webhook_secret,
        timeout_s=settings.provider_timeout_s,
    )
    try:
        # A unique reference every run -- Razorpay's `receipt` field must
        # be unique per account, and this doubles as the idempotency key
        # §15.2 would derive for a real attempt.
        key = f"ik_livesmoke_{new_id('x')[:20]}"
        order = await adapter.create_order(
            100, "INR", key, notes={"purpose": "actl P5 opt-in live smoke test"}
        )
    finally:
        await adapter.aclose()

    assert order.status == "created"
    assert order.amount_minor == 100
    assert order.currency == "INR"
    assert order.receipt == key

    print(f"real Razorpay test-mode order created: {order.id}")
