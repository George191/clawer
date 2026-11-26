"""Run with python3 tests/ui_select_browser.py (Playwright Chromium required)."""
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.request

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
PORT = int(os.environ.get('SELECT_TEST_PORT', '3102'))
ARTIFACTS = Path(os.environ.get('SELECT_TEST_OUTPUT', '/tmp/standard-select-qa'))
ARTIFACTS.mkdir(parents=True, exist_ok=True)


def check_geometry(control, popup):
    box = control.bounding_box()
    menu = popup.bounding_box()
    assert abs(box['x'] - menu['x']) < 1, (box, menu)
    assert abs(box['width'] - menu['width']) < 1, (box, menu)
    assert abs(menu['y'] - box['y'] - box['height'] - 4) < 1, (box, menu)
    return {'control': box, 'menu': menu}


with (ARTIFACTS / 'vite.log').open('w') as log:
    server = subprocess.Popen(['npm', 'exec', 'vite', '--', '--host', '127.0.0.1', '--port', str(PORT), '--strictPort'], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
    try:
        url = f'http://127.0.0.1:{PORT}/tests/fixtures/select.html'
        for _ in range(100):
            try:
                with urllib.request.urlopen(url, timeout=1) as response:
                    assert response.status == 200
                break
            except OSError:
                if server.poll() is not None:
                    raise RuntimeError('Vite failed to start')
                time.sleep(.1)
        else:
            raise RuntimeError('Vite did not start')
        results = []
        with sync_playwright() as p:
            browser = p.chromium.launch()
            for mode in ['light', 'dark']:
                page = browser.new_page(viewport={'width': 1366, 'height': 900})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(url + '?theme=' + mode)
                control = page.get_by_role('combobox', name='Connection', exact=True)
                expect(control).to_be_visible()
                expect(page.get_by_role('combobox', name='Numeric value')).to_contain_text('10 / page')
                expect(page.get_by_role('combobox', name='Disabled fieldset control')).to_be_disabled()
                before_validation = page.get_by_role('form').bounding_box()
                page.get_by_role('button', name='Submit', exact=True).click()
                expect(control).to_be_focused()
                expect(control).to_have_attribute('aria-invalid', 'true')
                expect(page.locator('.ui-message-notice')).to_be_visible()
                assert page.get_by_role('form').bounding_box() == before_validation
                expect(page.get_by_label('Submitted values')).to_have_text('')
                control.click()
                popup = page.locator('.ui-select-dropdown[data-state=open]')
                expect(popup).to_be_visible()
                page.wait_for_timeout(200)
                geometry = check_geometry(control, popup)
                assert popup.locator('.ui-select-item-option-content').first.evaluate('node => getComputedStyle(node).whiteSpace') == 'nowrap'
                expect(popup.locator('.ui-select-item-option-content').first).to_have_attribute('title', 'Select a connection')
                surface = control.evaluate('''node => {
                  const inner = getComputedStyle(node.querySelector('.ui-select-selector'));
                  const arrow = node.querySelector('.ui-select-arrow').getBoundingClientRect();
                  return { border: getComputedStyle(node).borderTopWidth, innerBorder: inner.borderTopWidth, innerBackground: inner.backgroundColor, arrowInset: node.getBoundingClientRect().right - arrow.right, leadingInset: node.querySelector('.ui-select-selector').getBoundingClientRect().left - node.getBoundingClientRect().left };
                }''')
                assert surface['border'] == '1px' and surface['innerBorder'] == '0px', surface
                assert surface['innerBackground'] == 'rgba(0, 0, 0, 0)', surface
                assert abs(surface['arrowInset'] - surface['leadingInset']) < 1, surface
                page.screenshot(path=str(ARTIFACTS / f'{mode}-open.png'))
                page.get_by_role('option', name='Beta', exact=True).click()
                expect(control).to_contain_text('Beta')
                expect(page.get_by_label('Change count')).to_have_text('1')
                closing = page.locator('.ui-select-dropdown[data-state=closed]')
                assert closing.evaluate('node => getComputedStyle(node).pointerEvents') == 'none'
                assert closing.get_attribute('aria-hidden') == 'true'
                control.click()
                page.wait_for_timeout(220)
                expect(popup).to_be_visible()
                control.press('Escape')
                page.wait_for_timeout(200)
                expect(page.locator('.ui-select-dropdown')).to_have_count(0)
                page.get_by_role('button', name='Submit', exact=True).click()
                values = json.loads(page.get_by_label('Submitted values').inner_text())
                assert values == {'connection': 'b', 'default': 'b', 'numeric': '10'}, values
                numeric = page.get_by_role('combobox', name='Numeric value')
                numeric.click()
                page.get_by_role('option', name='20 / page', exact=True).click()
                expect(numeric).to_contain_text('10 / page')
                assert page.get_by_role('form').evaluate('form => new FormData(form).get("numeric")') == '10'
                default = page.get_by_role('combobox', name='Default value', exact=True)
                default.click()
                page.get_by_role('option', name='Alpha', exact=True).click()
                expect(default).to_contain_text('Alpha')
                page.get_by_role('button', name='Reset defaults').click()
                expect(default).to_contain_text('Beta')
                expect(page.locator('#connection')).to_have_value('b')
                assert page.get_by_role('form').evaluate('form => new FormData(form).get("connection")') == 'b'
                search = page.get_by_role('combobox', name='Searchable connection')
                search.fill('bet')
                expect(page.get_by_role('option')).to_have_count(1)
                search.press('Enter')
                expect(page.locator('.ui-select').filter(has=search)).to_contain_text('Beta')
                search.press('Escape')
                multi = page.get_by_role('combobox', name='Multiple connections')
                multi.fill('alp')
                page.get_by_role('option', name='Alpha', exact=True).click()
                multi.fill('bet')
                page.get_by_role('option', name='Beta', exact=True).click()
                multi.press('Escape')
                page.get_by_role('button', name='Remove Alpha', exact=True).click()
                expect(page.get_by_role('button', name='Remove Beta', exact=True)).to_be_visible()
                adaptive = page.get_by_role('combobox', name='Adaptive height')
                configured = page.get_by_role('combobox', name='Configured dimensions')
                compact = page.get_by_role('combobox', name='Compact dimensions')
                assert adaptive.bounding_box()['height'] > configured.bounding_box()['height']
                assert configured.bounding_box()['height'] == 40
                assert compact.bounding_box()['height'] == 24
                for sized, leading, trailing in [(configured, 12, 3), (compact, 5, 5)]:
                    insets = sized.evaluate('''node => {
                      const box = node.getBoundingClientRect(), style = getComputedStyle(node);
                      const arrow = node.querySelector('.ui-select-arrow').getBoundingClientRect();
                      return { leading: node.querySelector('.ui-select-selector').getBoundingClientRect().left - box.left - parseFloat(style.borderLeftWidth), trailing: box.right - arrow.right - parseFloat(style.borderRightWidth), gap: arrow.left - node.querySelector('.ui-select-selection-wrap').getBoundingClientRect().right, paddingEnd: parseFloat(style.paddingInlineEnd) };
                    }''')
                    assert abs(insets['leading'] - leading) < 1 and abs(insets['trailing'] - trailing) < 1, insets
                    assert abs(insets['paddingEnd'] - trailing) < 1 and insets['gap'] >= 6, insets
                compact.click()
                page.wait_for_timeout(200)
                check_geometry(compact, page.locator('.ui-select-dropdown[data-state=open]'))
                page.get_by_role('option', name='Beta', exact=True).click()
                expect(compact).to_contain_text('Beta')
                formatter = page.get_by_role('combobox', name='Compact formatter')
                assert formatter.bounding_box()['width'] < 140
                formatter.click()
                formatter_menu = page.locator('.ui-select-dropdown[data-state=open]')
                expect(formatter_menu).to_be_visible()
                page.wait_for_timeout(200)
                assert formatter_menu.bounding_box()['width'] == 140
                assert formatter_menu.locator('.ui-select-item-option-content').evaluate_all('nodes => nodes.every(node => node.scrollWidth <= node.clientWidth)')
                formatter.press('Escape')
                category = page.get_by_role('combobox', name='Filter connector category')
                category.click()
                page.wait_for_timeout(200)
                category_popup = page.locator('.ui-select-dropdown[data-state=open]')
                check_geometry(category, category_popup)
                assert category.bounding_box()['width'] == 224
                assert category_popup.locator('.ui-select-item-option-content').evaluate_all('nodes => nodes.every(node => node.scrollWidth <= node.clientWidth)')
                page.screenshot(path=str(ARTIFACTS / f'{mode}-category-wide.png'))
                category.press('Escape')
                actions = page.get_by_role('button', name='Actions', exact=True)
                actions.click()
                page.wait_for_timeout(200)
                outer = page.locator('.ui-dropdown[data-state=open]')
                assert outer.bounding_box()['width'] >= actions.bounding_box()['width']
                assert outer.locator('.ui-dropdown-menu').evaluate('node => getComputedStyle(node).borderTopWidth') == '0px'
                page.get_by_role('menuitem', name='Open dialog').click()
                dialog_control = page.get_by_role('combobox', name='Dialog connection')
                dialog_control.click()
                page.wait_for_timeout(200)
                check_geometry(dialog_control, page.locator('.ui-select-dropdown[data-state=open]'))
                dialog_control.press('Escape')
                expect(page.get_by_role('dialog')).to_be_visible()
                page.get_by_role('button', name='Close dialog', exact=True).click()
                footer = page.get_by_role('combobox', name='Footer page size')
                footer.click()
                page.wait_for_timeout(200)
                footer_menu = page.locator('.ui-select-dropdown[data-state=open]')
                anchor_box, menu_box = footer.bounding_box(), footer_menu.bounding_box()
                assert menu_box['height'] >= 90, menu_box
                assert abs(menu_box['x'] - anchor_box['x']) < 1 and abs(menu_box['width'] - anchor_box['width']) < 1
                assert abs(anchor_box['y'] - menu_box['y'] - menu_box['height'] - 4) < 1
                page.get_by_role('option', name='50 / page', exact=True).click()
                expect(footer).to_contain_text('50 / page')
                spacing = page.get_by_role('region', name='Page select spacing').locator('.ui-select').evaluate_all('''nodes => nodes.map(node => {
                  const box = node.getBoundingClientRect(), style = getComputedStyle(node);
                  const selector = node.querySelector('.ui-select-selector').getBoundingClientRect();
                  const arrow = node.querySelector('.ui-select-arrow').getBoundingClientRect();
                  return { name: node.getAttribute('aria-label') || node.querySelector('input')?.getAttribute('aria-label'),
                    leading: selector.left - box.left - parseFloat(style.borderLeftWidth),
                    trailing: box.right - arrow.right - parseFloat(style.borderRightWidth),
                    gap: arrow.left - node.querySelector('.ui-select-selection-wrap').getBoundingClientRect().right };
                })''')
                assert len(spacing) == 11, spacing
                for insets in spacing:
                    assert abs(insets['leading'] - insets['trailing']) < 1, insets
                    assert 0 <= insets['trailing'] <= 13 and insets['gap'] >= 6, insets
                assert not errors, errors
                results.append({'theme': mode, 'geometry': geometry, 'surface': surface, 'page_spacing': spacing, 'form_values': values, 'page_errors': errors})
                page.close()
            page = browser.new_page(viewport={'width': 390, 'height': 844}, reduced_motion='reduce')
            page.goto(url + '?theme=dark')
            control = page.get_by_role('combobox', name='Connection', exact=True)
            control.click()
            popup = page.locator('.ui-select-dropdown[data-state=open]')
            expect(popup).to_be_visible()
            check_geometry(control, popup)
            assert popup.evaluate('node => getComputedStyle(node).animationName') == 'none'
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            control.press('Escape')
            expect(page.locator('.ui-select-dropdown')).to_have_count(0)
            browser.close()
        (ARTIFACTS / 'report.json').write_text(json.dumps(results, indent=2))
        print(json.dumps({'themes': 2, 'responsive_width': 390, 'form_events': 'passed', 'alignment_and_single_surface': 'passed', 'motion_and_reduced_motion': 'passed'}))
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
