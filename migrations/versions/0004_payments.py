"""payments — orders.provider_payment_id, orders.decline_reason (§15, §28 P5)

Not in §18.2's excerpt. `orders.provider_order_id` (P2) identifies the
Razorpay Order; a single Order can carry more than one payment attempt, so
capture/reconciliation need a separate `provider_payment_id` to know which
attempt is authoritative. `decline_reason` carries the provider's own
{code} for a terminal failure (§20 F2) — never the raw signature, never
card data (RazorpayAdapter's `_safe_error_body` already strips to
code/description before this ever reaches application code). See
docs/adr/0006-p5-payments-decisions.md.

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("provider_payment_id", sa.Text(), nullable=True))
    op.add_column("orders", sa.Column("decline_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("orders", "decline_reason")
    op.drop_column("orders", "provider_payment_id")
