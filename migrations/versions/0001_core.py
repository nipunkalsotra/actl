"""core — mandates, policy_decisions, quotes, orders, ledger_entries (§18.2)

`quotes` is not shown with DDL in §18.2's "(excerpt)" — only referenced via
orders.quote_id's FK. Its columns are the Quote v1 fields from §8.4 (the
only place this repository's shape is actually specified), kept to exactly
that set. ledger_entries carries a strict append-only trigger per §12.1
("rows are never updated or deleted", no carve-out). See
docs/adr/0003-p2-persistence-decisions.md.

Revision ID: 0001
Revises: 0000
Create Date: 2026-08-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = "0000"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_MANDATE_STATUS = sa.Enum(
    "DRAFT",
    "PENDING_CONFIRM",
    "LOCKED",
    "EXECUTING",
    "SETTLED",
    "COMPENSATED",
    "REVOKED",
    "EXPIRED",
    name="mandate_status",
)

_LEDGER_ENTRIES_IMMUTABLE_FN = """
CREATE OR REPLACE FUNCTION ledger_entries_immutable() RETURNS TRIGGER AS $$
BEGIN
  RAISE EXCEPTION 'ledger_entries is append-only (attempted % on id %)', TG_OP, OLD.id;
END; $$ LANGUAGE plpgsql;
"""

_LEDGER_ENTRIES_NO_UPDATE_TRIGGER = """
CREATE TRIGGER ledger_entries_no_update BEFORE UPDATE ON ledger_entries
  FOR EACH ROW EXECUTE FUNCTION ledger_entries_immutable();
"""

_LEDGER_ENTRIES_NO_DELETE_TRIGGER = """
CREATE TRIGGER ledger_entries_no_delete BEFORE DELETE ON ledger_entries
  FOR EACH ROW EXECUTE FUNCTION ledger_entries_immutable();
"""


def upgrade() -> None:
    # Not created explicitly: SQLAlchemy's create_table dispatch creates the
    # backing Postgres ENUM type automatically the first time this Enum is
    # used as a column type below.
    op.create_table(
        "mandates",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", _MANDATE_STATUS, nullable=False),
        sa.Column("principal_id", sa.Text(), nullable=False),
        sa.Column("delegate_id", sa.Text(), nullable=True),
        sa.Column("spec", postgresql.JSONB(), nullable=False),
        sa.Column("spec_hash", sa.Text(), nullable=False),
        sa.Column("signature", sa.Text(), nullable=True),
        sa.Column("currency", sa.CHAR(3), nullable=False),
        sa.Column("max_total_minor", sa.BigInteger(), nullable=False),
        sa.Column("max_unit_minor", sa.BigInteger(), nullable=True),
        sa.Column("max_transactions", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("not_before", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("max_total_minor > 0", name="mandates_max_total_minor_positive"),
        sa.CheckConstraint("max_unit_minor > 0", name="mandates_max_unit_minor_positive"),
        sa.CheckConstraint(
            "status <> 'LOCKED' OR signature IS NOT NULL", name="locked_has_hash"
        ),
    )
    op.create_index("ix_mandates_status_expires_at", "mandates", ["status", "expires_at"])

    op.create_table(
        "policy_decisions",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("mandate_id", sa.Text(), sa.ForeignKey("mandates.id"), nullable=False),
        sa.Column("mandate_spec_hash", sa.Text(), nullable=False),
        sa.Column("intent_hash", sa.Text(), nullable=False),
        sa.Column("verdict", sa.Text(), nullable=False),
        sa.Column("reason_codes", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("rule_trace", postgresql.JSONB(), nullable=False),
        sa.Column("engine_version", sa.Text(), nullable=False),
        sa.Column("inputs_digest", sa.Text(), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ttl_s", sa.Integer(), nullable=False, server_default="30"),
        sa.CheckConstraint("verdict IN ('ALLOW','DENY')", name="policy_decisions_verdict_check"),
    )
    op.create_index(
        "ix_policy_decisions_mandate_id_evaluated_at",
        "policy_decisions",
        ["mandate_id", sa.text("evaluated_at DESC")],
    )
    op.create_index("ix_policy_decisions_intent_hash", "policy_decisions", ["intent_hash"])

    # Not in §18.2's shown DDL — orders.quote_id (below) references quotes(id),
    # so this table must exist for that FK to be satisfiable. Columns are
    # exactly the Quote v1 fields from §8.4, nothing added beyond them.
    op.create_table(
        "quotes",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("mandate_id", sa.Text(), sa.ForeignKey("mandates.id"), nullable=False),
        sa.Column("sku", sa.Text(), nullable=False),
        sa.Column("unit_price_minor", sa.BigInteger(), nullable=False),
        sa.Column("nights", sa.Integer(), nullable=False),
        sa.Column("total_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.CHAR(3), nullable=False),
        sa.Column("catalog_version", sa.Integer(), nullable=False),
        sa.Column("refundable", sa.Boolean(), nullable=False),
        sa.Column("quote_token", sa.Text(), nullable=False),
        sa.Column("quote_hash", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("unit_price_minor > 0", name="quotes_unit_price_minor_positive"),
        sa.CheckConstraint("total_minor > 0", name="quotes_total_minor_positive"),
    )
    op.create_index("ix_quotes_mandate_id", "quotes", ["mandate_id"])

    op.create_table(
        "orders",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("mandate_id", sa.Text(), sa.ForeignKey("mandates.id"), nullable=False),
        sa.Column(
            "decision_id", sa.Text(), sa.ForeignKey("policy_decisions.id"), nullable=False
        ),
        sa.Column("quote_id", sa.Text(), sa.ForeignKey("quotes.id"), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.CHAR(3), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("idempotency_key", sa.Text(), nullable=False, unique=True),
        sa.Column("provider_order_id", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("amount_minor > 0", name="orders_amount_minor_positive"),
    )
    op.create_index(
        "ix_orders_mandate_id_attempt_no", "orders", ["mandate_id", "attempt_no"], unique=True
    )

    op.create_table(
        "ledger_entries",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("account", sa.Text(), nullable=False),
        sa.Column("direction", sa.Text(), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("ref_type", sa.Text(), nullable=False),
        sa.Column("ref_id", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "direction IN ('debit','credit')", name="ledger_entries_direction_check"
        ),
        sa.CheckConstraint("amount_minor > 0", name="ledger_entries_amount_minor_positive"),
    )
    op.create_index(
        "ix_ledger_entries_account_created_at", "ledger_entries", ["account", "created_at"]
    )
    op.create_index("ix_ledger_entries_ref_type_ref_id", "ledger_entries", ["ref_type", "ref_id"])

    # §12.1: "Append-only ledger_entries. Corrections are contra-entries;
    # rows are never updated or deleted." No carve-out (unlike audit_log's
    # narration column) — every UPDATE and every DELETE is rejected.
    op.execute(_LEDGER_ENTRIES_IMMUTABLE_FN)
    op.execute(_LEDGER_ENTRIES_NO_UPDATE_TRIGGER)
    op.execute(_LEDGER_ENTRIES_NO_DELETE_TRIGGER)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS ledger_entries_no_delete ON ledger_entries")
    op.execute("DROP TRIGGER IF EXISTS ledger_entries_no_update ON ledger_entries")
    op.execute("DROP FUNCTION IF EXISTS ledger_entries_immutable()")
    op.drop_table("ledger_entries")
    op.drop_table("orders")
    op.drop_table("quotes")
    op.drop_table("policy_decisions")
    op.drop_table("mandates")
    # op.drop_table() does not auto-drop the backing ENUM type (only
    # create_table's automatic CREATE TYPE is symmetric-free) — drop it
    # explicitly or a subsequent upgrade fails with "type already exists".
    _MANDATE_STATUS.drop(op.get_bind(), checkfirst=True)
