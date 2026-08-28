"""audit_outbox — audit_log (+ append-only trigger), audit_checkpoints,
outbox, webhook_events, idempotency_keys (§18.2)

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_IMMUTABLE_FN = """
CREATE OR REPLACE FUNCTION audit_log_immutable() RETURNS TRIGGER AS $$
BEGIN
  RAISE EXCEPTION 'audit_log is append-only (attempted % on seq %)', TG_OP, OLD.seq;
END; $$ LANGUAGE plpgsql;
"""

_NO_UPDATE_TRIGGER = """
CREATE TRIGGER audit_log_no_update BEFORE UPDATE ON audit_log
  FOR EACH ROW WHEN ((to_jsonb(OLD) - 'narration') IS DISTINCT FROM (to_jsonb(NEW) - 'narration'))
  EXECUTE FUNCTION audit_log_immutable();
"""

_NO_DELETE_TRIGGER = """
CREATE TRIGGER audit_log_no_delete BEFORE DELETE ON audit_log
  FOR EACH ROW EXECUTE FUNCTION audit_log_immutable();
"""


def upgrade() -> None:
    op.create_table(
        "audit_log",
        sa.Column("seq", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "ts", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("trace_id", sa.Text(), nullable=False),
        sa.Column("actor_type", sa.Text(), nullable=False),
        sa.Column("actor_id", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("subject", postgresql.JSONB(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("payload_hash", sa.Text(), nullable=False),
        sa.Column("prev_hash", sa.Text(), nullable=False),
        sa.Column("entry_hash", sa.Text(), nullable=False, unique=True),
        sa.Column("narration", sa.Text(), nullable=True),
    )
    op.create_index("ix_audit_log_trace_id", "audit_log", ["trace_id"])
    op.create_index("ix_audit_log_action_ts", "audit_log", ["action", sa.text("ts DESC")])
    op.create_index(
        "ix_audit_log_subject_order_id", "audit_log", [sa.text("(subject->>'order_id')")]
    )

    # Append-only enforced by the database, not by convention (§18.2 WHY
    # THIS WAY). The narration column has a carve-out: an UPDATE is allowed
    # through only when narration is the *sole* changed column — checked by
    # comparing the whole row minus narration, not just narration itself,
    # so a smuggled change to any other column in the same statement still
    # raises even though narration also changed. Any DELETE always raises.
    op.execute(_IMMUTABLE_FN)
    op.execute(_NO_UPDATE_TRIGGER)
    op.execute(_NO_DELETE_TRIGGER)

    op.create_table(
        "audit_checkpoints",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("from_seq", sa.BigInteger(), nullable=False),
        sa.Column("to_seq", sa.BigInteger(), nullable=False),
        sa.Column("merkle_root", sa.Text(), nullable=False),
        sa.Column("anchor_tx", sa.Text(), nullable=True),
        sa.Column("anchored_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )

    op.create_table(
        "outbox",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("aggregate", sa.Text(), nullable=False),
        sa.Column("aggregate_id", sa.Text(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_outbox_published_at_id",
        "outbox",
        [sa.text("published_at NULLS FIRST"), "id"],
    )

    op.create_table(
        "webhook_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("provider_event_id", sa.Text(), nullable=False, unique=True),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("signature_valid", sa.Boolean(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column(
            "received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "idempotency_keys",
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column("request_hash", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("response", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "state IN ('IN_FLIGHT','COMPLETED','FAILED')", name="idempotency_keys_state_check"
        ),
    )


def downgrade() -> None:
    op.drop_table("idempotency_keys")
    op.drop_table("webhook_events")
    op.drop_table("outbox")
    op.drop_table("audit_checkpoints")
    op.execute("DROP TRIGGER IF EXISTS audit_log_no_delete ON audit_log")
    op.execute("DROP TRIGGER IF EXISTS audit_log_no_update ON audit_log")
    op.execute("DROP FUNCTION IF EXISTS audit_log_immutable()")
    op.drop_table("audit_log")
