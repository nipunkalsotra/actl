"""§13.1 / Appendix A: GET /.well-known/agent-commerce.json -- capability
discovery so an unfamiliar agent can bootstrap without out-of-band
configuration. Advertises only what's actually live in P4:
order.propose/receipt.issue (§14) arrive with P7's agent envelope layer,
Ed25519 with P7's agent identity registry (§14.1) -- HMAC-SHA256 is the
documented development fallback and what's actually wired up (P4's
quote_token). "actl.acp/1" is the same protocol identifier §8.4's
AgentEnvelope example already uses.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from actl.config import settings

router = APIRouter()

_DOCUMENT: dict[str, Any] = {
    "protocol": "actl.acp/1",
    "currency": "INR",
    "endpoints": {
        "catalog": "/agent/v1/catalog",
        "quote": "/agent/v1/quote",
    },
    "signing": {
        "algorithms": ["HMAC-SHA256"],
    },
    "limits": {
        "quote_ttl_s": settings.quote_ttl_s,
    },
}


@router.get("/.well-known/agent-commerce.json")
async def agent_commerce_document() -> dict[str, Any]:
    return _DOCUMENT
