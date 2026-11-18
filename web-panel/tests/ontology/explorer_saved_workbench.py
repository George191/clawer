"""Exercise saved Explorer queries, authenticated links, lists, charts and exact exports."""

import ipaddress
import json
import os
import shutil
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from uuid import uuid4

from playwright.sync_api import expect, sync_playwright


def exercise(page, base, asset, owner, reader=None):
    target = base + "/ontology/explorer?" + urlencode(asset)

    def sign_in(tab, account, url):
        tab.goto(url)
        tab.get_by_placeholder("name@company.com").fill(account["email"])
        tab.get_by_placeholder("Enter your password").fill(account["password"])
        tab.get_by_role("button", name="Sign in", exact=True).click()
        tab.wait_for_url(lambda u: urlsplit(u).path.startswith("/ontology"))
        tab.goto(url)

    sign_in(page, owner, target)
    button = lambda name: page.get_by_role("button", name=name, exact=True)
    result = page.get_by_role("region", name="Object results", exact=True)
    first = result.locator("tbody tr").first
    title = first.locator("a").first
    expect(title).to_be_visible()
    title_text = title.inner_text()
    # Identity comes from the actual browser hyperlink, including large integer keys.
    from urllib.parse import parse_qs

    key = json.loads(parse_qs(urlsplit(title.get_attribute("href")).query)["key"][0])
    first.get_by_role("checkbox").check()
    expect(button("Clear selected objects")).to_be_visible()
    button("Export").click()
    with page.expect_download() as download:
        button("JSON · selected objects").click()
    records = json.loads(Path(download.value.path()).read_text(encoding="utf8"))
    assert len(records) == 1 and all(str(records[0][k]) == v for k, v in key.items())
    suffix = uuid4().hex[:8]
    list_name = "QA objects " + suffix
    button("Lists").click()
    button("Save selected objects as list").click()
    page.get_by_label("List name").fill(list_name)
    button("Save list").click()
    expect(page.get_by_role("status").filter(has_text="Saved " + list_name)).to_be_visible()
    button("Lists").click()
    button(list_name).click()
    expect(
        page.get_by_role("region", name="Explorer tools").get_by_role("link", name=title_text)
    ).to_be_visible()
    page.keyboard.press("Escape")
    button("Explore").click()
    chart = page.get_by_role("region", name="Explore results")
    expect(chart).to_be_visible()
    chart.get_by_role("combobox").select_option(next(iter(key)))
    expect(chart.get_by_text("Value counts across", exact=False)).to_be_visible()
    explore_name = "QA exploration " + suffix
    button("Save").click()
    page.get_by_label("Exploration name").fill(explore_name)
    button("Save exploration").click()
    expect(page.get_by_role("status").filter(has_text="Saved " + explore_name)).to_be_visible()
    button("Share").click()
    button("Enable link sharing").click()
    link = page.get_by_label("Share link").input_value()
    assert urlsplit(link).netloc == urlsplit(base).netloc
    if reader:
        peer = page.context.browser.new_context()
        try:
            other = peer.new_page()
            sign_in(other, reader, link)
            expect(other.get_by_role("region", name="Explore results")).to_be_visible()
            expect(other.get_by_role("combobox", name="Property")).to_have_value(next(iter(key)))
        finally:
            peer.close()
    button("Stop sharing").click()
    expect(button("Enable link sharing")).to_be_visible()
    page.keyboard.press("Escape")
    page.get_by_role("button", name="New exploration", exact=False).click()
    expect(result).to_be_visible()
    button("Explorations").click()
    button(explore_name).click()
    expect(chart).to_be_visible()
    button("Results").click()
    search = page.get_by_role("textbox", name="Search properties to add a chart or filter")
    search.fill(next(iter(key)))
    page.get_by_role("region", name="Explorer tools").get_by_role("button").first.click()
    button("Add filter").click()
    expect(page.locator(".flow-filter-rule").first).to_be_visible()
    # Remove only the exact private items created by this test.
    for menu, item in (("Explorations", explore_name), ("Lists", list_name)):
        button(menu).click()
        page.once("dialog", lambda d: d.accept())
        button("Delete " + item).click()
        expect(button("Delete " + item)).to_have_count(0)
        page.keyboard.press("Escape")
    return {
        "ok": True,
        "selectedTitle": title_text,
        "exactKey": key,
        "shareRead": bool(reader),
        "checks": [
            "selected JSON",
            "saved list",
            "list title",
            "chart",
            "save exploration",
            "link sharing",
            "revoke link",
            "reload exploration",
            "property filter",
            "delete private items",
        ],
    }


def run():
    fixture = json.loads(Path(os.environ["ONTOLOGY_QA_FIXTURE"]).read_text(encoding="utf8"))
    base = fixture["base"].rstrip("/")
    if (
        not ipaddress.ip_address(urlsplit(base).hostname).is_loopback
        or fixture["explorer"]["schema"] != "ts_explorer_selection_qa"
    ):
        raise RuntimeError("Requires the isolated Explorer fixture")
    executable = (
        os.getenv("ONTOLOGY_QA_CHROMIUM")
        or shutil.which("chromium")
        or shutil.which("chromium-browser")
    )
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            executable_path=executable, args=["--disable-dev-shm-usage", "--no-proxy-server"]
        )
        page = browser.new_page(viewport={"width": 1536, "height": 960})
        expect.set_options(timeout=25000)
        receipt = exercise(page, base, fixture["explorer"], fixture["owner"], fixture["reader"])
        browser.close()
        print(json.dumps(receipt))


if __name__ == "__main__":
    run()
