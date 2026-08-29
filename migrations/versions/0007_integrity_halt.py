"""integrity_halt -- durable, cross-process §20 F10 halt state (§28 P9
production-readiness correction)

A single always-present row (`id='default'`), same precedent as
`catalog_meta` (migrations/versions/0003_catalog.py): a dedicated row
rather than any in-process state, so every process/instance reading the
same Postgres database observes the identical halted/not-halted state
immediately, including a process that has never handled a request before
this row was tripped. Replaces the P9-era in-memory `application.
integrity.IntegrityHalt` singleton, which was process-local only and gave
no cross-process guarantee (docs/adr/0010 decision 2).

`tripped_seq` names the audit_log seq the triggering `verify_chain`
failure was found at, for forensic traceability. There is deliberately no
application code path that clears this row -- §20 gives F10 no automated
recovery ("halt all money actions ... refuse to proceed," no compensating
action named) -- clearing it is a direct, manual, documented database
operation an operator performs after off-band investigation (docs/
runbook.md), never a function this build exposes.

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "integrity_halt",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("halted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("tripped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tripped_seq", sa.BigInteger(), nullable=True),
        sa.Column("cleared_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cleared_by", sa.Text(), nullable=True),
    )
    op.execute("INSERT INTO integrity_halt (id, halted) VALUES ('default', false)")


def downgrade() -> None:
    op.drop_table("integrity_halt")
