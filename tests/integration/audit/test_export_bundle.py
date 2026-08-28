"""§16 / §28 P3 instruction 6: the exported bundle must verify with "no
database access, network access, ACTL source-tree import, or application
secrets required." This test exports a real bundle and runs its verifier as
a subprocess in an isolated temp directory, with `actl` deliberately
unimportable — proving the claim rather than assuming it."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
from scripts.export_audit_bundle import export_bundle
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from actl import config
from actl.application.audit_service import append_entry
from actl.domain.audit.events import AuditAction
from actl.infrastructure.db.uow import UnitOfWork
from actl.platform.ids import new_id

pytestmark = pytest.mark.asyncio(loop_scope="session")

CHECKPOINT_EVERY = 3
ENTRY_COUNT = 7


async def _seed_chain(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> tuple[int, int]:
    monkeypatch.setattr(config.settings, "audit_checkpoint_every", CHECKPOINT_EVERY)

    async with UnitOfWork(session_factory) as uow:
        tail = await uow.audit_log.get_tail()
    start_seq = tail[0] if tail is not None else 0

    for i in range(ENTRY_COUNT):
        async with UnitOfWork(session_factory) as uow:
            await append_entry(
                uow,
                trace_id=new_id("trc"),
                actor_type="system",
                actor_id="export_bundle_test",
                action=AuditAction.MANDATE_LOCKED,
                subject={},
                payload={"i": i, "nonce": new_id("nonce")},
            )
            await uow.commit()

    return start_seq + 1, start_seq + ENTRY_COUNT


def _run_verifier(bundle_dir: Path) -> subprocess.CompletedProcess[str]:
    """Runs verify_bundle.py as a fresh subprocess with `actl` deliberately
    absent from sys.path — the repo's src/ directory is not on PYTHONPATH,
    and cwd is the bundle directory itself, not the repo."""
    return subprocess.run(
        [sys.executable, "verify_bundle.py"],
        cwd=bundle_dir,
        env={"PATH": "/usr/bin:/bin"},  # no PYTHONPATH, no inherited env
        capture_output=True,
        text=True,
        timeout=30,
    )


async def test_exported_bundle_verifies_in_isolation(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from_seq, to_seq = await _seed_chain(session_factory, monkeypatch)

    bundle_dir = tmp_path / "bundle"
    await export_bundle(bundle_dir, from_seq, to_seq, session_factory)

    for name in ("audit_log.ndjson", "checkpoints.json", "metadata.json", "verify_bundle.py"):
        assert (bundle_dir / name).exists()

    # An actual import statement at the start of a line, not prose that
    # happens to mention the word "actl" somewhere in a docstring/comment.
    verifier_source = (bundle_dir / "verify_bundle.py").read_text()
    assert not re.search(r"^\s*(import actl\b|from actl\b)", verifier_source, re.MULTILINE)

    result = _run_verifier(bundle_dir)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "CHAIN VALID" in result.stdout
    assert f"entries={to_seq - from_seq + 1}" in result.stdout
    checkpoints = json.loads((bundle_dir / "checkpoints.json").read_text())
    for checkpoint in checkpoints:
        assert f"merkle root matched at checkpoint {checkpoint['to_seq']} ok" in result.stdout


async def test_tampered_bundle_is_rejected_in_isolation(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from_seq, to_seq = await _seed_chain(session_factory, monkeypatch)

    bundle_dir = tmp_path / "bundle_tampered"
    await export_bundle(bundle_dir, from_seq, to_seq, session_factory)

    ndjson_path = bundle_dir / "audit_log.ndjson"
    lines = ndjson_path.read_text().splitlines()
    entries = [json.loads(line) for line in lines]
    tampered_seq = entries[2]["seq"]
    entries[2]["payload"] = {"tampered": True}
    ndjson_path.write_text("\n".join(json.dumps(e, sort_keys=True) for e in entries) + "\n")

    result = _run_verifier(bundle_dir)

    assert result.returncode != 0
    assert f"CHAIN BROKEN at seq={tampered_seq}" in result.stdout
