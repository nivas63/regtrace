"""API smoke test over the database populated by test_pipeline (runs after it).

Exercises authentication, RBAC and every read endpoint's SQL against real data.
"""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from app.config import Settings
from app.container import build_services
from app.main import create_app
from app.services.seed import ensure_bank

PASSWORD = "test-password"


def _seed_users(settings: Settings) -> None:
    async def run() -> None:
        async with build_services(settings) as services:
            await ensure_bank(services, PASSWORD)

    asyncio.run(run())


def test_api_endpoints(db_settings: Settings) -> None:
    _seed_users(db_settings)
    app = create_app(db_settings)
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/api/v1/dashboard/summary").status_code == 401
        bad = client.post("/api/v1/auth/login", json={"email": "analyst@aravali.example", "password": "nope"})
        assert bad.status_code == 401

        def token(email: str) -> dict[str, str]:
            res = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
            assert res.status_code == 200, res.text
            return {"Authorization": f"Bearer {res.json()['access_token']}"}

        analyst, auditor = token("analyst@aravali.example"), token("auditor@aravali.example")

        for path in (
            "/api/v1/auth/me",
            "/api/v1/dashboard/summary",
            "/api/v1/documents",
            "/api/v1/regulations",
            "/api/v1/obligations",
            "/api/v1/controls",
            "/api/v1/mappings",
            "/api/v1/evidence",
            "/api/v1/compliance/findings",
            "/api/v1/compliance/findings?status=ALL",
            "/api/v1/risk/heatmap",
            "/api/v1/remediation",
            "/api/v1/impact",
            "/api/v1/simulation",
            "/api/v1/conflicts",
            "/api/v1/clusters",
            "/api/v1/agent-runs",
            "/api/v1/reviews",
            "/api/v1/observability/llm",
            "/api/v1/eval/runs",
        ):
            res = client.get(path, headers=analyst)
            assert res.status_code == 200, (path, res.text[:500])

        regulations = client.get("/api/v1/regulations", headers=analyst).json()
        if regulations:
            reg_id = regulations[0]["id"]
            assert client.get(f"/api/v1/regulations/{reg_id}", headers=analyst).status_code == 200
            assert client.get(f"/api/v1/regulations/{reg_id}/changes", headers=analyst).status_code == 200
        obligations = client.get("/api/v1/obligations", headers=analyst).json()
        if obligations:
            oid = obligations[0]["id"]
            assert client.get(f"/api/v1/obligations/{oid}", headers=analyst).status_code == 200
            assert client.get(f"/api/v1/graph?node_id={oid}", headers=analyst).status_code == 200
        controls = client.get("/api/v1/controls", headers=analyst).json()
        if controls:
            assert client.get(f"/api/v1/controls/{controls[0]['id']}", headers=analyst).status_code == 200
        runs = client.get("/api/v1/agent-runs", headers=analyst).json()
        if runs:
            trace = client.get(f"/api/v1/agent-runs/{runs[0]['id']}", headers=analyst).json()
            assert "steps" in trace and "llm_calls" in trace

        # RBAC: analysts cannot read the audit trail or decide reviews; auditors can read audit.
        assert client.get("/api/v1/audit", headers=analyst).status_code == 403
        assert client.get("/api/v1/audit", headers=auditor).status_code == 200
        verify = client.get("/api/v1/audit/verify", headers=auditor).json()
        assert verify["bank_chain"]["valid"] is True
        reviews = client.get("/api/v1/reviews", headers=analyst).json()
        if reviews:
            denied = client.post(
                f"/api/v1/reviews/{reviews[0]['id']}/decision", headers=analyst, json={"decision": "APPROVE"}
            )
            assert denied.status_code == 403
        evaluation = client.post("/api/v1/eval/run", headers=analyst)
        assert evaluation.status_code == 200, evaluation.text[:800]
        metrics = evaluation.json()
        assert metrics["parsing"]["paragraph_recovery"] == 1.0
        assert {"precision", "recall", "f1"} <= set(metrics["extraction"])
        assert "recall@5" in metrics["retrieval"]
        assert (
            client.post(
                "/api/v1/documents/upload",
                headers=auditor,
                data={"doc_class": "POLICY"},
                files={"file": ("x.txt", b"hello world policy text", "text/plain")},
            ).status_code
            == 403
        )
