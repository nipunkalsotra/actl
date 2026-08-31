"""§28 P11: build_anchor_worker picks between "nothing to do" (ANCHOR_
PROVIDER=noop, the default -- actl.worker never even starts the anchor
loop) and a real MonadAnchor, validating Testnet-only configuration at
construction (fail closed) so a misconfigured ANCHOR_PROVIDER=monad can
never silently no-op or, worse, silently target the wrong chain. Mirrors
`infrastructure/providers/factory.py::build_payment_provider` (§28 P5):
called only from `actl.worker`, never from `actl.application`/`actl.
interfaces` (see .importlinter contract 6).
"""

from __future__ import annotations

import re
from pathlib import Path

from actl.config import Settings
from actl.infrastructure.anchor.monad_testnet import MONAD_TESTNET_CHAIN_ID, MonadAnchor

_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


def build_anchor_worker(settings: Settings, *, audit_chain_id: str) -> MonadAnchor | None:
    if settings.anchor_provider == "noop":
        return None
    if settings.anchor_provider != "monad":
        raise SystemExit(f"FATAL: unknown anchor_provider: {settings.anchor_provider!r}")

    if settings.monad_chain_id != MONAD_TESTNET_CHAIN_ID:
        raise SystemExit(
            "FATAL: ANCHOR_PROVIDER=monad requires MONAD_CHAIN_ID="
            f"{MONAD_TESTNET_CHAIN_ID} (Monad Testnet). Refusing to start with "
            f"chain id {settings.monad_chain_id} -- Monad mainnet or any other "
            "chain is out of scope (§28 P11 non-negotiable rule)."
        )
    if not settings.monad_rpc_url:
        raise SystemExit("FATAL: ANCHOR_PROVIDER=monad requires MONAD_RPC_URL to be set.")
    if not _ADDRESS_RE.match(settings.monad_contract_address):
        raise SystemExit(
            "FATAL: ANCHOR_PROVIDER=monad requires a valid MONAD_CONTRACT_ADDRESS "
            "(0x-prefixed, 40 hex chars) -- deploy chain/src/AuditCheckpointAnchor.sol "
            "first, see docs/monad-testnet.md."
        )
    if not settings.monad_keystore_path:
        raise SystemExit("FATAL: ANCHOR_PROVIDER=monad requires MONAD_KEYSTORE_PATH to be set.")
    if not Path(settings.monad_keystore_path).is_file():
        raise SystemExit(
            f"FATAL: MONAD_KEYSTORE_PATH={settings.monad_keystore_path!r} does not exist. "
            "Create one with `cast wallet import`, see docs/monad-testnet.md."
        )
    if not settings.monad_keystore_password:
        raise SystemExit(
            "FATAL: ANCHOR_PROVIDER=monad requires MONAD_KEYSTORE_PASSWORD to be set."
        )

    return MonadAnchor(
        rpc_url=settings.monad_rpc_url,
        chain_id=settings.monad_chain_id,
        contract_address=settings.monad_contract_address,
        keystore_path=settings.monad_keystore_path,
        keystore_password=settings.monad_keystore_password,
        audit_chain_id=audit_chain_id,
    )
