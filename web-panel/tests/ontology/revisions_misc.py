"""Verify rejection, role revocation, and data incompatibility in isolated candidate Chromium sessions."""

from ipaddress import ip_address
import json
import os
from pathlib import Path
import re
import shutil
from urllib.parse import urlencode, urlsplit
from uuid import UUID, uuid4

from playwright.sync_api import expect, sync_playwright
from sqlalchemy.engine import make_url

from app.web.core.config import settings


DB = "ontology_workflow_regression"
QA_PROJECT = "Ontology capability QA"


def verify():
    stage = "validate isolated fixture and browser target"
    assertions = []
    try:
        target = make_url(settings.SQLALCHEMY_RUNNABLE_DATABASE_URI.unicode_string())
        if (settings.PROJECT_NAME, target.host, target.database) != (QA_PROJECT, "127.0.0.1", DB):
            raise RuntimeError("Expected isolated QA application and database")
        fixture = json.loads(
            Path(os.getenv("ONTOLOGY_QA_FIXTURE", "/tmp/ontology-fixture.json")).read_text(
                encoding="utf-8"
            )
        )
        base = fixture["base"].rstrip("/")
        endpoint = urlsplit(base)
        if (
            endpoint.scheme != "http"
            or not ip_address(endpoint.hostname).is_loopback
            or endpoint.path
            or endpoint.query
            or endpoint.fragment
            or endpoint.username
            or endpoint.password
        ):
            raise RuntimeError("Expected isolated QA loopback origin")
        source = fixture["source"]
        if source["name"] != "QA Work Order":
            raise RuntimeError("Expected the seeded QA Work Order")
        definition_id = str(UUID(source["id"]))
        field_id = source["numericField"]
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,62}", field_id) or not any(
            field_id in row["properties"] for row in source["records"]
        ):
            raise RuntimeError("The QA property must have persisted values")
        api = f"/api/ontology/v1/object-types/{definition_id}"
        url = base + "/ontology/explorer?" + urlencode({"objectType": definition_id})
        reviewer_name = fixture["reader"].get("name") or fixture["reader"]["email"]
        run = uuid4().hex[:8]
        rejected = "QA rejected " + run
        approved = "QA approved before revocation " + run
        pending = "QA pending before revocation " + run
        invalid = "QA remove populated property " + run
        chromium = (
            os.getenv("ONTOLOGY_QA_CHROMIUM")
            or shutil.which("chromium")
            or shutil.which("chromium-browser")
        )
        expect.set_options(timeout=25_000)

        def button(root, label):
            return root.get_by_role("button", name=label, exact=True)

        def response(page, method, path):
            return page.expect_response(
                lambda result: result.request.method == method and urlsplit(result.url).path == path
            )

        def login(browser, account):
            page = browser.new_context().new_page()
            page.set_default_timeout(25_000)
            page.goto(url, wait_until="domcontentloaded")
            page.get_by_placeholder("name@company.com").fill(account["email"])
            page.get_by_placeholder("Enter your password").fill(account["password"])
            with response(page, "POST", "/api/v1/login/token") as request:
                button(page, "Sign in").click()
            assert request.value.status == 200
            page.wait_for_url(url)
            return page

        def panel(page):
            workspace = page.get_by_role("region", name="Model revisions", exact=True)
            if not workspace.is_visible():
                button(page, "Open in").click()
                page.get_by_role("menuitem", name="Model revisions", exact=True).click()
            identity = workspace.get_by_text(re.compile(r"^Published revision \d+$"))
            return workspace, int(identity.text_content().rsplit(" ", 1)[-1])

        def role(manager, workspace, enabled):
            button(workspace, "Revision roles").click()
            button(workspace, "Edit revision roles for " + reviewer_name).click()
            form = workspace.get_by_role("region", name="Edit team revision roles")
            form.get_by_role("checkbox", name="Reviewer", exact=True).set_checked(enabled)
            form.get_by_role("checkbox", name="Editor", exact=True).uncheck()
            member = str(UUID(fixture["reader"]["id"]))
            with response(manager, "PUT", api + "/revision-roles/" + member) as request:
                button(form, "Save revision roles").click()
            assert request.value.status == 204
            expect(workspace.get_by_text("Revision roles saved.", exact=True)).to_be_visible()

        def proposal(workspace, title):
            button(workspace, "Proposals").click()
            button(workspace.get_by_role("region", name="Revision proposals"), title).click()
            detail = workspace.get_by_role("region", name="Proposal details")
            expect(detail.get_by_role("heading", name=title, exact=True)).to_be_visible()
            return detail

        def propose(manager, workspace, title):
            button(workspace, "Propose change").click()
            form = workspace.get_by_role("region", name="Change proposal")
            form.get_by_role("textbox", name="Proposal summary").fill(title)
            form.get_by_role("textbox", name="Description", exact=True).fill(
                "Isolated QA change " + title
            )
            with response(manager, "POST", api + "/revision-proposals") as request:
                button(form, "Submit proposal").click()
            assert request.value.status == 201
            expect(
                workspace.get_by_role("region", name="Proposal details").get_by_role(
                    "heading", name=title, exact=True
                )
            ).to_be_visible()
            return str(UUID(request.value.json()["id"]))

        with sync_playwright() as playwright:
            stage = "launch Chromium"
            browser = playwright.chromium.launch(
                executable_path=chromium, args=["--disable-dev-shm-usage", "--no-proxy-server"]
            )
            try:
                stage = "authenticate manager and grant the reviewer role"
                manager = login(browser, fixture["owner"])
                manager_panel, baseline = panel(manager)
                role(manager, manager_panel, True)
                reviewer = login(browser, fixture["reader"])
                reviewer_panel, reviewer_revision = panel(reviewer)
                assert reviewer_revision == baseline

                stage = "reject a proposal from the independent reviewer session"
                rejected_id = propose(manager, manager_panel, rejected)
                button(reviewer_panel, "Refresh revisions").click()
                details = proposal(reviewer_panel, rejected)
                details.get_by_role("textbox", name="Review note").fill(
                    "QA rejection from the independent reviewer"
                )
                with response(
                    reviewer, "POST", api + f"/revision-proposals/{rejected_id}/review"
                ) as request:
                    button(details, "Reject proposal").click()
                assert request.value.status == 200 and request.value.json()["status"] == "rejected"
                expect(reviewer_panel.get_by_text("Proposal rejected.", exact=True)).to_be_visible()
                button(manager_panel, "Refresh revisions").click()
                details = proposal(manager_panel, rejected)
                expect(
                    details.get_by_text("Rejected · Based on revision " + str(baseline), exact=True)
                ).to_be_visible()
                expect(button(details, "Merge proposal")).to_have_count(0)
                assertions.append("rejected proposal has no merge control")

                stage = "remove a published property with persisted record values"
                button(manager_panel, "Propose change").click()
                form = manager_panel.get_by_role("region", name="Change proposal")
                form.get_by_role("textbox", name="Proposal summary").fill(invalid)
                form.locator("summary").filter(has_text=re.compile(r"^Properties$")).click()
                property_row = form.locator("section.flow-semantic-config").filter(
                    has=manager.locator(f'input[value="{field_id}"]')
                )
                button(property_row, "Remove property").click()
                with response(manager, "POST", api + "/revision-proposals") as request:
                    button(form, "Submit proposal").click()
                assert request.value.status == 422
                expect(manager_panel.get_by_role("alert")).to_contain_text(
                    "Existing records contain removed fields"
                )
                expect(form).to_be_visible()
                button(form, "Cancel proposal").click()
                assertions.append(
                    "persisted field removal returns a compatibility error and keeps the form"
                )

                stage = "approve a proposal and leave another review open"
                approved_id = propose(manager, manager_panel, approved)
                pending_id = propose(manager, manager_panel, pending)
                button(reviewer_panel, "Refresh revisions").click()
                details = proposal(reviewer_panel, approved)
                with response(
                    reviewer, "POST", api + f"/revision-proposals/{approved_id}/review"
                ) as request:
                    button(details, "Approve proposal").click()
                assert request.value.status == 200
                expect(reviewer_panel.get_by_text("Proposal approved.", exact=True)).to_be_visible()
                pending_details = proposal(reviewer_panel, pending)
                expect(button(pending_details, "Approve proposal")).to_be_visible()

                stage = "revoke the reviewer role and reject its stale review request"
                role(manager, manager_panel, False)
                with response(
                    reviewer, "POST", api + f"/revision-proposals/{pending_id}/review"
                ) as request:
                    button(pending_details, "Approve proposal").click()
                assert request.value.status == 404
                expect(reviewer_panel.get_by_role("alert")).to_contain_text(
                    "Revision workspace not found"
                )
                reviewer.reload(wait_until="domcontentloaded")
                with response(reviewer, "GET", api + "/revision-workspace") as request:
                    button(reviewer, "Open in").click()
                    reviewer.get_by_role("menuitem", name="Model revisions", exact=True).click()
                assert request.value.status == 404
                expect(
                    reviewer.get_by_role("region", name="Model revisions", exact=True).get_by_role(
                        "alert"
                    )
                ).to_contain_text("Revision workspace not found")
                assertions.append(
                    "revoked reviewer cannot submit an open review or reopen the workspace"
                )

                stage = "try to merge approval from the now-revoked reviewer"
                details = proposal(manager_panel, approved)
                with response(
                    manager, "POST", api + f"/revision-proposals/{approved_id}/merge"
                ) as request:
                    button(details, "Merge proposal").click()
                assert request.value.status == 409
                expect(manager_panel.get_by_role("alert")).to_contain_text(
                    "The proposer or reviewer no longer has permission"
                )
                expect(
                    manager_panel.get_by_text(f"Published revision {baseline}", exact=True)
                ).to_be_visible()
                expect(manager.locator(".wb-type strong")).to_have_text(source["name"])
                assertions.append("revoked approval cannot change the publication")
            finally:
                browser.close()
        print(
            json.dumps(
                {
                    "ok": True,
                    "feature": "revision-negative-interactions",
                    "revision": baseline,
                    "two_browser_sessions": True,
                    "checks": assertions,
                }
            ),
            flush=True,
        )
        return 0
    except Exception as error:
        print(
            json.dumps(
                {
                    "ok": False,
                    "feature": "revision-negative-interactions",
                    "failed_stage": stage,
                    "error": type(error).__name__,
                    "checks": assertions,
                }
            ),
            flush=True,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(verify())
