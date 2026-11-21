"""Download two authorized SDK variants, compile them, and use them in signed-in Chromium."""
from ipaddress import ip_address
import json
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
from urllib.parse import urlencode, urlsplit
from uuid import UUID

from playwright.sync_api import expect, sync_playwright
from playwright._impl._driver import compute_driver_executable


def main():
    stage = "load isolated browser fixture"
    try:
        fixture = json.loads(Path(os.getenv("ONTOLOGY_QA_FIXTURE", "/tmp/ontology-fixture.json")).read_text(encoding="utf-8"))
        base = fixture["base"].rstrip("/")
        target = urlsplit(base)
        if target.scheme != "http" or not ip_address(target.hostname).is_loopback or target.path or target.username or target.password:
            raise RuntimeError("SDK browser acceptance requires an isolated loopback app")
        source = fixture["sdk"]
        assert source["semantic_id"] == "qa-sdk", "Expected the dedicated SDK test type"
        type_id = str(UUID(source["id"]))
        name = source["name"]
        public = str(UUID(source["visible_record"]))
        hidden = str(UUID(source["denied_record"]))
        assert public != hidden
        route = base + "/ontology/explorer?" + urlencode({"objectType": type_id})
        compiler = os.getenv("ONTOLOGY_QA_TSC") or str(Path.cwd() / "web-panel/node_modules/typescript/bin/tsc")
        node = os.getenv("ONTOLOGY_QA_NODE") or shutil.which("node") or compute_driver_executable()[0]
        chromium = os.getenv("ONTOLOGY_QA_CHROMIUM") or shutil.which("chromium") or shutil.which("chromium-browser")
        expect.set_options(timeout=20_000)

        def login(browser, account):
            page = browser.new_page(accept_downloads=True, viewport={"width": 1280, "height": 900})
            page.set_default_timeout(20_000)
            page.goto(route, wait_until="domcontentloaded")
            page.get_by_placeholder("name@company.com").fill(account["email"])
            page.get_by_placeholder("Enter your password").fill(account["password"])
            with page.expect_response(lambda response: urlsplit(response.url).path == "/api/v1/login/token") as login_response:
                page.get_by_role("button", name="Sign in", exact=True).click()
            assert login_response.value.status == 200
            page.wait_for_url(route)
            expect(page.get_by_role("region", name="Object results", exact=True)).to_be_visible()
            expect(page.locator(".wb-type strong")).to_have_text(name)
            return page

        def download_sdk(page, directory, account):
            page.get_by_role("button", name="Open in", exact=True).click()
            page.get_by_role("menuitem", name="TypeScript SDK", exact=True).click()
            dialog = page.get_by_role("dialog", name="TypeScript SDK")
            with page.expect_download() as pending:
                dialog.get_by_role("link", name="Download TypeScript SDK", exact=True).click()
            artifact = pending.value
            assert artifact.suggested_filename == source["semantic_id"] + "-ontology-sdk.ts"
            home = directory / account
            home.mkdir()
            path = home / artifact.suggested_filename
            artifact.save_as(path)
            code = path.read_text(encoding="utf-8")
            subprocess.run([node, compiler, "--strict", "--target", "ES2020", "--module", "ES2020",
                            "--lib", "ES2020,DOM", "--outDir", str(home), str(path)],
                           check=True, capture_output=True, timeout=25)
            dialog.get_by_role("button", name="Close", exact=True).click()
            return code, path.with_suffix(".js").read_text(encoding="utf-8")

        run_sdk = """async ({code, action, record, denied, key}) => {
          const file = URL.createObjectURL(new Blob([code], {type: 'text/javascript'}));
          try {
            const {createOntologyClient, OntologySdkError} = await import(file);
            const sdk = createOntologyClient({
              baseUrl: location.origin,
              getAccessToken: () => JSON.parse(localStorage.getItem('auth_session')).access_token
            });
            const row = await sdk.getRecord(record);
            const page = await sdk.listRecords({limit: 25});
            if (action === 'read') {
              let deniedStatus;
              try { await sdk.getRecord(denied); }
              catch (error) {
                if (!(error instanceof OntologySdkError)) throw error;
                deniedStatus = error.status;
              }
              return {row: row.id, ids: page.items.map(item => item.id), keys: Object.keys(row.properties),
                      deniedStatus, canModify: typeof sdk.modifyRecord === 'function', functions: Object.keys(sdk.functions ?? {})};
            }
            const invoke = sdk.functions[key];
            const input = {recordId: record, expectedRevision: row.revision,
                requestId: 'a5f2c335-5972-477b-9bb0-b47b18ac8c66', arguments: {count: 17}};
            if (action === 'denyFunction') {
              try { await invoke(input); }
              catch (error) {
                if (!(error instanceof OntologySdkError)) throw error;
                return {status: error.status};
              }
              throw new Error('A shared SDK must not grant functions to a different user.');
            }
            const receipt = await invoke(input);
            const replay = await invoke(input);
            const updated = await sdk.getRecord(record);
            return {receiptMatches: receipt.id === replay.id, count: updated.properties.count, revision: updated.revision};
          } finally { URL.revokeObjectURL(file); }
        }"""

        with TemporaryDirectory(prefix="ontology-sdk-browser-") as raw_dir, sync_playwright() as playwright:
            directory = Path(raw_dir)
            browser = playwright.chromium.launch(executable_path=chromium, args=["--disable-dev-shm-usage", "--no-proxy-server"])
            try:
                stage = "sign in as owner and download the writable SDK"
                owner = login(browser, fixture["owner"])
                owner_source, owner_js = download_sdk(owner, directory, "owner")
                assert '"private-note"' in owner_source and '"set_count"' in owner_source

                stage = "sign in as reader and download the projected SDK"
                viewer = login(browser, fixture["reader"])
                reader_source, reader_js = download_sdk(viewer, directory, "reader")
                assert '"private-note"' not in reader_source and '"set_count"' not in reader_source
                assert "modifyRecord" not in reader_source and "deleteRecord" not in reader_source

                stage = "use both compiled SDKs for authorized and denied reads"
                reader = viewer.evaluate(run_sdk, {"code": reader_js, "action": "read", "record": public, "denied": hidden})
                assert reader["row"] == public and reader["ids"] == [public]
                assert "private-note" not in reader["keys"] and reader["deniedStatus"] == 404
                assert not reader["canModify"] and reader["functions"] == []
                owner_read = owner.evaluate(run_sdk, {"code": owner_js, "action": "read", "record": hidden, "denied": public})
                assert "private-note" in owner_read["keys"] and owner_read["ids"] and set(owner_read["ids"]) == {public, hidden}
                assert owner_read["canModify"] and owner_read["functions"] == ["set_count"]
                forbidden = viewer.evaluate(run_sdk, {"code": owner_js, "action": "denyFunction", "record": public, "key": "set_count"})
                assert forbidden["status"] == 404

                stage = "invoke the downloaded compiled function and verify replay in the isolated fixture"
                result = owner.evaluate(run_sdk, {"code": owner_js, "action": "invoke", "record": public, "key": "set_count"})
                assert result["count"] == 17 and result["receiptMatches"] and result["revision"] == 2
            finally:
                browser.close()
        print(json.dumps({"ok": True, "downloads": 2, "compiled": 2, "authorizedRead": True,
                          "hiddenFieldOmitted": True, "deniedRead": 404, "deniedFunction": 404,
                          "functionInvocationReplay": True}))
        return 0
    except Exception as failure:
        print(json.dumps({"ok": False, "failed_stage": stage, "error": type(failure).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
