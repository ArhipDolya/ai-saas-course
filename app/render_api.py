"""Render entry point that serves the API and built React dashboard together."""

from pathlib import Path

from fastapi.staticfiles import StaticFiles

from app.api import app

FRONTEND_DIST = Path(__file__).resolve().parents[1] / "frontend_dist"

# This mount must remain after the API routes imported above so that /api/*,
# /health, /docs, and /openapi.json keep taking precedence over the frontend.
app.mount(
    "/",
    StaticFiles(directory=FRONTEND_DIST, html=True, check_dir=False),
    name="frontend",
)
