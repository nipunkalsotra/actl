"""Closed audit action registry (§16.3: events the chain records). Same
closed-set discipline as the policy engine's ReasonCode (§00 conventions):
free-text action strings are never load-bearing.

CATALOG_PRICE_MUTATED is a P4 addition, not in §16.3's table: the demo-only
admin price-mutation endpoint (§28 P4) is a real state change with no
listed action to reuse, so the registry gains one member for it rather than
overloading CATALOG_QUERIED (a read) or leaving the mutation unaudited. See
docs/adr/0005-p4-catalog-quote-decisions.md."""

from __future__ import annotations

from enum import StrEnum


class AuditAction(StrEnum):
    MANDATE_LOCKED = "mandate.locked"
    MANDATE_REVOKED = "mandate.revoked"
    CATALOG_QUERIED = "catalog.queried"
    CATALOG_PRICE_MUTATED = "catalog.price_mutated"
    QUOTE_ISSUED = "quote.issued"
    ORDER_PROPOSED = "order.proposed"
    POLICY_DECISION = "policy.decision"
    BUDGET_RESERVED = "budget.reserved"
    PAYMENT_INTENT = "payment.intent"
    PAYMENT_RESULT = "payment.result"
    WEBHOOK_RECEIVED = "webhook.received"
    COMPENSATION_APPLIED = "compensation.applied"
    SETTLEMENT_CLOSED = "settlement.closed"

    # §12.2 / §28 P6 — ledger reservation lifecycle events not already
    # covered by BUDGET_RESERVED (which records the gate's G4 decision;
    # these record the ledger's own state changes for a reservation).
    RESERVATION_RELEASED = "reservation.released"
    RESERVATION_EXPIRED = "reservation.expired"
    MANDATE_EXECUTING = "mandate.executing"
