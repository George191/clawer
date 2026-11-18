"""Deliver and retry through the candidate's real Transactions workbench."""

import json
import shutil
from pathlib import Path
from uuid import uuid4

import httpx
from playwright.sync_api import expect, sync_playwright

f = json.loads(Path("/tmp/ontology-fixture.json").read_text())
base = f["base"]
actor, source = f["owner"], f["source"]
project = f["projectId"]
name = "Delivery QA " + uuid4().hex[:8]
assert base.rstrip("/") == "http://127.0.0.1"
with httpx.Client(base_url=base, timeout=15, trust_env=False) as client:

    def login(user):
        resp = client.post(
            "/api/v1/login/token", data={"username": user["email"], "password": user["password"]}
        )
        resp.raise_for_status()
        return {"Authorization": "Bearer " + resp.json()["access_token"]}

    owner = login(actor)
    body = {
        "name": name,
        "requestId": str(uuid4()),
        "steps": [
            {
                "kind": "modify",
                "definitionId": source["id"],
                "recordId": source["records"][0]["id"],
                "expectedRevision": source["records"][0]["revision"],
                "values": {"amount": 53},
            }
        ],
    }
    resp = client.post(
        f"/api/ontology/v1/projects/{project}/transactions", headers=owner, json=body
    )
    resp.raise_for_status()
    tx = resp.json()["id"]
    path = f"/api/ontology/v1/projects/{project}/transactions/{tx}/writeback"
    assert client.get(path, headers=login(f["reader"])).status_code in (403, 404)
    assert client.post(path + "/unconfigured", headers=owner).status_code == 404
    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=shutil.which("chromium"),
            args=["--no-proxy-server", "--disable-dev-shm-usage"],
        )
        page = browser.new_page()
        page.goto(base + "/ontology")
        page.get_by_placeholder("name@company.com").fill(actor["email"])
        page.get_by_placeholder("Enter your password").fill(actor["password"])
        page.get_by_role("button", name="Sign in", exact=True).click()
        expect(page.get_by_placeholder("Enter your password")).to_have_count(0)
        page.goto(base + "/ontology?catalog=published&objectType=" + source["id"])
        page.get_by_role("button", name="Transactions", exact=True).click()
        entry = (
            page.get_by_role("region", name="Transaction workbench")
            .get_by_role("listitem")
            .filter(has_text=name)
        )
        entry.get_by_role("button", name="Writeback", exact=True).click()
        pane = entry.get_by_role("region", name="External writeback")
        pane.get_by_role("combobox", name="Business receiver").select_option("qa-business")
        pane.get_by_role("button", name="Send transaction", exact=True).click()
        expect(pane.get_by_role("status")).to_contain_text("Delivery failed", timeout=15000)
        pane.get_by_role("button", name="Retry delivery", exact=True).click()
        expect(pane.get_by_role("status")).to_contain_text("Delivery delivered", timeout=15000)
        expect(pane.get_by_role("status")).to_contain_text("Attempts 2")
        browser.close()
    data = client.get(path, headers=owner).json()["deliveries"][0]
    assert data["status"] == "delivered" and data["attempts"] == 2
    assert client.post(path + "/qa-business", headers=owner).json() == data
    print(
        json.dumps(
            {
                "ok": True,
                "transactionId": tx,
                "projectId": project,
                "recordId": source["records"][0]["id"],
                "deliveryId": data["id"],
                "receiptId": data["receiptId"],
                "attempts": data["attempts"],
            }
        )
    )
