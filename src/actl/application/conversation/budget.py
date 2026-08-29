"""§17.3: "Budget per transaction -- Hard ceiling of 3 LLM calls per
transaction, asserted in tests." U1's schema-repair loop can spend up to
2 calls, and so can U2's -- without a shared budget, a single transaction
that hits both repair loops plus narration could reach 5 calls. This
wraps any `LLMClient` so every U1/U2/U3 call in one transaction shares
one counter: once the ceiling is reached, every further call raises
`LLMUnavailable` immediately, without ever reaching the network -- the
caller takes the exact same safe-fallback path a real outage would.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from actl.application.ports import LLMClient, LLMUnavailable


@dataclass
class BudgetedLLMClient:
    inner: LLMClient
    max_calls: int
    calls_made: int = field(default=0, init=False)

    async def complete_json(
        self, *, system: str, user: str, max_tokens: int
    ) -> dict[str, object]:
        self._claim()
        return await self.inner.complete_json(system=system, user=user, max_tokens=max_tokens)

    async def complete_text(self, *, system: str, user: str, max_tokens: int) -> str:
        self._claim()
        return await self.inner.complete_text(system=system, user=user, max_tokens=max_tokens)

    def _claim(self) -> None:
        if self.calls_made >= self.max_calls:
            raise LLMUnavailable(
                f"LLM call budget ({self.max_calls} per transaction) already spent"
            )
        self.calls_made += 1
