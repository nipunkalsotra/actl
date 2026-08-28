from actl.application.ports import Anchor
from actl.infrastructure.anchor.noop import NoopAnchor


async def test_noop_anchor_returns_none() -> None:
    anchor = NoopAnchor()
    assert await anchor.anchor_root("sha256:deadbeef") is None


async def test_noop_anchor_satisfies_the_anchor_port() -> None:
    anchor: Anchor = NoopAnchor()
    assert await anchor.anchor_root("sha256:deadbeef") is None
