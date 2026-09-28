import unittest

import httpx

from app.render_api import app


class RenderDeploymentTests(unittest.IsolatedAsyncioTestCase):
    async def test_health_endpoint_is_available_without_ai_calls(self):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})


if __name__ == "__main__":
    unittest.main()
