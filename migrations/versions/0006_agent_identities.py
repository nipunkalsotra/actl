"""agent_identities -- signed agent-to-agent protocol identity registry
(§14.1, §28 P7)

Not in §18.2's excerpt (the doc's repository tree names this
`0003_agent_identities.sql`; renumbered here to follow this build's actual
migration sequence rather than the doc's illustrative one, same precedent
as catalog/payments/sagas before it). Stores only PUBLIC key material --
`public_key_hex` is the hex-encoded 32-byte Ed25519 public key; the
corresponding private key never touches this table, a file, or source
control (§28 P7 instruction 2). `hmac_secret` is nullable and only set for
an identity opting into §14.1's documented HMAC-SHA256 development
fallback -- that column *is* the shared secret (there is no
public/private split for a symmetric key), so it is never populated for
any identity outside test fixtures.

`status` is a plain ACTIVE/REVOKED switch, mirroring `mandates` -- there is
no separate stored "EXPIRED" status; expiry is always a derived
`now >= expires_at` check at verification time, exactly like the mandate
gate's G1 (§28 P6) never stores "MANDATE_EXPIRED" as a status either.

§14's order.status/receipt.issue handlers are the first code to query
audit_log by order id (via `AuditLogRepository.get_seq_range_for_order`);
`ix_audit_log_subject_order_id` already exists from P2's own
0002_audit_outbox migration (§18.2's `CREATE INDEX ON audit_log
((subject->>'order_id'))`), so no schema change is needed for it here.

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_identities",
        sa.Column("agent_id", sa.Text(), primary_key=True),
        sa.Column("key_id", sa.Text(), nullable=False, unique=True),
        sa.Column("alg", sa.Text(), nullable=False),
        sa.Column("public_key_hex", sa.Text(), nullable=True),
        sa.Column("hmac_secret", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="ACTIVE"),
        sa.Column("not_before", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("alg IN ('Ed25519','HMAC-SHA256')", name="agent_identities_alg_check"),
        sa.CheckConstraint(
            "status IN ('ACTIVE','REVOKED')", name="agent_identities_status_check"
        ),
        sa.CheckConstraint(
            "(alg = 'Ed25519' AND public_key_hex IS NOT NULL)"
            " OR (alg = 'HMAC-SHA256' AND hmac_secret IS NOT NULL)",
            name="agent_identities_key_material_check",
        ),
    )
    op.create_index("ix_agent_identities_key_id", "agent_identities", ["key_id"])


def downgrade() -> None:
    op.drop_table("agent_identities")
