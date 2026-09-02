from fastapi.testclient import TestClient

from actl.main import app


def test_healthz_returns_ok_with_no_dependencies() -> None:
    with TestClient(app) as client:
        response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readyz_reports_db_redis_and_migration() -> None:
    with TestClient(app) as client:
        response = client.get("/readyz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["db"] == "ok"
    assert body["redis"] == "ok"
    assert body["migration"] == "0010"
