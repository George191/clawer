"""Verify Explorer workbench dialog sizing and dismissal in an isolated browser."""

import asyncio
from ipaddress import ip_address
import json
import os
from pathlib import Path
import shutil
from urllib.parse import urlencode, urlsplit
from uuid import UUID

from playwright.async_api import async_playwright, expect


async def close_functions_dialog(page, dialog):
    # Capture the first closing render, even if the entire fade fits between two Playwright calls.
    state = await dialog.evaluate_handle("""dialog => {
        const root = dialog.closest('.ui-modal-root');
        const state = {root};
        const observer = new MutationObserver(() => {
            observer.disconnect();
            state.fading = root.getAnimations().some(animation => animation.transitionProperty === 'opacity');
            state.inert = root.inert;
            state.title = dialog.querySelector('.ui-modal-title')?.textContent;
            state.contentVisible = !!dialog.querySelector('[aria-label="Business function runner"]')?.getClientRects().length;
        });
        observer.observe(root, {attributes: true, attributeFilter: ['aria-hidden']});
        return state;
    }""")
    await dialog.get_by_role("button", name="Close", exact=True).click()
    await page.wait_for_function("s => !s.root.getClientRects().length", arg=state)
    snapshot = await state.evaluate("({root, ...snapshot}) => snapshot")
    assert snapshot == {"fading": True, "inert": True, "title": "Functions", "contentVisible": True}, snapshot
    await expect(page.get_by_role("button", name="Open in", exact=True)).to_be_focused()


async def main():
    fixture = json.loads(Path(os.environ["ONTOLOGY_QA_FIXTURE"]).read_text(encoding="utf-8"))
    base = fixture["base"].rstrip("/")
    origin = urlsplit(base)
    if (origin.scheme != "http" or not ip_address(origin.hostname).is_loopback
            or origin.username or origin.password or origin.path or origin.query or origin.fragment):
        raise RuntimeError("Expected an isolated loopback fixture")
    definition_id = str(UUID(fixture["source"]["id"]))
    page_url = base + "/ontology/explorer?" + urlencode({"objectType": definition_id})
    owner = fixture["owner"]
    function_path = base + f"/api/ontology/v1/object-types/{definition_id}/functions"
    expect.set_options(timeout=15000)

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            executable_path=os.getenv("ONTOLOGY_QA_CHROMIUM") or shutil.which("chromium"),
            args=["--no-proxy-server", "--disable-dev-shm-usage"],
        )
        for viewport in [{"width": 1440, "height": 900}, {"width": 390, "height": 670}]:
            context = await browser.new_context(viewport=viewport, service_workers="block", reduced_motion="no-preference")
            page = await context.new_page()
            await page.goto(page_url)
            await page.get_by_placeholder("name@company.com").fill(owner["email"])
            await page.get_by_placeholder("Enter your password").fill(owner["password"])
            await page.get_by_role("button", name="Sign in", exact=True).click()
            await expect(page.get_by_placeholder("Enter your password")).to_have_count(0)
            await expect(page.get_by_role("region", name="Object results")).to_be_visible()

            change = asyncio.Event()
            release = asyncio.Event()
            reads = 0

            async def catalog(route):
                nonlocal reads
                reads += 1
                if reads == 1:
                    await route.fulfill(json={"revision": 1, "items": []})
                elif reads == 2:
                    change.set()
                    await release.wait()
                    await route.fulfill(json={"revision": 2, "items": [{
                        "key": "layout_check", "name": "Synthetic long argument list",
                        "parameters": [{"key": f"property_{key}", "type": "string", "nullable": False}
                                       for key in range(40)],
                    }]})
                else:
                    await route.fulfill(status=503, json={"detail": "Synthetic function catalog error"})

            await page.route(function_path, catalog)
            await page.get_by_role("button", name="Explore", exact=True).click()
            chart = page.get_by_role("region", name="Explore results")
            await expect(chart).to_be_visible()
            await page.get_by_role("button", name="Open in", exact=True).click()
            await page.get_by_role("menuitem", name="Functions", exact=True).click()
            dialog = page.get_by_role("dialog", name="Functions", exact=True)
            close = dialog.get_by_role("button", name="Close", exact=True)
            shell = dialog.locator(".ui-modal-content")
            await expect(dialog.get_by_text("No published functions are available to this account.")).to_be_visible()
            await expect(close).to_be_in_viewport()
            before = await shell.bounding_box()
            refresh = dialog.get_by_role("button", name="Refresh", exact=True)
            await refresh.click()
            await asyncio.wait_for(change.wait(), 10)

            async def check_position():
                assert before
                after = await shell.bounding_box()
                assert after
                for dimension in ("x", "y", "width", "height"):
                    assert abs(after[dimension] - before[dimension]) < 1.1, (viewport, before, after)
                await expect(close).to_be_in_viewport()

            await check_position()
            release.set()
            await expect(dialog.get_by_role("heading", name="Synthetic long argument list")).to_be_visible()
            await check_position()
            await dialog.get_by_label("Argument property_39", exact=True).scroll_into_view_if_needed()
            await check_position()
            await refresh.scroll_into_view_if_needed()
            await refresh.click()
            await expect(page.locator(".explorer-toast").filter(has_text="Synthetic function catalog error")).to_be_visible()
            await check_position()
            await close_functions_dialog(page, dialog)
            await expect(chart).to_be_visible()
            await context.close()
        await browser.close()
    print("Functions dialog keeps its size and content through dismissal and restores focus on desktop and mobile.")


if __name__ == "__main__":
    asyncio.run(main())
