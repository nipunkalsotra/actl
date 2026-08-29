"""§17.3: "SHA-256 of the normalised prompt" -- the exact request shape
(mode, model, system, user; temperature is always 0, §17.2) canonicalised
via the same RFC 8785 JCS this build already uses for spec_hash/entry_hash
(§16.1), then sha256'd. One function, shared by the semantic cache
(infrastructure/cache/semantic_cache.py) and DEMO_REPLAY's cassette
lookup (infrastructure/llm/replay_client.py) -- a cassette recorded for an
exact prompt is found by the same key a live cache write would use.
"""

from __future__ import annotations

import hashlib

from actl.domain.audit.canonical import JSONValue, jcs


def canonical_prompt_key(*, mode: str, model: str, system: str, user: str) -> str:
    payload: dict[str, JSONValue] = {
        "mode": mode,
        "model": model,
        "system": system,
        "user": user,
        "temperature": 0,
    }
    return hashlib.sha256(jcs(payload).encode("utf-8")).hexdigest()
