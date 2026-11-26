"""Browser regression for the collection conversation preview; no external AI or collection requests."""

import argparse
import re
from ipaddress import ip_address
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

parser = argparse.ArgumentParser(
    description="Verify Scout response controls, state actions and history rail on a loopback frontend."
)
parser.add_argument("base")
parser.add_argument(
    "--channel", default=None, help="Installed Playwright browser channel, for example msedge"
)
args = parser.parse_args()
if not ip_address(urlsplit(args.base).hostname).is_loopback:
    raise ValueError("Expected a loopback frontend URL")
expect.set_options(timeout=30000)
with sync_playwright() as p:
    b = p.chromium.launch(
        channel=args.channel, args=["--no-proxy-server", "--enable-unsafe-swiftshader"]
    )
    ctx = b.new_context(
        viewport={"width": 1440, "height": 800}, permissions=["clipboard-read", "clipboard-write"]
    )
    page = ctx.new_page()
    errors = []
    posts = []
    page.add_init_script(
        "const n=Math.floor(Date.now()/1000);localStorage.setItem('auth_session',JSON.stringify({access_token:'test.'+btoa(JSON.stringify({sub:'fixture',type:'access',iat:n,exp:n+600,session_exp:n+600})).replace(/=+$/,'')+'.test',last_activity:Date.now(),locked:false}));window.__speech=[];Object.defineProperty(window,'speechSynthesis',{value:{cancel(){const u=window.__speech.at(-1);if(u)queueMicrotask(()=>u.dispatchEvent(new Event('error')))},speak(u){window.__speech.push(u)}}});"
    )

    pending_profiles = []
    profile_loading = True
    profile = {
        "user": {
            "id": "fixture",
            "email": "alex@example.test",
            "full_name": "Alex",
            "is_superuser": True,
        },
        "tenants": [],
        "teams": [],
    }

    def route(r):
        if r.request.method == "POST":
            posts.append(r.request.url)
        if "/users/me/context" in r.request.url and profile_loading:
            pending_profiles.append(r)
            return
        r.fulfill(json=profile if "/users/me/context" in r.request.url else {"items": []})

    page.route("**/api/**", route)
    page.route_web_socket("**/ws**", lambda ws: ws.on_message(lambda _: None))
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(args.base.rstrip("/") + "/scout", wait_until="domcontentloaded")
    entry = page.get_by_role("textbox", name="Message Scout", exact=True)
    expect(entry).to_be_visible()
    loading = page.get_by_role("status", name="Loading your profile")
    expect(loading).to_be_visible()
    expect(loading.locator("i")).to_have_count(3)
    home = page.locator(".scout-chat-preview").bounding_box()
    composer = page.locator(".scout-persistent-composer").bounding_box()
    greeting = page.locator(".scout-chat-greeting").bounding_box()
    dot = page.locator(".ai-chat-dot").bounding_box()
    assert abs(composer["y"] + composer["height"] / 2 - home["y"] - home["height"] / 2) < 2
    assert (
        dot["y"] + dot["height"]
        < greeting["y"]
        < greeting["y"] + greeting["height"]
        < composer["y"]
    )
    profile_loading = False
    for pending in pending_profiles:
        pending.fulfill(json=profile)
    expect(page.locator(".scout-loaded-name")).to_have_text("Alex")
    expect(loading).to_have_count(0)
    assert entry.evaluate("e=>e.tagName") == "INPUT"
    page.locator('input[aria-label="Composer files"]').set_input_files(
        {"name": "notes.py", "mimeType": "text/plain", "buffer": b"print('source')"}
    )
    page.get_by_role("button", name="Preview attachment notes.py", exact=True).click()
    expect(page.get_by_role("dialog", name="notes.py")).to_contain_text("print('source')")
    page.get_by_role("dialog", name="notes.py").get_by_role("button", name="Close dialog").click()
    page.get_by_role("button", name="Remove attachment notes.py", exact=True).click()
    expect(
        page.get_by_role("button", name="Preview attachment notes.py", exact=True)
    ).to_have_count(0)
    expect(page.get_by_role("button", name="Send message", exact=True)).to_be_disabled()
    entry.fill("Plan a collection")
    send = page.get_by_role("button", name="Send message", exact=True)
    before_send = send.bounding_box()
    send.hover()
    page.wait_for_timeout(200)
    assert send.bounding_box()["y"] == before_send["y"]
    entry.press("Enter")
    results = page.get_by_role("complementary", name="Collection results")
    expect(results).to_be_visible()
    assert entry.evaluate("e=>e.tagName") == "TEXTAREA"
    assert page.get_by_role("button", name="Hide collection results").count() == 0
    entry.fill("First line")
    entry.press("Shift+Enter")
    entry.type("Second line")
    expect(entry).to_have_value("First line\nSecond line")
    entry.fill("")
    context = page.get_by_role("complementary", name="Conversation context")
    expect(context).to_be_hidden()
    page.get_by_role("button", name="Conversation context", exact=True).click()
    expect(context).to_be_visible()
    context.get_by_role("button", name=re.compile("^Pause ")).click()
    context.get_by_role("button", name="Pin summary", exact=True).click()
    expect(context).to_have_class(re.compile("is-pinned"))
    expect(page.get_by_role("button", name="Unpin summary panel", exact=True)).to_have_attribute("aria-pressed", "true")
    page.get_by_role("button", name="Unpin summary panel", exact=True).click()
    expect(context).not_to_have_class(re.compile("is-pinned"))
    context.get_by_role("button", name="Pin summary", exact=True).click()
    assert page.get_by_role("button", name="Preview model").count() == 0
    assert page.get_by_role("button", name="Preview reasoning effort").count() == 0
    assert results.evaluate("e=>getComputedStyle(e).backgroundImage") == "none"
    expect(page.get_by_role("button", name="Continue", exact=True)).to_be_visible()
    page.get_by_role("button", name="Continue", exact=True).click()
    expect(results.locator(".scout-result-viewport")).to_have_attribute("data-browser-active", "true")
    expect(results.locator(".scout-browser-control-border")).to_have_attribute("data-active", "true")
    expect(results.locator(".scout-result-content")).to_have_attribute("data-phase", "source")
    entry.fill("Queued changes")
    entry.press("Enter")
    expect(page.locator(".scout-queued-message")).to_have_count(1)
    expect(page.get_by_role("button", name="Stop response", exact=True)).to_be_visible()
    expect(page.locator(".scout-chat-turn")).to_have_count(1)
    page.get_by_role("button", name="Edit queued message", exact=True).click()
    expect(entry).to_have_value("Queued changes")
    expect(page.locator(".scout-queued-message")).to_have_count(0)
    entry.press("Enter")
    page.get_by_role("button", name="Delete queued message", exact=True).click()
    expect(page.locator(".scout-queued-message")).to_have_count(0)
    expect(results.locator(".scout-result-content")).to_have_attribute("data-phase", "template")
    expect(results.locator(".scout-result-code")).to_contain_text("collection_articles")
    expect(results.locator(".scout-template-sheet")).to_have_count(0)
    raw_line = results.locator(".scout-result-code-line").first
    assert raw_line.evaluate("e=>getComputedStyle(e).display") == "flex"
    assert raw_line.locator("code").bounding_box()["height"] < 30
    assert raw_line.locator("code").bounding_box()["width"] > 120
    expect(page.locator(".ai-chat-dot")).to_have_attribute("data-activity", "Working")
    expect(results.get_by_text("Example artifact", exact=False)).to_have_count(0)
    expect(page.get_by_role("button", name="Confirm Template", exact=True)).to_be_visible()
    expect(results.locator(".scout-browser-control-border")).not_to_have_attribute("data-active", "true")
    page.get_by_role("button", name="Share file", exact=True).click()
    share = page.get_by_role("dialog", name="Share template.yaml", exact=True)
    expect(share.get_by_role("textbox", name="Shared file contents")).to_have_value(re.compile("name: collection_articles"))
    share.get_by_role("button", name="Copy contents", exact=True).click()
    assert page.evaluate("navigator.clipboard.readText()") == share.get_by_role("textbox", name="Shared file contents").input_value()
    with page.expect_download() as downloaded:
        share.get_by_role("button", name="Download file", exact=True).click()
    assert downloaded.value.suggested_filename == "template.yaml"
    share.get_by_role("button", name="Close dialog", exact=True).click()
    tab = results.get_by_role("tab", name="template.yaml", exact=True)
    expect(tab).to_have_attribute("aria-selected", "true")
    tab.focus()
    tab.press("ArrowLeft")
    expect(results.get_by_role("tab", name="Source.md", exact=True)).to_have_attribute("aria-selected", "true")
    page.keyboard.press("ArrowRight")
    expect(tab).to_have_attribute("aria-selected", "true")
    page.get_by_role("button", name="Preview options", exact=True).click()
    page.get_by_role("menuitem", name="Structured template preview", exact=True).click()
    template_rows = results.locator(".scout-template-sheet .ai-template-field")
    assert template_rows.count() > 15
    assert results.locator(".scout-template-row,.is-item-group").count() == 0
    metrics = template_rows.filter(has=page.locator(".ai-template-field-value")).first.evaluate("e=>[getComputedStyle(e).paddingTop,getComputedStyle(e).paddingBottom,getComputedStyle(e.querySelector('pre')).lineHeight]")
    assert metrics == ["2px", "2px", "17.4px"], metrics
    scroll = results.locator(".scout-result-content")
    scroll.evaluate("e=>e.scrollTo(0,0)")
    expect(results.locator(".scout-result-overflow-hint.is-top")).not_to_have_class(re.compile("is-visible"))
    expect(results.locator(".scout-result-overflow-hint.is-bottom")).to_have_class(re.compile("is-visible"))
    scroll.evaluate("e=>e.scrollTo(0,(e.scrollHeight-e.clientHeight)/2)")
    expect(results.locator(".scout-result-overflow-hint.is-top")).to_have_class(re.compile("is-visible"))
    expect(results.locator(".scout-result-overflow-hint.is-bottom")).to_have_class(re.compile("is-visible"))
    scroll.evaluate("e=>e.scrollTo(0,e.scrollHeight)")
    expect(results.locator(".scout-result-overflow-hint.is-bottom")).not_to_have_class(re.compile("is-visible"))
    assert scroll.evaluate("e=>getComputedStyle(e).scrollbarWidth") == "none"
    assert page.get_by_role("button", name="View adapter.py", exact=True).count() == 0
    context.get_by_role("button", name="Inspect Generating the template", exact=True).click()
    expect(results).to_be_visible()
    page.get_by_role("button", name="Confirm Template", exact=True).click()
    expect(results).to_be_visible()
    context.get_by_role("button", name="Inspect Writing the adapter", exact=True).click()
    expect(results).to_be_visible()
    expect(results.locator(".scout-result-content")).to_have_attribute("data-phase", "adapter")
    expect(results.locator(".scout-result-caret")).to_be_visible()
    expect(page.locator(".ai-chat-dot")).to_have_attribute("data-activity", "Working")
    keyword = results.locator(".ai-session-python-token.is-keyword").first
    function = results.locator(".ai-session-python-token.is-function-name").first
    string = results.locator(".ai-session-python-token.is-string").first
    expect(keyword).to_be_visible()
    expect(function).to_be_visible()
    assert keyword.evaluate("e=>getComputedStyle(e).color") == "rgb(255, 121, 198)"
    assert function.evaluate("e=>getComputedStyle(e).color") == "rgb(80, 250, 123)"
    assert string.evaluate("e=>getComputedStyle(e).color") == "rgb(241, 250, 140)"
    expect(page.get_by_role("button", name="Confirm Adapter", exact=True)).to_be_visible(timeout=60000)
    canvas = page.frame_locator(".scout-dot-host iframe").locator("canvas")
    expect(canvas).to_have_attribute("data-ready", "true", timeout=90000)
    expect(canvas).to_have_attribute("data-activity", "NeedsInput")
    page.get_by_role("button", name="Confirm Adapter", exact=True).click()
    expect(page.get_by_role("button", name="Copy response")).to_be_visible()
    actions = page.locator(".scout-response-actions")
    assert actions.locator("svg").evaluate_all("nodes=>nodes.map(e=>e.dataset.codexIcon)") == ["copy", "feedback", "speaker", "more"]
    assert actions.locator("svg").evaluate_all("nodes=>nodes.every(e=>e.getAttribute('viewBox')==='0 0 16 16' && getComputedStyle(e).stroke==='none' && e.getBoundingClientRect().width===16)")
    expect(canvas).to_have_attribute("data-activity", "Ready")
    expect(results.locator(".scout-result-code")).to_contain_text("def adapt_page")
    bounds = results.bounding_box()
    chat_bounds = page.locator(".scout-chat-messages").bounding_box()
    root_bounds = page.locator(".scout-chat-preview").bounding_box()
    dot_bounds = page.locator(".ai-chat-dot").bounding_box()
    assert abs(bounds["width"] - root_bounds["width"] / 2) < 1
    assert abs(bounds["x"] + bounds["width"] - chat_bounds["x"]) < 1
    assert abs(dot_bounds["x"] + dot_bounds["width"] / 2 - bounds["x"] - bounds["width"]) < 2
    assert abs(dot_bounds["y"] + dot_bounds["height"] / 2 - root_bounds["y"] - 104) < 2
    divider = page.locator(".scout-chat-preview").evaluate(
        "e=>{const s=getComputedStyle(e,'::after');return [s.content,s.width,s.backgroundImage]}"
    )
    assert divider[0] != "none" and divider[1] == "1px"
    assert "repeating-linear-gradient" in divider[2]
    assert context.bounding_box()["x"] >= chat_bounds["x"] + chat_bounds["width"]
    assert page.get_by_role("navigation", name="Stage results").count() == 0
    assert results.locator(".scout-result-actions,.scout-result-sample,h2").count() == 0
    expect(results.get_by_role("toolbar", name="File actions")).to_be_visible()
    assert results.get_by_role("button", name="Copy adapter.py").count() == 0
    assert results.get_by_role("button", name="Download adapter.py").count() == 0
    expect(
        context.get_by_role("button", name="Open template.yaml", exact=True).locator("img")
    ).to_have_attribute("src", "/file-icons/yaml.svg")
    expect(
        context.get_by_role("button", name="Open adapter.py", exact=True).locator("img")
    ).to_have_attribute("src", "/file-icons/python.svg")
    assert context.locator("img").first.evaluate("e=>e.complete && e.naturalWidth>0")
    context.get_by_role("button", name="Open request source", exact=True).click()
    expect(results.locator(".scout-result-brief")).to_contain_text("Plan a collection")
    context.get_by_role("button", name="Inspect Validating the adapter", exact=True).click()
    expect(results.get_by_role("table")).to_be_visible()
    context.get_by_role("button", name="Inspect Reviewing the collection plan", exact=True).click()
    expect(results.locator(".scout-result-brief")).to_contain_text("title, url")
    context.get_by_role("button", name="Open template.yaml", exact=True).click()
    expect(results.locator(".scout-result-content")).to_have_attribute("data-phase", "template")
    context.get_by_role("button", name="Open adapter.py", exact=True).click()
    expect(results.locator(".scout-result-content")).to_have_attribute("data-phase", "adapter")
    context.get_by_role("button", name="Changes", exact=True).click()
    expect(context.get_by_role("button", name="Open adapter.py", exact=True)).to_be_hidden()
    context.get_by_role("button", name="Changes", exact=True).click()
    expect(context.get_by_role("button", name="Open adapter.py", exact=True)).to_be_visible()
    context.get_by_role("button", name="Inspect Writing the adapter", exact=True).click()
    expect(results).to_be_visible()
    page.get_by_role("button", name="More file actions", exact=True).click()
    page.get_by_role("menuitem", name="Close file preview", exact=True).click()
    expect(results).to_be_hidden()
    expect(context.get_by_role("button", name="Inspect Writing the adapter", exact=True)).to_have_attribute("aria-pressed", "false")
    page.wait_for_timeout(700)
    expanded_composer = page.locator(".scout-persistent-composer").bounding_box()
    assert expanded_composer["width"] > 500
    context.get_by_role("button", name="Inspect Writing the adapter", exact=True).click()
    expect(results).to_be_visible()
    expect(context.get_by_role("button", name="Inspect Writing the adapter", exact=True)).to_have_attribute("aria-pressed", "true")
    expect(results.locator(".scout-result-content")).to_have_attribute("data-phase", "adapter")
    context.locator('input[type="file"]').set_input_files(
        {"name": "source-notes.txt", "mimeType": "text/plain", "buffer": b"Collection source notes"}
    )
    context.get_by_role("button", name="Preview source source-notes.txt", exact=True).click()
    dialog = page.get_by_role("dialog", name="source-notes.txt")
    expect(dialog).to_contain_text("Collection source notes")
    dialog.get_by_role("button", name="Close dialog").click()
    context.get_by_role("button", name="View all", exact=True).click()
    expect(context.get_by_role("button", name="Show less", exact=True)).to_be_visible()
    context.get_by_role("button", name="Close context panel").click()
    expect(context).to_be_hidden()
    expect(page.get_by_role("button", name="Conversation context", exact=True)).to_be_focused()
    page.get_by_role("button", name="Conversation context", exact=True).click()
    expect(context).to_be_visible()
    page.wait_for_timeout(700)
    rail = page.get_by_role("complementary", name="Conversation history")
    bars = rail.get_by_role("button")
    rail_bounds = rail.bounding_box()
    composer_bounds = page.locator(".scout-persistent-composer").bounding_box()
    chat_area = page.locator(".scout-chat-messages").bounding_box()
    assert rail_bounds["x"] + rail_bounds["width"] <= composer_bounds["x"] - 8
    assert abs(composer_bounds["x"] - chat_area["x"] - (chat_area["x"] + chat_area["width"] - composer_bounds["x"] - composer_bounds["width"])) < 1
    chat_bounds = page.locator(".scout-chat-messages").bounding_box()
    assert page.locator(".ai-collect-workbench").evaluate("e=>[e.scrollLeft,e.scrollTop]") == [0, 0]
    assert chat_bounds["x"] <= rail_bounds["x"] < chat_bounds["x"] + 56
    bars.nth(3).click()
    expect(results.locator(".scout-result-content")).to_have_attribute("data-phase", "template")
    expect(
        results.locator(".scout-template-sheet").get_by_text("fields", exact=True)
    ).to_be_visible()
    bars.nth(2).click()
    expect(results.get_by_text("Ideas worth collecting.", exact=True)).to_be_visible()
    bars.nth(4).click()
    expect(results.locator(".scout-result-code")).to_contain_text("def adapt_page")
    assert results.locator(".scout-step-content").count() == 0
    page.locator(".scout-worked-toggle").first.click()
    assert page.get_by_role("button", name="Hide collection results").count() == 0
    release = page.get_by_role("region", name="Release options")
    release.get_by_role("button", name="Draft", exact=True).click()
    release.get_by_role("button", name="Save Draft", exact=True).click()
    expect(release.get_by_role("status")).to_contain_text("Example release prepared")
    release.get_by_role("button", name="Publish", exact=True).click()
    release.get_by_role("checkbox", name="Create collection task").uncheck()
    expect(release.get_by_role("button", name="Publish Template", exact=True)).to_be_visible()
    toggle = page.locator(".scout-worked-toggle").first
    panel = page.locator(".scout-step-content").first
    expect(toggle).to_have_attribute("aria-expanded", "false")
    page.wait_for_timeout(300)
    assert panel.bounding_box()["height"] < 1
    toggle.click()
    expect(toggle).to_have_attribute("aria-expanded", "true")
    page.wait_for_function("e=>e.getBoundingClientRect().height>0", arg=panel.element_handle())
    arrow = toggle.locator(".scout-step-chevron").bounding_box()
    row = toggle.bounding_box()
    assert abs(arrow["y"] + arrow["height"] / 2 - row["y"] - row["height"] / 2) < 1
    toggle.click()
    expect(toggle).to_have_attribute("aria-expanded", "false")
    page.wait_for_function("e=>e.getBoundingClientRect().height<1", arg=panel.element_handle())
    rail = page.get_by_role("complementary", name="Conversation history")
    bars = rail.get_by_role("button")
    assert bars.count() == 7
    bars.nth(3).hover()
    page.wait_for_timeout(230)
    for i, w in [(3, 30), (2, 18), (1, 11)]:
        assert abs(bars.nth(i).locator("span").bounding_box()["width"] - w) < 1
    bars.nth(3).click()
    expect(toggle).to_have_attribute("aria-expanded", "true")
    expect(bars.nth(3)).to_have_attribute("aria-current", "step")
    page.wait_for_timeout(550)
    assert page.locator(".scout-chat-messages").evaluate("(e)=>e.scrollTop") > 0
    rate = page.get_by_role("button", name="Rate response", exact=True)
    rate.click()
    menu = page.locator(".scout-feedback-menu")
    menu.get_by_role("menuitem", name="Good response", exact=True).click()
    expect(rate).to_have_attribute("aria-pressed", "true")
    rate.click()
    expect(menu.get_by_role("menuitem", name="Good response", exact=True)).to_have_class(
        re.compile(".*selected.*")
    )
    menu.get_by_role("menuitem", name="Bad response", exact=True).click()
    page.get_by_role("button", name="Copy response").click()
    assert (
        page.evaluate("navigator.clipboard.readText()") == page.locator(".scout-reply").inner_text()
    )
    page.get_by_role("button", name="Read aloud", exact=True).click()
    expect(canvas).to_have_attribute("data-activity", "Working")
    page.get_by_role("button", name="Stop reading").click()
    expect(canvas).to_have_attribute("data-activity", "Ready")

    def choose(label):
        page.get_by_role("button", name="More conversation actions", exact=True).click()
        page.locator(".scout-conversation-menu").get_by_role("menuitem", name="Dot activity", exact=True).click()
        page.locator(".scout-conversation-menu").get_by_role(
            "menuitem", name=label, exact=True
        ).click()

    for label, activity in [
        ("Thinking", "Thinking"),
        ("Searching", "Searching"),
        ("Working", "Working"),
        ("Creating", "Creating"),
    ]:
        choose(label)
        expect(page.get_by_role("button", name="Stop response")).to_be_visible()
        expect(canvas).to_have_attribute("data-activity", activity)
    for label, activity, button in [
        ("Paused", "Paused", "Continue"),
        ("Needs input", "NeedsInput", "Confirm Template"),
        ("Payment", "Payment", "Continue"),
        ("Error", "Error", "Retry"),
        ("Idle", "None", "Continue"),
    ]:
        choose(label)
        expect(canvas).to_have_attribute("data-activity", activity)
        expect(page.get_by_role("button", name=button, exact=True)).to_be_visible()
        page.get_by_role("button", name=button, exact=True).click()
        expect(page.get_by_role("button", name="Stop response")).to_be_visible()
    choose("Ready")
    expect(canvas).to_have_attribute("data-activity", "Ready")
    page.get_by_role("button", name="More response actions").click()
    page.locator(".scout-response-more-menu").get_by_role(
        "menuitem", name="Replay response"
    ).click()
    expect(page.locator(".scout-chat-turn")).to_have_count(2)
    choose("Ready")
    first = page.locator("#scout-turn-1")
    second = page.locator("#scout-turn-2")
    expect(first).to_be_visible()
    first.get_by_role("button", name="Read aloud", exact=True).click()
    second.get_by_role("button", name="Read aloud", exact=True).click()
    expect(second.get_by_role("button", name="Stop reading")).to_be_visible()
    expect(first.get_by_role("button", name="Read aloud", exact=True)).to_be_visible()
    expect(canvas).to_have_attribute("data-activity", "Working")
    page.evaluate("window.__speech.at(-1).dispatchEvent(new Event('end'))")
    expect(second.get_by_role("button", name="Read aloud", exact=True)).to_be_visible()
    expect(canvas).to_have_attribute("data-activity", "Ready")
    rate = second.get_by_role("button", name="Rate response")
    rate.focus()
    rate.press("ArrowDown")
    expect(
        page.locator(".scout-feedback-menu").get_by_role("menuitem", name="Good response")
    ).to_be_focused()
    page.keyboard.press("Escape")
    expect(rate).to_be_focused()
    rate.click()
    page.keyboard.press("Escape")
    page.set_viewport_size({"width": 390, "height": 844})
    page.wait_for_timeout(700)
    expect(rail).to_be_visible()
    expect(page.locator(".scout-dot-host")).to_be_hidden()
    expect(context).to_be_hidden()
    page.get_by_role("button", name="Conversation context", exact=True).click()
    expect(context).to_be_visible()
    assert context.bounding_box()["x"] >= 0
    context.get_by_role("button", name="Close context panel").click()
    expect(results).to_be_visible()
    bounds = results.bounding_box()
    chat_bounds = page.locator(".scout-chat-messages").bounding_box()
    assert abs(bounds["y"] + bounds["height"] + 48 - chat_bounds["y"]) < 1
    box = page.locator(".scout-persistent-composer").bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= 390
    page.locator("#scout-turn-2").get_by_role("button", name="Archive", exact=True).click()
    expect(
        page.locator("#scout-turn-2").get_by_role("button", name="Archive Template", exact=True)
    ).to_be_visible()
    page.set_viewport_size({"width": 1440, "height": 800})
    page.wait_for_timeout(700)
    entry.fill("Queue lifecycle")
    entry.press("Enter")
    expect(page.locator(".scout-chat-turn")).to_have_count(3)
    entry.fill("Automatic followup")
    entry.press("Enter")
    expect(page.locator(".scout-queued-message")).to_have_count(1)
    choose("Ready")
    expect(page.locator(".scout-chat-turn")).to_have_count(4)
    expect(page.locator("#scout-turn-4 .scout-user-message")).to_contain_text("Automatic followup")
    expect(page.locator(".scout-queued-message")).to_have_count(0)
    entry.fill("Immediate followup")
    entry.press("Enter")
    page.get_by_role("button", name="Send queued message now", exact=True).click()
    expect(page.locator(".scout-chat-turn")).to_have_count(5)
    expect(page.locator("#scout-turn-5 .scout-user-message")).to_contain_text("Immediate followup")
    expect(page.locator(".scout-queued-message")).to_have_count(0)
    choose("Ready")
    assert not posts, posts
    assert not errors, errors
    print(
        "Homepage single-line and conversation multiline composer, attachments/queue, context navigation, top-divider Dot, clean result canvas and code highlighting passed",
        flush=True,
    )
    b.close()
