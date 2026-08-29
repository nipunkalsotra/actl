"""SQLAlchemy 2 async ORM models — one class per §18.2 table, columns and
constraints matching the migrations in migrations/versions/0001_core.py and
0002_audit_outbox.py exactly. Tables are created by Alembic, never by
`Base.metadata.create_all()` — `create_type=False` on the enum reflects that
the migration, not the ORM layer, owns `mandate_status`.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, ENUM, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

MandateStatusEnum = ENUM(
    "DRAFT",
    "PENDING_CONFIRM",
    "LOCKED",
    "EXECUTING",
    "SETTLED",
    "COMPENSATED",
    "REVOKED",
    "EXPIRED",
    name="mandate_status",
    create_type=False,
)


class Base(DeclarativeBase):
    pass


class MandateRow(Base):
    __tablename__ = "mandates"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(MandateStatusEnum, nullable=False)
    principal_id: Mapped[str] = mapped_column(Text, nullable=False)
    delegate_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    spec: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    spec_hash: Mapped[str] = mapped_column(Text, nullable=False)
    signature: Mapped[str | None] = mapped_column(Text, nullable=True)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    max_total_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    max_unit_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    max_transactions: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    not_before: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(

        DateTime(timezone=True), nullable=False, server_default=func.now()

    )

    __table_args__ = (
        CheckConstraint("max_total_minor > 0", name="mandates_max_total_minor_positive"),
        CheckConstraint("max_unit_minor > 0", name="mandates_max_unit_minor_positive"),
        CheckConstraint("status <> 'LOCKED' OR signature IS NOT NULL", name="locked_has_hash"),
    )


class DecisionRow(Base):
    __tablename__ = "policy_decisions"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    mandate_id: Mapped[str] = mapped_column(ForeignKey("mandates.id"), nullable=False)
    mandate_spec_hash: Mapped[str] = mapped_column(Text, nullable=False)
    intent_hash: Mapped[str] = mapped_column(Text, nullable=False)
    verdict: Mapped[str] = mapped_column(Text, nullable=False)
    reason_codes: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    rule_trace: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)
    engine_version: Mapped[str] = mapped_column(Text, nullable=False)
    inputs_digest: Mapped[str] = mapped_column(Text, nullable=False)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ttl_s: Mapped[int] = mapped_column(Integer, nullable=False, default=30)

    __table_args__ = (
        CheckConstraint("verdict IN ('ALLOW','DENY')", name="policy_decisions_verdict_check"),
    )


class QuoteRow(Base):
    """Not in §18.2's shown DDL — see docs/adr/0003-p2-persistence-decisions.md."""

    __tablename__ = "quotes"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    mandate_id: Mapped[str] = mapped_column(ForeignKey("mandates.id"), nullable=False)
    sku: Mapped[str] = mapped_column(Text, nullable=False)
    unit_price_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    nights: Mapped[int] = mapped_column(Integer, nullable=False)
    total_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    catalog_version: Mapped[int] = mapped_column(Integer, nullable=False)
    refundable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    quote_token: Mapped[str] = mapped_column(Text, nullable=False)
    quote_hash: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(

        DateTime(timezone=True), nullable=False, server_default=func.now()

    )

    __table_args__ = (
        CheckConstraint("unit_price_minor > 0", name="quotes_unit_price_minor_positive"),
        CheckConstraint("total_minor > 0", name="quotes_total_minor_positive"),
    )


class CatalogMetaRow(Base):
    """§13.1 — the single global, monotonic catalog_version counter. Always
    exactly one row (id='default'), inserted by migrations/versions/0003_catalog.py."""

    __tablename__ = "catalog_meta"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)


class CatalogItemRow(Base):
    """§13.1 catalog item. `location`/`attributes`/`policy` are flattened
    into individual typed columns rather than JSONB — every one of them is
    filterable/sortable in this build (category, location, price), and
    flattening keeps that a real SQL WHERE/ORDER BY instead of a JSONB
    operator query. Not in §18.2 (written before P4 was scoped)."""

    __tablename__ = "catalog_items"

    sku: Mapped[str] = mapped_column(Text, primary_key=True)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    merchant_id: Mapped[str] = mapped_column(Text, nullable=False)
    unit: Mapped[str] = mapped_column(Text, nullable=False)
    unit_price_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    available_units: Mapped[int] = mapped_column(Integer, nullable=False)
    location_city: Mapped[str] = mapped_column(Text, nullable=False)
    location_country: Mapped[str] = mapped_column(Text, nullable=False)
    rating: Mapped[float] = mapped_column(Float, nullable=False)
    sea_facing: Mapped[bool] = mapped_column(Boolean, nullable=False)
    breakfast_included: Mapped[bool] = mapped_column(Boolean, nullable=False)
    refundable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    cancellation_window_h: Mapped[int] = mapped_column(Integer, nullable=False)
    instant_confirm: Mapped[bool] = mapped_column(Boolean, nullable=False)
    taxes_included: Mapped[bool] = mapped_column(Boolean, nullable=False)
    quote_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "unit_price_minor > 0", name="catalog_items_unit_price_minor_positive"
        ),
        CheckConstraint(
            "available_units >= 0", name="catalog_items_available_units_non_negative"
        ),
    )


class OrderRow(Base):
    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    mandate_id: Mapped[str] = mapped_column(ForeignKey("mandates.id"), nullable=False)
    decision_id: Mapped[str] = mapped_column(ForeignKey("policy_decisions.id"), nullable=False)
    quote_id: Mapped[str] = mapped_column(ForeignKey("quotes.id"), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    provider_order_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_payment_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    decline_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(

        DateTime(timezone=True), nullable=False, server_default=func.now()

    )
    updated_at: Mapped[datetime] = mapped_column(

        DateTime(timezone=True), nullable=False, server_default=func.now()

    )

    __table_args__ = (CheckConstraint("amount_minor > 0", name="orders_amount_minor_positive"),)


class LedgerEntryRow(Base):
    __tablename__ = "ledger_entries"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    account: Mapped[str] = mapped_column(Text, nullable=False)
    direction: Mapped[str] = mapped_column(Text, nullable=False)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    ref_type: Mapped[str] = mapped_column(Text, nullable=False)
    ref_id: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(

        DateTime(timezone=True), nullable=False, server_default=func.now()

    )

    __table_args__ = (
        CheckConstraint("direction IN ('debit','credit')", name="ledger_entries_direction_check"),
        CheckConstraint("amount_minor > 0", name="ledger_entries_amount_minor_positive"),
    )


class SagaRow(Base):
    __tablename__ = "sagas"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    mandate_id: Mapped[str] = mapped_column(ForeignKey("mandates.id"), nullable=False)
    decision_id: Mapped[str] = mapped_column(ForeignKey("policy_decisions.id"), nullable=False)
    quote_id: Mapped[str] = mapped_column(ForeignKey("quotes.id"), nullable=False)
    order_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    step: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(

        DateTime(timezone=True), nullable=False, server_default=func.now()

    )
    updated_at: Mapped[datetime] = mapped_column(

        DateTime(timezone=True), nullable=False, server_default=func.now()

    )

    __table_args__ = (
        CheckConstraint("amount_minor > 0", name="sagas_amount_minor_positive"),
        CheckConstraint(
            "status IN ('RUNNING','AWAITING_AUTHORIZATION','COMPLETED',"
            "'COMPENSATING','COMPENSATED','FAILED')",
            name="sagas_status_check",
        ),
    )


class AuditLogRow(Base):
    __tablename__ = "audit_log"

    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(

        DateTime(timezone=True), nullable=False, server_default=func.now()

    )
    trace_id: Mapped[str] = mapped_column(Text, nullable=False)
    actor_type: Mapped[str] = mapped_column(Text, nullable=False)
    actor_id: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    subject: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    payload_hash: Mapped[str] = mapped_column(Text, nullable=False)
    prev_hash: Mapped[str] = mapped_column(Text, nullable=False)
    entry_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    narration: Mapped[str | None] = mapped_column(Text, nullable=True)


class AuditCheckpointRow(Base):
    __tablename__ = "audit_checkpoints"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    from_seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    to_seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    merkle_root: Mapped[str] = mapped_column(Text, nullable=False)
    anchor_tx: Mapped[str | None] = mapped_column(Text, nullable=True)
    anchored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(

        DateTime(timezone=True), nullable=False, server_default=func.now()

    )


class OutboxRow(Base):
    __tablename__ = "outbox"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    aggregate: Mapped[str] = mapped_column(Text, nullable=False)
    aggregate_id: Mapped[str] = mapped_column(Text, nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(

        DateTime(timezone=True), nullable=False, server_default=func.now()

    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class WebhookEventRow(Base):
    __tablename__ = "webhook_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    provider_event_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    signature_valid: Mapped[bool] = mapped_column(Boolean, nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    received_at: Mapped[datetime] = mapped_column(

        DateTime(timezone=True), nullable=False, server_default=func.now()

    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class IdempotencyKeyRow(Base):
    __tablename__ = "idempotency_keys"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    request_hash: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False)
    response: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(

        DateTime(timezone=True), nullable=False, server_default=func.now()

    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "state IN ('IN_FLIGHT','COMPLETED','FAILED')", name="idempotency_keys_state_check"
        ),
    )
