"""tests/chaos is a sibling of tests/integration, not nested under it, so
it does not inherit tests/integration/conftest.py's fixtures automatically
-- re-export the ones needed here (postgres_url, engine, session_factory),
same pattern used for tests/contract in P4."""

from __future__ import annotations

from tests.integration.conftest import engine, postgres_url, session_factory

__all__ = ["engine", "postgres_url", "session_factory"]
