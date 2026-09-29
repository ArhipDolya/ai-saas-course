import unittest

import httpx
from starlette.routing import Mount

from app.render_api import app


class RenderDeploymentTests(unittest.IsolatedAsyncioTestCase):
    def test_frontend_is_mounted_after_api_routes(self):
        frontend_route = app.routes[-1]

        self.assertIsInstance(frontend_route, Mount)
        self.assertEqual(frontend_route.name, "frontend")

    async def test_health_keeps_precedence_over_frontend(self):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})


if __name__ == "__main__":
    unittest.main()
