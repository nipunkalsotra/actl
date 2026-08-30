"""§16 / §28 P3 instruction 6 / §28 P10 instruction 5: the exported bundle
must verify with "no database access, network access, ACTL source-tree
import, or application secrets required." This test exports a real
bundle and runs its verifier as a subprocess in an isolated temp
directory, with `actl` deliberately unimportable -- proving the claim
rather than assuming it.

Tamper coverage spans every required evidence artifact (manifest,
NDJSON, checkpoints/Merkle roots, metadata) at two layers: a raw file
swap/corruption is caught by `manifest.json`'s own file-hash check before
any chain logic runs; a *consistent* tamper (the file AND its manifest
hash both updated, as a more capable attacker would) is still caught by
the deeper chain-semantic check (entry hash chain, Merkle root, or head
hash) -- proving the manifest is defense in depth, not the only line."""

from __future__ import annotations

import hashlib
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


def _resync_manifest_hash(bundle_dir: Path, filename: str) -> None:
    """Simulates a more capable attacker: after tampering `filename`,
    also updates manifest.json's own claimed hash to match -- isolating
    whatever the *chain-semantic* check catches from what the file-hash
    check alone would have caught."""
    manifest_path = bundle_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    target = bundle_dir / filename
    manifest["files"][filename] = "sha256:" + hashlib.sha256(target.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest, indent=2))


async def test_exported_bundle_verifies_in_isolation(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from_seq, to_seq = await _seed_chain(session_factory, monkeypatch)

    bundle_dir = tmp_path / "bundle"
    await export_bundle(bundle_dir, from_seq, to_seq, session_factory)

    for name in (
        "manifest.json",
        "audit_log.ndjson",
        "checkpoints.json",
        "metadata.json",
        "verify_bundle.py",
    ):
        assert (bundle_dir / name).exists()

    # An actual import statement at the start of a line, not prose that
    # happens to mention the word "actl" somewhere in a docstring/comment.
    verifier_source = (bundle_dir / "verify_bundle.py").read_text()
    assert not re.search(r"^\s*(import actl\b|from actl\b)", verifier_source, re.MULTILINE)

    result = _run_verifier(bundle_dir)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "manifest verified" in result.stdout
    assert "CHAIN VALID" in result.stdout
    assert f"entries={to_seq - from_seq + 1}" in result.stdout
    checkpoints = json.loads((bundle_dir / "checkpoints.json").read_text())
    for checkpoint in checkpoints:
        assert f"merkle root matched at checkpoint {checkpoint['to_seq']} ok" in result.stdout


async def test_tampered_ndjson_is_rejected_by_manifest_check(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A raw file swap -- manifest catches it before any chain logic runs."""
    from_seq, to_seq = await _seed_chain(session_factory, monkeypatch)
    bundle_dir = tmp_path / "bundle_tampered_ndjson_manifest"
    await export_bundle(bundle_dir, from_seq, to_seq, session_factory)

    ndjson_path = bundle_dir / "audit_log.ndjson"
    lines = ndjson_path.read_text().splitlines()
    entries = [json.loads(line) for line in lines]
    entries[2]["payload"] = {"tampered": True}
    ndjson_path.write_text("\n".join(json.dumps(e, sort_keys=True) for e in entries) + "\n")

    result = _run_verifier(bundle_dir)

    assert result.returncode != 0
    assert "MANIFEST MISMATCH: audit_log.ndjson" in result.stdout


async def test_tampered_ndjson_consistent_with_manifest_is_rejected_by_chain_check(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A more capable attacker updates the manifest hash too -- the
    entry-hash chain itself still breaks."""
    from_seq, to_seq = await _seed_chain(session_factory, monkeypatch)
    bundle_dir = tmp_path / "bundle_tampered_ndjson_chain"
    await export_bundle(bundle_dir, from_seq, to_seq, session_factory)

    ndjson_path = bundle_dir / "audit_log.ndjson"
    lines = ndjson_path.read_text().splitlines()
    entries = [json.loads(line) for line in lines]
    tampered_seq = entries[2]["seq"]
    entries[2]["payload"] = {"tampered": True}
    ndjson_path.write_text("\n".join(json.dumps(e, sort_keys=True) for e in entries) + "\n")
    _resync_manifest_hash(bundle_dir, "audit_log.ndjson")

    result = _run_verifier(bundle_dir)

    assert result.returncode != 0
    assert f"CHAIN BROKEN at seq={tampered_seq}" in result.stdout


async def test_tampered_checkpoints_merkle_root_is_rejected(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from_seq, to_seq = await _seed_chain(session_factory, monkeypatch)
    bundle_dir = tmp_path / "bundle_tampered_checkpoints"
    await export_bundle(bundle_dir, from_seq, to_seq, session_factory)

    checkpoints_path = bundle_dir / "checkpoints.json"
    checkpoints = json.loads(checkpoints_path.read_text())
    assert checkpoints, "seeded chain must cross at least one checkpoint boundary"
    checkpoints[0]["merkle_root"] = "sha256:" + "00" * 32
    checkpoints_path.write_text(json.dumps(checkpoints, indent=2))
    _resync_manifest_hash(bundle_dir, "checkpoints.json")

    result = _run_verifier(bundle_dir)

    assert result.returncode != 0
    assert "CHAIN BROKEN" in result.stdout
    assert "(checkpoint)" in result.stdout


async def test_tampered_metadata_head_hash_is_rejected(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from_seq, to_seq = await _seed_chain(session_factory, monkeypatch)
    bundle_dir = tmp_path / "bundle_tampered_metadata"
    await export_bundle(bundle_dir, from_seq, to_seq, session_factory)

    metadata_path = bundle_dir / "metadata.json"
    metadata = json.loads(metadata_path.read_text())
    metadata["head_entry_hash"] = "sha256:" + "ff" * 32
    metadata_path.write_text(json.dumps(metadata, indent=2))
    _resync_manifest_hash(bundle_dir, "metadata.json")

    result = _run_verifier(bundle_dir)

    assert result.returncode != 0
    assert "CHAIN BROKEN: head mismatch" in result.stdout


async def test_missing_manifest_file_is_rejected(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from_seq, to_seq = await _seed_chain(session_factory, monkeypatch)
    bundle_dir = tmp_path / "bundle_missing_manifest"
    await export_bundle(bundle_dir, from_seq, to_seq, session_factory)

    (bundle_dir / "manifest.json").unlink()

    result = _run_verifier(bundle_dir)

    assert result.returncode != 0
    assert "MANIFEST MISSING" in result.stdout


async def test_exported_bundle_contains_no_secrets(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """§28 P10 instruction 5: "ensure exported bundles contain no secrets
    or private keys" -- proven with the same canary technique as
    tests/integration/observability/test_secret_redaction.py, applied to
    every file the exporter writes."""
    canary = f"CANARY-{new_id('bundle')}-secret"
    for field in (
        "razorpay_key_secret",
        "razorpay_webhook_secret",
        "groq_api_key",
        "quote_signing_key",
        "mandate_signing_key",
        "admin_token",
        "read_token",
        "merchant_private_key_hex",
    ):
        monkeypatch.setattr(config.settings, field, canary)

    from_seq, to_seq = await _seed_chain(session_factory, monkeypatch)
    bundle_dir = tmp_path / "bundle_secret_check"
    await export_bundle(bundle_dir, from_seq, to_seq, session_factory)

    for path in bundle_dir.iterdir():
        assert canary not in path.read_text(), f"canary leaked into {path.name}"
