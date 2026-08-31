"""§28 P11 instruction 5: `actl verify-anchor --to <seq>` -- the opt-in
Testnet verifier's CLI wrapper. Fail-closed branches only (no local
checkpoint, ANCHOR_PROVIDER=noop) -- the real on-chain read path is
already proven end to end against anvil in
tests/integration/anchor/test_monad_testnet_anvil.py.
"""

from __future__ import annotations

from collections.abc import Coroutine
from typing import Any
from unittest.mock import patch

import pytest

from actl import cli, config


def _fake_asyncio_run(result: object) -> Any:
    def _run(coro: Coroutine[Any, Any, Any]) -> object:
        coro.close()  # never actually scheduled -- avoid "never awaited"
        return result

    return _run


def test_verify_anchor_fails_when_no_local_checkpoint_exists(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with patch("actl.cli.asyncio.run", side_effect=_fake_asyncio_run(None)):
        exit_code = cli._verify_anchor(999999999)
    out = capsys.readouterr().out
    assert exit_code == 1
    assert "no local checkpoint" in out


def test_verify_anchor_fails_when_anchor_provider_is_noop(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from actl.infrastructure.db.repositories.audit_checkpoints import AuditCheckpointRecord

    monkeypatch.setattr(config.settings, "anchor_provider", "noop")
    checkpoint = AuditCheckpointRecord(from_seq=1, to_seq=64, merkle_root="sha256:" + "ab" * 32)

    with patch("actl.cli.asyncio.run", side_effect=_fake_asyncio_run(checkpoint)):
        exit_code = cli._verify_anchor(64)
    out = capsys.readouterr().out
    assert exit_code == 1
    assert "ANCHOR_PROVIDER=noop" in out
