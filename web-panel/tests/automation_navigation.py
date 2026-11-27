"""Automation navigation and log inspection with isolated API/WebSocket fixtures."""
import argparse
import json
from ipaddress import ip_address
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

expect.set_options(timeout=15000)

parser = argparse.ArgumentParser()
parser.add_argument("base")
args = parser.parse_args()
assert ip_address(urlsplit(args.base).hostname).is_loopback

with sync_playwright() as p:
    browser = p.chromium.launch(channel="msedge", args=["--no-proxy-server"])
    page = browser.new_page(viewport={"width": 1440, "height": 960})
    page.add_init_script("const n=Math.floor(Date.now()/1000);localStorage.setItem('auth_session',JSON.stringify({access_token:'test.'+btoa(JSON.stringify({sub:'fixture',type:'access',iat:n,exp:n+600,session_exp:n+600})).replace(/=+$/,'')+'.test',last_activity:Date.now(),locked:false}));")
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    stamp = "2026-11-27T01:00:00Z"
    execution = {"id": "execution-1", "status": "completed", "started_at": stamp, "ended_at": stamp, "inserted_records": 10, "updated_records": 0}
    task = {"id": "task-1", "name": "Collect articles", "product_domain": "ai-collect", "status": "completed", "progress": 100, "records": 10, "owner": "Alex", "control_state": None, "task_path": "collect.articles", "download_state": "idle", "sync_state": "idle", "current_run_id": execution["id"], "current_run": execution, "log_runs": [execution], "log_run_count": 1, "started_at": stamp}
    task["schedule"] = {"mode": "manual"}
    workflow = {"name": "Article workflow", "product_domain": "ai-collect", "description": "Collect and validate", "enabled": True, "nodes": [{"name": "Collect", "type": "task"}], "updated_at": stamp}
    subscriptions = []

    def websocket(ws):
        ws.send(json.dumps({"type": "task_stream_status", "data": {"available": True}}))

        def on_message(raw):
            message = json.loads(raw)
            if message.get("type") != "subscribe":
                return
            channel = message["channel"]
            subscriptions.append(channel)
            if channel in {"workflows", "schedules", "tasks"}:
                ws.send(json.dumps({"type": f"{channel}_snapshot", "data": [workflow] if channel == "workflows" else [task] if channel == "tasks" else []}))

        ws.on_message(on_message)

    page.route_web_socket("**/ws**", websocket)

    def route(r):
        path = urlsplit(r.request.url).path
        if path.endswith("/automation/workers"):
            value = {"items": [{"name": "worker-1", "pid": 42, "uptime": 90, "concurrency": 4, "processed": 15, "active": [], "queues": ["collection"]}]}
        elif path.endswith("/tasks/executions"):
            value = {"items": [{**execution, "task_id": task["id"], "name": task["name"], "owner": task["owner"], "product_domain": "ai-collect", "task_path": "collect.articles"}]}
        elif path.endswith("/users/me/context"):
            value = {"user": {"id": "fixture", "full_name": "Alex", "email": "alex@example.test", "is_superuser": False}, "tenants": [], "teams": []}
        elif path.endswith("/tasks/runs"):
            value = {"items": [task]}
        elif path.endswith("/tasks/runs/task-1"):
            value = task
        elif "/logs/execution-1" in path:
            value = {"items": [{"id": str(i), "run_id": execution["id"], "level": level, "message": text, "created_at": stamp} for i, level, text in [(1, "info", "Collected ten articles"), (2, "error", "Example failure detail")]], "notifications": [], "has_more": False, "next_before_id": None}
        elif path.endswith("/agents/catalog"):
            value = {"agents": [], "connections": [], "harnesses": {"assistant": {"name": "Assistant", "tools": []}}}
        else:
            value = {"items": []}
        r.fulfill(json=value)

    page.route("**/api/**", route)
    page.goto(args.base + "/automation/workflows?domain=ai-collect")
    nav = page.get_by_role("navigation", name="Automation sections", exact=True)
    expect(page.get_by_text("Article workflow", exact=True)).to_be_visible()
    expect(nav.get_by_role("link", name="Workflows", exact=True)).to_have_attribute("aria-current", "page")
    page.get_by_role("button", name="Refresh", exact=True).click()
    expect(page.get_by_text("Article workflow", exact=True)).to_be_visible()
    page.get_by_text("Article workflow", exact=True).click()
    expect(page.get_by_role("dialog")).to_be_visible()
    page.get_by_role("button", name="Close drawer", exact=True).click()
    nav.get_by_role("link", name="Schedules", exact=True).click()
    expect(page).to_have_url(args.base + "/automation/schedules?domain=ai-collect")
    expect(page.get_by_role("heading", name="Schedules", exact=True)).to_be_visible()
    nav.get_by_role("link", name="Runs", exact=True).click()
    expect(page).to_have_url(args.base + "/automation/runs?domain=ai-collect")
    page.get_by_role("button", name="Collect articles", exact=False).click()
    expect(page.get_by_label("Run details", exact=True)).to_be_visible()
    expect(page.get_by_text("Collected ten articles", exact=True)).to_be_visible()
    page.get_by_label("Search execution logs", exact=True).fill("failure")
    expect(page.get_by_text("Collected ten articles", exact=True)).to_have_count(0)
    expect(page.get_by_text("Example failure detail", exact=True)).to_be_visible()
    page.get_by_label("Search execution logs", exact=True).fill("unmatched")
    expect(page.get_by_text("No loaded logs match these filters.", exact=True)).to_be_visible()
    page.get_by_label("Search execution logs", exact=True).fill("")
    page.get_by_label("Log level", exact=True).click()
    page.get_by_role("option", name="error", exact=True).click()
    expect(page.get_by_text("Collected ten articles", exact=True)).to_have_count(0)
    expect(page.get_by_text("Example failure detail", exact=True)).to_be_visible()
    page.get_by_role("button", name="Close run details", exact=True).click()
    page.get_by_role("group", name="Run status", exact=True).get_by_role("button", name="Canceled", exact=True).click()
    expect(page.get_by_role("button", name="Collect articles", exact=False)).to_have_count(0)
    nav.get_by_role("link", name="Workers", exact=True).click()
    page.get_by_role("button", name="worker-1", exact=True).click()
    expect(page.get_by_text("No active jobs reported.", exact=True)).to_be_visible()
    nav.get_by_role("link", name="Workflows", exact=True).click()
    page.get_by_role("button", name="New workflow", exact=True).click()
    expect(page.get_by_role("button", name="Create", exact=True)).to_be_in_viewport()
    page.get_by_label("Step 1 name", exact=True).fill("First step")
    page.get_by_role("button", name="Insert last step", exact=True).click()
    page.get_by_label("Step 2 name", exact=True).fill("Second step")
    page.get_by_role("button", name="Zoom flow in", exact=True).click()
    expect(page.locator(".windmill-flow-graph")).to_have_attribute("style", "transform: scale(1.1); transform-origin: center top;")
    page.get_by_role("button", name="Cancel", exact=True).click()
    nav.get_by_role("link", name="Agents", exact=True).click()
    expect(page).to_have_url(args.base + "/agents")
    expect(page.get_by_role("heading", name="Agents", exact=True)).to_be_visible()
    assert subscriptions.count("workflows") >= 2
    assert not errors, errors
    browser.close()
print("Automation section navigation, domain preservation, refresh, execution history and log filters passed")
