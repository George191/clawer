"""Browser contract for docked Summary and persisted chat actions, without AI calls."""

import argparse
import copy
import json
from ipaddress import ip_address
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from playwright.sync_api import expect, sync_playwright

parser = argparse.ArgumentParser()
parser.add_argument("base")
parser.add_argument("--channel", default="msedge")
parser.add_argument("--screenshot")
args = parser.parse_args()
assert ip_address(urlsplit(args.base).hostname).is_loopback

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(channel=args.channel, args=["--no-proxy-server"])
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    context.add_init_script("""const n=Math.floor(Date.now()/1000);if(!localStorage.getItem('auth_session'))localStorage.setItem('auth_session',JSON.stringify({access_token:'test.'+btoa(JSON.stringify({sub:'fixture',type:'access',iat:n,exp:n+3600,session_exp:n+3600})).replace(/=+$/,'')+'.test',last_activity:Date.now(),locked:false}));Object.defineProperty(navigator,'clipboard',{value:{writeText:async t=>{window.__clipboard=t},readText:async()=>window.__clipboard}});""")
    chats, shares, schedules = {}, {}, {}
    profile = {"user": {"id": str(uuid4()), "email": "alex@example.test", "full_name": "Alex", "is_superuser": True}, "tenants": [], "teams": []}
    errors = []

    def api(route):
        request = route.request
        url = urlsplit(request.url)
        path = url.path
        body = request.post_data_json if request.post_data else {}
        parts = path.split("/")
        status, data = 200, {"items": []}
        if "/users/me/context" in path:
            data = profile
        elif path.endswith("/agents/catalog"):
            data = {"agents": [{"id": "agent-fixture", "name": "Assistant", "harness": "assistant", "enabled": True}]}
        elif path.endswith("/scout/chats"):
            archived = parse_qs(url.query).get("archived", ["false"])[0] == "true"
            data = sorted([chat for chat in chats.values() if chat["archived"] == archived], key=lambda chat: not chat["pinned"])
        elif "/scout/chats/" in path:
            ident = parts[parts.index("chats") + 1]
            if path.endswith("/branch"):
                data = {**copy.deepcopy(chats[ident]), "id": str(uuid4()), "revision": 1, "pinned": False, "archived": False}
                chats[data["id"]] = data
                status = 201
            elif path.endswith("/shares"):
                share_id = str(uuid4())
                shares[share_id] = {**copy.deepcopy(body["snapshot"]), "chat_id": ident}
                data, status = {"id": share_id}, 201
            elif path.endswith("/schedule"):
                if request.method == "PUT":
                    schedules[ident] = {**body, "next_run_at": "2026-11-28T00:00:00Z"}
                elif request.method == "DELETE":
                    schedules.pop(ident, None)
                data = schedules.get(ident)
            elif request.method == "PUT":
                if body["revision"] != chats.get(ident, {"revision": 0})["revision"]:
                    status, data = 409, {"detail": "Revision conflict"}
                else:
                    data = {**copy.deepcopy(body), "id": ident, "revision": body["revision"] + 1}
                    chats[ident] = data
            elif request.method == "DELETE":
                chats.pop(ident, None)
                data = {"deleted": True}
            else:
                data = json.loads(json.dumps(chats[ident], sort_keys=True))
        elif "/scout/shares/" in path:
            data = shares[parts[-1]]
        route.fulfill(status=status, content_type="application/json", body=json.dumps(data))

    context.route("**/api/**", api)
    context.route_web_socket("**/ws**", lambda ws: ws.on_message(lambda _: None))
    page = context.new_page()
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(args.base + "/scout", wait_until="domcontentloaded")
    entry = page.get_by_role("textbox", name="Message Scout", exact=True)
    page.locator('.scout-persistent-composer input[type="file"]').set_input_files({"name": "notes.txt", "mimeType": "text/plain", "buffer": b"Public source notes"})
    entry.fill("Inspect a public webpage")
    entry.press("Enter")
    page.get_by_role("button", name="Stop response", exact=True).click()
    toolbar = page.get_by_role("toolbar", name="Conversation actions")
    results = page.get_by_role("complementary", name="Collection results")
    summary = page.locator("#scout-conversation-context")
    initial_chat_bounds = page.locator(".scout-chat-messages").bounding_box()
    toolbar.get_by_role("button", name="Conversation context", exact=True).click()
    expect(summary).to_be_visible()
    page.wait_for_timeout(700)
    chat_bounds = page.locator(".scout-chat-messages").bounding_box()
    assert abs(chat_bounds["width"] - initial_chat_bounds["width"]) < 1
    expect(summary).to_have_css("border-radius", "20px")
    assert abs(summary.bounding_box()["width"] - 300) < 1
    assert toolbar.locator("svg").evaluate_all("es=>es.every(e=>e.getBoundingClientRect().width===16 && e.getAttribute('viewBox')==='0 0 16 16')")
    expect(toolbar.locator('[data-codex-icon="sidebar-light-16"]')).to_have_count(1)
    if args.screenshot:
        page.screenshot(path=args.screenshot.replace(".png", "-summary.png"))
    expect(summary.locator(".scout-context-heading")).to_have_count(0)
    expect(summary.get_by_role("button", name="Sources", exact=True)).to_be_visible()
    expect(summary.get_by_role("button", name="Background processes", exact=True)).to_have_count(0)
    toolbar.get_by_role("button", name="Hide file preview", exact=True).click()
    expect(results).to_be_hidden()
    expect(summary).to_be_hidden()
    expect(toolbar.locator('[data-codex-icon="sidebar-hidden-light-16"]')).to_have_count(1)
    toolbar.get_by_role("button", name="Show file preview", exact=True).click()

    def more(name, child=None):
        expect(toolbar).to_have_count(1)
        toolbar.get_by_role("button", name="More conversation actions", exact=True).click()
        expect(page.get_by_role("menuitem", name="Dot activity", exact=True)).to_have_count(0)
        expect(page.locator('.codex-menu[data-state="open"]').first.get_by_role("menuitem")).to_have_count(7)
        if args.screenshot and name == "Rename":
            page.screenshot(path=args.screenshot.replace(".png", "-more.png"))
        page.get_by_role("menuitem", name=name, exact=True).click()
        if child:
            page.get_by_role("menuitem", name=child, exact=True).click()

    more("Rename")
    rename = page.get_by_role("dialog", name="Rename chat", exact=True)
    rename.get_by_role("textbox", name="Chat name").fill("Saved collection")
    rename.get_by_role("button", name="Save", exact=True).click()
    expect(rename).to_be_hidden()
    more("Pin chat")
    expect(page.locator(".codex-chat-title")).to_contain_text("● Saved collection")
    more("Copy", "Copy as Markdown")
    page.wait_for_function("window.__clipboard?.includes('# Saved collection')")
    original = next(iter(chats))
    assert parse_qs(urlsplit(page.url).query)["chat"][0] == original
    assert chats[original]["pinned"]
    assert chats[original]["turns"][0]["attachments"][0]["content"] == "UHVibGljIHNvdXJjZSBub3Rlcw=="

    toolbar.get_by_role("button", name="Share chat", exact=True).click()
    share = page.get_by_role("dialog", name="Share Saved collection", exact=True)
    if args.screenshot:
        page.screenshot(path=args.screenshot.replace(".png", "-share.png"))
    expect(share.get_by_role("region", name="Preview of the beginning of the shared chat")).to_contain_text("Inspect a public webpage")
    share.get_by_role("button", name="Copy link", exact=True).click()
    expect(share.get_by_role("button", name="Copied", exact=True)).to_be_visible()
    link = share.get_by_role("textbox", name="Share link").input_value()
    assert page.evaluate("window.__clipboard") == link
    share.get_by_label("Who can access", exact=True).select_option("invited")
    expect(share.get_by_role("button", name="Copy link", exact=True)).to_be_disabled()
    share.get_by_role("textbox", name="Add people by email").fill("invited@example.test")
    share.get_by_role("button", name="Copy link", exact=True).click()
    expect(share.get_by_role("button", name="Copied", exact=True)).to_be_visible()
    share.get_by_role("button", name="Close dialog", exact=True).click()
    shared = context.new_page()
    shared.goto(link, wait_until="domcontentloaded")
    expect(shared.get_by_role("heading", name="Saved collection")).to_be_visible()
    expect(shared.get_by_text("Inspect a public webpage", exact=True)).to_be_visible()
    shared.close()

    page.locator(".codex-chat-title").click()
    page.get_by_role("dialog", name="Chats", exact=True).get_by_role("button", name="Scheduled task…").click()
    schedule = page.get_by_role("dialog", name="Add scheduled task", exact=True)
    expect(schedule.get_by_role("button", name="Save task")).to_be_enabled(timeout=15000)
    schedule.get_by_label("Repeat", exact=True).select_option("3600")
    schedule.get_by_role("button", name="Save task").click()
    expect(page.get_by_role("dialog", name="Edit scheduled task")).to_be_visible()
    assert schedules[original]["interval_seconds"] == 3600
    schedule = page.get_by_role("dialog", name="Edit scheduled task")
    schedule.get_by_role("button", name="Remove task").click()
    expect(page.get_by_role("dialog", name="Add scheduled task")).to_be_visible()
    page.get_by_role("dialog", name="Add scheduled task").get_by_role("button", name="Close dialog", exact=True).click()

    with page.expect_popup() as popup_info:
        more("Open in", "New window")
    popup = popup_info.value
    expect(popup.locator(".codex-chat-title")).to_contain_text("Saved collection")
    popup.wait_for_timeout(1800)
    popup.close()
    more("Branch chat")
    page.wait_for_function("id=>{const current=new URLSearchParams(location.search).get('chat');return current && current!==id}", arg=original)
    branched = parse_qs(urlsplit(page.url).query)["chat"][0]
    assert branched != original and chats[branched]["turns"] == chats[original]["turns"]
    expect(page.get_by_role("button", name="Preview sent attachment notes.txt", exact=True)).to_be_visible()

    more("Archive")
    confirm = page.get_by_role("dialog", name="Archive chat?", exact=True)
    confirm.get_by_role("button", name="Archive", exact=True).click()
    expect(page.get_by_role("button", name="Unarchive and open")).to_be_visible()
    assert chats[branched]["archived"]
    page.locator(".codex-chat-title").click()
    library = page.get_by_role("dialog", name="Chats", exact=True)
    library.get_by_role("button", name="Archived", exact=True).click()
    library.locator(".codex-chat-library > button").first.click()
    page.get_by_role("button", name="Unarchive and open").click()
    expect(page.get_by_role("button", name="Unarchive and open")).to_be_hidden()

    for width in [1100, 390]:
        page.set_viewport_size({"width": width, "height": 844})
        if toolbar.get_by_role("button", name="Conversation context").get_attribute("aria-pressed") != "true":
            toolbar.get_by_role("button", name="Conversation context").click()
        expect(summary).to_be_visible()
        page.wait_for_timeout(700)
        assert summary.bounding_box()["x"] >= 0
        assert summary.bounding_box()["x"] + summary.bounding_box()["width"] <= width
        toolbar.get_by_role("button", name="Conversation context").click()
        expect(summary).to_be_hidden()
    if args.screenshot:
        page.set_viewport_size({"width": 1440, "height": 900})
        toolbar.get_by_role("button", name="Conversation context").click()
        page.wait_for_timeout(700)
        page.screenshot(path=args.screenshot)
    more("Delete")
    page.get_by_role("dialog", name="Delete chat?").get_by_role("button", name="Delete", exact=True).click()
    expect(page.locator(".codex-chat-title")).to_have_text("New chat")
    assert branched not in chats
    assert not errors, errors
    zh_context = browser.new_context(viewport={"width": 1100, "height": 800})
    auth = page.evaluate("localStorage.getItem('auth_session')")
    zh_context.add_init_script("localStorage.setItem('auth_session'," + json.dumps(auth) + ");localStorage.setItem('etl-panel-settings',JSON.stringify({state:{mode:'dark',language:'zh-CN'},version:0}));")
    zh_context.route("**/api/**", api)
    zh_context.route_web_socket("**/ws**", lambda ws: ws.on_message(lambda _: None))
    chats[original]["pinned"] = False
    chats[original]["turns"][0]["template"] = "name: articles\nfields:\n  title: h1"
    zh = zh_context.new_page()
    zh.goto(args.base + "/scout?chat=" + original, wait_until="domcontentloaded")
    zh.get_by_role("button", name="Conversation context", exact=True).click()
    expect(zh.get_by_role("button", name="更改", exact=True)).to_be_visible()
    expect(zh.get_by_role("button", name="来源", exact=True)).to_be_visible()
    if args.screenshot:
        zh.wait_for_timeout(350)
        zh.screenshot(path=args.screenshot.replace(".png", "-zh-summary.png"))
    zh.get_by_role("button", name="More conversation actions", exact=True).click()
    for label in ["重命名", "置顶聊天", "创建聊天分支", "复制", "打开方式", "归档", "删除"]:
        expect(zh.get_by_role("menuitem", name=label, exact=True)).to_be_visible()
    if args.screenshot:
        zh.screenshot(path=args.screenshot.replace(".png", "-zh-more.png"))
    zh.keyboard.press("r")
    expect(zh.get_by_role("dialog", name="Rename chat", exact=True)).to_be_visible()
    zh_context.close()
    browser.close()
    print("Chat persistence, Summary layout, share snapshot, schedule, rename/pin/branch/archive/restore/delete and new-window checks passed")
