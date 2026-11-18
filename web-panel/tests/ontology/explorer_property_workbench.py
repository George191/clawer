"""Chromium acceptance for the physical Properties editor against an isolated SQL fixture."""
import ipaddress
import json
import os
from pathlib import Path
import shutil
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright


def run():
    fixture = json.loads(Path(os.environ["ONTOLOGY_QA_FIXTURE"]).read_text(encoding="utf-8"))
    base = fixture["base"].rstrip("/")
    origin = urlsplit(base)
    if origin.scheme != "http" or not ipaddress.ip_address(origin.hostname).is_loopback:
        raise ValueError("Use the isolated loopback QA server")
    if fixture["explorer"] != {"schema": "ts_property_workbench_qa", "table": "assets"}:
        raise ValueError("Use the dedicated property workbench fixture")
    url = base + "/ontology/explorer?schema=ts_property_workbench_qa&table=assets&view=schema"
    path = "/api/v1/etl/ts_property_workbench_qa/assets/property-mapping"
    output = os.getenv("ONTOLOGY_QA_SCREENSHOTS")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=os.getenv("ONTOLOGY_QA_CHROMIUM") or shutil.which("chromium"),
                                             args=["--disable-dev-shm-usage", "--no-proxy-server"])
        expect.set_options(timeout=15000)

        def login(account):
            ctx = browser.new_context(viewport={"width": 1580, "height": 1024}, service_workers="block")
            page = ctx.new_page()
            page.goto(url, wait_until="domcontentloaded")
            page.get_by_placeholder("name@company.com").fill(account["email"])
            page.get_by_placeholder("Enter your password").fill(account["password"])
            page.get_by_role("button", name="Sign in", exact=True).click()
            page.wait_for_url(url)
            expect(page.get_by_role("table", name="Registered properties")).to_be_visible()
            return ctx, page

        ctx, page = login(fixture["owner"])
        work = page.get_by_role("region", name="Property workbench")
        reg = work.get_by_role("table", name="Registered properties")
        editor = work.get_by_role("complementary", name="Property editor")
        preview = work.get_by_role("region", name="Data and column mappings")

        def button(name, root=work):
            return root.get_by_role("button", name=name, exact=True)

        def stored():
            with page.expect_response(lambda res: urlsplit(res.url).path == path and res.request.method == "PUT") as info:
                button("Save changes").click()
            assert info.value.status == 200, info.value.text()[:500]
            expect(work.get_by_text("Property mapping saved.", exact=True)).to_be_visible()
            data = info.value.json()
            return data.get("data", data)

        second = ctx.new_page()
        second.goto(url)
        second_work = second.get_by_role("region", name="Property workbench")
        expect(second_work.get_by_role("table", name="Registered properties")).to_be_visible()
        expect(reg.get_by_role("row")).to_have_count(5)
        expect(preview.get_by_role("table")).to_contain_text("Sample A")
        expect(preview.get_by_role("table")).to_contain_text("9007199254740993")
        expect(editor.get_by_label("Primary key", exact=True)).to_be_checked()
        expect(editor.get_by_label("Primary key", exact=True)).to_be_disabled()
        button("Inspect name").click()
        editor.get_by_label("Name", exact=True).fill("Asset title")
        editor.get_by_label("Description").fill("QA description")
        editor.get_by_label("Status", exact=True).select_option("experimental")
        editor.get_by_label("Title key").check()
        button("Display", editor).click()
        editor.get_by_label("Visibility").select_option("prominent")
        editor.get_by_label("Include in summary").check()
        button("Details", editor).click()
        editor.get_by_label("New group").fill("Identification")
        button("Add", editor).click()
        button("Inspect datum").click()
        button("Interaction", editor).click()
        editor.get_by_label("Presentation").select_option("geoJson")
        reg.get_by_label("Formatter for amount").select_option("number")
        reg.get_by_label("Visibility for amount").select_option("prominent")
        saved = stored()
        props = {prop["id"]:prop for prop in saved["properties"]}
        assert (saved["titleProperty"], props["name"]["group"], props["name"]["summary"], props["datum"]["presentation"]) == (
            "name", "Identification", True, "geoJson")
        assert (props["amount"]["formatter"], props["amount"]["visibility"]) == ("number", "prominent")
        second_work.get_by_role("complementary", name="Property editor").get_by_label("Description").fill("Stale write")
        with second.expect_response(lambda res: urlsplit(res.url).path == path and res.request.method == "PUT") as stale:
            second_work.get_by_role("button", name="Save changes").click()
        assert stale.value.status == 409
        second_work.get_by_role("button", name="Discard and reload").click()
        expect(second_work.get_by_role("button", name="Inspect Asset title")).to_be_visible()
        second.close()
        button("Search properties and columns").click()
        work.get_by_label("Search properties or source columns").fill("Asset title")
        expect(reg.get_by_role("row")).to_have_count(2)
        button("Search properties and columns").click()
        button("Filter properties and columns").click()
        work.get_by_label("Filter properties", exact=True).select_option("experimental")
        expect(reg.get_by_role("row")).to_have_count(2)
        button("Filter properties and columns").click()
        button("Column mapping").click()
        work.get_by_label("Mapping for amount").select_option("")
        preview.get_by_label("Show mapped columns").uncheck()
        expect(preview.get_by_role("columnheader")).to_have_count(2)
        expect(preview.get_by_role("columnheader", name="amount")).to_be_visible()
        button("Automap all").click()
        expect(work.get_by_text("4 of 4 columns mapped")).to_be_visible()
        expect(preview.get_by_text("No unmapped source columns.")).to_be_visible()
        preview.get_by_label("Show mapped columns").check()
        button("Create property").click()
        editor.get_by_label("Name", exact=True).fill("Unmapped note")
        button("Advanced", editor).click()
        editor.get_by_label("Property ID").fill("qa_note")
        saved = stored()
        assert any(p["id"]=="qa_note" and p["sourceColumn"] is None for p in saved["properties"])
        page.reload()
        button("Inspect Unmapped note").click()
        button("Delete property Unmapped note", editor).click()
        button("Remove property", editor).click()
        assert all(p["id"]!="qa_note" for p in stored()["properties"])
        button("Preview objects").click()
        expect(preview.get_by_role("table", name="Mapped objects preview")).to_contain_text("Asset title")
        expect(preview.get_by_role("table")).to_contain_text("Sample A")
        button("Preview table").click()
        button("Inspect Asset title").click()
        expect(editor.get_by_label("Title key")).to_be_checked()
        if output:
            page.screenshot(path=Path(output) / "physical-property-workbench.png", animations="disabled")
        reader_ctx, reader = login(fixture["reader"])
        readonly = reader.get_by_role("region", name="Property workbench")
        expect(readonly.get_by_role("button", name="Create property")).to_have_count(0)
        readonly.get_by_role("button", name="Inspect Asset title").click()
        expect(readonly.get_by_label("Name", exact=True)).to_be_disabled()
        expect(readonly.get_by_label("Title key")).to_be_checked()
        expect(readonly.get_by_role("table",name="Source table preview")).to_contain_text("Sample A")
        reader_ctx.close()
        ctx.close()
        browser.close()
    result = {"realHttp":True,"crud":True,"allInspectorTabs":True,"casConflict":409,"unmapAndAutomap":True,
        "mappedColumnsToggle":True,"objectAndTablePreview":True,"bigintIdentity":True,"readOnlyReader":True}
    if output:
        (Path(output) / "physical-property-workbench.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))


if __name__ == "__main__":
    run()
