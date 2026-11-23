"""Verify Explorer entry, chooser selection, and popup dismissal on Chromium."""
import asyncio
from ipaddress import ip_address
import json
import os
from pathlib import Path
import re
from urllib.parse import parse_qs, urlencode, urlsplit

from playwright.async_api import expect, async_playwright


async def verify_directory(page, base):
    offsets = []
    fail_next = True

    async def catalog(route):
        nonlocal fail_next
        params = parse_qs(urlsplit(route.request.url).query)
        limit = int(params["limit"][0])
        offset = int(params.get("offset", [0])[0])
        total = 0 if "favoritesOnly" in params else 1001
        if total:
            offsets.append(offset)
        if total and offset and fail_next:
            fail_next = False
            await route.fulfill(status=503, json={"detail": "Directory unavailable"})
            return
        rows = [{"id": f"ts_ods.asset_{i}", "schema": "ts_ods", "table": f"asset_{i}", "displayName": f"Asset {i}",
                 "name": f"asset_{i}", "dataType": "table", "description": "", "propertiesCount": 1, "status": "available",
                 "titleProperty": None, "visibility": "normal", "resourceGroup": "ingestion", "objectCount": 0,
                 "objectCountEstimated": False}
                for i in range(offset, min(offset + limit, total))]
        await route.fulfill(json={"total": total, "offset": offset, "limit": limit, "items": rows,
                                 "schemaCounts": {"ts_ods": total}, "groupCounts": {"ingestion": total},
                                 "groupSchemaCounts": {"ingestion": {"ts_ods": total}}})

    await page.route("**/etl/object-types?**", catalog)
    await page.goto(base + "/ontology")
    cards = page.get_by_test_id("ontology-object-card")
    await expect(cards.first).to_be_visible()
    size = await cards.count()
    assert size < 1001 and offsets == [0]
    await page.get_by_role("button", name="Next page", exact=True).click()
    await expect(page.get_by_text("Unable to load object types. Retry to update the catalog.")).to_be_visible()
    assert await cards.count() == size
    assert await cards.first.evaluate("el => !!el.closest('[inert]')")
    await page.get_by_role("button", name="Retry", exact=True).click()
    await expect(cards.first).to_have_attribute("data-object-id", f"ts_ods.asset_{size}")
    assert offsets == [0, size, size]

    await page.get_by_role("button", name="Published", exact=True).click()
    entry = page.locator("[data-published-type-link]:enabled").first
    await expect(entry).to_be_visible()
    await entry.click()
    await expect(page.get_by_test_id("published-type-inspector")).to_be_visible()
    await page.evaluate("""() => { window.drawerExits = 0; document.addEventListener('animationstart', e => {
      if (e.animationName === 'flow-ontology-drawer-exit') window.drawerExits++;
    }); }""")
    await page.get_by_role("button", name="Close asset preview").click()
    await expect(page.get_by_test_id("published-type-inspector")).to_have_count(0)
    await expect(entry).to_be_focused()
    assert await page.evaluate("window.drawerExits") == 1
    await page.route("**/ontology/v1/object-types?**",
                     lambda route: route.fulfill(status=503, json={"detail": "Search unavailable"}))
    await page.get_by_role("textbox", name="Search published types").fill("unavailable")
    await expect(page.get_by_text("Search unavailable", exact=False)).to_be_visible()
    await expect(page.locator("[data-published-type-link]")).to_have_count(0)


async def main():
    fixture = json.loads(Path(os.environ["ONTOLOGY_QA_FIXTURE"]).read_text(encoding="utf-8"))
    base = fixture["base"].rstrip("/")
    origin = urlsplit(base)
    if origin.scheme != "http" or not ip_address(origin.hostname).is_loopback:
        raise RuntimeError("Expected an isolated Explorer fixture")
    schema = fixture["explorer"]["schema"]
    table = fixture["explorer"]["table"]
    expect.set_options(timeout=20000)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(executable_path=os.getenv("ONTOLOGY_QA_CHROMIUM") or None,
                                          args=["--no-proxy-server", "--disable-dev-shm-usage"])
        page = await browser.new_page(viewport={"width": 1440, "height": 960})
        entry = base + "/ontology/explorer?" + urlencode({"schema": schema, "table": table})
        await page.goto(entry)
        await page.get_by_placeholder("name@company.com").fill(fixture["owner"]["email"])
        await page.get_by_placeholder("Enter your password").fill(fixture["owner"]["password"])
        await page.get_by_role("button", name="Sign in", exact=True).click()
        await page.wait_for_url(entry)
        results = page.get_by_role("region", name="Object results", exact=True)
        await expect(results).to_be_visible()

        # Bare entry is guidance, with no default catalog or record read.
        reads = []
        def record_reads(request):
            if urlsplit(request.url).path.startswith("/api/v1/etl/"):
                reads.append(request.url)
        page.on("request", record_reads)
        await page.goto(base + "/ontology/explorer")
        await expect(page.get_by_role("region", name="Empty exploration")).to_be_visible()
        await expect(page.get_by_role("link", name="Browse Storage-backed object types")).to_be_visible()
        await expect(results).to_have_count(0)
        assert not reads
        page.remove_listener("request", record_reads)

        started, release, continued = (asyncio.Event() for _ in range(3))

        async def gate(route):
            started.set()
            await release.wait()
            await route.continue_()
            continued.set()

        layers = base + "/api/v1/etl/layers"
        await page.route(layers, gate)
        await page.goto(entry, wait_until="domcontentloaded")
        await asyncio.wait_for(started.wait(), 20)
        try:
            await expect(page.get_by_text("Loading schema resources…", exact=True)).to_be_visible()
            await expect(page.get_by_role("heading", name=table, exact=True)).to_be_visible()
            await expect(page.get_by_role("complementary", name="Explorer catalog")).to_be_hidden(timeout=1000)
        finally:
            release.set()
        await asyncio.wait_for(continued.wait(), 20)
        await page.unroute(layers, gate)
        await expect(results).to_be_visible()

        catalog = page.get_by_role("complementary", name="Explorer catalog")
        toggle = page.get_by_role("button", name="Browse object types")
        await toggle.locator(".ui-icon-search").click()
        await expect(catalog).to_be_visible()
        catalog_search = catalog.get_by_role("textbox", name="Search catalog")
        await expect(catalog_search).to_be_focused()
        await catalog.get_by_role("button", name="Refresh catalog").focus()
        await catalog.locator(".ui-input-prefix .ui-icon-search").click()
        await expect(catalog_search).to_be_focused()
        await page.get_by_role("button", name="Results", exact=True).click()
        await expect(catalog).to_be_hidden()
        await toggle.click()
        await toggle.locator(".ui-icon-search").click()
        await expect(catalog).to_be_hidden()
        await toggle.click()
        await page.keyboard.press("Escape")
        await expect(catalog).to_be_hidden()

        property_search = page.get_by_role("textbox", name="Search properties to add a chart or filter")
        await page.locator(".wb-property-search .ui-icon-search").click()
        await property_search.fill("id")
        tools = page.get_by_role("region", name="Explorer tools")
        await tools.get_by_role("button", name="id", exact=True).click()
        await expect(tools.get_by_role("button", name="Add filter")).to_be_visible()
        await page.locator(".wb-current").click()
        await expect(tools).to_have_count(0)

        # Both tabs may open the same asset without sharing selection or view state.
        tabs = page.get_by_role("navigation", name="Explorations", exact=True)
        await page.get_by_role("button", name=re.compile("^Filter & sort")).click()
        await page.get_by_role("textbox", name="Search all columns").fill("Legacy record 2")
        await page.get_by_role("button", name="Apply filters", exact=True).click()
        applied = page.locator(".flow-schema-applied:visible")
        await expect(applied).to_contain_text("Legacy record 2")
        await results.get_by_role("button", name="Column actions for id", exact=True).click()
        await page.get_by_text("Sort descending", exact=True).last.click()
        first_title = results.locator("tbody tr").first.locator(".flow-explorer-selection__table-title a")
        await expect(first_title).to_contain_text("9007199254741017")
        original_title = await first_title.inner_text()
        await results.locator("tbody tr").first.get_by_role("checkbox").check()
        preview = page.get_by_role("complementary", name="Selection Preview")
        await expect(preview.get_by_role("heading", name=original_title, exact=True)).to_be_visible()

        await tabs.get_by_role("button", name="New exploration", exact=True).click()
        await expect(page.get_by_role("region", name="Empty exploration")).to_be_visible()
        assert parse_qs(urlsplit(page.url).query).get("exploration")
        assert "table" not in parse_qs(urlsplit(page.url).query)
        await page.get_by_role("button", name="Browse object types").click()
        await expect(catalog_search).to_be_focused()
        await catalog.locator(".flow-catalog-schema").filter(has_text=schema).click()
        await catalog.get_by_role("button", name=table, exact=True).click()
        await expect(catalog).to_be_visible()
        await page.get_by_role("button", name="Browse object types").click()
        await expect(catalog).to_be_hidden()
        tab_url = page.url
        assert parse_qs(urlsplit(tab_url).query)["table"] == [table]
        await expect(results).to_be_visible()
        await expect(preview).to_have_count(0)
        await expect(results.get_by_role("button", name="Column actions for id", exact=True)).not_to_contain_text("1")
        await expect(applied).to_have_count(0)
        await page.get_by_role("button", name="Explore", exact=True).click()
        chart = page.get_by_role("region", name="Explore results")
        await expect(chart).to_be_visible()

        await tabs.get_by_role("button", name=table, exact=True).first.click()
        await expect(preview.get_by_role("heading", name=original_title, exact=True)).to_be_visible()
        await expect(results.get_by_role("button", name="Column actions for id", exact=True)).to_contain_text("1")
        await expect(applied).to_contain_text("Legacy record 2")
        await page.go_back()
        await expect(chart).to_be_visible()
        assert page.url == tab_url
        await page.go_forward()
        await expect(preview.get_by_role("heading", name=original_title, exact=True)).to_be_visible()
        await page.go_back()
        await expect(chart).to_be_visible()
        await tabs.get_by_role("button", name="Close " + table, exact=True).last.click()
        await expect(preview.get_by_role("heading", name=original_title, exact=True)).to_be_visible()
        assert "exploration" not in parse_qs(urlsplit(page.url).query)

        await preview.get_by_role("button", name="Actions").click()
        await page.get_by_role("menuitem", name="Edit", exact=True).click()
        await preview.get_by_role("textbox", name="Edit name").fill("Unsaved tab draft")
        close_original = tabs.get_by_role("button", name="Close " + table, exact=True)
        await expect(close_original).to_be_disabled()
        await tabs.get_by_role("button", name="New exploration", exact=True).click()
        await expect(page.get_by_role("region", name="Empty exploration")).to_be_visible()
        await expect(close_original).to_be_disabled()
        await tabs.get_by_role("button", name="Close Exploration 2", exact=True).click()
        await expect(preview.get_by_role("textbox", name="Edit name")).to_have_value("Unsaved tab draft")
        await preview.get_by_role("button", name="Cancel", exact=True).click()
        await expect(close_original).to_be_enabled()

        # The surviving tab still deep-links its own resource on a reload.
        await page.reload()
        await expect(results).to_be_visible()
        assert parse_qs(urlsplit(page.url).query)["table"] == [table]
        await page.set_viewport_size({"width": 390, "height": 844})
        await toggle.locator(".ui-icon-search").click()
        await expect(catalog_search).to_be_focused()
        await catalog.get_by_role("button", name="Refresh catalog").focus()
        await catalog.locator(".ui-input-prefix .ui-icon-search").click()
        await expect(catalog_search).to_be_focused()
        await toggle.locator(".ui-icon-search").click()
        await expect(catalog).to_be_hidden()
        await verify_directory(page, base)
        await browser.close()
    print(json.dumps({"ok": True, "stableEntry": True, "catalogOutsideOrEscapeCloses": True,
                      "propertyOptionSelects": True, "independentExplorationTabs": True, "historyAndRefresh": True,
                      "tabFilterAndSortIsolation": True, "closePreservesPendingEdits": True,
                      "catalogIconFocusDesktopAndMobile": True}))


if __name__ == "__main__":
    asyncio.run(main())
