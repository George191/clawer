"""Exercise published-record view transitions against the isolated workflow fixture."""
import asyncio
from ipaddress import ip_address
import json
import os
from pathlib import Path
import shutil
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import UUID, uuid4

from playwright.async_api import Error, expect, async_playwright


async def main():
    fixture = json.loads(Path(os.environ["ONTOLOGY_QA_FIXTURE"]).read_text(encoding="utf-8"))
    base = fixture["base"].rstrip("/")
    origin = urlsplit(base)
    if (origin.scheme != "http" or not ip_address(origin.hostname).is_loopback
            or origin.username or origin.password or origin.path or origin.query or origin.fragment):
        raise RuntimeError("Expected an isolated loopback workflow")
    source = fixture["source"]
    if source["name"] != "QA Work Order":
        raise RuntimeError("Expected the seeded QA Work Order")
    definition = str(UUID(source["id"]))
    active_row = source["records"][0]
    seed_id = uuid4().hex[:12]
    deleted_title = "Deleted QA record " + seed_id
    resource = f"/api/ontology/v1/object-types/{definition}"
    target = base + "/ontology?" + urlencode({"catalog": "published", "objectType": definition})
    chromium = os.getenv("ONTOLOGY_QA_CHROMIUM") or shutil.which("chromium")
    screenshots = os.getenv("ONTOLOGY_QA_SCREENSHOTS")
    expect.set_options(timeout=20000)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(executable_path=chromium, args=["--no-proxy-server", "--disable-dev-shm-usage"])
        context = await browser.new_context(viewport={"width": 1440, "height": 960})
        page = await context.new_page()
        await page.goto(target)
        await page.get_by_placeholder("name@company.com").fill(fixture["owner"]["email"])
        await page.get_by_placeholder("Enter your password").fill(fixture["owner"]["password"])
        await page.get_by_role("button", name="Sign in", exact=True).click()
        await page.wait_for_url(target)
        results = page.get_by_role("region", name="Object results table")
        active = page.get_by_role("button", name="Show active objects")
        deleted = page.get_by_role("button", name="Show deleted objects")
        preview = page.get_by_role("complementary", name="Selection Preview")
        await expect(results.get_by_role("button", name=active_row["title"], exact=True)).to_be_visible()

        status = await page.evaluate("""async ({resource, values}) => {
            const token = JSON.parse(localStorage.getItem('auth_session')).access_token;
            const headers = {Authorization: 'Bearer ' + token, 'Content-Type': 'application/json'};
            const created = await fetch(resource + '/actions/create', {method: 'POST', headers, body: JSON.stringify({values})});
            if (!created.ok) return created.status;
            const payload = await created.json(), row = payload.data ?? payload;
            const response = await fetch(resource + '/actions/delete', {method: 'POST', headers,
              body: JSON.stringify({recordId: row.id, expectedRevision: row.revision})});
            return response.status;
        }""", {"resource": resource, "values": {**active_row["properties"], "id": "views-" + seed_id, "name": deleted_title}})
        assert status == 200, status
        await page.reload()
        await expect(results.get_by_role("button", name=active_row["title"], exact=True)).to_be_visible()
        metadata_requests = []
        page.on("request", lambda req: metadata_requests.append(req.url)
                if urlsplit(req.url).path == resource else None)
        results_node = await results.element_handle()
        tools_node = await page.get_by_role("navigation", name="Object type tools").element_handle()
        title_node = await page.get_by_role("heading", name=source["name"], exact=True).element_handle()
        await results.get_by_role("button", name=active_row["title"], exact=True).click()
        await expect(preview).to_be_visible()
        if screenshots:
            await page.screenshot(path=Path(screenshots) / "published-active-selection.png", animations="disabled")

        arrived = asyncio.Event()
        release = asyncio.Event()
        stale_finished = asyncio.Event()
        fail = False

        async def intercept(route):
            query = parse_qs(urlsplit(route.request.url).query)
            if query.get("deleted") != ["true"]:
                return await route.continue_()
            if fail:
                return await route.fulfill(status=503, json={"detail": "Temporary records failure"})
            response = await route.fetch()
            arrived.set()
            await release.wait()
            try:
                await route.fulfill(response=response)
            except Error:
                # Chromium can cancel the original request after the new view starts.
                pass
            finally:
                await response.dispose()
                stale_finished.set()

        await page.route(base + resource + "/records?*", intercept)
        await deleted.click()
        await asyncio.wait_for(arrived.wait(), 20)
        await expect(deleted).to_have_attribute("aria-pressed", "true")
        await expect(results.get_by_text("Loading deleted objects…", exact=True)).to_be_visible()
        await expect(preview).to_have_count(0)
        await expect(results.get_by_role("button", name=active_row["title"], exact=True)).to_have_count(0)
        await active.click()
        await expect(active).to_have_attribute("aria-pressed", "true")
        await expect(results.get_by_role("button", name=active_row["title"], exact=True)).to_be_visible()
        release.set()
        await asyncio.wait_for(stale_finished.wait(), 20)
        await expect(results.get_by_role("button", name=active_row["title"], exact=True)).to_be_visible()
        await expect(results.get_by_role("button", name=deleted_title, exact=True)).to_have_count(0)
        fail = True
        await deleted.click()
        await expect(results.get_by_role("alert")).to_contain_text("Temporary records failure")
        await expect(active).to_be_enabled()
        await expect(deleted).to_have_attribute("aria-pressed", "true")
        fail = False
        await results.get_by_role("button", name="Retry").click()
        await expect(results.get_by_role("button", name=deleted_title, exact=True)).to_be_visible()
        await expect(results.get_by_role("button", name=active_row["title"], exact=True)).to_have_count(0)
        await results.get_by_role("button", name=deleted_title, exact=True).click()
        await expect(preview.get_by_role("heading", name=deleted_title, exact=True)).to_be_visible()
        await expect(preview.get_by_text("Actions", exact=True)).to_have_count(0)
        if screenshots:
            await page.screenshot(path=Path(screenshots) / "published-deleted-selection.png", animations="disabled")
        assert len(metadata_requests) == 0
        for node in (results_node, tools_node, title_node):
            assert await node.evaluate("el => el.isConnected")
        await active.click()
        await expect(preview).to_have_count(0)
        await expect(results.get_by_role("button", name=active_row["title"], exact=True)).to_be_visible()
        await page.set_viewport_size({"width": 390, "height": 844})
        assert await tools_node.evaluate("el => el.scrollWidth <= el.clientWidth")
        assert (await results.bounding_box())["height"] >= 180
        if screenshots:
            await page.screenshot(path=Path(screenshots) / "published-active-mobile.png", animations="disabled")
        await browser.close()
    print(json.dumps({"ok": True, "stableShell": True, "cancelOldRead": True,
                      "scopeFailureAndRetry": True, "noStaleSelection": True, "metadataRefetches": 0,
                      "mobileToolsAndResults": True}))


if __name__ == "__main__":
    asyncio.run(main())
