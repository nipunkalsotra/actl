"""anchor metadata on audit_checkpoints (§28 P11)

Adds the columns the optional Monad Testnet anchor worker needs to track
per-checkpoint anchor state. `anchor_tx`/`anchored_at` already existed
(0002) but were never populated by any code path -- P11 is the first
writer. `anchor_status` defaults to 'unanchored' for every row regardless
of ANCHOR_PROVIDER, since it is only ever read by the new opt-in worker
loop (ANCHOR_PROVIDER=noop, the default, never runs that loop at all).

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-31
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "audit_checkpoints",
        sa.Column(
            "anchor_status", sa.Text(), nullable=False, server_default=sa.text("'unanchored'")
        ),
    )
    op.add_column(
        "audit_checkpoints", sa.Column("anchor_chain_id", sa.BigInteger(), nullable=True)
    )
    op.add_column(
        "audit_checkpoints", sa.Column("anchor_contract_address", sa.Text(), nullable=True)
    )
    op.add_column(
        "audit_checkpoints",
        sa.Column("anchor_attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column("audit_checkpoints", sa.Column("anchor_last_error", sa.Text(), nullable=True))
    op.create_check_constraint(
        "ck_audit_checkpoints_anchor_status",
        "audit_checkpoints",
        "anchor_status IN ('unanchored', 'anchored', 'conflict')",
    )
    op.create_index(
        "ix_audit_checkpoints_anchor_status", "audit_checkpoints", ["anchor_status"]
    )


def downgrade() -> None:
    op.drop_index("ix_audit_checkpoints_anchor_status", table_name="audit_checkpoints")
    op.drop_constraint(
        "ck_audit_checkpoints_anchor_status", "audit_checkpoints", type_="check"
    )
    op.drop_column("audit_checkpoints", "anchor_last_error")
    op.drop_column("audit_checkpoints", "anchor_attempts")
    op.drop_column("audit_checkpoints", "anchor_contract_address")
    op.drop_column("audit_checkpoints", "anchor_chain_id")
    op.drop_column("audit_checkpoints", "anchor_status")
