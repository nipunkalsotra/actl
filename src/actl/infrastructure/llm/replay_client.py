"""§28 P8 instruction 6: DEMO_REPLAY -- serves committed, versioned
fixtures instead of ever calling Groq. Keyed by the exact same
`canonical_prompt_key` the semantic cache uses (infrastructure/llm/
canonical_prompt.py), so a cassette is simply "what the semantic cache
would have written," recorded once and committed. A prompt with no
matching cassette is a normal, expected `LLMUnavailable` -- not an
error -- so replay runs exercise the same deterministic-fallback path a
real outage would.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from actl.application.ports import LLMUnavailable
from actl.infrastructure.llm.canonical_prompt import canonical_prompt_key
from actl.infrastructure.llm.health import LLMHealth


class ReplayLLMClient:
    def __init__(self, *, cassette_dir: Path, model: str, health: LLMHealth | None = None) -> None:
        self._dir = cassette_dir
        self._model = model
        self._health = health or LLMHealth()

    async def complete_json(self, *, system: str, user: str, max_tokens: int) -> dict[str, object]:
        result = self._load(mode="json", system=system, user=user)
        if not isinstance(result, dict):
            raise LLMUnavailable("cassette response was not a JSON object")
        return cast(dict[str, object], result)

    async def complete_text(self, *, system: str, user: str, max_tokens: int) -> str:
        result = self._load(mode="text", system=system, user=user)
        if not isinstance(result, str):
            raise LLMUnavailable("cassette response was not text")
        return result

    def _load(self, *, mode: str, system: str, user: str) -> object:
        cache_key = canonical_prompt_key(mode=mode, model=self._model, system=system, user=user)
        path = self._dir / f"{cache_key}.json"
        if not path.exists():
            raise LLMUnavailable(f"no DEMO_REPLAY cassette recorded for this prompt ({cache_key})")
        cassette = json.loads(path.read_text())
        self._health.mark_success()
        return cassette["response"]
