"""Run inside the isolated acceptance Web container with a seeded fixture and Chromium."""

from ipaddress import ip_address
import json
import os
import re
import shutil
import sys
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from playwright.sync_api import expect, sync_playwright


def launch_context(browser, fixture, actor):
    context = browser.new_context(service_workers="block")
    page = context.new_page()
    page.set_default_timeout(30000)
    page.goto(fixture["base"].rstrip("/") + "/ontology", wait_until="domcontentloaded")
    page.get_by_placeholder("name@company.com").fill(actor["email"])
    page.get_by_placeholder("Enter your password").fill(actor["password"])
    with page.expect_response(
        lambda r: r.url.split("?")[0].endswith("/api/v1/login/token")
    ) as login:
        page.get_by_role("button", name="Sign in", exact=True).click()
    assert login.value.status == 200, "Fixture sign-in failed"
    expect(page.get_by_placeholder("Enter your password")).to_have_count(0)
    return context, page, login.value.json()["access_token"]


def select_record(page, base, typ, record):
    page.goto(
        base.rstrip("/") + "/ontology/explorer?objectType=" + typ["id"],
        wait_until="domcontentloaded",
    )
    page.get_by_role("region", name="Object results").get_by_role(
        "checkbox", name="Select " + record["title"], exact=True
    ).check()


def open_tool(page, title, workbench):
    page.get_by_role("button", name="Open in", exact=True).click()
    page.get_by_role("menuitem", name=title, exact=True).click()
    panel = page.get_by_role("dialog", name=title, exact=True).get_by_role("region", name=workbench, exact=True)
    expect(panel).to_be_visible()
    return panel


def close_tool(page, title):
    page.get_by_role("dialog", name=title, exact=True).get_by_role("button", name="Close", exact=True).click()


def open_links(page, relation, direction):
    page.get_by_role("navigation", name="Selected object view").get_by_role("button", name="Links", exact=True).click()
    panel = page.get_by_role("region", name="Related records", exact=True)
    panel.get_by_role("combobox", name="Relationship", exact=True).select_option(relation + ":" + direction)
    return panel


def main():
    fixture = json.loads(
        Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/ontology-fixture.json").read_text()
    )
    base, source, target = fixture["base"], fixture["source"], fixture["target"]
    origin = urlsplit(base)
    if (origin.scheme != "http" or not origin.hostname or not ip_address(origin.hostname).is_loopback
            or origin.username or origin.password or origin.path or origin.query or origin.fragment):
        raise RuntimeError("Expected an isolated loopback fixture")
    left, right = source["records"][0], target["records"][0]
    label = "QA graph " + uuid4().hex[:8]
    key = "graph-" + uuid4().hex[:10]
    checks = []
    expect.set_options(timeout=30000)
    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=os.getenv("ONTOLOGY_SMOKE_CHROMIUM")
            or shutil.which("chromium")
            or shutil.which("google-chrome"),
            headless=True,
            args=["--disable-dev-shm-usage", "--no-proxy-server"],
        )
        try:
            owner_context, owner, owner_token = launch_context(browser, fixture, fixture["owner"])
            with owner.expect_response(
                lambda r: r.request.method == "POST"
                and r.url.split("?")[0].endswith("/object-types/" + source["id"] + "/explorer/query")
            ) as loaded:
                select_record(owner, base, source, left)
                view = open_tool(owner, "Relationships", "Relationship management")
            source_page = loaded.value.json()
            source_page = source_page.get("data", source_page)
            assert not source_page["hasMore"], (
                "Graph browser fixture expects a single page of controlled QA records"
            )
            create = view.get_by_role("button", name="New relationship", exact=True)
            create.click()
            view.get_by_role("button", name="Cancel creation").click()
            create.click()
            view.get_by_label("Relationship name", exact=True).fill(label)
            view.get_by_label("Relationship key", exact=True).fill(key)
            picker = view.get_by_role("combobox", name="New relationship target type")
            picker.fill(target["name"])
            owner.get_by_role("option", name=target["name"], exact=True).click()
            view.get_by_role("combobox", name="Cardinality").select_option("many_to_one")
            with owner.expect_response(
                lambda r: r.request.method == "POST" and r.url.endswith("/link-types")
            ) as action:
                view.get_by_role("button", name="Create relationship").click()
            assert action.value.status == 201
            typ = action.value.json()
            typ = typ.get("data", typ)
            rel = typ["id"]
            checks.append("create and cancel relationship type")
            # Seed a link via the supported API. This configuration surface browses
            # links; instance creation belongs to the Explorer's linking workflow.
            edge = owner_context.request.post(
                base.rstrip("/") + f"/api/ontology/v1/link-types/{rel}/links",
                headers={"Authorization": "Bearer " + owner_token},
                data={"sourceRecordId": left["id"], "targetRecordId": right["id"]},
            )
            assert edge.status == 201
            catalog = view.get_by_role("complementary", name="Ontology relationships")
            outgoing = catalog.get_by_role("button", name=re.compile(re.escape(label) + r".*Outgoing"))
            expect(outgoing).to_have_attribute("aria-pressed", "true")
            expect(view.get_by_role("region", name="Relationship definition", exact=True)).to_be_visible()
            expect(view.get_by_role("region", name="Connected objects", exact=True)).to_have_count(0)
            expect(view.get_by_role("button", name="Dynamic object sets", exact=True)).to_have_count(0)
            close_tool(owner, "Relationships")
            related = open_links(owner, rel, "outgoing")
            expect(related.get_by_role("link", name=right["title"], exact=True)).to_be_visible()
            checks.append("definition-only workbench and separate outgoing object Links")
            view = open_tool(owner, "Dynamic object sets", "Dynamic object set workbench")
            # Apply a filter that matches the selected fixture record and verify both metrics.
            field = source["filterField"]
            group = source["groupField"]
            numeric = source["numericField"]
            records = [r["properties"] for r in source_page["items"]]
            value = next(
                r["properties"][field] for r in source_page["items"] if r["id"] == left["id"]
            )
            matches = [r for r in records if r[field] == value]
            expected_count = len(matches)
            expected_sum = sum(r[numeric] for r in matches)
            view.get_by_role("combobox", name="Filter property").select_option(field)
            view.get_by_role("combobox", name="Operator").select_option("eq")
            view.get_by_label("Filter value").fill(str(value))
            view.get_by_role("button", name="Add filter").click()
            view.get_by_role("button", name="Remove filter 1").click()
            view.get_by_label("Filter value").fill(str(value))
            view.get_by_role("button", name="Add filter").click()
            view.get_by_role("combobox", name="Group by").select_option(group)
            view.get_by_role("combobox", name="Sum property").select_option(numeric)
            view.get_by_role("button", name="Preview set", exact=True).click()
            expect(
                view.get_by_text(
                    "Matching records: " + str(expected_count) + " · Sum: " + str(expected_sum),
                    exact=True,
                )
            ).to_be_visible()
            expect(view.get_by_role("table").get_by_role("row")).to_have_count(
                1 + len({r[group] for r in matches})
            )
            view.get_by_label("Set name", exact=True).fill(label)
            with owner.expect_response(
                lambda r: r.request.method == "POST" and r.url.endswith("/sets")
            ) as action:
                view.get_by_role("button", name="Save object set").click()
            assert action.value.status == 201
            saved = action.value.json()
            saved = saved.get("data", saved)["id"]
            view.get_by_role("combobox", name="Saved set").select_option(saved)
            view.get_by_role("button", name="Run saved set").click()
            expect(
                view.get_by_text(
                    "Matching records: " + str(expected_count) + " · Sum: " + str(expected_sum),
                    exact=True,
                )
            ).to_be_visible()
            checks.append("add/remove filter, group, count, sum, save and evaluate set")
            member_context, member, _ = launch_context(browser, fixture, fixture["reader"])
            select_record(member, base, target, right)
            member_view = open_tool(member, "Relationships", "Relationship management")
            member_view.get_by_role("complementary", name="Ontology relationships").get_by_role(
                "button", name=re.compile(re.escape(label) + r".*Incoming")
            ).click()
            expect(member_view.get_by_role("button", name="New relationship")).to_have_count(0)
            expect(member_view.get_by_role("region", name="Connected objects", exact=True)).to_have_count(0)
            close_tool(member, "Relationships")
            member_related = open_links(member, rel, "incoming")
            expect(member_related.get_by_role("link", name=left["title"], exact=True)).to_be_visible()
            expect(member_related.get_by_role("button", name="Add link")).to_have_count(0)
            expect(member_related.get_by_role("button", name="Remove relationship to " + left["title"])).to_have_count(0)
            select_record(member, base, source, left)
            with member.expect_response(
                lambda response: response.request.method == "GET"
                and ("/object-types/" + source["id"] + "/sets?") in response.url
            ) as personal:
                member_view = open_tool(member, "Dynamic object sets", "Dynamic object set workbench")
            private_page = personal.value.json()
            assert personal.value.status == 200 and saved not in {
                item["id"] for item in private_page.get("data", private_page)["items"]
            }
            expect(
                member_view.get_by_role("combobox", name="Saved set").locator(
                    'option[value="' + saved + '"]'
                )
            ).to_have_count(0)
            checks.append(
                "reader traverses without edit controls and cannot load owner's private set"
            )
            member_context.close()
            view.get_by_role("combobox", name="Saved set").select_option(saved)
            view.get_by_role("button", name="Delete saved set", exact=True).click()
            owner.get_by_role("dialog", name="Delete object set").get_by_role(
                "button", name="Delete object set", exact=True
            ).click()
            expect(
                view.get_by_role("combobox", name="Saved set").locator(
                    'option[value="' + saved + '"]'
                )
            ).to_have_count(0)
            close_tool(owner, "Dynamic object sets")
            related = open_links(owner, rel, "outgoing")
            related.get_by_role("button", name="Remove relationship to " + right["title"]).click()
            owner.get_by_role("dialog", name="Remove relationship?").get_by_role(
                "button", name="Remove relationship", exact=True
            ).click()
            expect(related.get_by_text("No linked objects", exact=True)).to_be_visible()
            checks.append("delete saved set and remove original link")
            owner_context.close()
        finally:
            browser.close()
    print(json.dumps({"ok": True, "checks": checks}), flush=True)


if __name__ == "__main__":
    main()
