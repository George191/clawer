"""Verify fullscreen login loading, scene readiness, and compact layout."""

import argparse
import base64
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
        for condition in ("stalled scene", "reduced motion", "normal desktop", "no WebGL", "compact layout"):
            flags = ["--disable-dev-shm-usage", "--no-proxy-server"]
            if condition == "no WebGL":
                flags.append("--disable-webgl")
            browser = playwright.chromium.launch(executable_path=chromium, args=flags)
            paused = []
            try:
                page = browser.new_page(
                    viewport={"width": 390, "height": 844} if condition == "compact layout" else {"width": 1280, "height": 800},
                    reduced_motion="reduce" if condition == "reduced motion" else "no-preference",
                )
                page.set_default_timeout(10_000)
                page.add_init_script("""
                    window.removedLoadingSatellites = 0;
                    new MutationObserver(records => {
                        for (const record of records) for (const node of record.removedNodes) {
                            if (node.nodeType === 1) window.removedLoadingSatellites +=
                                Number(node.matches('.sp-auth-loading-satellite')) +
                                node.querySelectorAll('.sp-auth-loading-satellite').length;
                        }
                    }).observe(document, { childList: true, subtree: true });
                """)
                start = monotonic()
                if condition in ("stalled scene", "reduced motion"):
                    page.route("**/assets/CinematicSpace-*.js", lambda route: paused.append(route))
                    with page.expect_request("**/assets/CinematicSpace-*.js"):
                        page.goto(base + "/", wait_until="domcontentloaded")
                else:
                    page.goto(base + "/", wait_until="domcontentloaded")
                email = page.get_by_placeholder("name@company.com", exact=True)
                password = page.get_by_placeholder("Enter your password", exact=True)
                sign_in = page.get_by_role("button", name="Sign in", exact=True)
                loader = page.locator(".sp-auth-boot")
                state = page.locator(".sp-login-background-loader")
                if condition in ("stalled scene", "reduced motion"):
                    expect(loader).to_be_visible()
                    expect(state).not_to_have_class("sp-login-background-loader is-ready")
                    expect(loader).to_have_css("position", "fixed")
                    expect(loader).to_have_css("pointer-events", "auto")
                    expect(email).to_be_hidden()
                    expect(sign_in).to_be_hidden()
                    expect(loader.locator(".sp-auth-loading-satellite")).to_have_css(
                        "animation-name", "none" if condition == "reduced motion" else "sp-auth-boot-orbit"
                    )
                    assert page.evaluate("window.removedLoadingSatellites") == 0, "Boot animation must survive React mounting"
                    if condition == "stalled scene":
                        cdp = page.context.new_cdp_session(page)
                        layers = []
                        cdp.on('LayerTree.layerTreeDidChange', lambda event: layers.extend(event.get('layers', [])))
                        cdp.send('LayerTree.enable')
                        clip = loader.locator('.sp-auth-loading-stage').bounding_box()
                        def frame():
                            return base64.b64decode(cdp.send('Page.captureScreenshot', {
                                'format': 'png', 'clip': {**clip, 'scale': 1},
                            })['data'])
                        first = frame()
                        page.wait_for_timeout(250)
                        second = frame()
                        assert first != second, "Loading satellite must keep moving while the scene is stalled"
                        satellite = cdp.send('DOM.querySelector', {
                            'nodeId': cdp.send('DOM.getDocument')['root']['nodeId'],
                            'selector': '.sp-auth-boot .sp-auth-loading-satellite',
                        })['nodeId']
                        backend_id = cdp.send('DOM.describeNode', {'nodeId': satellite})['node']['backendNodeId']
                        satellite_layers = {layer['layerId'] for layer in layers if layer.get('backendNodeId') == backend_id}
                        reasons = [reason for layer_id in satellite_layers for reason in cdp.send(
                            'LayerTree.compositingReasons', {'layerId': layer_id}
                        )['compositingReasons']]
                        assert any('accelerated transform animation' in reason for reason in reasons), reasons
                    assert loader.bounding_box() == {"x": 0, "y": 0, "width": 1280, "height": 800}
                    assert loader.evaluate("node => [[1, 1], [innerWidth - 1, innerHeight - 1], [innerWidth / 2, innerHeight / 2]].every(([x, y]) => node.contains(document.elementFromPoint(x, y)))"), "Loader must cover the entire page"
                    assert paused, "The cinematic chunk must still be waiting"
                    for request in paused:
                        request.continue_()
                    paused.clear()
                if condition == "compact layout":
                    expect(state).to_have_count(0)
                    expect(loader).to_be_hidden()
                else:
                    expect(state).to_have_class("sp-login-background-loader is-ready", timeout=60_000)
                    expect(loader).to_be_hidden()
                    expect(loader).to_have_css("pointer-events", "none")
                expect(email).to_be_visible()
                expect(page.locator('.scout-dot-host')).to_have_count(0)
                expect(password).to_be_enabled()
                expect(sign_in).to_be_enabled()
                email.click()
                email.fill("desktop-check@example.org")
                expect(email).to_have_value("desktop-check@example.org")
                expect(email).to_be_focused()
                results[condition] = {
                    "viewportWidth": page.viewport_size["width"],
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
