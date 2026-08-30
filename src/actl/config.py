"""Typed settings, loaded once (§26). No module reads os.environ directly —
everything goes through `settings` below.

Defaults mirror .env.example so `make up && make migrate && make demo`
works on a clean checkout with no manually-created .env: this is a 100%
free-tier, test-mode-only build (§01.3), so the default Razorpay key is a
harmless test-mode placeholder, never a real credential.
"""

from __future__ import annotations

import os

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ---- runtime ---------------------------------------------------------
    app_env: str = "local"
    log_level: str = "INFO"
    log_format: str = "json"

    # ---- datastores --------------------------------------------------------
    database_url: str = "postgresql+asyncpg://actl:actl@localhost:5432/actl"
    db_pool_size: int = 10
    redis_url: str = "redis://localhost:6379/0"

    # ---- payments (TEST MODE ONLY — enforced below) -----------------------
    razorpay_key_id: str = "rzp_test_placeholder000000"
    razorpay_key_secret: str = "test_secret_placeholder"
    razorpay_webhook_secret: str = "test_webhook_secret_placeholder"
    payment_provider: str = "razorpay"
    provider_timeout_s: int = 8
    reconcile_after_s: int = 45

    # ---- llm ----------------------------------------------------------------
    # §28 P8 locks GROQ_MODEL=llama-3.3-70b-versatile, but Groq retired that
    # model for free/developer-tier accounts on 2026-08-16 (confirmed on
    # console.groq.com/docs/deprecations, 13 days before this phase was
    # built) -- this is a 100%-free-tier build, so the literal architecture
    # value would fail at the API boundary on every live (non-replay) call.
    # openai/gpt-oss-120b is Groq's own primary recommended replacement,
    # confirmed to support JSON mode; see docs/adr/0009-p8-llm-decisions.md.
    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-120b"
    llm_enabled: bool = True
    llm_timeout_s: int = 12
    llm_max_calls_per_txn: int = 3
    llm_rate_limit_per_min: int = 20
    llm_cache_ttl_s: int = 86400
    demo_replay: bool = False

    # ---- policy and mandate defaults ----------------------------------------
    mandate_default_ttl_s: int = 1800
    quote_ttl_s: int = 120
    decision_ttl_s: int = 30
    reservation_ttl_s: int = 300
    max_retry_attempts: int = 3

    # ---- trust layer ----------------------------------------------------------
    audit_checkpoint_every: int = 64
    anchor_enabled: bool = False
    anchor_rpc_url: str = ""
    agent_signing_alg: str = "ed25519"

    # ---- agent commerce protocol (§13, §14) ------------------------------------
    # HMAC-SHA256 is §14.1's documented development fallback; this is a
    # 100%-test-mode build, so it is what's actually wired up for
    # quote_token signing (P4). Ed25519 arrives with P7's agent identity
    # registry. Placeholder values, same spirit as the Razorpay test key.
    quote_signing_key: str = "demo-quote-signing-key-change-me"
    admin_token: str = "demo-admin-token-change-me"
    # §28 P10 / Appendix A: "Read token" auth tier for read-only audit
    # surfaces (GET /audit/explain/{order_id}) -- deliberately a separate
    # token from admin_token, so a reviewer/dashboard credential can never
    # also mutate the catalog.
    read_token: str = "demo-read-token-change-me"

    # ---- money action gate (§11, §28 P6) ---------------------------------
    # HMAC key the gate's G1 re-verifies mandate.signature against — same
    # symmetric-secret-in-test-mode spirit as quote_signing_key above; a
    # real Ed25519 keypair-per-agent registry arrives with P7.
    mandate_signing_key: str = "demo-mandate-signing-key-change-me"

    # ---- agent-to-agent protocol (§14, §28 P7) ---------------------------
    # The merchant-agent's own Ed25519 identity, used to sign every response
    # envelope this process sends. A generated, harmless test-mode keypair —
    # same placeholder spirit as every other secret above; the private key
    # lives only here (an env-var-backed setting), never in agent_identities
    # or any other persisted table (§28 P7 instruction 2).
    merchant_agent_id: str = "agt_merchant_01"
    merchant_key_id: str = "ed25519:merchant-demo"
    merchant_private_key_hex: str = (
        "3d0881a8072b0d907fe5e29ca3b01932c9114613d64d06613c8ce6a0e3f49871"
    )

    # §14.1 documents HMAC-SHA256 as an envelope-signing "development
    # fallback" — but accepting it in any normal runtime would mean a
    # weaker signature scheme is honoured wherever Ed25519 is required.
    # Ed25519 is the only algorithm `application.agents.envelope_service.
    # verify_envelope` accepts unless this is explicitly true, and
    # `_enforce_no_hmac_outside_pytest` below refuses to even start the
    # process if it is ever true outside a pytest run — so no real .env
    # or production config can turn it on.
    agent_envelope_hmac_test_only: bool = False


def _enforce_test_mode(s: Settings) -> None:
    """§21.4 — fail closed, loudly. Runs at import time, before any router is
    mounted. This build has no authorisation to move real money."""
    if not s.razorpay_key_id.startswith("rzp_test_"):
        raise SystemExit(
            "FATAL: ACTL is a test-mode-only system. "
            f"Refusing to start with key id prefix {s.razorpay_key_id[:9]!r}. "
            "This build has no authorisation to move real money."
        )


def _enforce_no_hmac_outside_pytest(s: Settings) -> None:
    """`agent_envelope_hmac_test_only` must never be true outside a pytest
    run. PYTEST_VERSION is set by pytest itself in os.environ for the
    whole session (pytest >= 7.2), never by application config — so this
    check cannot be satisfied by any real .env/development/production
    settings file, only by actually running under pytest."""
    if s.agent_envelope_hmac_test_only and "PYTEST_VERSION" not in os.environ:
        raise SystemExit(
            "FATAL: agent_envelope_hmac_test_only is enabled outside a pytest run. "
            "The HMAC-SHA256 agent-envelope signing fallback is test-only and must "
            "never be enabled in development or production."
        )


settings = Settings()
_enforce_test_mode(settings)
_enforce_no_hmac_outside_pytest(settings)
