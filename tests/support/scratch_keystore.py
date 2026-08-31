"""§28 P11 test-only keystore helper -- a well-known, publicly documented
Anvil/Foundry test private key (Hardhat's default account #0, never
funded on any real network) encrypted into a Foundry-compatible V3
keystore JSON file, for exercising MonadAnchor's decrypt/sign code paths
without ever touching a real credential.
"""

from __future__ import annotations

import json
from pathlib import Path

SCRATCH_PRIVATE_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"


def write_scratch_keystore(path: Path, password: str) -> str:
    """Encrypts SCRATCH_PRIVATE_KEY with `password` and writes it to
    `path`. Returns `password` unchanged (for call-site symmetry with
    the path)."""
    from eth_account import Account

    path.write_text(json.dumps(Account.encrypt(SCRATCH_PRIVATE_KEY, password)))
    return password
