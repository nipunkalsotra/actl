"""§14 / §28 P7 instruction 6: the buyer-agent's deterministic candidate
handling. Pure -- no I/O, no LLM, no randomness, no provider call. Given a
locked mandate and a list of catalog candidates, `filter_candidates`
excludes anything the mandate's own bounds cannot admit, and `rank`
orders what remains: price ascending, then rating descending, then sku
ascending as the final, deterministic tie-break (§28 P7 instruction 6).
"""

from __future__ import annotations

from actl.domain.catalog.models import CatalogItem
from actl.domain.mandate.models import Mandate


def is_feasible(item: CatalogItem, mandate: Mandate) -> bool:
    """Deterministic pre-screen against the mandate's own bounds -- the
    same facts the P1 policy engine will re-check authoritatively later,
    applied here client-side so an infeasible item is never even
    quoted for."""
    if item.category not in mandate.bounds.allowed_categories:
        return False
    if item.merchant_id in mandate.bounds.blocked_merchants:
        return False
    if item.unit_price_minor > mandate.bounds.max_unit_minor:
        return False
    if item.available_units <= 0:
        return False
    if mandate.bounds.require_refundable and not item.policy.refundable:
        return False
    mandate_city = mandate.intent.location.split(",")[0].strip().lower()
    return item.location.city.lower() == mandate_city


def filter_candidates(items: list[CatalogItem], mandate: Mandate) -> list[CatalogItem]:
    return [item for item in items if is_feasible(item, mandate)]


def _sort_key(item: CatalogItem) -> tuple[int, float, str]:
    # price ascending, then rating descending, then sku ascending --
    # `-item.attributes.rating` turns "descending" into the same
    # ascending sort direction as the other two keys, so a single
    # stable sort produces the exact required order.
    return (item.unit_price_minor, -item.attributes.rating, item.sku)


def rank(items: list[CatalogItem]) -> list[CatalogItem]:
    """§28 P7 instruction 6: "price ascending, then rating descending",
    with sku ascending as the final deterministic tie-break. `sorted` is
    stable and pure -- no randomness, repeated calls on equal input
    always produce byte-identical output."""
    return sorted(items, key=_sort_key)


def filter_and_rank(items: list[CatalogItem], mandate: Mandate) -> list[CatalogItem]:
    return rank(filter_candidates(items, mandate))


def apply_llm_ranking(
    candidates: list[CatalogItem], ranked_skus: list[str]
) -> list[CatalogItem] | None:
    """§17.1 U2: "An ordering of the supplied SKUs... any SKU not in the
    input list is a hard rejection of the whole response." `ranked_skus`
    must be exactly a permutation of `candidates`' own skus -- no
    additions, no omissions, no duplicates -- or this returns None and
    the caller must fall back to `rank()`. On success, returns the *same*
    CatalogItem objects the deterministic filter already produced,
    reordered only; nothing about price, rating, or any other field can
    be altered by this call, by construction."""
    by_sku = {item.sku: item for item in candidates}
    if len(ranked_skus) != len(candidates):
        return None
    if len(set(ranked_skus)) != len(ranked_skus):
        return None
    if not set(ranked_skus) <= by_sku.keys():
        return None
    return [by_sku[sku] for sku in ranked_skus]
