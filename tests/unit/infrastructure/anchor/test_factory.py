"""§28 P11 configuration guard tests: build_anchor_worker's fail-closed
behaviour for ANCHOR_PROVIDER=monad, and that ANCHOR_PROVIDER=noop
(default) never validates or touches anything -- no file I/O, no network,
no exception, for any value of the monad_* fields.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from actl.config import Settings
from actl.infrastructure.anchor.factory import build_anchor_worker
from actl.infrastructure.anchor.monad_testnet import MonadAnchor
from tests.support.scratch_keystore import write_scratch_keystore

_VALID_ADDRESS = "0x5FbDB2315678afecb367f032d93F642f64180aa3"


def _monad_settings(**overrides: object) -> Settings:
    base = {
        "anchor_provider": "monad",
        "monad_rpc_url": "http://127.0.0.1:1",
        "monad_chain_id": 10143,
        "monad_contract_address": _VALID_ADDRESS,
        "monad_keystore_path": "",
        "monad_keystore_password": "x",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def test_noop_provider_returns_none_and_never_validates_monad_fields() -> None:
    # Deliberately garbage monad_* values -- must never be inspected at
    # all when anchor_provider="noop" (the default).
    settings = Settings(
        anchor_provider="noop",
        monad_chain_id=1,  # would be a mainnet-guard violation if checked
        monad_contract_address="not-an-address",
        monad_keystore_path="/does/not/exist",
    )
    assert build_anchor_worker(settings, audit_chain_id="actl.audit_log") is None


def test_unknown_provider_raises_system_exit() -> None:
    settings = Settings(anchor_provider="ethereum")
    with pytest.raises(SystemExit, match="unknown anchor_provider"):
        build_anchor_worker(settings, audit_chain_id="actl.audit_log")


def test_monad_provider_rejects_any_chain_id_other_than_testnet() -> None:
    """§28 P11 non-negotiable rule: "This is Testnet only. Do not use
    Monad mainnet." -- checked mechanically, not just documented."""
    settings = _monad_settings(monad_chain_id=1)  # Ethereum mainnet id
    with pytest.raises(SystemExit, match="10143"):
        build_anchor_worker(settings, audit_chain_id="actl.audit_log")


def test_monad_provider_requires_rpc_url() -> None:
    settings = _monad_settings(monad_rpc_url="")
    with pytest.raises(SystemExit, match="MONAD_RPC_URL"):
        build_anchor_worker(settings, audit_chain_id="actl.audit_log")


@pytest.mark.parametrize("bad_address", ["", "not-an-address", "0x1234"])
def test_monad_provider_rejects_invalid_contract_address(bad_address: str) -> None:
    settings = _monad_settings(monad_contract_address=bad_address)
    with pytest.raises(SystemExit, match="MONAD_CONTRACT_ADDRESS"):
        build_anchor_worker(settings, audit_chain_id="actl.audit_log")


def test_monad_provider_requires_keystore_path_to_be_set() -> None:
    settings = _monad_settings(monad_keystore_path="")
    with pytest.raises(SystemExit, match="MONAD_KEYSTORE_PATH"):
        build_anchor_worker(settings, audit_chain_id="actl.audit_log")


def test_monad_provider_requires_keystore_file_to_exist() -> None:
    settings = _monad_settings(monad_keystore_path="/definitely/does/not/exist.json")
    with pytest.raises(SystemExit, match="does not exist"):
        build_anchor_worker(settings, audit_chain_id="actl.audit_log")


def test_monad_provider_requires_keystore_password(tmp_path: Path) -> None:
    keystore = tmp_path / "keystore.json"
    keystore.write_text("{}")
    settings = _monad_settings(monad_keystore_path=str(keystore), monad_keystore_password="")
    with pytest.raises(SystemExit, match="MONAD_KEYSTORE_PASSWORD"):
        build_anchor_worker(settings, audit_chain_id="actl.audit_log")


def test_monad_provider_builds_a_real_client_with_full_valid_config(tmp_path: Path) -> None:
    """Construction itself makes zero RPC calls (Web3(HTTPProvider(...))
    doesn't connect eagerly) -- a garbage, unreachable RPC URL is fine
    here; only the config *shape* is under test."""
    keystore = tmp_path / "keystore.json"
    write_scratch_keystore(keystore, "pw")

    settings = _monad_settings(monad_keystore_path=str(keystore), monad_keystore_password="pw")
    client = build_anchor_worker(settings, audit_chain_id="actl.audit_log")
    assert isinstance(client, MonadAnchor)
    assert client.contract_address == _VALID_ADDRESS
