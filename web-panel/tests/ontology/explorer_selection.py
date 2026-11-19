"""Verify the physical Explorer's non-modal selection workflow in an isolated Chromium session."""
import ipaddress
import json
import os
from pathlib import Path
import shutil
from urllib.parse import parse_qs, urlencode, urlsplit

from playwright.sync_api import expect, sync_playwright


SCHEMA = "ts_explorer_selection_qa"
TABLE = "assets"
START = 9_007_199_254_740_993


def run():
    path = Path(os.getenv("ONTOLOGY_QA_FIXTURE", "/tmp/ontology-fixture.json"))
    fixture = json.loads(path.read_text(encoding="utf-8"))
    base = fixture["base"].rstrip("/")
    origin = urlsplit(base)
    if (origin.scheme != "http" or not ipaddress.ip_address(origin.hostname).is_loopback
            or origin.username or origin.password or origin.path or origin.query or origin.fragment
            or fixture.get("explorer") != {"schema": SCHEMA, "table": TABLE}):
        raise RuntimeError("Explorer acceptance requires the dedicated loopback fixture")
    target = base + "/ontology/explorer?" + urlencode({"schema": SCHEMA, "table": TABLE})
    endpoint = f"/api/v1/etl/{SCHEMA}/{TABLE}/rows"
    screenshots = os.getenv("ONTOLOGY_QA_SCREENSHOTS")
    executable = os.getenv("ONTOLOGY_QA_CHROMIUM") or shutil.which("chromium") or shutil.which("chromium-browser")
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(executable_path=executable, args=["--disable-dev-shm-usage", "--no-proxy-server"])
    expect.set_options(timeout=25_000)
    try:
        context = browser.new_context(viewport={"width": 1536, "height": 980}, service_workers="block")

        def visit(page):
            page.goto(target, wait_until="domcontentloaded")
            expect(page.get_by_role("region", name="Object results", exact=True)).to_be_visible()

        def row(page, index):
            return page.get_by_role("row").filter(has=page.get_by_role("checkbox", name=f"Select Preview row {index:02}"))

        def configure_mapping(page, title_property):
            status = page.evaluate("""async ({schema, table, titleProperty}) => {
                const token = JSON.parse(localStorage.getItem('auth_session')).access_token;
                const headers = {Authorization: 'Bearer ' + token, 'Content-Type': 'application/json'};
                const path = '/api/v1/etl/' + schema + '/' + table + '/property-mapping';
                const current = await fetch(path, {headers});
                if (!current.ok) return {read: current.status};
                const json = await current.json(), mapping = json.data ?? json;
                const {version, sourceDefinitionId, description, icon, color} = mapping;
                const properties = mapping.properties.map(({sqlType, primaryKeyPosition, ...property}) => ({
                    ...property, summary: ['origin','destination','carrier','status'].includes(property.sourceColumn),
                    group: property.sourceColumn === 'probe' ? 'Timing details' : null,
                }));
                const body = {version, sourceDefinitionId, description, properties, titleProperty,
                    displayName: 'Service trip', icon, color, groups: ['Timing details']};
                const saved = await fetch(path, {method: 'PUT', headers, body: JSON.stringify(body)});
                return {write: saved.status};
            }""", {"schema": SCHEMA, "table": TABLE, "titleProperty": title_property})
            assert status == {"write": 200}, status

        owner = fixture["owner"]
        page = context.new_page()
        page.set_default_timeout(25_000)
        requests = []
        page.on("response", lambda r: requests.append((r.request.method, urlsplit(r.url).path, r.status))
                if urlsplit(r.url).path.startswith("/api/") else None)
        page.on("pageerror", lambda e: requests.append(("pageerror", str(e)[:200])))
        page.goto(target, wait_until="domcontentloaded")
        page.get_by_placeholder("name@company.com").fill(owner["email"])
        page.get_by_placeholder("Enter your password").fill(owner["password"])
        with page.expect_response(lambda response: urlsplit(response.url).path == "/api/v1/login/token") as auth:
            page.get_by_role("button", name="Sign in", exact=True).click()
        assert auth.value.status == 200
        page.wait_for_url(target)
        configure_mapping(page, "display_title")
        visit(page)
        try:
            expect(row(page, 1)).to_be_visible()
        except AssertionError:
            print(json.dumps({"api": requests[-20:], "workspace": page.locator("main").last.inner_text()[:1800]}), flush=True)
            raise
        panel = page.get_by_role("complementary", name="Selection Preview")
        expect(panel).to_have_count(0)
        header_cells = page.get_by_role("region", name="Object results").locator("thead tr:first-child > th")
        expect(header_cells.nth(1)).to_have_text("TITLE")
        first_title = row(page, 1).get_by_role("link", name="Preview row 01")
        assert first_title.get_attribute("target") == "_blank"
        full_route = urlsplit(first_title.get_attribute("href"))
        identity = json.loads(parse_qs(full_route.query)["key"][0])
        assert full_route.path == "/ontology/explorer/record" and identity == {"id": str(START)}

        # Click a non-title cell. The table remains actionable while the pane is open.
        row(page, 1).get_by_text(str(START), exact=True).click()
        expect(panel).to_be_visible()
        expect(panel.get_by_role("heading", name="Preview row 01")).to_be_visible()
        header_glyph = page.locator(".wb-type .workspace-glyph")
        expect(header_glyph).to_be_visible()

        def glyph_style(locator):
            return locator.evaluate("""el => ({
                svg: el.innerHTML,
                color: getComputedStyle(el).color,
                background: getComputedStyle(el.parentElement).backgroundColor,
            })""")

        glyph = glyph_style(header_glyph)
        assert glyph["color"] == "rgb(255, 255, 255)"
        assert glyph_style(page.locator(".wb-current .workspace-glyph")) == glyph
        assert glyph_style(row(page, 1).locator(".workspace-glyph")) == glyph
        assert glyph_style(panel.locator(".workspace-glyph")) == glyph
        assert panel.evaluate("""el => {
            const style = getComputedStyle(el);
            const root = getComputedStyle(document.documentElement);
            return style.colorScheme === document.documentElement.dataset.theme &&
                style.getPropertyValue('--ontology-bg').trim() === root.getPropertyValue('--ui-bg').trim();
        }""")
        expect(panel.get_by_role("button", name="Overview")).to_have_attribute("aria-pressed", "true")
        summary = panel.locator("dl[aria-label='Summary properties']")
        expect(summary.locator("dt")).to_have_count(4)
        expect(summary).to_contain_text("North Terminal")
        expect(panel.get_by_role("region", name="Key Service trip Information")).to_contain_text("Legacy record 01")
        panel.get_by_role("button", name="Timing details").click()
        expect(panel.get_by_role("region", name="Timing details")).to_contain_text("No value")
        panel.get_by_role("button", name="Overview").click()
        expect(page.get_by_role("dialog", name="Selection Preview")).to_have_count(0)
        expect(page.locator(".ui-drawer-mask:visible,.ui-modal-mask:visible")).to_have_count(0)
        assert panel.bounding_box()["x"] > row(page, 1).bounding_box()["x"]
        assert panel.get_by_role("link", name="Open full view").get_attribute("href") == first_title.get_attribute("href")
        with page.expect_popup() as full_opened:
            panel.get_by_role("link", name="Open full view").click()
        full = full_opened.value
        expect(full.get_by_role("heading", name="Preview row 01")).to_be_visible()
        assert glyph_style(full.locator(".explorer-full-record > header .workspace-glyph")) == glyph
        expect(full.get_by_role("region", name="Key Service trip Information")).to_contain_text("Legacy record 01")
        if screenshots:
            full.screenshot(path=Path(screenshots) / "selection-full-overview.png", animations="disabled")
        full.close()
        with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/assets/preview-rows")):
            panel.get_by_role("button", name="Refresh selected records").click()
        expect(panel.get_by_role("heading", name="Preview row 01")).to_be_visible()
        if screenshots:
            page.screenshot(path=Path(screenshots) / "selection-overview.png", animations="disabled")
        panel.get_by_role("button", name="Properties").click()
        properties = panel.get_by_role("region", name="Object properties")
        expect(properties).to_contain_text(str(START))
        if screenshots:
            page.screenshot(path=Path(screenshots) / "selection-desktop.png", animations="disabled")
        properties.get_by_role("textbox", name="Search properties").fill("probe")
        expect(properties.locator("dt")).to_have_count(1)
        expect(properties).to_contain_text("No value")
        properties.get_by_role("textbox", name="Search properties").fill("")
        properties.get_by_label("Property values", exact=True).select_option("empty")
        expect(properties.locator("dt")).to_have_count(1)
        properties.get_by_label("Property type", exact=True).select_option("integer")
        expect(properties.get_by_role("status")).to_have_text("No properties match these filters.")
        properties.get_by_label("Property values", exact=True).select_option("")
        expect(properties.locator("dt")).to_have_count(1)
        panel.get_by_role("button", name="JSON preview").click()
        expect(panel.get_by_role("region", name="Row JSON")).to_contain_text("fixture")

        # Add another row by its native checkbox, compare the pair, and hide equal values.
        row(page, 2).get_by_role("checkbox").check()
        expect(page.get_by_role("button", name="Clear selected objects")).to_contain_text("2 Selected")
        group = panel.get_by_role("group", name="Selected objects")
        expect(group.get_by_role("button")).to_have_count(2)
        expect(panel.get_by_role("button", name="Actions")).to_have_count(0)
        panel.get_by_role("button", name="Compare objects").click()
        compare = panel.get_by_role("region", name="Object comparison")
        expect(compare.get_by_role("columnheader", name="Preview row 01")).to_be_visible()
        expect(compare.get_by_role("columnheader", name="Preview row 02")).to_be_visible()
        expect(compare.get_by_role("rowheader", name="probe", exact=False)).to_contain_text("Different")
        compare.get_by_role("checkbox", name="Differences only").check()
        expect(compare.get_by_role("rowheader", name="datum", exact=False)).to_have_count(0)
        if screenshots:
            page.screenshot(path=Path(screenshots) / "selection-compare.png", animations="disabled")
        panel.get_by_role("button", name="Collapse selection preview").click()
        expect(panel).to_have_count(0)
        restore = page.get_by_role("button", name="Show selection preview")
        expect(restore).to_be_focused()
        restore.press("Enter")
        expect(compare).to_be_visible()
        panel.get_by_role("button", name="Clear selection").click()
        expect(panel).to_have_count(0)

        row(page, 1).press("Enter")
        expect(panel.get_by_role("heading", name="Selection Preview")).to_be_focused()
        row(page, 1).focus()
        expect(row(page, 1)).to_be_focused()
        page.keyboard.press("Shift+ArrowDown")
        expect(row(page, 2)).to_be_focused()
        expect(page.get_by_role("button", name="Clear selected objects")).to_contain_text("2 Selected")
        panel.get_by_role("button", name="Clear selection").click()
        page.get_by_label("Rows per page").select_option("50")
        expect(row(page, 25)).to_be_visible()
        page.get_by_role("checkbox", name="Select all rows on this page").check()
        expect(page.get_by_role("button", name="Clear selected objects")).to_contain_text("25 Selected")
        expect(group.get_by_role("button")).to_have_count(20)
        expect(panel).to_contain_text("Showing the first 20 of 25")
        group.get_by_role("button", name="Preview row 20").click()
        expect(panel.get_by_role("heading", name="Preview row 20")).to_be_visible()
        panel.get_by_role("button", name="Clear selection").click()

        # Two browser tabs keep different immutable copies. A stale form must stay open and
        # must not silently switch its expected version or its bigint primary key.
        other = context.new_page()
        visit(other)
        other_panel = other.get_by_role("complementary", name="Selection Preview")
        for candidate, preview in ((page, panel), (other, other_panel)):
            row(candidate, 1).press("Enter")
            preview.get_by_role("button", name="Actions").click()
            candidate.get_by_role("menuitem", name="Edit row").click()
            expect(preview.get_by_role("textbox", name="Edit id")).to_have_value(str(START))
            expect(row(candidate, 2).get_by_role("checkbox")).to_be_disabled()

        def edit(candidate, preview, value):
            preview.get_by_role("textbox", name="Edit name").fill(value)
            with candidate.expect_response(lambda r: r.request.method == "PATCH" and urlsplit(r.url).path == endpoint) as sent:
                preview.get_by_role("button", name="Save row").click()
            body = sent.value.request.post_data_json
            assert body["key"] == {"id": str(START)}
            return sent.value.status, body["expectedVersion"]

        status, initial_version = edit(page, panel, "Preview updated")
        assert status == 200
        stale_status, stale_version = edit(other, other_panel, "Preview stale")
        assert stale_status == 409 and initial_version == stale_version
        expect(other_panel.get_by_role("alert")).to_contain_text("select the row again")
        expect(other_panel.get_by_role("button", name="Save row")).to_be_disabled()
        other.close()

        # Navigate with the newly refreshed snapshot and keep the mobile panel in the page.
        page.set_viewport_size({"width": 400, "height": 850})
        visit(page)
        row(page, 2).press("Enter")
        expect(panel.get_by_role("heading", name="Preview row 02")).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert panel.bounding_box()["width"] <= 400
        if screenshots:
            page.screenshot(path=Path(screenshots) / "selection-mobile.png", animations="disabled")
        panel.get_by_role("button", name="Actions").click()
        page.get_by_role("menuitem", name="Delete row").click()
        expect(panel.get_by_role("region", name="Confirm row deletion")).to_be_visible()
        panel.get_by_role("button", name="Cancel deletion").click()
        panel.get_by_role("button", name="Actions").click()
        page.get_by_role("menuitem", name="Delete row").click()
        with page.expect_response(lambda r: r.request.method == "DELETE" and urlsplit(r.url).path == endpoint) as deleted:
            panel.get_by_role("button", name="Confirm delete").click()
        assert deleted.value.status == 200 and deleted.value.request.post_data_json["key"] == {"id": str(START + 1)}
        expect(row(page, 2)).to_have_count(0)
        configure_mapping(page, None)
        visit(page)
        expect(page.get_by_role("link", name=f"Primary key: id={START}", exact=True)).to_be_visible()
        expect(page.get_by_role("link", name="Preview updated", exact=True)).to_have_count(0)
        print(json.dumps({"ok": True, "nonmodal": True, "keyboardRange": True, "selected": 25, "cards": 20,
                          "compare": True, "propertyFilters": True, "collapseAndRestore": True,
                          "optimisticConflict": 409, "exactBigintIdentity": True,
                          "deleteConfirmation": True, "mobileWidth": 400, "configuredBusinessTitle": True,
                          "fullViewOverview": True, "refreshRetainsSelection": True, "explicitPrimaryKeyFallback": True,
                          "inheritsApplicationTheme": True}), flush=True)
        return 0
    finally:
        browser.close()
        playwright.stop()


if __name__ == "__main__":
    raise SystemExit(run())
