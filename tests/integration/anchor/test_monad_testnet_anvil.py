"""§28 P11 instruction 4: "Add real integration tests with a local EVM/
Anvil-compatible environment where practical." Deploys the real, compiled
AuditCheckpointAnchor.sol to a local `anvil` node via `forge create`, then
drives MonadAnchor against it for real -- real ABI encoding, real
signing, real submission, real idempotency and conflict behaviour on a
real chain. No Monad Testnet RPC call is ever made (anvil is a local
simulated chain on 127.0.0.1); this is separate from, and does not affect,
"tests/CI remain fully offline" (§28 P11 non-negotiable rules refer to
*Monad* RPC calls specifically).

Skipped automatically when `forge`/`anvil` aren't on PATH (this repo's CI
image does not install Foundry) -- see docs/monad-testnet.md.
"""

from __future__ import annotations

import shutil
import socket
import subprocess
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from actl.infrastructure.anchor.monad_testnet import AnchorConflictError, MonadAnchor
from tests.support.scratch_keystore import SCRATCH_PRIVATE_KEY, write_scratch_keystore

CHAIN_DIR = Path(__file__).resolve().parents[3] / "chain"
_TEST_PRIVATE_KEY = SCRATCH_PRIVATE_KEY
_TEST_PASSWORD = "anvil-integration-test-only"

pytestmark = pytest.mark.skipif(
    shutil.which("forge") is None or shutil.which("anvil") is None,
    reason="forge/anvil not on PATH -- see docs/monad-testnet.md",
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@dataclass
class AnvilDeployment:
    rpc_url: str
    contract_address: str
    keystore_path: str


@pytest.fixture(scope="module")
def anvil_deployment(tmp_path_factory: pytest.TempPathFactory) -> Iterator[AnvilDeployment]:
    port = _free_port()
    rpc_url = f"http://127.0.0.1:{port}"
    proc = subprocess.Popen(
        ["anvil", "--port", str(port), "--silent"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(50):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("anvil did not start listening in time")

        deploy = subprocess.run(
            [
                "forge",
                "create",
                "src/AuditCheckpointAnchor.sol:AuditCheckpointAnchor",
                "--rpc-url",
                rpc_url,
                "--private-key",
                _TEST_PRIVATE_KEY,
                "--broadcast",
            ],
            cwd=CHAIN_DIR,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert deploy.returncode == 0, deploy.stdout + deploy.stderr
        address = next(
            line.split(":", 1)[1].strip()
            for line in deploy.stdout.splitlines()
            if line.startswith("Deployed to:")
        )

        tmp_dir = tmp_path_factory.mktemp("anvil-keystore")
        keystore_path = tmp_dir / "test.keystore"
        write_scratch_keystore(keystore_path, _TEST_PASSWORD)

        yield AnvilDeployment(
            rpc_url=rpc_url, contract_address=address, keystore_path=str(keystore_path)
        )
    finally:
        proc.terminate()
        proc.wait(timeout=10)


@pytest.fixture
def client(anvil_deployment: AnvilDeployment) -> MonadAnchor:
    return MonadAnchor(
        rpc_url=anvil_deployment.rpc_url,
        chain_id=31337,  # anvil's default chain id
        contract_address=anvil_deployment.contract_address,
        keystore_path=anvil_deployment.keystore_path,
        keystore_password=_TEST_PASSWORD,
        audit_chain_id="actl.audit_log.test",
    )


def _root(byte: str) -> str:
    return "sha256:" + byte * 32


async def test_anchor_checkpoint_submits_a_real_transaction_and_getCheckpoint_reflects_it(
    client: MonadAnchor,
) -> None:
    result = await client.anchor_checkpoint(start_seq=1, end_seq=64, merkle_root_hex=_root("11"))
    assert result.already_anchored is False
    assert result.tx_hash is not None and result.tx_hash.startswith("0x")

    on_chain_root, anchored_at = client.read_checkpoint(start_seq=1, end_seq=64)
    assert on_chain_root == "0x" + "11" * 32
    assert anchored_at > 0


async def test_anchor_checkpoint_resubmission_is_idempotent_no_duplicate_tx(
    client: MonadAnchor,
) -> None:
    root = _root("22")
    first = await client.anchor_checkpoint(start_seq=65, end_seq=128, merkle_root_hex=root)
    assert first.tx_hash is not None

    second = await client.anchor_checkpoint(start_seq=65, end_seq=128, merkle_root_hex=root)
    assert second.already_anchored is True
    assert second.tx_hash is None


async def test_anchor_checkpoint_raises_conflict_for_a_different_root_on_the_same_range(
    client: MonadAnchor,
) -> None:
    await client.anchor_checkpoint(start_seq=129, end_seq=192, merkle_root_hex=_root("33"))

    with pytest.raises(AnchorConflictError):
        await client.anchor_checkpoint(start_seq=129, end_seq=192, merkle_root_hex=_root("44"))

    # The original root must survive the rejected conflicting write.
    on_chain_root, _ = client.read_checkpoint(start_seq=129, end_seq=192)
    assert on_chain_root == "0x" + "33" * 32


def test_read_checkpoint_returns_none_for_a_never_anchored_range(client: MonadAnchor) -> None:
    on_chain_root, anchored_at = client.read_checkpoint(start_seq=99999, end_seq=100063)
    assert on_chain_root is None
    assert anchored_at == 0
