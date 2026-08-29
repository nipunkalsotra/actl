"""sagas -- durable saga state (§11, §15, §28 P6)

Not in §18.2's excerpt (written before P6 was scoped). One row per purchase
attempt, keyed by the same idempotency key `payment_service.py` (P5)
already derives from (mandate_id, intent_hash, attempt_no) -- a saga and
its order share that identity 1:1, so no second id scheme is invented.
`step`/`status` are updated in place (not append-only like ledger_entries
or audit_log): saga state is a *current-state* record, not an event log --
the event trail for a saga's transitions is the audit_log entries it writes
alongside each step (§15 "Durability guarantees: saga state in PG, every
transition committed before the side effect"). See
docs/adr/0007-p6-gate-ledger-saga-decisions.md.

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sagas",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("mandate_id", sa.Text(), sa.ForeignKey("mandates.id"), nullable=False),
        sa.Column("decision_id", sa.Text(), sa.ForeignKey("policy_decisions.id"), nullable=False),
        sa.Column("quote_id", sa.Text(), sa.ForeignKey("quotes.id"), nullable=False),
        sa.Column("order_id", sa.Text(), nullable=True),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.CHAR(3), nullable=False),
        sa.Column("step", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("amount_minor > 0", name="sagas_amount_minor_positive"),
        sa.CheckConstraint(
            "status IN ('RUNNING','AWAITING_AUTHORIZATION','COMPLETED',"
            "'COMPENSATING','COMPENSATED','FAILED')",
            name="sagas_status_check",
        ),
    )
    op.create_index("ix_sagas_mandate_id", "sagas", ["mandate_id"])


def downgrade() -> None:
    op.drop_table("sagas")
