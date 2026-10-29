import pytest
from fastapi.testclient import TestClient

from clinic_sidecar.main import create_app

TOKEN = "test-token"


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(token=TOKEN))


def test_health_with_token(client: TestClient) -> None:
    res = client.get("/health", headers={"Authorization": f"Bearer {TOKEN}"})
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["service"] == "clinic-sidecar"


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer wrong"}, {"Authorization": TOKEN}, {"Authorization": "Basic x"}],
)
def test_rejects_missing_or_wrong_token(client: TestClient, headers: dict[str, str]) -> None:
    res = client.get("/health", headers=headers)
    assert res.status_code == 401


def test_unknown_route_still_needs_token(client: TestClient) -> None:
    assert client.get("/nope").status_code == 401


def test_refuses_to_start_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SIDECAR_TOKEN", raising=False)
    with pytest.raises(RuntimeError):
        create_app()
