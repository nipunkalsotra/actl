"""catalog — catalog_meta, catalog_items (§13.1, §28 P4)

Not in §18.2's excerpt (written before P4 was scoped in §28). `catalog_meta`
is a single always-present row (`id='default'`) holding the global,
monotonic `catalog_version` counter (§13.1: "increments on any change to
price, stock or policy of any item"); a dedicated row rather than a bare
Postgres SEQUENCE because a SEQUENCE is non-transactional (nextval()
survives a rolled-back transaction) — the exact BIGSERIAL/explicit-seq
lesson from P3 (docs/adr/0004-p3-trust-layer-decisions.md decision 4)
applies here too. `catalog_items.version` is the global catalog_version as
of that item's last price/stock/policy change — a per-row watermark, not a
duplicate of the global counter. See docs/adr/0005-p4-catalog-quote-decisions.md.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "catalog_meta",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
    )
    op.execute("INSERT INTO catalog_meta (id, version) VALUES ('default', 1)")

    op.create_table(
        "catalog_items",
        sa.Column("sku", sa.Text(), primary_key=True),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("merchant_id", sa.Text(), nullable=False),
        sa.Column("unit", sa.Text(), nullable=False),
        sa.Column("unit_price_minor", sa.BigInteger(), nullable=False),
        sa.Column("available_units", sa.Integer(), nullable=False),
        sa.Column("location_city", sa.Text(), nullable=False),
        sa.Column("location_country", sa.Text(), nullable=False),
        sa.Column("rating", sa.Float(), nullable=False),
        sa.Column("sea_facing", sa.Boolean(), nullable=False),
        sa.Column("breakfast_included", sa.Boolean(), nullable=False),
        sa.Column("refundable", sa.Boolean(), nullable=False),
        sa.Column("cancellation_window_h", sa.Integer(), nullable=False),
        sa.Column("instant_confirm", sa.Boolean(), nullable=False),
        sa.Column("taxes_included", sa.Boolean(), nullable=False),
        sa.Column("quote_required", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "unit_price_minor > 0", name="catalog_items_unit_price_minor_positive"
        ),
        sa.CheckConstraint(
            "available_units >= 0", name="catalog_items_available_units_non_negative"
        ),
    )
    op.create_index(
        "ix_catalog_items_category_location", "catalog_items", ["category", "location_city"]
    )
    op.create_index(
        "ix_catalog_items_price_sku", "catalog_items", ["unit_price_minor", "sku"]
    )


def downgrade() -> None:
    op.drop_table("catalog_items")
    op.drop_table("catalog_meta")
