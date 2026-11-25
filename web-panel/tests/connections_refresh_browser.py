"""python tests/connections_refresh_browser.py (existing Playwright and Chromium required)."""
import asyncio
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import tempfile
import urllib.request
from urllib.parse import urlsplit

from playwright.async_api import async_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
PORT = int(os.environ.get('CONNECTIONS_TEST_PORT', '3103'))
OUTPUT = Path(os.environ.get('CONNECTIONS_TEST_OUTPUT', str(Path(tempfile.gettempdir()) / 'connections-refresh-qa')))
OUTPUT.mkdir(parents=True, exist_ok=True)
GEOMETRY = """() => {
  const workspace = document.querySelector('.ontology-connections-workspace');
  const rect = selector => { const r = document.querySelector(selector).getBoundingClientRect(); return [r.x, r.y, r.width, r.height]; };
  return { modal: rect('.ontology-connections-inventory-modal'), replay: rect('[aria-label="Sandbox CDC replay"]'),
    width: workspace.clientWidth, height: workspace.scrollHeight, scrollTop: workspace.scrollTop };
}"""


async def check(browser, width, height, armed):
    page = await browser.new_page(viewport={'width': width, 'height': height})
    page.set_default_timeout(10000)
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    await page.add_init_script('window.__APP_CONFIG__ = {backendDomainSuffix:"", frontendDomainSuffix:"", httpsPort:""};')
    gate = asyncio.Event()
    gate.set()
    mode = 'success'
    compare_gate, mutation_gate = asyncio.Event(), asyncio.Event()
    compare_started, mutation_started = asyncio.Event(), asyncio.Event()
    requests = []
    status = dict(available=armed, state='armed' if armed else 'blocked', generation=1 if armed else None,
                  appliedThrough='10', sourceHighWater='20', source='ts_cdc_sandbox.event_log',
                  target='ts_cdc_sandbox.target_rows', maxRows=50, maxBytes=65536)
    receipts = [dict(id=f'receipt-{i}', requestId=f'request-{i}', generation=1, fromExclusive='0', appliedThrough='10',
                     rowsAffected=10, recordedAt='2026-11-25T01:00:00Z') for i in range(2)]
    versions = [dict(id=f'version-{i}', receiptId=f'receipt-{i}', generation=1, appliedThrough='10',
                     rowsCaptured=10, bytesCaptured=100, recordedAt='2026-11-25T01:00:00Z') for i in range(2)]

    async def route_api(route):
        path = urlsplit(route.request.url).path
        requests.append(path)
        if route.request.method == 'POST':
            assert mode == 'mutation' and path.endswith('/etl/cdc-sandbox/runs'), 'Unexpected mutation'
            mutation_started.set()
            await mutation_gate.wait()
            await route.fulfill(json={**receipts[0], 'id': 'late-receipt'})
            return
        assert route.request.method == 'GET', 'Unexpected mutation'
        code, data = 200, {'available': False}
        if path.endswith('/users/me/context'):
            data = {'user': {'is_superuser': True}}
        elif path.endswith(('/etl/data-connections', '/etl/connector-types')):
            data = []
        elif path.endswith('/etl/cdc-sandbox'):
            await gate.wait()
            code, data = (403, {'detail': 'Scope denied'}) if mode == 'denied' else (500, {}) if mode == 'failed' else (200, status)
        elif path.endswith('/etl/cdc-sandbox/runs'):
            await gate.wait()
            code, data = (200, receipts) if armed else (500, {})
        elif path.endswith('/etl/cdc-sandbox/versions'):
            await gate.wait()
            data = {'available': mode != 'unavailable', 'versions': versions[1:] if mode == 'filtered' else versions}
        elif path.endswith('/etl/cdc-sandbox/versions/compare'):
            compare_started.set()
            await compare_gate.wait()
            code, data = 403, {'detail': 'Scope denied'}
        elif '/etl/cdc-sandbox/versions/' in path:
            data = {**versions[0], 'rows': [{'id': 'row', 'value': 'snapshot-test-value'}]}
        await route.fulfill(status=code, json=data)

    await page.route('**/api/v1/**', route_api)
    await page.goto(f'http://127.0.0.1:{PORT}/tests/fixtures/connections-refresh.html')
    try:
        await page.get_by_role('button', name='Review test replay', exact=True).click()
    except Exception:
        print(json.dumps({'errors': errors, 'requests': requests, 'body': await page.locator('body').inner_text()}), flush=True)
        raise
    refresh = page.get_by_role('button', name='Refresh replay status', exact=True)
    await expect(page.get_by_text('Reviewed generation', exact=True)).to_be_visible()
    await expect(refresh).to_be_enabled()
    if armed:
        await page.get_by_role('button', name='Open version history', exact=True).click()
        await expect(page.get_by_role('list', name='Captured target versions')).to_be_visible()
    await refresh.focus()
    before = await page.evaluate(GEOMETRY)
    gate.clear()
    await refresh.click()
    await expect(refresh).to_be_disabled()
    await page.wait_for_timeout(150)
    during = await page.evaluate(GEOMETRY)
    assert before == during, {'before': before, 'during': during}
    if armed:
        await expect(page.get_by_role('button', name='Apply next change batch')).to_be_disabled()
        await expect(page.get_by_role('list', name='Captured target versions')).to_be_visible()
    gate.set()
    await expect(refresh).to_be_enabled()
    after = await page.evaluate(GEOMETRY)
    assert before == after, {'before': before, 'after': after}
    await page.screenshot(path=str(OUTPUT / f'{width}-{height}-{armed}.png'))
    if armed and width == 1366:
        for mode in ['unavailable', 'filtered']:
            gate.clear()
            await refresh.click()
            await page.get_by_role('button', name='Inspect version', exact=True).first.click()
            await expect(page.get_by_text('snapshot-test-value', exact=True)).to_be_visible()
            gate.set()
            await expect(page.get_by_role('dialog', name='Historical target snapshot', exact=True)).to_have_count(0)
            await expect(refresh).to_be_enabled()
            mode = 'success'
            await refresh.click()
            await expect(page.get_by_role('button', name='Inspect version', exact=True)).to_have_count(2)
            await expect(refresh).to_be_enabled()
        for name, index in [('From version', 0), ('To version', 1)]:
            await page.get_by_role('combobox', name=name, exact=True).click()
            await page.get_by_role('option', name=re.compile('Checkpoint')).nth(index).click()
        mode = 'mutation'
        await page.get_by_role('button', name='Compare versions', exact=True).click()
        await asyncio.wait_for(compare_started.wait(), 3)
        await page.get_by_role('button', name='Apply next change batch', exact=True).click()
        await page.get_by_role('button', name='Confirm apply', exact=True).click()
        await asyncio.wait_for(mutation_started.wait(), 3)
        compare_gate.set()
        await expect(page.get_by_role('alert').filter(has_text='Sandbox replay status could not be loaded.')).to_be_visible()
        denied_count = len(requests)
        mutation_gate.set()
        await expect(refresh).to_be_enabled()
        await expect(page.get_by_text(re.compile('receipt confirmed: late-receipt'))).to_have_count(0)
        await page.wait_for_timeout(150)
        assert len(requests) == denied_count, 'A late mutation must not undo scope denial or auto-refresh'
    mode = 'failed'
    await refresh.click()
    await expect(page.get_by_role('alert').filter(has_text='Sandbox replay status could not be loaded.')).to_be_visible()
    await expect(page.get_by_role('button', name='Apply next change batch')).to_have_count(0)
    mode = 'success'
    await refresh.click()
    await expect(page.get_by_text('Reviewed generation', exact=True)).to_be_visible()
    await expect(refresh).to_be_enabled()
    mode = 'denied'
    await refresh.click()
    await expect(page.get_by_role('alert').filter(has_text='Sandbox replay status could not be loaded.')).to_be_visible()
    await expect(page.get_by_text('Reviewed generation', exact=True)).to_have_count(0)
    await expect(page.get_by_role('region', name='CDC dataset versions')).to_have_count(0)
    denied_count = len(requests)
    await page.wait_for_timeout(150)
    assert len(requests) == denied_count, 'Permission denial must not create an automatic retry loop'
    assert not errors, errors
    await page.close()
    return {'viewport': [width, height], 'armed': armed, 'geometry_unchanged': True, 'failures_block_execution': True}


async def main():
    log = (OUTPUT / 'vite.log').open('w')
    server = subprocess.Popen([shutil.which('node'), str(ROOT / 'node_modules/vite/bin/vite.js'), '--host', '127.0.0.1', '--port', str(PORT), '--strictPort'], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(f'http://127.0.0.1:{PORT}', timeout=.5).close()
                break
            except OSError:
                if server.poll() is not None:
                    raise RuntimeError('Vite failed to start; see ' + str(OUTPUT / 'vite.log'))
                await asyncio.sleep(.1)
        else:
            raise RuntimeError('Vite startup timed out')
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(channel=os.environ.get('CONNECTIONS_TEST_BROWSER', 'chrome') if os.name == 'nt' else None)
            results = [await check(browser, w, h, armed) for w, h in [(1366, 900), (800, 700)] for armed in [False, True]]
            await browser.close()
        print(json.dumps(results, indent=2))
    finally:
        server.terminate()
        server.wait(timeout=15)
        log.close()


if __name__ == '__main__':
    asyncio.run(main())
