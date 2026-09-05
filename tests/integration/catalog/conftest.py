"""HTTP-level fixtures for tests/integration/catalog. Container/engine/
session fixtures (postgres_url, engine, session_factory) live in the parent
tests/integration/conftest.py, shared across every tests/integration/*
subdirectory -- this module only reuses `postgres_url` (the bare connection
string), not the session-scoped `engine`/`session_factory` fixtures: those
are bound to pytest-asyncio's session event loop, while Starlette's
TestClient runs the ASGI app on its own background-thread loop, and an
asyncpg engine used across that boundary fails with "attached to a
different loop". `CatalogTestClient.run_async` exists so test bodies can
seed data through the *same* loop the app's own requests run on, via
TestClient's `.portal`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from dataclasses import dataclass
from typing import Any, TypeVar

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from actl.domain.mandate.models import Mandate
from actl.domain.mandate.state_machine import MandateStatus
from actl.infrastructure.db.repositories.catalog import CatalogItemRecord
from actl.infrastructure.db.uow import UnitOfWork
from actl.interfaces.http.deps import get_uow
from actl.main import app

_T = TypeVar("_T")


@dataclass
class CatalogTestClient:
    http: TestClient
    session_factory: async_sessionmaker[AsyncSession]

    def run_async(self, fn: Callable[..., Awaitable[_T]], *args: Any) -> _T:
        assert self.http.portal is not None, "TestClient not entered as a context manager"
        return self.http.portal.call(fn, *args)

    def seed_items(self, items: list[CatalogItemRecord]) -> None:
        self.run_async(_seed_items, self.session_factory, items)

    def seed_mandate(self, mandate: Mandate) -> None:
        self.run_async(_seed_mandate, self.session_factory, mandate)


async def _seed_items(
    session_factory: async_sessionmaker[AsyncSession], items: list[CatalogItemRecord]
) -> None:
    async with UnitOfWork(session_factory) as uow:
        for item in items:
            await uow.catalog.upsert_item(item)
        await uow.commit()


async def _seed_mandate(
    session_factory: async_sessionmaker[AsyncSession], mandate: Mandate
) -> None:
    async with UnitOfWork(session_factory) as uow:
        await uow.mandates.add(mandate, MandateStatus.LOCKED)
        await uow.commit()


@pytest.fixture
def client(postgres_url: str) -> Iterator[CatalogTestClient]:
    test_engine = create_async_engine(postgres_url, pool_size=5, max_overflow=10)
    test_session_factory = async_sessionmaker(test_engine, expire_on_commit=False)

    async def _override_get_uow() -> AsyncIterator[UnitOfWork]:
        async with UnitOfWork(test_session_factory) as uow:
            yield uow

    app.dependency_overrides[get_uow] = _override_get_uow
    try:
        with TestClient(app) as http_client:
            yield CatalogTestClient(http=http_client, session_factory=test_session_factory)
    finally:
        app.dependency_overrides.pop(get_uow, None)


def make_catalog_item(
    sku: str,
    *,
    unit_price_minor: int,
    available_units: int = 5,
    category: str = "travel.hotel",
    location_city: str = "Goa",
    location_country: str = "IN",
    rating: float = 4.0,
    refundable: bool = True,
    version: int = 1,
    is_buyer_listable: bool = True,
) -> CatalogItemRecord:
    return CatalogItemRecord(
        sku=sku,
        category=category,
        merchant_id="mrc_test",
        unit="night",
        unit_price_minor=unit_price_minor,
        available_units=available_units,
        location_city=location_city,
        location_country=location_country,
        rating=rating,
        sea_facing=False,
        breakfast_included=False,
        refundable=refundable,
        cancellation_window_h=24,
        is_buyer_listable=is_buyer_listable,
        instant_confirm=True,
        taxes_included=True,
        quote_required=True,
        version=version,
    )
