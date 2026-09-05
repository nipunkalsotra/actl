"""catalog_items.is_buyer_listable (§28 P12 follow-up)

Trust Lab (application.demo._seed_item) and growth-simulation
(application.growth.simulation._seed_catalog) both upsert real
`travel.hotel` rows into this same table so gate/saga/policy see genuine
catalog facts -- but those rows are internal test/demo fixtures, not
curated partner inventory, and must never render in the buyer-facing
catalog grid. `is_buyer_listable` is that explicit boundary, defaulting
true so every ordinary catalog row (including future ones) is buyer-listed
unless a caller opts out.

Backfill: any database that already ran a Trust Lab scenario or growth
simulation before this migration has real `HTL-DEMO-*`/`HTL-GROWTH-*` rows
sitting in `catalog_items` already, alongside the six curated `HTL-GOA-*`
seed hotels. This flips only those two prefixes to not-listable -- no row
is deleted, no audit evidence touched, and every other existing row
(including the six curated hotels and the demo-partner add-ons) keeps its
server-default `true`.

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "catalog_items",
        sa.Column("is_buyer_listable", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.execute(
        "UPDATE catalog_items SET is_buyer_listable = false "
        "WHERE sku LIKE 'HTL-DEMO-%' OR sku LIKE 'HTL-GROWTH-%'"
    )


def downgrade() -> None:
    op.drop_column("catalog_items", "is_buyer_listable")
