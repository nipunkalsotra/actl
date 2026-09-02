"""Tracks whether the configured LLM adapter (Groq or replay) has
completed at least one real request successfully since process start.
Purely a read signal for `/buyer/v1/config`'s `llm_status` field -- it
never gates a call (the circuit breaker already does that) and carries no
request/response content, only a boolean. One instance per process,
built in `actl.main`'s lifespan and handed to both the router (via
`app.state`) and the concrete adapter (via its constructor) -- the same
composition-root pattern `app.state.breaker` already uses.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class LLMHealth:
    _succeeded: bool = field(default=False, init=False)

    def mark_success(self) -> None:
        self._succeeded = True

    @property
    def succeeded_once(self) -> bool:
        return self._succeeded
