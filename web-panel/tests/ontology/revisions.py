"""Accept published revisions through two real browser sessions in the isolated QA app.

Run with Playwright and Chromium in the candidate container. Read fixture credentials
from /tmp/ontology-fixture.json (or ONTOLOGY_QA_FIXTURE). Never log request bodies.
"""

from ipaddress import ip_address
import json
import os
from pathlib import Path
import re
import shutil
from urllib.parse import urlencode, urlsplit
from uuid import uuid4

from playwright.sync_api import expect, sync_playwright


def verify():
    expect.set_options(timeout=25_000)
    fixture = json.loads(
        Path(os.getenv("ONTOLOGY_QA_FIXTURE", "/tmp/ontology-fixture.json")).read_text(
            encoding="utf-8"
        )
    )
    base = fixture["base"].rstrip("/")
    endpoint = urlsplit(base)
    if (
        endpoint.scheme not in {"http", "https"}
        or not ip_address(endpoint.hostname).is_loopback
        or endpoint.path
    ):
        raise RuntimeError("The revision browser test requires a literal loopback fixture URL")
    definition_id = fixture["source"]["id"]
    page_url = (
        base + "/ontology/explorer?" + urlencode({"objectType": definition_id})
    )
    original_name = fixture["source"]["name"]
    new_name = original_name[:102] + " revised"
    reviewer_name = fixture["reader"].get("name") or fixture["reader"]["email"]
    suffix = " " + uuid4().hex[:8]
    editor_title, restore_title = "QA revision" + suffix, "QA rollback" + suffix
    executable = (
        os.getenv("ONTOLOGY_QA_CHROMIUM")
        or shutil.which("chromium")
        or shutil.which("chromium-browser")
    )
    stage = "launch Chromium"
    merge_status = {}
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=executable, args=["--disable-dev-shm-usage", "--no-proxy-server"]
            )
            try:

                def login(account):
                    context = browser.new_context()
                    page = context.new_page()
                    page.set_default_timeout(25_000)
                    page.goto(page_url, wait_until="domcontentloaded")
                    page.get_by_placeholder("name@company.com").fill(account["email"])
                    page.get_by_placeholder("Enter your password").fill(account["password"])
                    with page.expect_response(
                        lambda response: urlsplit(response.url).path == "/api/v1/login/token"
                    ) as login_response:
                        page.get_by_role("button", name="Sign in", exact=True).click()
                    assert login_response.value.status == 200
                    page.wait_for_url(page_url)
                    expect(page.get_by_role("region", name="Object results", exact=True)).to_be_visible()
                    return page

                def button(parent, name):
                    return parent.get_by_role("button", name=name, exact=True)

                def workspace(page):
                    panel = page.get_by_role("region", name="Model revisions", exact=True)
                    if not panel.is_visible():
                        button(page, "Open in").click()
                        page.get_by_role("menuitem", name="Model revisions", exact=True).click()
                    panel.get_by_text(re.compile(r"^Published revision \d+$")).wait_for()
                    return panel

                def merge_from_browser(page, proposal, label):
                    path_prefix = f"/api/ontology/v1/object-types/{definition_id}/revision-proposals/"
                    with page.expect_response(lambda response: response.request.method == "POST"
                                              and urlsplit(response.url).path.startswith(path_prefix)
                                              and urlsplit(response.url).path.endswith("/merge")) as response:
                        button(proposal, "Merge proposal").click()
                    merge_status[label] = response.value.status
                    assert response.value.status == 200

                def select_proposal(panel, title):
                    button(panel, "Proposals").click()
                    button(panel.get_by_role("region", name="Revision proposals"), title).click()
                    return panel.get_by_role("region", name="Proposal details")

                def current_revision(panel):
                    text = panel.get_by_text(re.compile(r"^Published revision \d+$")).text_content()
                    return int(text.rsplit(" ", 1)[1])

                stage = "sign in as project manager"
                manager = login(fixture["owner"])
                pane = workspace(manager)
                baseline = current_revision(pane)

                stage = "assign evaluator role in browser"
                button(pane, "Revision roles").click()
                button(pane, "Edit revision roles for " + reviewer_name).click()
                roles = pane.get_by_role("region", name="Edit team revision roles")
                roles.get_by_role("checkbox", name="Reviewer", exact=True).check()
                roles.get_by_role("checkbox", name="Editor", exact=True).uncheck()
                button(roles, "Save revision roles").click()
                expect(pane.get_by_text("Revision roles saved.", exact=True)).to_be_visible()

                stage = "submit metadata proposal and verify the structured diff"
                button(pane, "Propose change").click()
                form = pane.get_by_role("region", name="Change proposal")
                form.get_by_role("textbox", name="Proposal summary").fill(editor_title)
                form.get_by_role("textbox", name="Display name", exact=True).fill(new_name)
                button(form, "Submit proposal").click()
                proposal = pane.get_by_role("region", name="Proposal details")
                changes = proposal.get_by_role("region", name="Definition changes")
                expect(changes.get_by_text(new_name, exact=True)).to_be_visible()
                expect(changes.get_by_text(original_name, exact=True)).to_be_visible()
                expect(button(proposal, "Approve proposal")).to_have_count(0)

                stage = "sign in separately and approve as delegated reviewer"
                reviewer = login(fixture["reader"])
                review_pane = workspace(reviewer)
                expect(button(review_pane, "Propose change")).to_have_count(0)
                review = select_proposal(review_pane, editor_title)
                expect(
                    review.get_by_role("region", name="Definition changes").get_by_text(
                        new_name, exact=True
                    )
                ).to_be_visible()
                review.get_by_role("textbox", name="Review note").fill(
                    "Checked independently in browser"
                )
                button(review, "Approve proposal").click()
                expect(review_pane.get_by_text("Proposal approved.", exact=True)).to_be_visible()
                expect(button(review, "Merge proposal")).to_have_count(0)

                stage = "send the approved merge request"
                button(pane, "Refresh revisions").click()
                merge_from_browser(manager, proposal, "change")
                stage = "verify the title refresh after merge"
                expect(manager.locator(".wb-type strong")).to_have_text(new_name)
                stage = "verify the revision workspace after merge"
                pane = workspace(manager)
                expect(
                    pane.get_by_text(f"Published revision {baseline + 1}", exact=True)
                ).to_be_visible()

                stage = "propose restoration from the original revision"
                button(pane, "Revision history").click()
                button(
                    pane.get_by_role("region", name="Published revisions"),
                    "Revision " + str(baseline),
                ).click()
                historic = pane.get_by_role("region", name="Historical revision")
                button(historic, "Propose restore").click()
                restore = pane.get_by_role("region", name="Restore proposal")
                restore.get_by_role("textbox", name="Proposal summary").fill(restore_title)
                button(restore, "Submit proposal").click()
                expect(
                    pane.get_by_role("region", name="Proposal details").get_by_text(
                        restore_title, exact=True
                    )
                ).to_be_visible()

                stage = "approve restoration as reviewer and merge as manager"
                button(review_pane, "Refresh revisions").click()
                review = select_proposal(review_pane, restore_title)
                button(review, "Approve proposal").click()
                expect(review_pane.get_by_text("Proposal approved.", exact=True)).to_be_visible()
                stage = "send the approved restore merge request"
                button(pane, "Refresh revisions").click()
                merge_from_browser(manager, pane.get_by_role("region", name="Proposal details"), "restore")
                stage = "verify the title refresh after restore"
                expect(manager.locator(".wb-type strong")).to_have_text(original_name)
                stage = "verify the revision workspace after restore"
                pane = workspace(manager)
                expect(
                    pane.get_by_text(f"Published revision {baseline + 2}", exact=True)
                ).to_be_visible()
                button(pane, "Revision history").click()
                expect(
                    pane.get_by_role("region", name="Published revisions").get_by_text(
                        "Restored revision " + str(baseline), exact=True
                    )
                ).to_be_visible()
            finally:
                browser.close()
        print(
            json.dumps(
                {
                    "ok": True,
                    "feature": "published-revisions",
                    "two_browser_sessions": True,
                    "baseline": baseline,
                    "merged": baseline + 1,
                    "restored": baseline + 2,
                    "merge_http_status": merge_status,
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
                    "feature": "published-revisions",
                    "failed_stage": stage,
                    "merge_http_status": merge_status,
                    "error": type(error).__name__,
                }
            ),
            flush=True,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(verify())
