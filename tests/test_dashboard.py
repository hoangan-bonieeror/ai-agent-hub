"""The API serves the built dashboard, and lists the runtimes it can execute."""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.frontend import mount_dashboard
from app.orchestrator.dependencies import get_runtime_registry
from app.runtimes import RuntimeRegistry
from app.runtimes.fake_runtime import FakeAgentRuntime


@pytest.fixture
def dist(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<html>dashboard</html>")
    (tmp_path / "assets" / "app.js").write_text("console.log(1)")
    (tmp_path.parent / "secret.txt").write_text("nope")
    return tmp_path


def make_app(dist_dir) -> FastAPI:
    app = FastAPI()

    @app.get("/api/v1/thing")
    async def thing() -> dict:
        return {"ok": True}

    assert mount_dashboard(app, str(dist_dir))
    return app


async def get(app: FastAPI, path: str):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        return await c.get(path)


async def test_serves_index_and_assets(dist):
    app = make_app(dist)
    index = await get(app, "/")
    assert index.status_code == 200 and "dashboard" in index.text
    assert index.headers["cache-control"] == "no-cache"
    asset = await get(app, "/assets/app.js")
    assert asset.status_code == 200 and asset.text == "console.log(1)"


async def test_client_routes_fall_back_to_index(dist):
    response = await get(make_app(dist), "/runs/1234")
    assert response.status_code == 200 and "dashboard" in response.text


async def test_api_routes_win_and_unknown_api_paths_404(dist):
    app = make_app(dist)
    assert (await get(app, "/api/v1/thing")).json() == {"ok": True}
    assert (await get(app, "/api/v1/missing")).status_code == 404
    assert (await get(app, "/health/nope")).status_code == 404


async def test_no_files_outside_the_build(dist):
    response = await get(make_app(dist), "/..%2Fsecret.txt")
    assert "nope" not in response.text


def test_nothing_mounted_without_a_build(tmp_path):
    app = FastAPI()
    assert mount_dashboard(app, str(tmp_path / "missing")) is False
    assert len(app.routes) == len(FastAPI().routes)


async def test_runtimes_endpoint(client):
    from app.main import app

    app.dependency_overrides[get_runtime_registry] = lambda: RuntimeRegistry([FakeAgentRuntime()])
    try:
        response = await client.get("/api/v1/runtimes")
    finally:
        app.dependency_overrides.pop(get_runtime_registry, None)
    assert response.status_code == 200
    assert response.json() == {"items": ["fake"]}
