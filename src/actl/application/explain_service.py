"""§22 / §23 / §28 P10: GET /audit/explain/{order_id} -- "the entire
'explainable' requirement, in one response" (§22's own words). Assembles
the ordered causal timeline for one order from every source that
actually carries part of it.

Most items come straight from the hash-chained `audit_log` (order.
proposed, budget.reserved, payment.intent, payment.result, settlement.
closed, compensation.applied, quote.issued). Three items this build never
writes to `audit_log` at all are synthesized directly from their own
source-of-truth table instead of invented:

- `mandate.locked` -- mandate issuance is the buyer-agent's own system,
  out of this merchant-side build's scope (no application code path ever
  creates or locks a mandate here); the `mandates` row's own `created_at`
  is this merchant's ingestion fact.
- `policy.decision` -- the policy engine's verdict is already durably
  recorded in `policy_decisions` (richer than order.proposed's own
  embedded verdict/reason_codes -- it also has the full rule_trace).
- `webhook.received` -- `webhook_events` is the append-only evidence
  table §15.3 already requires; no separate audit_log write exists for
  webhook receipt.

`catalog.queried` is a deliberate, documented omission: its audit subject
carries only category/location filters, never a mandate_id/order_id/
quote_id, so a specific order's catalog lookup cannot be correlated back
without a broader session/cart-id design change this endpoint doesn't
make (see the P10 report's "justified deviations").
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from actl.application.payment_service import extract_payment_entity
from actl.domain.audit.events import AuditAction
from actl.infrastructure.db.uow import UnitOfWork

# §28 P11: testnet.monadscan.com is Monad's own documented Testnet
# explorer (docs/monad-testnet.md has the full source list) -- the only
# chain this build ever anchors to (infrastructure.anchor.factory refuses
# any other MONAD_CHAIN_ID at construction), so no further gating on
# chain_id is needed before building this URL.
_MONAD_TESTNET_EXPLORER_TX_URL = "https://testnet.monadscan.com/tx/{tx_hash}"

TimelineItemType = Literal["fact", "decision", "provider_event", "compensation"]

_ITEM_TYPE_BY_ACTION: dict[str, TimelineItemType] = {
    AuditAction.MANDATE_LOCKED: "fact",
    AuditAction.MANDATE_REVOKED: "fact",
    AuditAction.QUOTE_ISSUED: "fact",
    AuditAction.ORDER_PROPOSED: "fact",
    AuditAction.BUDGET_RESERVED: "fact",
    AuditAction.PAYMENT_INTENT: "fact",
    AuditAction.PAYMENT_RESULT: "provider_event",
    AuditAction.WEBHOOK_RECEIVED: "provider_event",
    AuditAction.SETTLEMENT_CLOSED: "fact",
    AuditAction.COMPENSATION_APPLIED: "compensation",
    AuditAction.RESERVATION_RELEASED: "compensation",
    AuditAction.RESERVATION_EXPIRED: "compensation",
    AuditAction.MANDATE_EXECUTING: "fact",
    "policy.decision": "decision",
}


@dataclass(frozen=True)
class TimelineItem:
    ts: datetime | None
    type: TimelineItemType
    action: str
    trace_id: str | None
    payload: dict[str, object]
    seq: int | None = None
    entry_hash: str | None = None
    prev_hash: str | None = None
    payload_hash: str | None = None


@dataclass(frozen=True)
class AnchorInfo:
    """§28 P11 instruction 5: anchor status/contract address/tx hash/chain
    id/explorer URL for the checkpoint covering this order's latest audit
    entry. `status="unanchored"` covers both "ANCHOR_PROVIDER=noop" (the
    default -- every checkpoint stays unanchored forever) and "monad
    worker just hasn't gotten to it yet"; a typed backend response only,
    the trust panel that renders it is out of scope for this build."""

    status: str  # unanchored | anchored | conflict
    checkpoint_from_seq: int
    checkpoint_to_seq: int
    chain_id: int | None = None
    contract_address: str | None = None
    tx_hash: str | None = None
    explorer_url: str | None = None


@dataclass(frozen=True)
class ExplainResult:
    order_id: str
    terminal_status: str
    timeline: list[TimelineItem]
    anchor: AnchorInfo | None = None


class OrderNotFoundForExplain(Exception):
    def __init__(self, order_id: str) -> None:
        super().__init__(f"no order {order_id}")
        self.order_id = order_id


async def explain_order(uow: UnitOfWork, order_id: str) -> ExplainResult:
    order = await uow.orders.get(order_id)
    if order is None:
        raise OrderNotFoundForExplain(order_id)

    audit_entries = await uow.audit_log.get_for_explain(
        order_id=order_id, mandate_id=order.mandate_id, quote_id=order.quote_id
    )
    timeline = [
        TimelineItem(
            ts=e.ts,
            type=_ITEM_TYPE_BY_ACTION.get(e.action, "fact"),
            action=e.action,
            trace_id=e.trace_id,
            payload=e.payload,
            seq=e.seq,
            entry_hash=e.entry_hash,
            prev_hash=e.prev_hash,
            payload_hash=e.payload_hash,
        )
        for e in audit_entries
    ]

    mandate_created_at = await uow.mandates.get_created_at(order.mandate_id)
    if mandate_created_at is not None:
        timeline.append(
            TimelineItem(
                ts=mandate_created_at,
                type="fact",
                action=str(AuditAction.MANDATE_LOCKED),
                trace_id=None,
                payload={"mandate_id": order.mandate_id},
            )
        )

    decision = await uow.decisions.get(order.decision_id)
    if decision is not None:
        timeline.append(
            TimelineItem(
                ts=decision.evaluated_at,
                type="decision",
                action="policy.decision",
                trace_id=None,
                payload={
                    "mandate_id": decision.mandate_id,
                    "verdict": decision.verdict,
                    "reason_codes": [str(c) for c in decision.reason_codes],
                    "rule_trace": [t.model_dump(mode="json") for t in decision.rule_trace],
                },
            )
        )

    if order.provider_order_id is not None:
        for event in await uow.webhook_events.list_all():
            entity = extract_payment_entity(event.payload)
            if entity is None or entity.get("order_id") != order.provider_order_id:
                continue
            timeline.append(
                TimelineItem(
                    ts=event.received_at,
                    type="provider_event",
                    action=str(AuditAction.WEBHOOK_RECEIVED),
                    trace_id=None,
                    payload={
                        "event_type": event.event_type,
                        "provider_payment_id": entity.get("id"),
                        "status": entity.get("status"),
                    },
                )
            )

    timeline.sort(key=lambda item: (item.ts is None, item.ts, item.seq or 0))

    seqs = [item.seq for item in timeline if item.seq is not None]
    anchor = await _anchor_info_for(uow, max(seqs)) if seqs else None

    return ExplainResult(
        order_id=order_id, terminal_status=order.status, timeline=timeline, anchor=anchor
    )


async def _anchor_info_for(uow: UnitOfWork, seq: int) -> AnchorInfo | None:
    """§28 P11: the checkpoint whose [from_seq, to_seq] segment covers this
    order's latest audit entry, if that segment has been checkpointed yet
    (still-open tail segments -- fewer than AUDIT_CHECKPOINT_EVERY entries
    so far -- have no checkpoint at all, so this returns None)."""
    checkpoint = await uow.audit_checkpoints.get_covering_seq(seq)
    if checkpoint is None:
        return None

    explorer_url = None
    if checkpoint.anchor_status == "anchored" and checkpoint.anchor_tx:
        explorer_url = _MONAD_TESTNET_EXPLORER_TX_URL.format(tx_hash=checkpoint.anchor_tx)

    return AnchorInfo(
        status=checkpoint.anchor_status,
        checkpoint_from_seq=checkpoint.from_seq,
        checkpoint_to_seq=checkpoint.to_seq,
        chain_id=checkpoint.anchor_chain_id,
        contract_address=checkpoint.anchor_contract_address,
        tx_hash=checkpoint.anchor_tx,
        explorer_url=explorer_url,
    )
