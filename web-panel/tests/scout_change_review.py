"""Codex change-review interactions against persisted correction fixtures."""

import argparse
import copy
import json
import re
import time
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
    browser = playwright.chromium.launch(channel=args.channel, args=["--no-proxy-server", "--disable-gpu"])
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    context.add_init_script("""const n=Math.floor(Date.now()/1000);if(!localStorage.getItem('auth_session'))localStorage.setItem('auth_session',JSON.stringify({access_token:'test.'+btoa(JSON.stringify({sub:'fixture',type:'access',iat:n,exp:n+3600,session_exp:n+3600})).replace(/=+$/,'')+'.test',last_activity:Date.now(),locked:false}));Object.defineProperty(navigator,'clipboard',{value:{writeText:async t=>{window.__clipboard=t},readText:async()=>window.__clipboard}});""")
    chats, shares, schedules = {}, {}, {}
    puts = []
    reject_next = [False]
    delay_next = [False]
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
                puts.append(copy.deepcopy(body))
                if delay_next[0]:
                    delay_next[0] = False
                    time.sleep(2)
                if reject_next[0]:
                    reject_next[0] = False
                    route.fulfill(status=500, content_type="application/json", body=json.dumps({"detail": "Injected save failure"}))
                    return
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
    ident = str(uuid4())
    template = "name: articles\n" + "".join(f"field_{i}: original_{i}\n" for i in range(1, 42))
    corrected = template.replace("original_4", "corrected_4").replace("original_35", "corrected_35")
    adapter = "import json\n\n" + "".join(f"value_{i} = {i}\n" for i in range(1, 35))
    adapter2 = adapter.replace("import json", "import json, sys").replace("value_20 = 20", "value_20 =  20").replace("value_30 = 30", "value_30 = 300")
    def turn(number, prompt, yaml, python):
        return {"id": number, "prompt": prompt, "template": yaml, "adapter": python, "reply": "Saved correction", "step": 5, "done": 6, "complete": True, "createdAt": f"2026-11-27T08:0{number}:00Z", "attachments": []}
    chats[ident] = {"id": ident, "title": "Correction review", "revision": 1, "pinned": False, "archived": False, "turns": [turn(1, "Initial generation", template, adapter), turn(2, "Correct article fields", corrected, adapter2)]}
    page.goto(args.base + "/scout?chat=" + ident, wait_until="domcontentloaded")
    panel = page.get_by_label("Changes review", exact=True)
    try:
        expect(panel).to_be_visible(timeout=30000)
    except AssertionError:
        print("Page errors:", errors, flush=True)
        print(page.locator("body").inner_text()[:1600], flush=True)
        if args.screenshot:
            page.screenshot(path=args.screenshot.replace(".png", "-failed.png"))
        raise
    changes = panel.get_by_role("toolbar", name="Changes controls")
    template_file = panel.get_by_role("region", name="Changes to template.yaml")
    if not template_file.count():
        template_file = panel.locator("#codex-change-template")
    renderer = template_file.locator("diffs-container")
    expect(renderer.locator("[data-line]").first).to_be_visible(timeout=30000)
    tabs = panel.get_by_role("tablist", name="Preview tabs", exact=True)
    expect(tabs.get_by_role("tab", name="Changes", exact=True)).to_have_attribute("aria-selected", "true")
    expect(template_file.locator('.codex-change-file-header [data-codex-icon="eye-light-16"]')).to_have_count(1)
    expect(template_file.locator('.codex-change-file-header [data-codex-icon="tab-badge-plus-light-16"]')).to_have_count(1)
    expect(template_file.locator('.codex-change-file-header button[aria-label^="File actions"]')).to_have_count(0)
    panel.get_by_role("button", name="Open template.yaml in tab", exact=True).click()
    expect(tabs.get_by_role("tab", name="template.yaml", exact=True)).to_have_attribute("aria-selected", "true")
    expect(panel.locator('#codex-file-panel diffs-container [data-line]').first).to_be_visible()
    tabs.get_by_role("tab", name="Changes", exact=True).click()
    panel.get_by_role("button", name="Open template.yaml in tab", exact=True).click()
    expect(tabs.get_by_role("tab", name="template.yaml", exact=True)).to_have_count(1)
    tabs.get_by_role("tab", name="Changes", exact=True).click()
    panel.get_by_role("button", name="Open adapter.py in tab", exact=True).click()
    expect(tabs.get_by_role("tab")).to_have_count(3)
    tabs.get_by_role("button", name="Close adapter.py tab", exact=True).click()
    expect(tabs.get_by_role("tab", name="template.yaml", exact=True)).to_have_attribute("aria-selected", "true")
    tabs.get_by_role("button", name="Close template.yaml tab", exact=True).click()
    expect(tabs.get_by_role("tab")).to_have_count(1)
    expect(template_file.get_by_role("button", name="Comment on template.yaml", exact=True)).to_have_count(0)
    panel.get_by_role("button", name="Preview template.yaml", exact=True).click()
    expect(tabs.locator('.codex-review-file-tab.is-preview')).to_have_count(1)
    tabs.get_by_role("tab", name="Changes", exact=True).click()
    panel.get_by_role("button", name="Preview adapter.py", exact=True).click()
    expect(tabs.get_by_role("tab", name="template.yaml", exact=True)).to_have_count(0)
    tabs.get_by_role("tab", name="adapter.py", exact=True).dblclick()
    expect(tabs.locator('.codex-review-file-tab.is-preview')).to_have_count(0)
    file_gutter = panel.locator('#codex-file-panel diffs-container [data-column-number]').first
    file_line = int(file_gutter.get_attribute('data-column-number'))
    file_gutter.click()
    file_comment_dialog = page.get_by_role('dialog', name='Leave a comment', exact=True)
    file_comment_dialog.get_by_role('textbox', name='Comment', exact=True).fill('Full-file line comment')
    file_comment_dialog.get_by_role('button', name='Add comment', exact=True).click()
    expect(file_comment_dialog).to_be_hidden()
    assert chats[ident]['turns'][-1]['review']['comments'][-1]['start'] == file_line > 0
    file_comment = panel.locator('#codex-file-panel .codex-inline-comment').filter(has_text='Full-file line comment')
    expect(file_comment).to_be_visible()
    file_comment.get_by_role('button', name='Delete', exact=True).click()
    tabs.get_by_role('tab', name='adapter.py', exact=True).focus()
    page.keyboard.press('ArrowLeft')
    expect(tabs.get_by_role('tab', name='Changes', exact=True)).to_have_attribute('aria-selected', 'true')
    tabs.get_by_role('button', name='Close adapter.py tab', exact=True).click()
    if args.screenshot:
        page.screenshot(path=args.screenshot)
    toggle_file = template_file.locator('.codex-change-file-toggle')
    toggle_file.focus()
    page.keyboard.press('Enter')
    expect(toggle_file).to_have_attribute('aria-expanded', 'false')
    expect(page.get_by_role('menu')).to_have_count(0)
    page.keyboard.press('Space')
    expect(toggle_file).to_have_attribute('aria-expanded', 'true')
    def options(label):
        changes.get_by_role("button", name="Changes options", exact=True).click()
        page.get_by_role("menuitem", name=label, exact=True).click()

    def file_action(label, key="template"):
        panel.locator(f"#codex-change-{key} .codex-change-file-toggle").click(button="right")
        page.get_by_role("menuitem", name=label, exact=True).click()

    def source(label):
        panel.locator(".codex-changes-sources > button").nth(1).click()
        page.get_by_role("menuitem", name=label, exact=True).click()

    # Repository selection and refresh use persisted state without writing a new revision.
    panel.locator(".codex-changes-sources > button").first.click()
    page.get_by_role("menuitem", name="Scout", exact=True).click()
    expect(panel.locator(".codex-changes-sources > button").first).to_have_text("Scout")
    panel.locator(".codex-changes-sources > button").first.click()
    page.get_by_role("menuitem", name="All repos", exact=True).click()
    previous_puts = len(puts)
    chats[ident]["turns"][-1]["adapter"] += "# refreshed from saved state\n"
    changes.get_by_role("button", name="Refresh changes", exact=True).click()
    page.wait_for_function("!document.querySelector('.codex-changes-controls button[disabled]')")
    assert len(puts) == previous_puts
    # A real comparison, rather than the whole file painted green.
    expect(panel.locator(".codex-change-stats").first).to_contain_text("-7")
    before_lines = renderer.locator("[data-line]").count()
    assert before_lines < 43
    expect(renderer.locator("[data-unmodified-lines]").first).to_be_visible()
    renderer.locator("[data-unmodified-lines]").first.click()
    assert renderer.locator("[data-line]").count() > before_lines
    options("Load full files")
    assert renderer.locator("[data-line]").count() >= 43
    options("Load full files")
    changes.get_by_role("button", name="Switch to split diff", exact=True).click()
    expect(panel.locator(".codex-review-scroll")).to_have_attribute("data-view", "split")
    if args.screenshot:
        page.screenshot(path=args.screenshot.replace(".png", "-split.png"))
    changes.get_by_role("button", name="Switch to Auto diff", exact=True).click()
    expect(changes.get_by_role("button", name="Auto diff: switch to unified diff", exact=True)).to_have_attribute("data-diff-mode", "auto")
    changes.get_by_role("button", name="Auto diff: switch to unified diff", exact=True).click()
    changes.get_by_role("button", name="Word wrap", exact=True).click()
    expect(changes.get_by_role("button", name="Word wrap", exact=True)).to_have_attribute("aria-pressed", "true")
    changes.get_by_role("button", name="Collapse all diffs", exact=True).click()
    expect(renderer).to_have_count(0)
    changes.get_by_role("button", name="Expand all diffs", exact=True).click()
    expect(renderer).to_be_visible()
    template_file.get_by_role("button", name="Collapse template.yaml", exact=True).click()
    expect(renderer).to_have_count(0)
    template_file.get_by_role("button", name="Expand template.yaml", exact=True).click()
    expect(renderer).to_be_visible()
    options("Word diffs")
    assert renderer.locator("[data-diff-span]").count() == 0
    options("Word diffs")
    options("Hide white space")
    expect(panel.locator(".codex-change-stats").first).to_contain_text("-6")
    options("Hide white space")
    options("Hide imports")
    python_lines = panel.locator("#codex-change-adapter diffs-container [data-line]")
    imported = python_lines.filter(has_text="import json")
    assert imported.count() >= 1
    expect(imported.first).to_be_hidden()
    # Hiding imports must not renumber the remaining source lines.
    assert python_lines.filter(has_text="value_30 = 300").first.get_attribute("data-line") == "32"
    options("Hide imports")
    expect(imported.first).to_be_visible()
    options("Rich preview")
    expect(template_file.locator(".codex-rich-preview")).to_be_visible()
    options("Rich preview")
    options("Line numbers")
    expect(renderer.locator("[data-line-number-content]").first).to_be_hidden()
    options("Line numbers")
    changes.get_by_role("button", name="Jump to file", exact=True).click()
    panel.get_by_role("textbox", name="Jump to file", exact=True).fill("adapter")
    panel.locator(".codex-jump-files").get_by_role("button", name="adapter.py", exact=True).click()
    expect(panel.locator(".codex-jump-files")).to_have_count(0)
    panel.get_by_role("button", name="Show file tree", exact=True).click()
    expect(panel.get_by_role("navigation", name="Changed files")).to_be_visible()
    panel.get_by_role("navigation", name="Changed files").get_by_role("button", name=re.compile("template.yaml")).click()
    panel.get_by_role("button", name="Hide file tree", exact=True).click()
    options("Find in diff")
    panel.get_by_role("textbox", name="Find in diff", exact=True).fill("corrected_35")
    panel.get_by_role("textbox", name="Find in diff", exact=True).press("Enter")
    expect(page.get_by_role("status").filter(has_text=re.compile(r"1 /"))).to_be_visible()
    panel.get_by_role("button", name="Close find", exact=True).click()
    file_action("Copy path")
    page.wait_for_function("window.__clipboard === 'template.yaml'")
    file_action("Copy file contents")
    page.wait_for_function("text=>window.__clipboard===text", arg=corrected)
    file_action("Copy patch")
    page.wait_for_function("window.__clipboard?.includes('--- a/template.yaml') && window.__clipboard?.includes('-field_4: original_4')")
    options("Copy git apply command")
    page.wait_for_function("window.__clipboard?.startsWith('git apply')")
    with page.expect_download() as result:
        options("Download patch")
    assert result.value.suggested_filename == "changes.patch"

    # Stage exactly one hunk, then verify the staged comparison excludes other hunks.
    template_file.hover()
    template_file.get_by_role("button", name="Actions for template.yaml hunk 1", exact=True).click()
    page.get_by_role("menuitem", name="Stage", exact=True).click()
    page.wait_for_function("!document.querySelector('.codex-changes-controls button[disabled]')")
    assert "corrected_4" in chats[ident]["turns"][-1]["review"]["staged"]["template"]
    assert "corrected_35" not in chats[ident]["turns"][-1]["review"]["staged"]["template"]
    source("Staged")
    assert panel.locator("#codex-change-template [data-line]").filter(has_text="corrected_35").count() == 0
    template_file.hover()
    template_file.get_by_role("button", name="Actions for template.yaml hunk 1", exact=True).click()
    page.get_by_role("menuitem", name="Unstage", exact=True).click()
    expect(panel.get_by_role("status", name="", exact=True).filter(has_text="No changes")).to_be_visible()
    options("Unstage all")
    expect(panel.get_by_role("status", name="", exact=True).filter(has_text="No changes")).to_be_visible()
    source("Last turn")
    options("Stage all")
    page.wait_for_function("!document.querySelector('.codex-changes-controls button[disabled]')")
    assert set(chats[ident]["turns"][-1]["review"]["staged"]) == {"template", "adapter"}
    options("Unstage all")

    # Edit -> immutable correction -> server round-trip -> historical restore.
    file_action("Stage file")
    page.wait_for_function("!document.querySelector('.codex-changes-controls button[disabled]')")
    page.get_by_role("button", name="Rate response", exact=True).last.click()
    page.get_by_role("menuitem", name="Good response", exact=True).click()
    file_action("Open file")
    dialog = page.get_by_role("dialog", name="template.yaml", exact=True)
    dialog.get_by_role("button", name="Edit", exact=True).click()
    edited = corrected.replace("name: articles", "name: corrected_articles")
    dialog.get_by_role("textbox", name="Edit file contents", exact=True).fill(edited)
    delay_next[0] = True
    dialog.get_by_role("button", name="Save revision", exact=True).click()
    expect(dialog).to_be_hidden()
    saved_review = chats[ident]["turns"][-1]["review"]
    assert len(saved_review["revisions"]) == 2
    assert saved_review["revisions"][0]["template"] == corrected
    assert saved_review["revisions"][1]["template"] == edited
    page.wait_for_timeout(1800)
    assert chats[ident]["turns"][-1]["template"] == edited, "A pending autosave overwrote an explicit correction"
    assert chats[ident]["turns"][-1]["vote"] == "up"
    assert saved_review["staged"]["template"] == corrected
    source("Staged")
    expect(panel.locator("#codex-change-template")).to_be_visible()
    assert panel.locator("#codex-change-template [data-line]").filter(has_text="corrected_35").count() > 0
    source("Unstaged")
    expect(panel.locator("#codex-change-template [data-line]").filter(has_text="corrected_articles")).to_be_visible()
    assert panel.locator("#codex-change-template [data-line]").filter(has_text="corrected_35").count() == 0
    file_action("Stage file")
    page.wait_for_function("!document.querySelector('.codex-changes-controls button[disabled]')")
    assert chats[ident]["turns"][-1]["review"]["staged"]["template"] == edited
    source("Staged")
    file_action("Unstage file")
    page.wait_for_function("!document.querySelector('.codex-changes-controls button[disabled]')")
    assert "template" not in chats[ident]["turns"][-1]["review"]["staged"]
    source("Last turn")
    page.reload(wait_until="domcontentloaded")
    expect(panel.locator("#codex-change-template [data-line]").filter(has_text="name: corrected_articles")).to_be_visible(timeout=30000)
    options("Correction history")
    timeline = panel.get_by_role("navigation", name="Correction history", exact=True)
    expect(timeline.get_by_role("button")).to_have_count(3)
    timeline.get_by_role("button", name=re.compile("Correct article fields")).click()
    expect(panel.get_by_role("button", name="Restore this revision", exact=True)).to_be_visible()
    panel.get_by_role("button", name="Restore this revision", exact=True).click()
    expect(panel.locator(".codex-revision-banner")).to_have_count(0)
    assert len(chats[ident]["turns"][-1]["review"]["revisions"]) == 3
    assert chats[ident]["turns"][-1]["template"] == corrected
    options("Correction history")

    # Comments persist, resolve/reopen and can prefill a new correction request.
    renderer.locator("[data-column-number]").first.click()
    comment_dialog = page.get_by_role("dialog", name="Leave a comment", exact=True)
    comment_dialog.get_by_role("textbox", name="Comment", exact=True).fill("Please verify the article name")
    comment_dialog.get_by_role("button", name="Add comment", exact=True).click()
    expect(comment_dialog).to_be_hidden()
    comment = panel.locator(".codex-inline-comment").filter(has_text="Please verify the article name")
    expect(comment).to_be_visible()
    comment.get_by_role("button", name="Resolve", exact=True).click()
    expect(comment).to_have_count(0)
    options("Show resolved comments")
    expect(comment).to_be_visible()
    comment.get_by_role("button", name="Reopen", exact=True).click()
    comment.get_by_role("button", name="Add to chat", exact=True).click()
    assert "Please verify" in page.get_by_role("textbox", name="Message Scout", exact=True).input_value()
    page.get_by_role("textbox", name="Message Scout", exact=True).fill("")
    comment.get_by_role("button", name="Delete", exact=True).click()
    expect(comment).to_have_count(0)

    # Revert and a failed save preserve the previous corrections and the draft.
    file_action("Revert file")
    confirm = page.get_by_role("dialog", name="Revert changes?", exact=True)
    confirm.get_by_role("button", name="Revert", exact=True).click()
    expect(confirm).to_be_hidden()
    assert len(chats[ident]["turns"][-1]["review"]["revisions"]) == 4
    source("All changes")
    file_action("Open file")
    dialog = page.get_by_role("dialog", name="template.yaml", exact=True)
    dialog.get_by_role("button", name="Edit", exact=True).click()
    failed_text = chats[ident]["turns"][-1]["template"] + "failed_save: retained\n"
    dialog.get_by_role("textbox", name="Edit file contents", exact=True).fill(failed_text)
    reject_next[0] = True
    dialog.get_by_role("button", name="Save revision", exact=True).click()
    expect(dialog.get_by_role("alert")).to_contain_text("Injected save failure")
    expect(dialog).to_be_visible()
    expect(panel.locator("#codex-change-template [data-line]").filter(has_text="failed_save: retained")).to_have_count(0)
    expect(dialog.get_by_role("textbox", name="Edit file contents", exact=True)).to_have_value(failed_text)
    dialog.get_by_role("button", name="Save revision", exact=True).click()
    expect(dialog).to_be_hidden()
    assert chats[ident]["turns"][-1]["template"] == failed_text
    page.wait_for_timeout(1800)
    put_count = len(puts)
    page.reload(wait_until="domcontentloaded")
    expect(panel).to_be_visible(timeout=30000)
    page.wait_for_timeout(2200)
    assert len(puts) == put_count, "JSONB key ordering caused an unsolicited save"
    previous_revisions = len(chats[ident]["turns"][-1]["review"]["revisions"])
    template_file.hover()
    template_file.get_by_role("button", name="Actions for template.yaml hunk 1", exact=True).click()
    page.get_by_role("menuitem", name="Revert", exact=True).click()
    page.wait_for_function("!document.querySelector('.codex-changes-controls button[disabled]')")
    assert "failed_save: retained" not in chats[ident]["turns"][-1]["template"]
    assert len(chats[ident]["turns"][-1]["review"]["revisions"]) == previous_revisions + 1
    assert "failed_save: retained" in chats[ident]["turns"][-1]["review"]["revisions"][-2]["template"]
    gutter = renderer.locator("[data-column-number]").first
    line_number = int(gutter.get_attribute("data-column-number"))
    gutter.click()
    line_dialog = page.get_by_role("dialog", name="Leave a comment", exact=True)
    line_dialog.get_by_role("textbox", name="Comment", exact=True).fill("Preserve this line coordinate")
    line_dialog.get_by_role("button", name="Add comment", exact=True).click()
    expect(line_dialog).to_be_hidden()
    assert chats[ident]["turns"][-1]["review"]["comments"][-1]["start"] == line_number
    for width in [1100, 390]:
        page.set_viewport_size({"width": width, "height": 844})
        page.wait_for_timeout(700)
        expect(changes).to_be_visible()
        bounds = changes.bounding_box()
        assert bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= width
        if args.screenshot:
            page.screenshot(path=args.screenshot.replace(".png", f"-{width}.png"))
    assert not errors, errors
    browser.close()
    print("Native diff controls, hunk staging, file corrections, persisted history/restore, comments, failed save, reload and mobile checks passed", flush=True)
