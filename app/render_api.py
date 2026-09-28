"""Render entry point: serve the API and the built React dashboard together."""

from pathlib import Path

from fastapi.staticfiles import StaticFiles

from app.api import app

FRONTEND_DIST = Path(__file__).resolve().parents[1] / "frontend_dist"


@app.get("/health", include_in_schema=False)
async def health() -> dict[str, str]:
    """Lightweight Render liveness check; startup already verifies the database."""
    return {"status": "ok"}


if FRONTEND_DIST.is_dir():
    # Registered after every API route so /api/* keeps taking precedence.
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
