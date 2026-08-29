"""§13.1 / Appendix A: GET /.well-known/agent-commerce.json -- capability
discovery so an unfamiliar agent can bootstrap without out-of-band
configuration. "actl.acp/1" is the same protocol identifier §8.4's
AgentEnvelope example already uses. `messages` (§28 P7) is the single
signed-envelope dispatch endpoint for all seven §14 message types,
including order.propose/order.status/receipt.issue; `catalog`/`quote`
remain the plain, unsigned P4 REST routes (see
docs/adr/0005-p4-catalog-quote-decisions.md decision 11 and
docs/adr/0008-p7-agent-protocol-decisions.md). Ed25519 is now live via
P7's agent identity registry; HMAC-SHA256 remains the documented
development fallback.
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
        "messages": "/agent/v1/messages",
    },
    "signing": {
        "algorithms": ["Ed25519", "HMAC-SHA256"],
    },
    "limits": {
        "quote_ttl_s": settings.quote_ttl_s,
    },
}


@router.get("/.well-known/agent-commerce.json")
async def agent_commerce_document() -> dict[str, Any]:
    return _DOCUMENT
