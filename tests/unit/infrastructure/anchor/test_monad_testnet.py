"""§28 P11 unit tests -- no network, no anvil (see tests/integration/anchor
for the real-chain proofs).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from actl.application.ports import Anchor
from actl.infrastructure.anchor.monad_testnet import (
    AnchorConflictError,
    MonadAnchor,
    TransientAnchorError,
)
from tests.support.scratch_keystore import SCRATCH_PRIVATE_KEY, write_scratch_keystore

_SCRATCH_PRIVATE_KEY = SCRATCH_PRIVATE_KEY
_SCRATCH_PASSWORD = "unit-test-password-only"
_ROOT_HEX = "sha256:" + "ab" * 32


@pytest.fixture
def keystore_path(tmp_path: Path) -> str:
    path = tmp_path / "scratch.keystore"
    write_scratch_keystore(path, _SCRATCH_PASSWORD)
    return str(path)


def _client(keystore_path: str, rpc_url: str = "http://127.0.0.1:1") -> MonadAnchor:
    return MonadAnchor(
        rpc_url=rpc_url,
        chain_id=31337,
        contract_address="0x5FbDB2315678afecb367f032d93F642f64180aa3",
        keystore_path=keystore_path,
        keystore_password=_SCRATCH_PASSWORD,
        audit_chain_id="actl.audit_log",
    )


def test_monad_anchor_satisfies_the_anchor_port(keystore_path: str) -> None:
    anchor: Anchor = _client(keystore_path)
    assert anchor is not None


async def test_anchor_root_is_intentionally_not_implemented(keystore_path: str) -> None:
    """§28 P11: never wired into application.audit_service.append_entry's
    synchronous path -- calling it directly must fail loudly, not
    silently no-op like NoopAnchor (that would be misleading: it would
    look like a successful integration that never actually happened)."""
    client = _client(keystore_path)
    with pytest.raises(NotImplementedError, match="anchor_checkpoint"):
        await client.anchor_root(_ROOT_HEX)


def test_construction_never_exposes_the_password_or_private_key(keystore_path: str) -> None:
    client = _client(keystore_path)
    dump = repr(vars(client)) + repr(client)
    assert _SCRATCH_PASSWORD not in dump
    assert _SCRATCH_PRIVATE_KEY.removeprefix("0x") not in dump
    assert _SCRATCH_PRIVATE_KEY not in dump


def test_construction_accepts_lowercase_and_checksums_the_contract_address(
    keystore_path: str,
) -> None:
    client = MonadAnchor(
        rpc_url="http://127.0.0.1:1",
        chain_id=31337,
        contract_address="0x5fbdb2315678afecb367f032d93f642f64180aa3",
        keystore_path=keystore_path,
        keystore_password=_SCRATCH_PASSWORD,
        audit_chain_id="actl.audit_log",
    )
    assert client.contract_address == "0x5FbDB2315678afecb367f032d93F642f64180aa3"


async def test_anchor_checkpoint_raises_conflict_when_on_chain_root_differs(
    keystore_path: str,
) -> None:
    """Pure logic test: a stubbed contract read returning a different,
    already-anchored root must raise AnchorConflictError directly from the
    pre-flight check, with no transaction ever attempted (§28 P11
    instruction 4: never retried, a permanent finding)."""
    client = _client(keystore_path)

    class _Call:
        def call(self) -> tuple[bytes, int]:
            return bytes.fromhex("cd" * 32), 12345

    class _Functions:
        def getCheckpoint(self, *args: object, **kwargs: object) -> _Call:
            return _Call()

    class _StubContract:
        functions = _Functions()

    client._contract = _StubContract()  # type: ignore[assignment]

    with pytest.raises(AnchorConflictError, match="on-chain root disagrees"):
        await client.anchor_checkpoint(start_seq=1, end_seq=64, merkle_root_hex=_ROOT_HEX)


async def test_anchor_checkpoint_reports_already_anchored_when_root_matches(
    keystore_path: str,
) -> None:
    client = _client(keystore_path)
    expected_root_bytes = bytes.fromhex("ab" * 32)

    class _Call:
        def call(self) -> tuple[bytes, int]:
            return expected_root_bytes, 12345

    class _Functions:
        def getCheckpoint(self, *args: object, **kwargs: object) -> _Call:
            return _Call()

    class _StubContract:
        functions = _Functions()

    client._contract = _StubContract()  # type: ignore[assignment]

    result = await client.anchor_checkpoint(start_seq=1, end_seq=64, merkle_root_hex=_ROOT_HEX)
    assert result.already_anchored is True
    assert result.tx_hash is None


async def test_anchor_checkpoint_classifies_rpc_failure_as_transient(keystore_path: str) -> None:
    """§28 P11 instruction 4: "RPC timeout/transient error -> retry via
    existing retry/outbox behaviour" -- an unreachable contract call must
    surface as TransientAnchorError, the type the worker loop's retry
    classification (`retry_on=(TransientAnchorError,)`) actually catches."""
    client = _client(keystore_path)

    class _Call:
        def call(self) -> tuple[bytes, int]:
            raise ConnectionError("simulated RPC failure")

    class _Functions:
        def getCheckpoint(self, *args: object, **kwargs: object) -> _Call:
            return _Call()

    class _StubContract:
        functions = _Functions()

    client._contract = _StubContract()  # type: ignore[assignment]

    with pytest.raises(TransientAnchorError):
        await client.anchor_checkpoint(start_seq=1, end_seq=64, merkle_root_hex=_ROOT_HEX)
