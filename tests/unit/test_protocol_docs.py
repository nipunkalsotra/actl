"""§28 P10 release-readiness correction: docs/protocol.md links the
committed JSON Schemas under docs/protocol/ throughout -- this is the
executable check that keeps those links honest. A renamed or deleted
schema file, or a typo in a markdown link, fails here rather than being
discovered by a reviewer's dead click.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PROTOCOL_DOC = REPO_ROOT / "docs" / "protocol.md"
PROTOCOL_DIR = REPO_ROOT / "docs" / "protocol"

_MARKDOWN_LINK = re.compile(r"\]\((protocol/[^)\s]+\.schema\.json)\)")


def _referenced_schema_paths() -> list[str]:
    text = PROTOCOL_DOC.read_text(encoding="utf-8")
    return _MARKDOWN_LINK.findall(text)


def test_protocol_doc_exists() -> None:
    assert PROTOCOL_DOC.is_file()


def test_protocol_doc_references_at_least_one_schema() -> None:
    assert len(_referenced_schema_paths()) > 0


def test_every_schema_link_in_protocol_doc_resolves_to_a_real_file() -> None:
    docs_dir = PROTOCOL_DOC.parent
    for relative_path in _referenced_schema_paths():
        target = docs_dir / relative_path
        assert target.is_file(), f"docs/protocol.md links {relative_path!r}, which does not exist"


def test_every_schema_link_in_protocol_doc_is_valid_json() -> None:
    docs_dir = PROTOCOL_DOC.parent
    for relative_path in _referenced_schema_paths():
        target = docs_dir / relative_path
        json.loads(target.read_text(encoding="utf-8"))  # raises on malformed JSON


def test_every_committed_schema_file_is_referenced_by_the_protocol_doc() -> None:
    """The other direction: a schema nobody links is exactly as stale a
    doc as a link to a schema that no longer exists."""
    referenced = {Path(p).name for p in _referenced_schema_paths()}
    on_disk = {p.name for p in PROTOCOL_DIR.glob("*.schema.json")}
    assert on_disk <= referenced, f"schemas on disk but never linked: {on_disk - referenced}"
