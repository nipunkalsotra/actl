"""§28 P8 instruction 1: "LLM_ENABLED=false must completely disable Groq
calls." `NullLLMClient` never constructs a Groq client, never touches the
network, never reads GROQ_API_KEY -- it raises `LLMUnavailable`
immediately on every call, so every U1/U2/U3 caller takes the exact same
deterministic-fallback path LLM_ENABLED=true + a live outage would.
"""

from __future__ import annotations

from actl.application.ports import LLMUnavailable


class NullLLMClient:
    async def complete_json(
        self, *, system: str, user: str, max_tokens: int
    ) -> dict[str, object]:
        raise LLMUnavailable("LLM_ENABLED=false")

    async def complete_text(self, *, system: str, user: str, max_tokens: int) -> str:
        raise LLMUnavailable("LLM_ENABLED=false")
