"""Author a project marking in Chromium and verify live two-account propagation."""

import json
import os
import shutil
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from uuid import uuid4

from playwright.sync_api import expect, sync_playwright


def run():
    fixture = json.loads(
        Path(os.getenv("ONTOLOGY_QA_FIXTURE", "/tmp/ontology-fixture.json")).read_text()
    )
    base = fixture["base"].rstrip("/")
    assert ip_address(urlsplit(base).hostname).is_loopback
    owner, reader, project = fixture["owner"], fixture["reader"], fixture["projectId"]
    unique = "qa-clearance-" + uuid4().hex[:8]
    with sync_playwright() as pw:

        def sign_in(person):
            client = pw.request.new_context(base_url=base)
            response = client.post(
                "/api/v1/login/token",
                form={"username": person["email"], "password": person["password"]},
            )
            assert response.status == 200
            token = response.json()["access_token"]
            client.dispose()
            return pw.request.new_context(
                base_url=base, extra_http_headers={"Authorization": "Bearer " + token}
            )

        admin, member = sign_in(owner), sign_in(reader)

        def call(client, method, path, data=None, status=200):
            response = client.fetch("/api/ontology/v1" + path, method=method, data=data)
            assert response.status == status, (
                f"{method} {path}: {response.status} expected {status}"
            )
            return response.json() if 200 <= status < 300 and status != 204 else None

        marks = f"/projects/{project}/markings"
        group = [{"kind": "group", "id": project}]

        def publish(name, labels):
            document = {
                "id": name,
                "name": name,
                "pluralName": name + "s",
                "projectId": project,
                "source": {"mode": "none"},
                "properties": [{"id": "key", "name": "Key", "type": "string", "required": True}],
                "primaryKey": ["key"],
                "titleKey": "key",
                "actions": [{"kind": "create", "fields": ["key"], "submitters": group}],
                "security": {"mode": "explicit", "objectReaders": group, "markings": labels},
            }
            draft = call(admin, "POST", "/drafts", document, 201)
            call(
                admin,
                "POST",
                "/drafts/" + draft["id"] + "/publish",
                {"revision": draft["revision"]},
            )
            return draft["id"]

        first = publish(unique, [])
        path = "/object-types/" + first
        roles = call(admin, "GET", path + "/revision-roles?limit=100")["items"]
        role = next(r for r in roles if r["id"] == reader["id"])
        if not role["canReview"]:
            call(
                admin,
                "PUT",
                path + "/revision-roles/" + reader["id"],
                {"canReview": True, "canEdit": role["canEdit"]},
                204,
            )
        principals = call(admin, "GET", path + "/revision-principals")["items"]

        def label(person):
            return (
                next(
                    p["name"] for p in principals if p["kind"] == "user" and p["id"] == person["id"]
                )
                + " (user)"
            )

        browser = pw.chromium.launch(
            executable_path=os.getenv("ONTOLOGY_QA_CHROMIUM")
            or shutil.which("chromium")
            or shutil.which("chromium-browser"),
            args=["--disable-dev-shm-usage", "--no-proxy-server"],
        )
        try:
            href = base + "/ontology?" + urlencode({"catalog": "published", "objectType": first})

            def tab(person):
                page = browser.new_page()
                page.set_default_timeout(25000)
                page.goto(href)
                page.get_by_placeholder("name@company.com").fill(person["email"])
                page.get_by_placeholder("Enter your password").fill(person["password"])
                page.get_by_role("button", name="Sign in", exact=True).click()
                page.wait_for_url(href)
                return page

            author = tab(owner)
            viewer = tab(reader)

            def edit_form():
                author.goto(href)
                pane = author.get_by_role("region", name="Model revisions", exact=True)
                if not pane.is_visible():
                    author.get_by_role("button", name="Model revisions", exact=True).click()
                pane.get_by_role("button", name="Propose change", exact=True).click()
                form = pane.get_by_role("region", name="Change proposal")
                form.locator("summary").filter(has_text="Security").click()
                return form, form.get_by_role("region", name="Security markings")

            def choose(panel, combo, text):
                picker = panel.get_by_role("combobox", name=combo, exact=True)
                picker.click()
                author.get_by_role("listbox").get_by_role("option", name=text, exact=True).click()
                picker.press("Escape")

            def submission(button, method, path_suffix):
                with author.expect_response(
                    lambda r: r.request.method == method
                    and urlsplit(r.url).path.endswith(path_suffix)
                ) as seen:
                    button.click()
                response = seen.value
                assert response.ok, f"{method} {path_suffix}: {response.status}"
                return response.json()

            form, panel = edit_form()
            panel.get_by_text("Manage project clearances", exact=True).click()
            panel.get_by_label("Marking name").fill(unique)
            choose(panel, "Clearance holders", label(owner))
            choose(panel, "Clearance holders", label(reader))
            mark = submission(panel.get_by_role("button", name="Create marking"), "POST", marks)[
                "id"
            ]
            choose(panel, "Required project markings", unique)
            form.get_by_label("Proposal summary").fill("Require project clearance")
            proposal = submission(
                form.get_by_role("button", name="Submit proposal"),
                "POST",
                path + "/revision-proposals",
            )
            with viewer.expect_response(
                lambda r: r.request.method == "GET"
                and urlsplit(r.url).path.endswith("/revision-workspace")
            ):
                viewer.get_by_role("button", name="Model revisions", exact=True).click()
            viewer.get_by_role("button", name="Proposals", exact=True).click()
            viewer.get_by_role("button", name="Require project clearance", exact=True).click()
            with viewer.expect_response(
                lambda r: r.request.method == "POST" and r.url.endswith("/review")
            ) as approved:
                viewer.get_by_role("button", name="Approve proposal").click()
            assert approved.value.ok
            version = call(admin, "GET", path + "/revision-workspace")["revision"]
            call(
                admin,
                "POST",
                path + "/revision-proposals/" + proposal["id"] + "/merge",
                {"revision": version},
            )
            second = publish(unique + "-related", [mark])
            bpath = "/object-types/" + second
            txpath = f"/projects/{project}/transactions"
            payload = {
                "requestId": str(uuid4()),
                "name": unique,
                "steps": [
                    {"kind": "create", "definitionId": first, "values": {"key": "first-value"}},
                    {"kind": "create", "definitionId": second, "values": {"key": "second-value"}},
                ],
            }
            receipt = call(member, "POST", txpath, payload)
            ids = [call(member, "GET", p + "/records")["items"][0]["id"] for p in (path, bpath)]
            typ = call(
                admin,
                "POST",
                f"/projects/{project}/link-types",
                {
                    "key": unique,
                    "name": unique,
                    "sourceDefinitionId": first,
                    "targetDefinitionId": second,
                },
                201,
            )["id"]
            call(
                admin,
                "POST",
                "/link-types/" + typ + "/links",
                {"sourceRecordId": ids[0], "targetRecordId": ids[1]},
                201,
            )
            neighbors = f"/link-types/{typ}/records/{ids[0]}/neighbors?direction=outgoing"
            assert call(member, "GET", neighbors)["items"][0]["record"]["id"] == ids[1]
            _, panel = edit_form()
            panel.get_by_text("Manage project clearances", exact=True).click()
            panel.get_by_label("Project marking", exact=True).select_option(mark)
            choose(panel, "Clearance holders", label(reader))
            submission(
                panel.get_by_role("button", name="Save clearances"), "PUT", marks + "/" + mark
            )
            for restricted in [
                path,
                bpath,
                path + "/records",
                path + "/records/" + ids[0] + "/history",
                neighbors,
            ]:
                call(member, "GET", restricted, status=404)
            call(member, "POST", path + "/sets/preview", {}, 404)
            call(member, "POST", txpath, payload, 404)
            call(member, "POST", txpath + "/" + receipt["id"] + "/undo", status=404)
            assert receipt["id"] not in {r["id"] for r in call(member, "GET", txpath)["items"]}
            assert not call(member, "GET", "/object-types?q=" + unique)["items"]
            choose(panel, "Clearance holders", label(reader))
            submission(
                panel.get_by_role("button", name="Save clearances"), "PUT", marks + "/" + mark
            )
            panel.get_by_role("button", name="View clearance history").click()
            expect(
                panel.get_by_role("list", name="Clearance history").get_by_role("listitem")
            ).to_have_count(3)
            assert call(member, "GET", neighbors)["items"][0]["record"]["id"] == ids[1]
            assert call(member, "POST", txpath, payload)["id"] == receipt["id"]
        finally:
            browser.close()
            if not role["canReview"]:
                call(
                    admin,
                    "PUT",
                    path + "/revision-roles/" + reader["id"],
                    {"canReview": False, "canEdit": role["canEdit"]},
                    204,
                )
            member.dispose()
            admin.dispose()
    print(
        json.dumps(
            {
                "ok": True,
                "feature": "markings",
                "browsers": 2,
                "types": 2,
                "clearanceRevisions": 3,
                "replayRestored": True,
            }
        )
    )


if __name__ == "__main__":
    run()
