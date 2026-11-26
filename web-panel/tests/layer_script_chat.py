"""Exercise the Layer Script dialog and Scout's URL-only landing page on a loopback frontend."""

import argparse
from ipaddress import ip_address
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright


ORIGINAL = "def transform(row):\n    return row"
FIRST = "def transform(row):\n    return row['name']"
REVISED = "def transform(row):\n    return row['name'].strip()"
MANUAL = ORIGINAL + "\n# manual edit"
HANDLER = "/api/v1/etl/handlers/ts_ods/example"
MODEL = "Helio-AI-LayerScript"
REPLY_MODEL = "Helio-AI-LayerScript-revision"
ENTRY = "/ontology/explorer?schema=ts_ods&table=example&view=stream"


def verify(base):
    if not ip_address(urlsplit(base).hostname).is_loopback:
        raise ValueError("Expected the URL of a local frontend")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(args=["--no-proxy-server", "--enable-unsafe-swiftshader"])
        page = browser.new_page(viewport={"width": 1440, "height": 950}, service_workers="block")
        page.add_init_script("""if (window.top === window) {
            const now = Math.floor(Date.now()/1000);
            const claims = {sub:'fixture', type:'access', iat:now, exp:now+600, session_exp:now+600};
            localStorage.setItem('auth_session', JSON.stringify({access_token:'test.'+btoa(JSON.stringify(claims))+'.test',
                last_activity:Date.now(), locked:false}));
        }""")
        pending = []
        reads = {
            "/api/v1/etl/handlers/chat-model": {"model": MODEL},
            "/api/v1/users/me/context": {"user": {"id": "fixture", "email": "fixture@example.test",
                                                  "is_superuser": True}, "tenants": [], "teams": []},
            "/api/v1/etl/layers": [{"key": "ts_ods", "label": "ODS", "status": "running"}],
            "/api/v1/etl/ts_ods/tables": [{"name": "example", "rowCount": 0, "size": "0", "updatedAt": ""}],
            "/api/v1/etl/ts_ods/example/stream": {"available": False, "offsets": []},
            "/api/v1/etl/ts_ods/example/script": {"available": True, "code": ORIGINAL},
            HANDLER: {"code": ""},
        }

        def api(route):
            path = urlsplit(route.request.url).path
            if route.request.method != "GET" and path.startswith(HANDLER):
                pending.append(route)
            elif path in reads:
                route.fulfill(json=reads[path])
            else:
                route.fulfill(status=404, json={"detail": "No fixture for this endpoint"})

        page.route("**/api/**", api)
        page.goto(base + ENTRY, wait_until="domcontentloaded")
        page.get_by_role("button", name="Layer script", exact=True).click()
        dialog = page.get_by_role("dialog", name="Layer script", exact=True)
        editor = dialog.get_by_role("textbox", name="Layer script code")
        assistant = dialog.get_by_role("region", name="Layer script assistant")
        prompt = assistant.get_by_role("textbox", name="Message Dot")
        dot = assistant.locator(".native-dot-view")
        apply = assistant.get_by_role("button", name="Apply to editor")
        save = dialog.get_by_role("button", name="Save draft")
        expect(editor).to_have_value(ORIGINAL)
        expect(assistant.get_by_text(MODEL, exact=True)).to_be_visible()
        expect(dot).to_have_attribute("data-activity", "NeedsInput")
        expect(dot.locator("iframe")).to_be_in_viewport()
        native = dot.frame_locator("iframe").locator("canvas")
        expect(native).to_have_attribute("data-ready", "true", timeout=75_000)

        def send(text):
            prompt.fill(text)
            with page.expect_request(lambda req: urlsplit(req.url).path == HANDLER + "/chat"):
                assistant.get_by_role("button", name="Send", exact=True).click()
            expect(dot).to_have_attribute("data-activity", "Thinking")
            expect(native).to_have_attribute("data-activity", "Thinking")
            assert len(pending) == 1
            return pending.pop()

        first = send("Extract the name.")
        request = first.request.post_data_json
        assert request["code"] == ORIGINAL and request["messages"][-1]["content"] == "Extract the name."
        first.fulfill(json={"reply": "Extracted name.", "code": FIRST, "model": REPLY_MODEL})
        expect(assistant.get_by_role("log").get_by_text(REPLY_MODEL, exact=True)).to_be_visible()
        expect(apply).to_be_enabled()
        expect(assistant.get_by_label("Original range")).to_have_text("    return row")
        expect(assistant.get_by_label("Proposed range")).to_have_text("    return row['name']")
        expect(editor).to_have_value(ORIGINAL)
        follow = send("Trim whitespace too.")
        request = follow.request.post_data_json
        assert set(request) == {"code", "messages"}
        assert request["code"] == FIRST and [m["role"] for m in request["messages"]] == ["user", "assistant", "user"]
        assert "Extracted name." in request["messages"][1]["content"]
        follow.fulfill(json={"reply": "Trimmed whitespace.", "code": REVISED, "model": REPLY_MODEL})
        expect(apply).to_be_enabled()
        editor.fill(MANUAL)
        assert apply.count() == 0 or apply.is_disabled()
        recovery = send("Apply trimming to my manual edit.")
        assert recovery.request.post_data_json["code"] == MANUAL
        recovery.fulfill(status=503, json={"detail": "Model temporarily unavailable"})
        expect(dot).to_have_attribute("data-activity", "Error")
        retry = send("Keep my edits and trim.")
        candidate = REVISED + "\n# manual edit"
        retry.fulfill(json={"reply": "Preserved the edit.", "code": candidate, "model": REPLY_MODEL})
        apply.click()
        expect(editor).to_have_value(candidate)
        assert not pending
        with page.expect_request(lambda req: req.url.endswith("/validate")):
            dialog.get_by_role("button", name="Validate syntax").click()
        expect(dot).to_have_attribute("data-activity", "Working")
        validation = pending.pop()
        assert validation.request.post_data_json == {"code": candidate}
        validation.fulfill(json={"valid": False, "errors": ["Invalid indentation"]})
        expect(dot).to_have_attribute("data-activity", "Error")
        with page.expect_request(lambda req: req.url.endswith("/validate")):
            dialog.get_by_role("button", name="Validate syntax").click()
        expect(dot).to_have_attribute("data-activity", "Working")
        validation = pending.pop()
        validation.fulfill(json={"valid": True, "errors": []})
        expect(dot).to_have_attribute("data-activity", "Ready")
        with page.expect_request(lambda req: req.method == "PUT" and req.url.endswith(HANDLER)):
            save.click()
        expect(dot).to_have_attribute("data-activity", "Working")
        write = pending.pop()
        assert write.request.post_data_json == {"code": candidate}
        write.fulfill(json={})
        expect(save).to_be_disabled()
        expect(dot).to_have_attribute("data-activity", "Ready")
        assert not pending
        page.goto(base + "/scout", wait_until="domcontentloaded")
        expect(page.get_by_placeholder("输入 HTTP/HTTPS 网址", exact=True)).to_be_visible()
        expect(page.get_by_text("网关模型", exact=False)).to_have_count(0)
        expect(page.get_by_text("站点采集", exact=True)).to_have_count(0)
        expect(page.get_by_text("网页搜索", exact=True)).to_have_count(0)
        expect(page.get_by_text(MODEL, exact=True)).to_have_count(0)
        browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base", help="URL of the local frontend, e.g. http://127.0.0.1:3000")
    verify(parser.parse_args().base.rstrip("/"))
    print("Layer Script chat, follow-up edits, Dot states, review, validation and draft save passed.")
