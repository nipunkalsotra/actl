"""Typed settings, loaded once (§26). No module reads os.environ directly —
everything goes through `settings` below.

Defaults mirror .env.example so `make up && make migrate && make demo`
works on a clean checkout with no manually-created .env: this is a 100%
free-tier, test-mode-only build (§01.3), so the default Razorpay key is a
harmless test-mode placeholder, never a real credential.
"""

from __future__ import annotations

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
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"
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


def _enforce_test_mode(s: Settings) -> None:
    """§21.4 — fail closed, loudly. Runs at import time, before any router is
    mounted. This build has no authorisation to move real money."""
    if not s.razorpay_key_id.startswith("rzp_test_"):
        raise SystemExit(
            "FATAL: ACTL is a test-mode-only system. "
            f"Refusing to start with key id prefix {s.razorpay_key_id[:9]!r}. "
            "This build has no authorisation to move real money."
        )


settings = Settings()
_enforce_test_mode(settings)
