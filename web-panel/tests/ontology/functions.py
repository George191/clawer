"""Author a reviewed calculation, execute it in Chromium and verify replay and undo."""

import json
import os
import shutil
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from playwright.sync_api import expect, sync_playwright


def run():
    fixture_path = Path(os.getenv("ONTOLOGY_QA_FIXTURE", "/tmp/ontology-fixture.json"))
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    base = fixture["base"].rstrip("/")
    target = urlsplit(base)
    assert target.netloc in {"127.0.0.1", f"127.0.0.1:{target.port}"}
    assert target.scheme == "http" and not (target.path or target.query or target.fragment)
    source, actor, reviewer = fixture["source"], fixture["owner"], fixture["reader"]
    record = source["records"][0]
    path = "/api/ontology/v1/object-types/" + source["id"]
    with (
        httpx.Client(base_url=base, trust_env=False) as owner,
        httpx.Client(base_url=base, trust_env=False) as reader,
        sync_playwright() as p,
    ):
        for client, user in [(owner, actor), (reader, reviewer)]:
            token = client.post(
                "/api/v1/login/token",
                data={"username": user["email"], "password": user["password"]},
            )
            token.raise_for_status()
            client.headers["Authorization"] = "Bearer " + token.json()["access_token"]
        grant = owner.put(
            path + "/revision-roles/" + reviewer["id"], json={"canEdit": False, "canReview": True}
        )
        grant.raise_for_status()
        browser = p.chromium.launch(
            executable_path=os.getenv("ONTOLOGY_QA_CHROMIUM") or shutil.which("chromium"),
            args=["--disable-dev-shm-usage", "--no-proxy-server"],
        )
        page = browser.new_page()
        expect.set_options(timeout=22000)
        page.goto(base + "/ontology/explorer?objectType=" + source["id"])
        page.get_by_placeholder("name@company.com").fill(actor["email"])
        page.get_by_placeholder("Enter your password").fill(actor["password"])
        page.get_by_role("button", name="Sign in", exact=True).click()

        def button(name):
            return page.get_by_role("button", name=name, exact=True)

        def open_tool(name):
            button("Open in").click()
            page.get_by_role("menuitem", name=name, exact=True).click()

        open_tool("Model revisions")
        button("Propose change").click()
        form = page.get_by_role("region", name="Change proposal")
        form.get_by_role("textbox", name="Proposal summary").fill("QA multiply amount")
        form.get_by_text("Business functions", exact=True).click()
        button("Add function").click()
        form.get_by_label("Function identifier", exact=True).fill("calculate_amount")
        form.get_by_label("Function name", exact=True).fill("Calculate amount")
        form.get_by_label("Output property", exact=True).select_option("amount")
        form.get_by_label("Calculation", exact=True).select_option("multiply")
        form.get_by_label("Parameter 1 name", exact=True).fill("units")
        form.get_by_label("Operand 2", exact=True).select_option("constant")
        form.get_by_label("Constant 2", exact=True).fill("3")
        with page.expect_response(
            lambda r: r.request.method == "POST" and r.url.endswith("/revision-proposals")
        ) as proposal:
            button("Submit proposal").click()
        assert proposal.value.ok
        prop_id = proposal.value.json()["id"]
        revision = owner.get(path + "/revision-workspace").json()["revision"]
        reader.post(
            path + f"/revision-proposals/{prop_id}/review",
            json={"approve": True, "note": "Checked multiplication"},
        ).raise_for_status()
        owner.post(
            path + f"/revision-proposals/{prop_id}/merge", json={"revision": revision}
        ).raise_for_status()
        page.reload()
        open_tool("Functions")
        panel = page.get_by_role("region", name="Business function runner")
        expect(panel.get_by_role("button", name="Run function")).to_be_disabled()
        page.get_by_role("dialog", name="Functions", exact=True).get_by_role(
            "button", name="Close", exact=True
        ).click()
        page.get_by_role("region", name="Object results").get_by_role(
            "checkbox", name="Select " + record["title"], exact=True
        ).check()
        open_tool("Functions")
        panel.get_by_label("Published function").select_option("calculate_amount")
        panel.get_by_label("Argument units").fill("9")
        with page.expect_response(
            lambda r: r.request.method == "POST" and r.url.endswith("/calculate_amount/invoke")
        ) as result:
            panel.get_by_role("button", name="Run function").click()
        assert result.value.status == 200
        url = path + "/functions/calculate_amount/invoke"
        body = result.value.request.post_data_json
        receipt = result.value.json()
        assert owner.post(url, json=body).json()["id"] == receipt["id"]
        assert owner.get(path + "/records/" + record["id"]).json()["properties"]["amount"] == 27
        assert reader.get(path + "/functions").json()["items"] == []
        assert reader.post(url, json=body).status_code == 404
        assert owner.post(url, json={**body, "arguments": {"units": "invalid"}}).status_code == 422
        assert owner.post(url, json={**body, "modelRevision": revision}).status_code == 409
        assert owner.post(url, json={**body, "requestId": str(uuid4())}).status_code == 409
        undo = owner.post(
            "/api/ontology/v1/projects/"
            + fixture["projectId"]
            + "/transactions/"
            + receipt["id"]
            + "/undo"
        )
        undo.raise_for_status()
        assert (
            owner.get(path + "/records/" + record["id"]).json()["properties"]["amount"]
            == record["properties"]["amount"]
        )
        browser.close()
        print(
            json.dumps(
                {
                    "ok": True,
                    "function": "multiply",
                    "approvedBySecondUser": True,
                    "result": 27,
                    "replay": True,
                    "unauthorized": 404,
                    "badParameter": 422,
                    "staleModel": 409,
                    "staleRecord": 409,
                    "undo": True,
                }
            )
        )


if __name__ == "__main__":
    run()
