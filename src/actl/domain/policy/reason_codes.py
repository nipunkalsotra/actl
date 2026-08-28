"""Closed reason-code registry (§00 conventions / Appendix C): free-text
error strings are never load-bearing — only these SCREAMING_SNAKE constants
are, and every one of them traces to exactly one rule in §10.1."""

from __future__ import annotations

from enum import StrEnum


class ReasonCode(StrEnum):
    OK = "OK"
    CURRENCY_MISMATCH = "CURRENCY_MISMATCH"
    CATEGORY_NOT_ALLOWED = "CATEGORY_NOT_ALLOWED"
    MERCHANT_BLOCKED = "MERCHANT_BLOCKED"
    MANDATE_EXPIRED = "MANDATE_EXPIRED"
    MANDATE_NOT_YET_VALID = "MANDATE_NOT_YET_VALID"
    UNIT_CAP_EXCEEDED = "UNIT_CAP_EXCEEDED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    TXN_LIMIT_EXCEEDED = "TXN_LIMIT_EXCEEDED"
    QUANTITY_MISMATCH = "QUANTITY_MISMATCH"
    REFUND_POLICY_VIOLATION = "REFUND_POLICY_VIOLATION"
    PRICE_DRIFT = "PRICE_DRIFT"
    STALE_PRICE = "STALE_PRICE"
    INTENT_MISMATCH = "INTENT_MISMATCH"
