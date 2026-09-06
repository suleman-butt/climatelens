from fastapi.testclient import TestClient

from climatelens.api import app

client = TestClient(app)


def test_healthz_is_live_without_models() -> None:
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
