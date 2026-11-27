"""Management UI contracts, with no external credentials or model requests."""
import argparse
from ipaddress import ip_address
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

parser = argparse.ArgumentParser()
parser.add_argument("base")
args = parser.parse_args()
assert ip_address(urlsplit(args.base).hostname).is_loopback
with sync_playwright() as p:
    browser = p.chromium.launch(channel="msedge", args=["--no-proxy-server"])
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.add_init_script("const n=Math.floor(Date.now()/1000);localStorage.setItem('auth_session',JSON.stringify({access_token:'test.'+btoa(JSON.stringify({sub:'fixture',type:'access',iat:n,exp:n+600,session_exp:n+600})).replace(/=+$/,'')+'.test',last_activity:Date.now(),locked:false}));")
    catalog = {"connections": [], "agents": [], "harnesses": {"collection": {"name": "Collection", "tools": ["capture", "template", "adapter", "validate"]}}}
    requests = []
    errors = []
    page.on("pageerror", lambda err: errors.append(str(err)))

    def route(r):
        path = urlsplit(r.request.url).path
        if "/agents/" in path:
            method = r.request.method
            payload = r.request.post_data_json if method in {"POST", "PUT"} else None
            requests.append((method, path, payload))
            if path.endswith("/test"):
                catalog["agents"][0]["verified"] = True
                return r.fulfill(json={"ok": True, "latency_ms": 24})
            if method in {"POST", "PUT"}:
                connection = "/connections" in path
                target = catalog["connections" if connection else "agents"]
                ident = "connection-1" if connection else "agent-1"
                old = next((row for row in target if row["id"] == ident), None)
                if old:
                    target.remove(old)
                fields = {key: value for key, value in payload.items() if key not in {"api_key", "expected_revision"}}
                target.append({**fields, "id": ident, "revision": (old or {}).get("revision", 0) + 1, **({} if connection else {"verified": (old or {}).get("verified", False), "system_key": None, "can_edit": True})})
                return r.fulfill(json={"id": ident})
            return r.fulfill(json=catalog)
        profile = {"user": {"id": "fixture", "full_name": "Alex", "email": "alex@example.test", "is_superuser": False}, "tenants": [], "teams": []}
        r.fulfill(json=profile if "/users/me/context" in path else {"items": []})

    page.route("**/api/**", route)
    page.goto(args.base + "/agents")
    page.get_by_role("button", name="Model connections").click()
    page.get_by_role("button", name="New connection", exact=True).click()
    page.get_by_label("Connection name", exact=True).fill("My connection")
    page.get_by_label("API key", exact=True).fill("fixture-only-key")
    page.get_by_role("button", name="Save", exact=True).click()
    expect(page.get_by_text("Revision 1", exact=True)).to_be_visible()
    expect(page.get_by_label("API key", exact=True)).to_have_value("")
    page.get_by_role("button", name="Close drawer", exact=True).click()
    page.get_by_role("button", name="Agents 0", exact=True).click()
    page.get_by_role("button", name="New agent", exact=True).click()
    page.get_by_label("Agent name", exact=True).fill("Article collector")
    page.get_by_label("Model ID", exact=True).fill("model-alpha")
    page.get_by_label("Agent instructions", exact=True).fill("Extract title and source URL.")
    page.get_by_role("button", name="Save", exact=True).click()
    expect(page.get_by_text("Revision 1", exact=True)).to_be_visible()
    page.get_by_role("button", name="Test connection", exact=True).click()
    expect(page.get_by_role("status").filter(has_text="Saved model configuration verified.")).to_be_visible()
    page.get_by_role("switch", name="Enable agent").click()
    page.get_by_role("button", name="Save", exact=True).click()
    expect(page.get_by_text("Revision 2", exact=True)).to_be_visible()
    assert catalog["agents"][0]["connection_id"] == "connection-1"
    assert catalog["agents"][0]["model"] == "model-alpha"
    assert catalog["agents"][0]["enabled"] is True
    assert not any("api_key" in row for row in catalog["connections"])
    page.get_by_role("button", name="Close drawer", exact=True).click()
    page.get_by_label("Filter agent status", exact=True).select_option("unverified")
    expect(page.get_by_role("button", name="Article collector", exact=True)).to_have_count(0)
    page.get_by_label("Filter agent status", exact=True).select_option("enabled")
    page.get_by_role("button", name="Article collector", exact=True).click()
    page.get_by_label("Agent name", exact=True).fill("Unsaved name")
    page.get_by_role("button", name="Close drawer", exact=True).click()
    expect(page.get_by_text("Discard unsaved changes?", exact=True)).to_be_visible()
    page.get_by_role("button", name="Cancel", exact=True).click()
    expect(page.get_by_label("Agent name", exact=True)).to_have_value("Unsaved name")
    page.get_by_role("button", name="Discard", exact=True).click()
    expect(page.get_by_label("Agent name", exact=True)).to_have_value("Article collector")
    page.get_by_role("button", name="Close drawer", exact=True).click()
    page.get_by_role("button", name="Executors 1", exact=True).click()
    expect(page.get_by_text("capture", exact=True)).to_be_visible()
    page.get_by_role("button", name="Agents 1", exact=True).click()
    page.get_by_label("Search agents and connections", exact=True).fill("model-alpha")
    expect(page.get_by_role("button", name="Article collector", exact=True)).to_be_visible()
    page.get_by_label("Search agents and connections", exact=True).fill("nonexistent")
    expect(page.get_by_role("button", name="Article collector", exact=True)).to_have_count(0)
    page.get_by_label("Search agents and connections", exact=True).fill("")
    catalog["agents"].append({**catalog["agents"][0], "id": "system-1", "name": "System agent", "system_key": "collection", "can_edit": False})
    page.get_by_role("button", name="Refresh", exact=True).click()
    page.get_by_role("button", name="System agent", exact=True).click()
    expect(page.get_by_label("Agent name", exact=True)).to_be_disabled()
    expect(page.get_by_role("button", name="Delete", exact=True)).to_have_count(0)
    page.get_by_role("button", name="Close drawer", exact=True).click()
    assert all(request[2]["expected_revision"] >= 0 for request in requests if request[0] in {"POST", "PUT"} and not request[1].endswith("/test"))
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.locator(".agent-management").bounding_box()["width"] <= 390
    page.get_by_role("button", name="Article collector", exact=True).click()
    expect(page.get_by_role("dialog")).to_be_visible()
    assert page.get_by_role("dialog").bounding_box()["width"] <= 390
    assert not errors, errors
    browser.close()
print("Agent creation, reusable connections, verification, revisioned edits and responsive layout passed")
