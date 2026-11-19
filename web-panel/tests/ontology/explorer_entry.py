"""Verify Explorer entry, chooser selection, and popup dismissal on Chromium."""
import asyncio
from ipaddress import ip_address
import json
import os
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

from playwright.async_api import expect, async_playwright


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

        # Re-enter from the workspace navigation while the catalog is still in flight.
        navigation = page.get_by_role("navigation", name="Ontology Foundry workspace")
        await navigation.get_by_role("link", name="Discover", exact=True).click()
        await page.wait_for_url(lambda url: urlsplit(url).path == "/ontology")
        started, release, continued = (asyncio.Event() for _ in range(3))

        async def gate(route):
            started.set()
            await release.wait()
            await route.continue_()
            continued.set()

        layers = base + "/api/v1/etl/layers"
        await page.route(layers, gate)
        await navigation.get_by_role("link", name="Explorer", exact=True).click()
        await asyncio.wait_for(started.wait(), 20)
        try:
            await expect(page.get_by_text("Loading schema resources…", exact=True)).to_be_visible()
            await expect(page.get_by_role("complementary", name="Explorer catalog")).to_be_hidden(timeout=1000)
        finally:
            release.set()
        await asyncio.wait_for(continued.wait(), 20)
        await page.unroute(layers, gate)
        await page.goto(entry)
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

        await page.get_by_role("button", name="New exploration", exact=False).click()
        await expect(catalog.get_by_role("textbox", name="Search catalog")).to_be_focused()
        await expect(property_search).to_have_value("")
        await catalog.get_by_role("textbox", name="Search catalog").fill(table)
        await catalog.get_by_role("button", name=table, exact=True).click()
        await expect(catalog).to_be_hidden()
        assert parse_qs(urlsplit(page.url).query)["table"] == [table]
        await expect(results).to_be_visible()
        await page.set_viewport_size({"width": 390, "height": 844})
        await toggle.locator(".ui-icon-search").click()
        await expect(catalog_search).to_be_focused()
        await catalog.get_by_role("button", name="Refresh catalog").focus()
        await catalog.locator(".ui-input-prefix .ui-icon-search").click()
        await expect(catalog_search).to_be_focused()
        await toggle.locator(".ui-icon-search").click()
        await expect(catalog).to_be_hidden()
        await browser.close()
    print(json.dumps({"ok": True, "stableEntry": True, "catalogOutsideOrEscapeCloses": True,
                      "propertyOptionSelects": True, "newExplorationOpensChooser": True,
                      "catalogIconFocusDesktopAndMobile": True}))


if __name__ == "__main__":
    asyncio.run(main())
