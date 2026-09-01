"""§28 P12: CORS is a small, explicit local-dev allow-list (main.py) --
exactly localhost:5173 and 127.0.0.1:5173, never a wildcard or regex. This
proves the real browser-facing behaviour Starlette's CORSMiddleware
produces: a 400 on preflight for any origin outside that list (the exact
failure mode a Vite dev server silently drifting off port 5173 hits), and
a real 200 with the matching Access-Control-Allow-Origin header for the
two origins actually supported.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from actl.main import app

_ALLOWED_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")


def _preflight(client: TestClient, origin: str) -> object:
    return client.options(
        "/buyer/v1/config",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
        },
    )


def test_preflight_from_localhost_5173_succeeds() -> None:
    with TestClient(app) as client:
        response = _preflight(client, "http://localhost:5173")
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_preflight_from_127_0_0_1_5173_succeeds() -> None:
    with TestClient(app) as client:
        response = _preflight(client, "http://127.0.0.1:5173")
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:5173"


def test_preflight_from_an_untrusted_origin_is_rejected() -> None:
    """The exact failure mode in the bug report -- OPTIONS -> 400 -- is
    correct, expected CORSMiddleware behaviour for any origin *not* on the
    allow-list (e.g. a Vite dev server that silently drifted off 5173).
    This is not the bug; a drifted origin reaching this branch is."""
    with TestClient(app) as client:
        response = _preflight(client, "http://evil.example.com")
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_buyer_catalog_is_fetchable_from_each_allowed_origin() -> None:
    """The actual (non-preflight) request also carries the CORS header --
    a browser only exposes the response body to JS when this is present,
    so a successful status code alone would not prove the frontend can
    really read it."""
    with TestClient(app) as client:
        for origin in _ALLOWED_ORIGINS:
            response = client.get("/buyer/v1/catalog", headers={"Origin": origin})
            assert response.status_code == 200, (origin, response.text)
            assert response.headers["access-control-allow-origin"] == origin
            assert "items" in response.json()
