"""foundation — rails only, no domain schema yet (P0)

P1/P2 have not run: there is no mandate, order, ledger or audit table to
create. This revision exists so `make migrate` and /readyz have a real
migration history to report. P2's "0001_core" (§25) chains on top of this
one via down_revision, so it does not collide with this id.

Revision ID: 0000
Revises:
Create Date: 2026-08-28
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "0000"
down_revision: str | None = None
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
