"""§17.1 U2: candidate ranking over an ALREADY-filtered list
(§28 P7 instruction 6's `domain.agent.buyer.filter_candidates`, unchanged
this phase). The LLM is a ranking *hint* only: `RankingResult.degraded`
tells the caller whether the deterministic scorer had to run instead, but
either way the returned items are exactly the same, already-filtered
CatalogItem set -- the LLM cannot add a candidate, relax a constraint, or
modify a price, by construction (`domain.agent.buyer.apply_llm_ranking`
only ever reorders the objects the deterministic filter already produced).
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field

from actl.application.ports import LLMClient, LLMUnavailable
from actl.domain.agent.buyer import apply_llm_ranking, filter_candidates, rank
from actl.domain.catalog.models import CatalogItem
from actl.domain.mandate.models import Mandate
from actl.infrastructure.llm.prompts.ranking import (
    SYSTEM_PROMPT,
    build_repair_prompt,
    build_user_prompt,
)
from actl.infrastructure.llm.repair import complete_json_with_repair

MAX_TOKENS = 400


class _RankingResponse(BaseModel):
    ranked_skus: list[str]
    rationale: dict[str, str] = Field(default_factory=dict)


@dataclass(frozen=True)
class RankingResult:
    items: list[CatalogItem]
    degraded: bool
    rationale: dict[str, str]


async def rank_candidates(
    llm: LLMClient, items: list[CatalogItem], mandate: Mandate
) -> RankingResult:
    """Never raises. `degraded=True` means the LLM path failed, was
    invalid, or referenced an unsupplied SKU (§28 P8 instruction 3) --
    `items` is the deterministic price-ascending/rating-descending order
    (§28 P7 instruction 6) either way it happened."""
    filtered = filter_candidates(items, mandate)
    if not filtered:
        return RankingResult(items=[], degraded=False, rationale={})

    try:
        response = await complete_json_with_repair(
            llm,
            schema=_RankingResponse,
            system=SYSTEM_PROMPT,
            user_prompt=build_user_prompt(filtered),
            build_repair_prompt=build_repair_prompt,
            max_tokens=MAX_TOKENS,
        )
        validated = apply_llm_ranking(filtered, response.ranked_skus)
        if validated is None:
            raise LLMUnavailable(
                "LLM ranking referenced a SKU outside the supplied candidate list"
            )
        return RankingResult(items=validated, degraded=False, rationale=response.rationale)
    except LLMUnavailable:
        return RankingResult(items=rank(filtered), degraded=True, rationale={})
