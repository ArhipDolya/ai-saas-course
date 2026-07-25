from pathlib import Path

from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import app


class SPAStaticFiles(StaticFiles):
    """Serve React files and return index.html for frontend routes."""

    async def get_response(self, path: str, scope):
        # Не маскуємо неправильні API endpoint-и React-сторінкою.
        if path.startswith("api/"):
            raise StarletteHTTPException(status_code=404)

        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as error:
            if error.status_code != 404:
                raise

            # Підтримка React Router і відкриття сторінок напряму.
            return await super().get_response("index.html", scope)


FRONTEND_DIRECTORY = (
    Path(__file__).resolve().parent.parent / "frontend_dist"
)

if not FRONTEND_DIRECTORY.exists():
    raise RuntimeError(
        "Frontend build was not found. "
        "Build the React application before starting Render."
    )


# Важливо: mount додається після всіх /api endpoint-ів.
app.mount(
    "/",
    SPAStaticFiles(
        directory=FRONTEND_DIRECTORY,
        html=True,
    ),
    name="frontend",
)