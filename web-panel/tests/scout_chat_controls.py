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

    def route(r):
        if r.request.method == "POST":
            posts.append(r.request.url)
        r.fulfill(
            json={
                "user": {
                    "id": "fixture",
                    "email": "alex@example.test",
                    "full_name": "Alex",
                    "is_superuser": True,
                },
                "tenants": [],
                "teams": [],
            }
            if "/users/me/context" in r.request.url
            else {"items": []}
        )

    page.route("**/api/**", route)
    page.route_web_socket("**/ws**", lambda ws: ws.on_message(lambda _: None))
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(args.base.rstrip("/") + "/scout")
    entry = page.get_by_role("textbox", name="Message Scout", exact=True)
    expect(entry).to_be_visible()
    entry.fill("Plan a collection")
    entry.press("Enter")
    progress = page.get_by_role("complementary", name="Live collection progress")
    expect(progress).to_be_visible()
    expect(progress.get_by_text("Analyzing the webpage", exact=True)).to_be_visible()
    expect(progress.get_by_text("Writing the adapter", exact=True)).to_be_visible()
    expect(progress.locator("li.is-current h3")).to_have_text("Analyzing the webpage")
    expect(progress.locator("li.is-current h3")).to_have_text("Writing the adapter")
    canvas = page.frame_locator(".scout-dot-host iframe").locator("canvas")
    expect(canvas).to_have_attribute("data-ready", "true", timeout=90000)
    expect(page.get_by_role("button", name="Confirm and continue")).to_be_visible()
    expect(canvas).to_have_attribute("data-activity", "NeedsInput")
    page.get_by_role("button", name="Confirm and continue").click()
    expect(page.get_by_role("button", name="Copy response")).to_be_visible()
    expect(canvas).to_have_attribute("data-activity", "Ready")
    expect(progress.locator("li.is-done")).to_have_count(6)
    expect(progress.locator("li.is-current")).to_have_count(0)
    bounds = progress.bounding_box()
    chat_bounds = page.locator(".scout-chat-messages").bounding_box()
    dot_bounds = page.locator(".ai-chat-dot").bounding_box()
    assert abs(bounds["x"] + bounds["width"] - chat_bounds["x"]) < 1
    assert abs(dot_bounds["x"] + dot_bounds["width"] / 2 - chat_bounds["x"]) < 1
    page.get_by_role("button", name="Hide live progress").click()
    expect(progress).to_be_hidden()
    page.get_by_role("button", name="Show live progress").click()
    expect(progress).to_be_visible()
    page.wait_for_timeout(700)
    toggle = page.locator(".scout-worked-toggle").first
    panel = page.locator(".scout-step-content").first
    expect(toggle).to_have_attribute("aria-expanded", "false")
    page.wait_for_timeout(300)
    assert panel.bounding_box()["height"] < 1
    toggle.click()
    page.wait_for_timeout(80)
    mid = panel.bounding_box()["height"]
    page.wait_for_timeout(260)
    full = panel.bounding_box()["height"]
    assert 0 < mid < full, (mid, full)
    arrow = toggle.locator(".scout-step-chevron").bounding_box()
    row = toggle.bounding_box()
    assert abs(arrow["y"] + arrow["height"] / 2 - row["y"] - row["height"] / 2) < 1
    toggle.click()
    page.wait_for_timeout(80)
    assert 0 < panel.bounding_box()["height"] < full
    page.wait_for_timeout(260)
    assert panel.bounding_box()["height"] < 1
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
        page.get_by_role("button", name="Dot activity", exact=True).click()
        page.locator(".scout-dot-state-menu").get_by_role(
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
        ("Needs input", "NeedsInput", "Confirm and continue"),
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
    expect(progress).to_be_visible()
    bounds = progress.bounding_box()
    chat_bounds = page.locator(".scout-chat-messages").bounding_box()
    assert abs(bounds["y"] + bounds["height"] - chat_bounds["y"]) < 1
    box = page.locator(".scout-persistent-composer").bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= 390
    assert not posts, posts
    assert not errors, errors
    print(
        "Live progress, responsive split panels, Dot alignment, native state actions, animated steps, history, feedback, clipboard and speech passed",
        flush=True,
    )
    b.close()
