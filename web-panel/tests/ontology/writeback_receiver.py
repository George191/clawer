"""Isolated acceptance receiver. Persists each signed business delivery once."""

import hashlib
import hmac
import json
import os
import sqlite3
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import UUID, uuid4

DB = Path(os.environ["QA_WRITEBACK_STORE"])
SECRET = os.environ["QA_WRITEBACK_SECRET"].encode()


class Receiver(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if self.path != "/deliver":
            return self.reply(404, {})
        try:
            length = int(self.headers["Content-Length"])
            if not 0 < length <= 4_000_000:
                return self.reply(413, {})
            body = self.rfile.read(length)
            key = str(UUID(self.headers["Idempotency-Key"]))
            timestamp = self.headers["X-Ontology-Timestamp"]
            actual = hashlib.sha256(body).hexdigest()
            signature = hmac.digest(
                SECRET, f"v1.{timestamp}.{key}.{actual}".encode(), "sha256"
            ).hex()
            if (
                abs(time.time() - int(timestamp)) > 120
                or self.headers["X-Ontology-SHA256"] != actual
                or not hmac.compare_digest(self.headers["X-Ontology-Signature"], "v1=" + signature)
            ):
                return self.reply(401, {})
            project = json.loads(body)["projectId"]
            if str(UUID(project)) != Path(os.environ["QA_WRITEBACK_PROJECT_FILE"]).read_text().strip():
                return self.reply(403, {})
        except OSError:
            return self.reply(503, {})
        except (TypeError, KeyError, ValueError):
            return self.reply(400, {})
        with sqlite3.connect(DB, timeout=8) as db:
            db.execute("BEGIN IMMEDIATE")
            saved = db.execute(
                "SELECT digest, receipt FROM deliveries WHERE id = ?", (key,)
            ).fetchone()
            if saved and saved[0] != actual:
                return self.reply(409, {})
            receipt = saved[1] if saved else str(uuid4())
            if not saved:
                db.execute(
                    "INSERT INTO deliveries VALUES (?, ?, ?, ?, 1)", (key, actual, receipt, body)
                )
            else:
                db.execute("UPDATE deliveries SET requests = requests + 1 WHERE id = ?", (key,))
            db.commit()
        # Exercise the ambiguous outcome between persistence and acknowledgement explicitly.
        if not saved and os.environ.get("QA_WRITEBACK_FAIL_FIRST_ACK") == "1":
            return self.reply(503, {})
        return self.reply(
            200 if saved else 201,
            {"deliveryId": key, "payloadSha256": actual, "receiptId": receipt},
        )


if __name__ == "__main__":
    with sqlite3.connect(DB) as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS deliveries (id TEXT PRIMARY KEY, digest TEXT, receipt TEXT, body BLOB, requests INTEGER)"
        )
    ThreadingHTTPServer(
        ("127.0.0.1", int(os.environ.get("QA_WRITEBACK_PORT", "18882"))), Receiver
    ).serve_forever()
