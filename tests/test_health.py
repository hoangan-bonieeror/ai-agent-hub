from app.main import app


async def test_health(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_ready_reports_each_dependency(client):
    resp = await client.get("/health/ready")
    body = resp.json()
    assert set(body) == {"status", "postgres", "redis"}
    assert resp.status_code == (200 if body["status"] == "ok" else 503)


def test_v1_routes_are_mounted():
    paths = set(app.openapi()["paths"])
    for path in [
        "/api/v1/agents",
        "/api/v1/agents/{agent_id}",
        "/api/v1/agents/{agent_id}/run",
        "/api/v1/workflows",
        "/api/v1/workflows/{workflow_id}/run",
        "/api/v1/tasks/{task_id}",
        "/api/v1/executions/{execution_id}",
    ]:
        assert path in paths
