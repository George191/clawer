"""Exercise atomic record and link transactions, conflicts, history and undo in Chromium."""

import json, shutil
from pathlib import Path
from uuid import uuid4
import httpx
from playwright.sync_api import sync_playwright, expect

f = json.loads(Path("/tmp/ontology-fixture.json").read_text())
base = f["base"]
assert base == "http://127.0.0.1"
sem = "/api/ontology/v1"
source, target = f["source"], f["target"]
srow, trow = source["records"][0], target["records"][0]
key = uuid4().hex[:9]
checks = []


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


with httpx.Client(base_url=base, trust_env=False, timeout=15) as api:
    actor = f["owner"]
    login = api.post(
        "/api/v1/login/token", data={"username": actor["email"], "password": actor["password"]}
    )
    login.raise_for_status()
    api.headers["Authorization"] = "Bearer " + login.json()["access_token"]

    def call(method, path, code=200, **kw):
        reply = api.request(method, sem + path, **kw)
        require(reply.status_code == code, f"{method} {path} -> {reply.status_code}")
        return reply.json() if reply.content else None

    link = call(
        "POST",
        "/projects/" + f["projectId"] + "/link-types",
        201,
        json={
            "key": "tx-" + key,
            "name": "QA assignment " + key,
            "cardinality": "many_to_many",
            "sourceDefinitionId": source["id"],
            "targetDefinitionId": target["id"],
        },
    )
    record_path = "/object-types/" + source["id"] + "/records"
    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=shutil.which("chromium"),
            headless=True,
            args=["--no-proxy-server", "--disable-dev-shm-usage"],
        )
        ctx = browser.new_context()
        page = ctx.new_page()
        expect.set_options(timeout=18000)
        page.goto(base + "/ontology")
        page.get_by_placeholder("name@company.com").fill(actor["email"])
        page.get_by_placeholder("Enter your password").fill(actor["password"])
        page.get_by_role("button", name="Sign in", exact=True).click()
        expect(page.get_by_placeholder("Enter your password")).to_have_count(0)
        url = base + "/ontology/explorer?objectType=" + source["id"]
        page.goto(url)

        def open_tool(name):
            page.get_by_role("button", name="Open in", exact=True).click()
            page.get_by_role("menuitem", name=name, exact=True).click()

        def close_tool(name):
            page.get_by_role("dialog", name=name, exact=True).get_by_role(
                "button", name="Close", exact=True
            ).click()

        open_tool("Transactions")
        panel = page.get_by_role("region", name="Transaction workbench")

        def btn(name):
            return panel.get_by_role("button", name=name, exact=True)

        def label(name):
            return panel.get_by_label(name, exact=True)

        def add_create(typ, keycode, name):
            label("Step operation").select_option("create")
            label("Step object type").select_option(typ["id"])
            label("Step value id").fill(keycode)
            label("Step value name").fill(name)
            btn("Add step").click()

        def edge(kind, s, t):
            label("Step operation").select_option(kind)
            label("Step object type").select_option(source["id"])
            label("Step relationship").select_option(link["id"])
            label("Source record").select_option(s)
            label("Target record").select_option(t)
            btn("Add step").click()

        def commit(name):
            label("Transaction name").fill(name)
            btn("Commit transaction").click()
            expect(page.get_by_role("status").filter(has_text="Transaction committed").filter(has_text=name)).to_be_visible()

        def undo(name):
            item = panel.get_by_role("listitem").filter(has_text=name)
            item.get_by_role("button", name="Undo transaction").click()
            expect(page.get_by_role("status").filter(has_text="Transaction undone").filter(has_text=name)).to_be_visible()

        name1 = "QA create and link " + key
        add_create(source, "tx-a-" + key, "Tx A " + key)
        add_create(target, "tx-b-" + key, "Tx B " + key)
        edge("link", "@item1", "@item2")
        btn("Remove last step").click()
        edge("link", "@item1", "@item2")
        commit(name1)
        rows = call("GET", record_path)["items"]
        ra = next(r for r in rows if r["properties"]["id"] == "tx-a-" + key)
        undo(name1)
        require(
            all(r["id"] != ra["id"] for r in call("GET", record_path)["items"]),
            "undo did not remove created value",
        )
        checks += [
            "create two object types and link new aliases atomically",
            "remove queued step and commit",
            "undo entire graph transaction",
        ]
        name2 = "QA update " + key
        label("Step operation").select_option("modify")
        label("Step object type").select_option(source["id"])
        label("Step record").select_option(srow["id"])
        label("Step value amount").fill("77")
        btn("Add step").click()
        label("Step operation").select_option("delete")
        label("Step object type").select_option(target["id"])
        label("Step record").select_option(trow["id"])
        btn("Add step").click()
        commit(name2)
        require(
            call("GET", record_path + "/" + srow["id"])["properties"]["amount"] == 77,
            "expected edited amount",
        )
        call("GET", "/object-types/" + target["id"] + "/records/" + trow["id"], 404)
        undo(name2)
        require(
            call("GET", record_path + "/" + srow["id"])["properties"]["amount"]
            == srow["properties"]["amount"],
            "expected restored amount",
        )
        require(
            call("GET", "/object-types/" + target["id"] + "/records/" + trow["id"])["id"]
            == trow["id"],
            "expected restored ID",
        )
        checks += ["modify and delete across types", "restore field and deleted identity"]
        name3 = "QA link " + key
        edge("link", srow["id"], trow["id"])
        commit(name3)
        name4 = "QA unlink " + key
        edge("unlink", srow["id"], trow["id"])
        commit(name4)
        undo(name4)
        panel.get_by_role("listitem").filter(has_text=name3).get_by_role(
            "button", name="Undo transaction"
        ).click()
        expect(page.locator(".explorer-toast").filter(has_text="removed or replaced")).to_be_visible()
        checks += [
            "unlink existing objects",
            "undo unlink",
            "reject stale undo against replacement edge",
        ]
        close_tool("Transactions")
        open_tool("Deleted objects")
        deleted = page.get_by_role("region", name="Deleted objects", exact=True)
        expect(deleted.get_by_role("button", name="Tx A " + key, exact=True)).to_be_visible()
        close_tool("Deleted objects")
        results = page.get_by_role("region", name="Object results", exact=True)
        results.get_by_role("checkbox", name="Select " + srow["title"], exact=True).check()
        page.get_by_role("button", name="History", exact=True).click()
        expect(page.get_by_role("region", name="Record action history")).to_contain_text("amount")
        checks += ["inspect deleted records", "field-level history values"]
        single_title = "QA single edit " + key
        page.get_by_role("button", name="Create object", exact=True).click()
        dialog = page.get_by_role("dialog")
        dialog.get_by_role("textbox", name="Id", exact=True).fill("single-" + key)
        dialog.get_by_role("textbox", name="Name", exact=True).fill(single_title)
        dialog.get_by_role("button", name="Create object", exact=True).click()
        expect(dialog).to_have_count(0)
        results.get_by_role("checkbox", name="Select " + single_title, exact=True).check()
        selection = page.get_by_role("complementary", name="Selection Preview")
        selection.get_by_role("button", name="Actions", exact=True).click()
        page.get_by_role("menuitem", name="Edit", exact=True).click()
        selection.get_by_role("spinbutton", name="Edit Amount", exact=True).fill("31")
        selection.get_by_role("button", name="Save changes", exact=True).click()
        expect(selection.get_by_role("region", name="Edit object", exact=True)).to_have_count(0)
        results.get_by_role("checkbox", name="Select " + single_title, exact=True).check()
        selection.get_by_role("button", name="History", exact=True).click()
        with page.expect_response(
            lambda r: r.request.method == "POST" and r.url.endswith("/undo")
        ) as inverse:
            selection.get_by_role("button", name="Undo edit", exact=True).click()
        assert inverse.value.status == 200
        saved = next(r for r in call("GET", record_path)["items"] if r["title"] == single_title)
        assert "amount" not in saved["properties"] and saved["revision"] == 3
        results.get_by_role("checkbox", name="Select " + single_title, exact=True).check()
        selection.get_by_role("button", name="Actions", exact=True).click()
        page.get_by_role("menuitem", name="Delete", exact=True).click()
        selection.get_by_role("button", name="Delete", exact=True).click()
        expect(selection.get_by_role("region", name="Confirm object deletion", exact=True)).to_have_count(0)
        open_tool("Deleted objects")
        deleted.get_by_role("button", name=single_title, exact=True).click()
        with page.expect_response(
            lambda r: r.request.method == "POST" and r.url.endswith("/undo")
        ) as inverse:
            deleted.get_by_role("button", name="Undo edit", exact=True).click()
        assert inverse.value.status == 200
        close_tool("Deleted objects")
        expect(results.get_by_role("link", name=single_title, exact=True)).to_be_visible()
        assert call("GET", record_path + "/" + saved["id"])["revision"] == 5
        checks += [
            "single create and modify",
            "undo to missing field",
            "delete and restore from history",
        ]
        browser.close()
print(json.dumps({"ok": True, "checks": checks}))
