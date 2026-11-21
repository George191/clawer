"""Verify record rule authoring, independent review and enforcement on the isolated QA app."""

from ipaddress import ip_address
import json
import os
from pathlib import Path
import shutil
from urllib.parse import urlencode, urlsplit
from uuid import uuid4

from playwright.sync_api import sync_playwright


def run():
    fixture = json.loads(
        Path(os.getenv("ONTOLOGY_QA_FIXTURE", "/tmp/ontology-fixture.json")).read_text()
    )
    base = fixture["base"].rstrip("/")
    endpoint = urlsplit(base)
    assert (
        endpoint.scheme in {"http", "https"}
        and ip_address(endpoint.hostname).is_loopback
        and not endpoint.path
    )
    owner, reader, project = fixture["owner"], fixture["reader"], fixture["projectId"]
    key = "qa-policy-" + uuid4().hex[:10]
    fields = ["id", "title", "owner", "category"]
    aud = [{"kind": "group", "id": project}]
    rule = {
        "kind": "property",
        "field": "owner",
        "match": "currentUser",
        "readers": [{"kind": "user", "id": owner["id"]}],
    }
    model = {
        "id": key,
        "name": key,
        "pluralName": key + "s",
        "projectId": project,
        "source": {"mode": "none"},
        "properties": [
            {"id": field, "name": field, "type": "string", "required": True} for field in fields
        ],
        "primaryKey": ["id"],
        "titleKey": "title",
        "security": {"mode": "explicit", "objectReaders": aud, "recordRules": [rule]},
        "actions": [
            {"kind": kind, "fields": params, "submitters": aud}
            for kind, params in [
                ("create", fields),
                ("modify", ["title", "category"]),
                ("delete", []),
            ]
        ],
    }
    with sync_playwright() as playwright:

        def sign_in(account):
            api = playwright.request.new_context(base_url=base)
            res = api.post(
                "/api/v1/login/token",
                form={"username": account["email"], "password": account["password"]},
            )
            assert res.status == 200, "API sign-in failed"
            token = res.json()["access_token"]
            api.dispose()
            return playwright.request.new_context(
                base_url=base, extra_http_headers={"Authorization": "Bearer " + token}
            )

        editor, viewer = sign_in(owner), sign_in(reader)

        def call(client, method, path, body=None, status=200):
            result = client.fetch("/api/ontology/v1" + path, method=method, data=body)
            assert result.status == status, (
                f"{method} {path} returned {result.status}, expected {status}"
            )
            if 200 <= status < 300 and status != 204:
                return result.json()

        draft = call(editor, "POST", "/drafts", model, 201)
        oid = draft["id"]
        path = "/object-types/" + oid
        call(editor, "POST", "/drafts/" + oid + "/publish", {"revision": draft["revision"]})
        revision = call(editor, "GET", path + "/revision-workspace")["revision"]
        rows = {}
        for name in ("shared", "linked", "hidden", "anchor"):
            rows[name] = call(
                editor,
                "POST",
                path + "/actions/create",
                {
                    "values": {
                        "id": name,
                        "title": name,
                        "owner": owner["id"],
                        "category": "shared" if name == "shared" else "private",
                    }
                },
            )["id"]
        assert call(viewer, "GET", path + "/records")["items"] == []
        relation = call(
            editor,
            "POST",
            f"/projects/{project}/link-types",
            {
                "key": key,
                "name": key,
                "sourceDefinitionId": oid,
                "targetDefinitionId": oid,
                "cardinality": "many_to_many",
            },
            201,
        )
        edge_path = "/link-types/" + relation["id"] + "/links"
        ends = {"sourceRecordId": rows["linked"], "targetRecordId": rows["anchor"]}
        edge = call(editor, "POST", edge_path, ends, 201)
        roles = call(editor, "GET", path + "/revision-roles?limit=100")["items"]
        role = next(item for item in roles if item["id"] == reader["id"])
        principals = call(editor, "GET", path + "/revision-principals")["items"]
        reader_label = next(
            p["name"] for p in principals if p["kind"] == "user" and p["id"] == reader["id"]
        )
        role_path = path + "/revision-roles/" + reader["id"]
        if not role["canReview"]:
            call(editor, "PUT", role_path, {"canReview": True, "canEdit": role["canEdit"]}, 204)
        executable = (
            os.getenv("ONTOLOGY_QA_CHROMIUM")
            or shutil.which("chromium")
            or shutil.which("chromium-browser")
        )
        try:
            browser = playwright.chromium.launch(
                executable_path=executable, args=["--disable-dev-shm-usage", "--no-proxy-server"]
            )
            page = browser.new_page()
            page.set_default_timeout(25_000)
            try:
                page_url = (
                    base + "/ontology/explorer?" + urlencode({"objectType": oid})
                )
                page.goto(page_url)
                page.get_by_placeholder("name@company.com").fill(owner["email"])
                page.get_by_placeholder("Enter your password").fill(owner["password"])
                page.get_by_role("button", name="Sign in", exact=True).click()
                page.wait_for_url(page_url)
                page.get_by_role("button", name="Open in", exact=True).click()
                page.get_by_role("menuitem", name="Model revisions", exact=True).click()
                page.get_by_role("button", name="Propose change", exact=True).click()
                form = page.get_by_role("region", name="Change proposal")
                form.get_by_label("Proposal summary").fill(
                    "Authorize the shared category and a designated relationship"
                )
                form.locator("summary").filter(has_text="Security").click()
                policy = form.get_by_role("region", name="Record access rules")

                def add_reader(n):
                    combo = policy.get_by_role("combobox", name=f"Readers for rule {n}")
                    combo.click()
                    page.get_by_role("option", name=reader_label + " (user)", exact=True).click()
                    combo.press("Escape")

                policy.get_by_role("button", name="Add property rule").click()
                add_reader(2)
                attr = policy.get_by_role("group", name="Property rule 2")
                attr.get_by_label("Record property").select_option("category")
                attr.get_by_label("Required value").fill("shared")
                policy.get_by_role("button", name="Add relationship rule").click()
                add_reader(3)
                rel = policy.get_by_role("group", name="Relationship rule 3")
                rel.get_by_label("Link type key").fill(key)
                rel.get_by_label("Related record ID").fill(rows["anchor"])
                with page.expect_response(
                    lambda r: r.request.method == "POST"
                    and urlsplit(r.url).path.endswith("/revision-proposals")
                ) as response:
                    form.get_by_role("button", name="Submit proposal", exact=True).click()
                assert response.value.status == 201, (
                    f"Submit proposal returned {response.value.status}"
                )
                proposal_path = path + "/revision-proposals/" + response.value.json()["id"]
            finally:
                browser.close()
            assert call(viewer, "GET", path + "/records")["items"] == []
            call(
                viewer,
                "POST",
                proposal_path + "/review",
                {"approve": True, "note": "Checked both rules"},
            )
            call(editor, "POST", proposal_path + "/merge", {"revision": revision})
            assert "recordRules" not in call(viewer, "GET", path)["definition"]["security"]
            head = call(viewer, "GET", path + "/records?limit=1")
            tail = call(viewer, "GET", path + "/records?limit=1&offset=1")
            assert head["hasMore"] and not tail["hasMore"]
            assert [item["id"] for item in head["items"] + tail["items"]] == sorted(
                [rows["shared"], rows["linked"]]
            )
            for name in ("shared", "linked"):
                assert (
                    call(viewer, "GET", path + "/records/" + rows[name])["properties"]["title"]
                    == name
                )
            for suffix in ("", "/history"):
                call(viewer, "GET", path + "/records/" + rows["hidden"] + suffix, status=404)
            call(
                viewer,
                "POST",
                path + "/actions/modify",
                {"recordId": rows["hidden"], "expectedRevision": 1, "values": {"title": "updated"}},
                404,
            )
            call(
                viewer,
                "POST",
                path + "/actions/modify",
                {
                    "recordId": rows["shared"],
                    "expectedRevision": 1,
                    "values": {"category": "private"},
                },
                403,
            )
            events = call(viewer, "GET", path + "/records/" + rows["linked"] + "/history")["items"]
            assert events and all(e["changes"] is None for e in events)
            result = call(
                viewer,
                "POST",
                path + "/sets/preview",
                {
                    "filters": [{"field": "category", "op": "eq", "value": "private"}],
                    "groupBy": "category",
                },
            )
            assert result["count"] == 1 and [r["id"] for r in result["items"]] == [rows["linked"]]
            call(editor, "DELETE", edge_path + "/" + edge["id"] + "?" + urlencode(ends), status=204)
            call(viewer, "GET", path + "/records/" + rows["linked"], status=404)
            call(viewer, "GET", path + "/records/" + rows["linked"] + "/history", status=404)
            assert [r["id"] for r in call(viewer, "GET", path + "/records")["items"]] == [
                rows["shared"]
            ]
        finally:
            if not role["canReview"]:
                call(
                    editor, "PUT", role_path, {"canReview": False, "canEdit": role["canEdit"]}, 204
                )
            editor.dispose()
            viewer.dispose()
    print(
        json.dumps(
            {
                "ok": True,
                "feature": "record-policy",
                "browserRules": ["property", "related"],
                "visibleBefore": 0,
                "visibleAfterReviewAndMerge": 2,
                "privateAggregate": 1,
                "visibleAfterUnlink": 1,
            }
        )
    )


if __name__ == "__main__":
    run()
