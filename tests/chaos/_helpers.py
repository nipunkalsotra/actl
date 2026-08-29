"""Shared assertion/setup helpers for tests/chaos/test_f1.py..test_f10.py.
Not a test file itself (no `test_` prefix) -- pytest never collects it.

§28 P9 instruction 2: every F1-F10 test proves three properties (typed
status + audit evidence, terminal state, reserved balance exactly zero)
plus no duplicate charges/captures/ledger entries/outbox events/chain
forks after recovery -- `reserved_balance`/`settled_balance` and
`count_ledger_entries_for_ref` are the common primitives every one of
those proofs is built from.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from actl.config import settings
from actl.domain.ledger.model import account, net_balance
from actl.domain.mandate.hashing import compute_spec_hash
from actl.domain.mandate.models import (
    Delegate,
    Mandate,
    MandateBounds,
    MandateControls,
    MandateIntent,
    MandateSignature,
    MandateTemporal,
    Principal,
)
from actl.domain.mandate.signing import sign_spec_hash
from actl.infrastructure.db.uow import UnitOfWork
from actl.platform.ids import new_id


def build_mandate(
    *,
    max_total_minor: int = 900000,
    max_unit_minor: int = 300000,
    max_transactions: int = 1,
    max_price_delta_bps: int = 0,
    nights: int = 3,
    require_refundable: bool = True,
    blocked_merchants: list[str] | None = None,
    expires_at: str = "2027-01-01T00:00:00.000Z",
) -> Mandate:
    """Same shape/signing path as `tests/integration/db/conftest.py::
    make_locked_mandate` (fresh id, `settings.mandate_signing_key`), with
    every bound a chaos test might need to vary explicitly overridable --
    `make_locked_mandate` itself takes no parameters and several F-mode
    scenarios need a bound `make_locked_mandate`'s fixed defaults don't
    allow (e.g. F1's re-quoted price is a genuine ~4% shift, which needs
    `max_price_delta_bps` above the shared fixture's zero-tolerance
    default -- §20.1's own worked example treats that shift as
    acceptable, not a second denial layered on top of STALE_PRICE)."""
    draft = Mandate(
        mandate_id=new_id("mdt"),
        version=1,
        principal=Principal(type="human", id="usr_chaos_test"),
        delegate=Delegate(type="agent", id="agt_chaos_test", key_id="ed25519:chaos-test"),
        intent=MandateIntent(
            category="travel.hotel",
            location="Goa, IN",
            check_in="2026-09-12",
            nights=nights,
            rooms=1,
        ),
        bounds=MandateBounds(
            currency="INR",
            max_total_minor=max_total_minor,
            max_unit_minor=max_unit_minor,
            max_transactions=max_transactions,
            allowed_categories=["travel.hotel"],
            blocked_merchants=blocked_merchants or [],
            require_refundable=require_refundable,
            max_price_delta_bps=max_price_delta_bps,
        ),
        temporal=MandateTemporal(
            not_before="2026-01-01T00:00:00.000Z",
            expires_at=expires_at,
            quote_ttl_s=120,
        ),
        controls=MandateControls(human_confirm_required=True, revocable=True),
    )
    spec_hash = compute_spec_hash(draft)
    signature = MandateSignature(
        alg="HMAC-SHA256",
        key_id="mk_chaos_test",
        value=sign_spec_hash(spec_hash, settings.mandate_signing_key.encode("utf-8")),
    )
    return draft.model_copy(update={"spec_hash": spec_hash, "signature": signature})


async def reserved_balance(
    session_factory: async_sessionmaker[AsyncSession], mandate_id: str
) -> int:
    async with UnitOfWork(session_factory) as uow:
        entries = await uow.ledger_entries.list_for_account(account(mandate_id, "reserved"))
    return net_balance([(e.direction, e.amount_minor) for e in entries])


async def settled_balance(
    session_factory: async_sessionmaker[AsyncSession], mandate_id: str
) -> int:
    async with UnitOfWork(session_factory) as uow:
        entries = await uow.ledger_entries.list_for_account(account(mandate_id, "settled"))
    return net_balance([(e.direction, e.amount_minor) for e in entries])


async def available_balance(
    session_factory: async_sessionmaker[AsyncSession], mandate_id: str
) -> int:
    async with UnitOfWork(session_factory) as uow:
        entries = await uow.ledger_entries.list_for_account(account(mandate_id, "available"))
    return net_balance([(e.direction, e.amount_minor) for e in entries])


async def count_ledger_entries_for_ref(
    session_factory: async_sessionmaker[AsyncSession], ref_id: str
) -> int:
    async with UnitOfWork(session_factory) as uow:
        entries = await uow.ledger_entries.list_for_ref_id(ref_id)
    return len(entries)
