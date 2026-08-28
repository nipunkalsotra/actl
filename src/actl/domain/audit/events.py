"""Closed audit action registry (§16.3: events the chain records). Same
closed-set discipline as the policy engine's ReasonCode (§00 conventions):
free-text action strings are never load-bearing."""

from __future__ import annotations

from enum import StrEnum


class AuditAction(StrEnum):
    MANDATE_LOCKED = "mandate.locked"
    MANDATE_REVOKED = "mandate.revoked"
    CATALOG_QUERIED = "catalog.queried"
    QUOTE_ISSUED = "quote.issued"
    ORDER_PROPOSED = "order.proposed"
    POLICY_DECISION = "policy.decision"
    BUDGET_RESERVED = "budget.reserved"
    PAYMENT_INTENT = "payment.intent"
    PAYMENT_RESULT = "payment.result"
    WEBHOOK_RECEIVED = "webhook.received"
    COMPENSATION_APPLIED = "compensation.applied"
    SETTLEMENT_CLOSED = "settlement.closed"
