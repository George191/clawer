import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

from app.web.frontend import FrontendFiles


class FrontendRouteTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        (root / "index.html").write_text("<!doctype html><title>Test panel</title>")
        (root / "assets").mkdir()
        (root / "assets" / "panel.js").write_text("console.log('panel');")
        app = FastAPI()

        @app.get("/v1/example")
        def example():
            return {"status": "ok"}

        @app.get("/v1/missing-record")
        def missing_record():
            raise HTTPException(status_code=404, detail="Record not found")

        @app.websocket("/ws")
        async def websocket(websocket: WebSocket):
            await websocket.accept()
            await websocket.send_text("connected")
            await websocket.close()

        app.mount("/static", StaticFiles(directory=root), name="static")
        app.mount("/", FrontendFiles(directory=root, html=True, api_prefix="/v1"), name="frontend")
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def test_browser_routes_and_query_refresh_return_entry_document(self):
        for path in (
            "/",
            "/login",
            "/settings",
            "/organization?view=tenants",
            "/organization/users/42",
            "/ontology/explorer/record?object_type=company&pk=42",
            "/automation/workflows",
            "/legal/privacy",
        ):
            with self.subTest(path=path):
                response = self.client.get(path, headers={"Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8"})
                self.assertEqual(response.status_code, 200)
                self.assertIn("text/html", response.headers["content-type"])
                self.assertEqual(response.text, "<!doctype html><title>Test panel</title>")
                self.assertFalse(response.history)

    def test_browser_head_returns_entry_headers_without_body(self):
        response = self.client.head("/ontology/explorer?view=objects", headers={"Accept": "text/html"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["content-type"])
        self.assertGreater(int(response.headers["content-length"]), 0)
        self.assertEqual(response.content, b"")

    def test_api_namespace_and_business_errors_keep_json_responses(self):
        for path, detail in (
            ("/api", "Not Found"),
            ("/v1", "Not Found"),
            ("/v1/does-not-exist", "Not Found"),
            ("/v1/missing-record", "Record not found"),
        ):
            with self.subTest(path=path):
                response = self.client.get(path, headers={"Accept": "text/html"})
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.json(), {"detail": detail})
        response = self.client.get("/v1/example", headers={"Accept": "text/html"})
        self.assertEqual(response.json(), {"status": "ok"})

    def test_missing_assets_and_websocket_http_paths_remain_404(self):
        for path in (
            "/assets/missing.js",
            "/assets/missing",
            "/static/missing.css",
            "/static/missing",
            "/missing.css",
            "/favicon.ico",
            "/ontology/missing.js",
            "/ws",
            "/ws/missing",
        ):
            with self.subTest(path=path):
                response = self.client.get(path, headers={"Accept": "text/html"})
                self.assertEqual(response.status_code, 404)
                self.assertIn("application/json", response.headers["content-type"])
        response = self.client.get("/assets/panel.js", headers={"Accept": "text/html"})
        self.assertEqual(response.text, "console.log('panel');")

    def test_json_fetches_and_unacceptable_html_are_not_browser_routes(self):
        for accept in (
            "application/json",
            "*/*",
            "text/html;q=0,application/json",
            "text/html;q=0.0",
            "text/html;q=invalid",
            "text/html;q=2",
        ):
            with self.subTest(accept=accept):
                response = self.client.get("/ontology", headers={"Accept": accept})
                self.assertEqual(response.status_code, 404)

    def test_non_navigation_methods_do_not_return_entry_document(self):
        for method in ("POST", "PUT", "PATCH", "DELETE", "OPTIONS"):
            with self.subTest(method=method):
                response = self.client.request(method, "/ontology", headers={"Accept": "text/html"})
                self.assertEqual(response.status_code, 405)
                self.assertIn("application/json", response.headers["content-type"])

    def test_websocket_routes_remain_available(self):
        with self.client.websocket_connect("/ws") as websocket:
            self.assertEqual(websocket.receive_text(), "connected")
