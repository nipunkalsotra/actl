"""addon_purchases table + orders.source (§28 P12 contextual upsell)

`addon_purchases` is the single source of truth for the real, buyer-driven
post-booking upsell flow: it doubles as the duplicate-purchase guard (the
UNIQUE constraint on (base_order_id, offer_sku) makes "buy the same add-on
twice for the same booking" impossible at the database level, not just a
client-side check) and as the offered/accepted/settled/declined counter
merchant metrics read from -- no outbox events needed for this, unlike the
synthetic growth-simulator A/B arms (application/growth/events.py), which
stay entirely separate on purpose: real buyer behaviour must never be
blended into the synthetic baseline-vs-upsell experiment.

`orders.source` is a nullable tag, written only by application/demo.py's
Demo Lab scenarios and application/growth/simulation.py's seeded sessions
(both call it from their own orchestration code, after the shared
gate/saga functions already ran -- gate.py/saga.py/state_machine.py are
untouched). NULL means organic. This lets merchant KPIs report real gross
sales without Demo Lab clicks or growth-simulation seeding inflating them,
and lets Live Orders visibly label non-organic rows instead of presenting
them as real customer activity.

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("source", sa.Text(), nullable=True))
    op.create_check_constraint(
        "ck_orders_source",
        "orders",
        "source IS NULL OR source IN ('demo_lab', 'growth_simulation')",
    )

    op.create_table(
        "addon_purchases",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("base_order_id", sa.Text(), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("offer_sku", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("addon_mandate_id", sa.Text(), nullable=True),
        sa.Column("addon_order_id", sa.Text(), sa.ForeignKey("orders.id"), nullable=True),
        sa.Column("price_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.CHAR(3), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("price_minor > 0", name="addon_purchases_price_minor_positive"),
        sa.CheckConstraint(
            "status IN ('offered', 'pending', 'settled', 'failed', 'declined')",
            name="ck_addon_purchases_status",
        ),
        sa.UniqueConstraint("base_order_id", "offer_sku", name="uq_addon_purchases_base_offer"),
    )
    op.create_index(
        "ix_addon_purchases_base_order_id", "addon_purchases", ["base_order_id"]
    )
    op.create_index("ix_addon_purchases_status", "addon_purchases", ["status"])


def downgrade() -> None:
    op.drop_index("ix_addon_purchases_status", table_name="addon_purchases")
    op.drop_index("ix_addon_purchases_base_order_id", table_name="addon_purchases")
    op.drop_table("addon_purchases")
    op.drop_constraint("ck_orders_source", "orders", type_="check")
    op.drop_column("orders", "source")
