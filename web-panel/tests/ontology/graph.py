"""Run inside the isolated acceptance Web container with a seeded fixture and Chromium."""

import json
import os
import shutil
import sys
from pathlib import Path
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
    return context, page


def open_graph(page, base, typ, record):
    page.goto(
        base.rstrip("/") + "/ontology?catalog=published&objectType=" + typ["id"],
        wait_until="domcontentloaded",
    )
    page.get_by_role("region", name="Object results table").get_by_role(
        "button", name=record["title"], exact=True
    ).click()
    toggle = page.get_by_role("button", name="Relationships and sets", exact=True)
    if toggle.get_attribute("aria-pressed") != "true":
        toggle.click()
    graph = page.get_by_role("region", name="Object relationships and sets")
    expect(graph).to_be_visible()
    return graph


def main():
    fixture = json.loads(
        Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/ontology-fixture.json").read_text()
    )
    base, source, target = fixture["base"], fixture["source"], fixture["target"]
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
            owner_context, owner = launch_context(browser, fixture, fixture["owner"])
            with owner.expect_response(
                lambda r: r.request.method == "GET"
                and ("/object-types/" + source["id"] + "/records?") in r.url
            ) as loaded:
                view = open_graph(owner, base, source, left)
            source_page = loaded.value.json()
            source_page = source_page.get("data", source_page)
            assert not source_page["hasMore"], (
                "Graph browser fixture expects a single page of controlled QA records"
            )
            create = view.get_by_role("button", name="New relationship type", exact=True)
            create.click()
            view.get_by_role("button", name="Cancel relationship type").click()
            create.click()
            view.get_by_label("Relationship name", exact=True).fill(label)
            view.get_by_label("Relationship key", exact=True).fill(key)
            view.get_by_label("Find target type", exact=True).fill(target["name"])
            view.get_by_role("combobox", name="Target type").select_option(target["id"])
            view.get_by_role("combobox", name="Cardinality").select_option("many_to_one")
            with owner.expect_response(
                lambda r: r.request.method == "POST" and r.url.endswith("/link-types")
            ) as action:
                view.get_by_role("button", name="Save relationship type").click()
            assert action.value.status == 201
            typ = action.value.json()
            typ = typ.get("data", typ)
            rel = typ["id"]
            checks.append("create and cancel relationship type")
            selector = view.get_by_role("combobox", name="Relationship and direction")
            selector.select_option(rel + ":outgoing")
            view.get_by_role("combobox", name="Record to link").select_option(right["id"])
            view.get_by_role("button", name="Add link", exact=True).click()
            neighbor = view.get_by_role("button", name=right["title"], exact=True)
            expect(neighbor).to_be_visible()
            neighbor.click()
            selector.select_option(rel + ":incoming")
            expect(view.get_by_role("button", name=left["title"], exact=True)).to_be_visible()
            view.get_by_role("button", name="Back one record").click()
            selector.select_option(rel + ":outgoing")
            checks.append("create edge, outbound and inbound traversal, return")
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
            member_context, member = launch_context(browser, fixture, fixture["reader"])
            member_view = open_graph(member, base, target, right)
            member_view.get_by_role("combobox", name="Relationship and direction").select_option(
                rel + ":incoming"
            )
            expect(
                member_view.get_by_role("button", name=left["title"], exact=True)
            ).to_be_visible()
            expect(member_view.get_by_role("button", name="New relationship type")).to_have_count(0)
            expect(member_view.get_by_role("button", name="Add link")).to_have_count(0)
            expect(
                member_view.get_by_role("button", name="Remove link to " + left["title"])
            ).to_have_count(0)
            with member.expect_response(
                lambda response: response.request.method == "GET"
                and ("/object-types/" + source["id"] + "/sets?") in response.url
            ) as personal:
                member_view = open_graph(member, base, source, left)
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
            expect(
                view.get_by_role("combobox", name="Saved set").locator(
                    'option[value="' + saved + '"]'
                )
            ).to_have_count(0)
            view.get_by_role("combobox", name="Relationship and direction").select_option(
                rel + ":outgoing"
            )
            view.get_by_role("button", name="Remove link to " + right["title"]).click()
            expect(view.get_by_text("No linked records.", exact=True)).to_be_visible()
            checks.append("delete saved set and remove original link")
            owner_context.close()
        finally:
            browser.close()
    print(json.dumps({"ok": True, "checks": checks}), flush=True)


if __name__ == "__main__":
    main()
