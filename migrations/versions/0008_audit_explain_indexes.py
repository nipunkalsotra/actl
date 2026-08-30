"""audit_log subject expression indexes for mandate_id/quote_id (§28 P10
GET /audit/explain/{order_id})

The full causal timeline for one order includes entries written *before*
the order exists -- quote.issued (subject has sku+quote_id, no order_id
or mandate_id yet) and budget.reserved (subject has mandate_id, no
order_id yet). The existing `ix_audit_log_subject_order_id` (0002) alone
therefore misses them; explain-time assembly needs to also query by
mandate_id and quote_id, so both get the same expression-index treatment
that column already has.

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_audit_log_subject_mandate_id", "audit_log", [sa.text("(subject->>'mandate_id')")]
    )
    op.create_index(
        "ix_audit_log_subject_quote_id", "audit_log", [sa.text("(subject->>'quote_id')")]
    )


def downgrade() -> None:
    op.drop_index("ix_audit_log_subject_quote_id", table_name="audit_log")
    op.drop_index("ix_audit_log_subject_mandate_id", table_name="audit_log")
