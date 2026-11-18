"""Exercise desktop login with normal Chromium, a stalled scene, and WebGL disabled."""

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
from threading import Thread
from time import monotonic

from playwright.sync_api import expect, sync_playwright


class QuietStaticFiles(SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        return


def verify(base, chromium):
    expect.set_options(timeout=10_000)
    results = {}
    with sync_playwright() as playwright:
        for condition in ("stalled scene", "normal desktop", "no WebGL"):
            flags = ["--disable-dev-shm-usage", "--no-proxy-server"]
            if condition == "no WebGL":
                flags.append("--disable-webgl")
            browser = playwright.chromium.launch(executable_path=chromium, args=flags)
            paused = []
            try:
                page = browser.new_page(
                    viewport={"width": 1280, "height": 800}, reduced_motion="no-preference"
                )
                page.set_default_timeout(10_000)
                start = monotonic()
                if condition == "stalled scene":
                    page.route("**/assets/CinematicSpace-*.js", lambda route: paused.append(route))
                    with page.expect_request("**/assets/CinematicSpace-*.js"):
                        page.goto(base + "/", wait_until="domcontentloaded")
                else:
                    page.goto(base + "/", wait_until="domcontentloaded")
                email = page.get_by_placeholder("name@company.com", exact=True)
                password = page.get_by_placeholder("Enter your password", exact=True)
                sign_in = page.get_by_role("button", name="Sign in", exact=True)
                expect(email).to_be_visible()
                expect(password).to_be_enabled()
                expect(sign_in).to_be_enabled()
                email.click()
                email.fill("desktop-check@example.org")
                expect(email).to_have_value("desktop-check@example.org")
                expect(email).to_be_focused()
                if condition == "stalled scene":
                    loader = page.locator(".sp-login-background-loader")
                    expect(loader).to_be_visible()
                    expect(loader).not_to_have_class("sp-login-background-loader is-ready")
                    expect(loader).to_have_css("pointer-events", "none")
                    expect(loader.locator(".sp-auth-loading-satellite")).to_have_css(
                        "animation-name", "sp-auth-loader-orbit"
                    )
                    loading_area, panel = (
                        loader.bounding_box(),
                        page.locator(".sp-login-panel").bounding_box(),
                    )
                    assert loading_area["x"] + loading_area["width"] <= panel["x"] + 1, (
                        "Loading animation overlaps the login panel"
                    )
                    assert paused, "The cinematic chunk must still be waiting"
                results[condition] = {
                    "viewportWidth": 1280,
                    "usableWithinSeconds": round(monotonic() - start, 2),
                }
            finally:
                for request in paused:
                    request.abort()
                browser.close()
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    targets = parser.add_mutually_exclusive_group(required=True)
    targets.add_argument("--base")
    targets.add_argument("--static", type=Path)
    parser.add_argument(
        "--chromium", default=shutil.which("chromium") or shutil.which("chromium-browser")
    )
    args = parser.parse_args()
    if args.base:
        result = verify(args.base.rstrip("/"), args.chromium)
    else:
        assert (args.static / "index.html").is_file(), "Supply the built Web directory"
        with ThreadingHTTPServer(
            ("127.0.0.1", 0), partial(QuietStaticFiles, directory=args.static)
        ) as server:
            worker = Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                result = verify("http://127.0.0.1:" + str(server.server_port), args.chromium)
            finally:
                server.shutdown()
                worker.join()
    print(json.dumps({"ok": True, "cases": result}), flush=True)


if __name__ == "__main__":
    main()
