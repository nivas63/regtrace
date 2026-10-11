from __future__ import annotations

from fastapi import APIRouter
from fastapi.testclient import TestClient

from app.config import Settings
from app.core.errors import NotFoundError
from app.main import create_app


def _app_with_failing_routes(settings: Settings) -> TestClient:
    app = create_app(settings)
    router = APIRouter()

    @router.get("/boom/not-found")
    async def not_found() -> None:
        raise NotFoundError("Regulation 42 does not exist")

    @router.get("/boom/crash")
    async def crash() -> None:
        raise RuntimeError("database password is hunter2")

    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


def test_live(client: TestClient) -> None:
    response = client.get("/api/v1/health/live")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.headers["x-request-id"]


def test_request_id_is_propagated(client: TestClient) -> None:
    response = client.get("/api/v1/health/live", headers={"X-Request-ID": "abc-123"})
    assert response.headers["x-request-id"] == "abc-123"


def test_unsafe_request_id_is_replaced(client: TestClient) -> None:
    response = client.get("/api/v1/health/live", headers={"X-Request-ID": "bad id\nwith newline"})
    assert response.headers["x-request-id"] != "bad id\nwith newline"


def test_ready_returns_problem_json_when_db_down(client: TestClient) -> None:
    response = client.get("/api/v1/health/ready")
    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["code"] == "dependency_unavailable"
    assert body["request_id"]


def test_llm_health_makes_no_calls_and_lists_nothing_without_keys(client: TestClient) -> None:
    response = client.get("/api/v1/health/llm")
    assert response.status_code == 200
    assert response.json()["configured_providers"] == []


def test_domain_error_maps_to_problem(settings: Settings) -> None:
    with _app_with_failing_routes(settings) as test_client:
        response = test_client.get("/boom/not-found")
    assert response.status_code == 404
    assert response.json()["detail"] == "Regulation 42 does not exist"


def test_unhandled_error_does_not_leak_internals(settings: Settings) -> None:
    with _app_with_failing_routes(settings) as test_client:
        response = test_client.get("/boom/crash")
    assert response.status_code == 500
    assert "hunter2" not in response.text
    assert response.json()["code"] == "internal_error"
