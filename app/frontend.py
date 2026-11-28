"""Serve the built dashboard (frontend/dist) from the API process.

One URL for everything: http://localhost:8000 opens the dashboard, /api/v1 is
the API and /docs the OpenAPI docs. Paths that are not files in the build fall
back to index.html, so the dashboard's own routes (e.g. /runs/<id>) survive a
page reload. Nothing is mounted when the build folder does not exist.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, status
from fastapi.responses import FileResponse

REPO_ROOT = Path(__file__).resolve().parents[1]

# Unknown paths under these prefixes are API 404s, not dashboard routes.
_RESERVED = ("api/", "health", "docs", "redoc", "openapi.json")


def resolve_dist(dist_dir: str) -> Path:
    path = Path(dist_dir)
    return path if path.is_absolute() else REPO_ROOT / path


def mount_dashboard(app: FastAPI, dist_dir: str) -> bool:
    dist = resolve_dist(dist_dir).resolve()
    index = dist / "index.html"
    if not index.is_file():
        return False

    @app.get("/{path:path}", include_in_schema=False)
    async def dashboard(path: str) -> FileResponse:
        if path.startswith(_RESERVED):
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not Found")
        if path:
            candidate = (dist / path).resolve()
            if candidate.is_relative_to(dist) and candidate.is_file():
                return FileResponse(candidate)
        # index.html must not be cached, so a new build is picked up at once;
        # hashed files under assets/ can be.
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

    return True
