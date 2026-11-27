"""Browser regression for loading alignment and table viewport placement."""
import argparse
from ipaddress import ip_address
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

parser = argparse.ArgumentParser()
parser.add_argument("base")
parser.add_argument("--channel", default="msedge")
parser.add_argument("--screenshot-prefix")
args = parser.parse_args()
if not ip_address(urlsplit(args.base).hostname).is_loopback:
    raise ValueError("Expected a loopback frontend URL")

with sync_playwright() as p:
    browser = p.chromium.launch(channel=args.channel, args=["--no-proxy-server"])
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.route("**/src/main.tsx*", lambda route: route.abort())
    page.route("https://fonts.googleapis.com/**", lambda route: route.abort())
    page.route("**/api/**", lambda route: route.fulfill(json={}))
    pending_shares = []
    page.route("**/api/v1/scout/shares/**", lambda route: pending_shares.append(route))
    page.goto(args.base, wait_until="domcontentloaded")
    page.evaluate("""async () => {
      const React = (await import('/node_modules/.vite/deps/react.js')).default;
      const { createRoot } = (await import('/node_modules/.vite/deps/react-dom_client.js')).default;
      const { ConfigProvider, theme, Table, PageLoading } = await import('/src/components/ui/index.tsx');
      const { default: ProjectRouter } = await import('/src/ProjectRouter.tsx');
      const { default: ManagementWorkspace } = await import('/src/components/ManagementWorkspace/index.tsx');
      const { default: SharedChatPage } = await import('/src/pages/AICollect/SharedChatPage.tsx');
      const { default: Preview } = await import('/src/pages/Pipeline/ObjectTypePreview.tsx');
      const { default: Selection } = await import('/src/pages/Pipeline/ExplorerSelection.tsx');
      const { default: CatalogLoading } = await import('/src/pages/Pipeline/OntologyCatalogSkeleton.tsx');
      for (const path of ['styles/global.css', 'pages/ProductWorkspace/workspace.css',
        'pages/Pipeline/ontology-frame.css', 'pages/Pipeline/foundry-workbench-tokens.css',
        'pages/Pipeline/ontology-workspace.css', 'pages/Pipeline/ontology-explorer.css', 'pages/Pipeline/published-results.css', 'pages/Dashboard/style.css']) {
        await import('/src/' + path);
      }
      document.querySelector('.sp-auth-boot').remove();
      const root = createRoot(document.getElementById('root')), h = React.createElement;
      const columns = Array.from({ length: 12 }, (_, i) => ({ title: 'Column ' + i, dataIndex: 'c' + i, width: 160 }));
      window.renderLoading = (mode, dark = true, loading = true) => {
        let content;
        const region = child => h('main', { style: { height: 420, display: 'flex', flexDirection: 'column', background: 'var(--ui-bg)' } }, child);
        if (mode === 'fullscreen') content = h(PageLoading, { fullscreen: true });
        else if (mode === 'warehouse-catalog') content = region(h('div', { className: 'flow-ontology' },
          h('div', { className: 'flow-ontology__body' }, h('aside', { className: 'flow-ontology__sidebar' }, 'Table groups'),
            h('main', { className: 'flow-ontology__main' }, h(CatalogLoading, { label: 'Loading warehouse tables' }),
              h('footer', { className: 'flow-ontology__catalog-footer' }, 'Updating catalog…')))));
        else if (mode === 'published-catalog') content = region(h('div', { className: 'flow-ontology' },
          h('div', { className: 'flow-ontology__body' }, h('main', { className: 'flow-ontology__published flow-foundry-workbench flow-published-catalog' },
            h('header', { className: 'flow-ontology__published-header' }, 'Published object types'),
            h('section', { className: 'flow-create-section' }, h(CatalogLoading, { label: 'Loading published object types' }))))));
        else if (mode === 'shared') {
          history.replaceState(null, '', '/share/scout/loading-test');
          content = h(SharedChatPage);
        }
        else if (mode === 'page') content = region(h(PageLoading));
        else if (mode === 'dashboard') content = region(h('div', { className: 'dashboard-v2 dashboard-v2--root' }, h(PageLoading)));
        else if (mode === 'management') {
          history.replaceState(null, '', '/organization');
          content = h(ProjectRouter, null, region(h(ManagementWorkspace, null, h(PageLoading))));
        }
        else if (mode === 'preview') content = h(Preview, { open: true, id: 'ods.ods_article', name: 'ods_article',
          loading: true, icon: null, onClose() {}, onExplore() {}, properties: [], allProperties: false,
          onPropertiesToggle() {}, linkedTypes: {} });
        else if (mode === 'selection') content = h('div', { className: 'flow-console flow-console--ontology flow-foundry-workbench' },
          h('div', { className: 'flow-schema-workbench', style: { height: 420, display: 'flex', flexDirection: 'column' } },
            h(Selection, { schema: 'ods', table: 'ods_article', rows: [], columns: columns.map(c => c.dataIndex),
              dataColumns: columns, rowKey: (_, i) => String(i), rowIdentity: r => r, rowVersion: () => undefined,
              loading, locked: false })));
        else content = h('div', { className: mode === 'explorer' ? 'flow-console flow-console--ontology flow-foundry-workbench' : '' },
          h('div', { className: mode === 'explorer' ? 'flow-schema-workbench' : '' },
            h('div', { className: mode === 'explorer' ? 'flow-inspector-body' : '', style: { height: 420, display: 'flex', flexDirection: 'column' } },
              h(Table, { className: mode === 'explorer' ? 'flow-data-table' : '',
                style: { height: '100%' }, columns, scroll: { x: 1920 }, pagination: false,
                loading: { spinning: loading, tip: 'Loading object data…' },
                dataSource: mode === 'populated' ? Array.from({ length: 30 }, (_, i) => ({ key: i, c0: 'Row ' + i })) : [] }))));
        root.render(h(ConfigProvider, { theme: { algorithm: dark ? theme.darkAlgorithm : theme.defaultAlgorithm } }, content));
      };
    }""")

    def centered(selector):
        result = page.locator(selector).evaluate("""e => {
          const orb = e.querySelector('canvas').getBoundingClientRect();
          const text = e.querySelector('span').getBoundingClientRect();
          const row = e.getBoundingClientRect(), css = getComputedStyle(e);
          return { delta: Math.abs(orb.y + orb.height / 2 - text.y - text.height / 2),
            width: row.width, background: css.backgroundColor, border: css.borderTopWidth };
        }""")
        assert result["delta"] < 0.6, result
        assert result["background"] == "rgba(0, 0, 0, 0)", result
        assert result["border"] == "0px", result

    for width, dark in [(3200, True), (3200, False), (1280, True), (1280, False), (375, True), (375, False)]:
        page.set_viewport_size({"width": width, "height": 900})
        for mode in ["fullscreen", "page", "dashboard", "management", "shared", "warehouse-catalog", "published-catalog"]:
            page.evaluate("([mode, dark]) => renderLoading(mode, dark)", [mode, dark])
            try:
                page.locator(".ui-page-loading .ui-loading-indicator").wait_for(timeout=5000)
            except Exception as failure:
                raise AssertionError((mode, width, dark, errors)) from failure
            page.evaluate("document.fonts.ready")
            centered(".ui-page-loading .ui-loading-indicator")
            placement = page.locator(".ui-page-loading").evaluate("""e => {
              const row = e.querySelector('.ui-loading-indicator').getBoundingClientRect();
              const orb = e.querySelector('canvas').getBoundingClientRect();
              const parent = e.classList.contains('ui-page-loading-fullscreen')
                ? { x:0, y:0, width:innerWidth, height:innerHeight }
                : e.parentElement.getBoundingClientRect();
              return { x: Math.abs(row.x+row.width/2-parent.x-parent.width/2),
                y: Math.abs(row.y+row.height/2-parent.y-parent.height/2), orb:orb.width,
                fits: e.scrollWidth <= e.clientWidth, background: getComputedStyle(e).backgroundColor };
            }""")
            assert placement["x"] < 1 and placement["y"] < 1, (mode, placement)
            assert placement["orb"] == 64 and placement["fits"], (mode, placement)
            if mode in ["warehouse-catalog", "published-catalog"]:
                assert page.locator(".flow-ontology__assets, .flow-ontology__asset").count() == 0
                assert page.locator(".flow-catalog-skeleton").count() == 0
                assert page.locator(".flow-ontology__catalog-footer:visible").count() == 0
                if dark and width == 3200 and mode == "warehouse-catalog" and args.screenshot_prefix:
                    page.screenshot(path=args.screenshot_prefix + "-catalog.png")
            if mode in ["fullscreen", "shared"]:
                expected = "rgb(23, 27, 33)" if dark else "rgb(243, 245, 247)"
                assert placement["background"] == expected, (mode, placement)
            if dark and width == 1280 and mode == "fullscreen" and args.screenshot_prefix:
                page.screenshot(path=args.screenshot_prefix + "-fullscreen.png")
            if mode == "shared":
                assert pending_shares
                for route in pending_shares:
                    route.fulfill(status=404, json={"detail": "Shared chat unavailable in layout test"})
                pending_shares.clear()
                page.locator(".codex-shared-page [role='alert']").wait_for()
                assert page.locator(".ui-page-loading").count() == 0
        page.evaluate("dark => renderLoading('preview', dark)", dark)
        page.locator(".flow-ontology__muted .ui-loading-indicator").wait_for()
        centered(".flow-ontology__muted .ui-loading-indicator")
        if dark and width == 1280 and args.screenshot_prefix:
            page.screenshot(path=args.screenshot_prefix + "-preview.png")
        for mode in ["table", "explorer", "selection", "populated"]:
            page.evaluate("([mode, dark]) => renderLoading(mode, dark)", [mode, dark])
            indicator = page.locator(".ui-table-loading .ui-loading-indicator")
            indicator.wait_for()
            centered(".ui-table-loading .ui-loading-indicator")
            for scroll in [0, 700]:
                page.locator(".ui-table-content").evaluate("(e, x) => {e.scrollLeft=x;e.scrollTop=120}", scroll)
                delta = indicator.evaluate("""e => {
                  const a = e.getBoundingClientRect();
                  const b = e.closest('.ui-spin-nested-loading').getBoundingClientRect();
                  const table = e.closest('.ui-table-wrapper').getBoundingClientRect();
                  const css = getComputedStyle(e.closest('.ui-table-loading'));
                  return { x: Math.abs(a.x+a.width/2-b.x-b.width/2),
                    y: Math.abs(a.y+a.height/2-b.y-b.height/2), height: b.height, tableHeight: table.height,
                    background: css.backgroundColor, border: css.borderTopWidth };
                }""")
                assert delta["x"] < 1 and delta["y"] < 1, (mode, delta)
                assert delta["height"] >= 140 and abs(delta["height"] - delta["tableHeight"]) < 3, (mode, delta)
                assert delta["background"] == "rgba(0, 0, 0, 0)" and delta["border"] == "0px", delta
            assert page.locator(".flow-schema-state").count() == 0
            if dark and width == 1280 and mode == "explorer" and args.screenshot_prefix:
                page.screenshot(path=args.screenshot_prefix + "-table.png")
            page.evaluate("([mode, dark]) => renderLoading(mode, dark, false)", [mode, dark])
            page.locator(".ui-table-loading").wait_for(state="detached")
            if mode == "populated":
                assert page.locator(".ui-table-tbody>tr").count() == 30
                assert page.get_by_text("Row 29", exact=True).count() == 1
            else:
                assert page.locator(".ui-table-tbody").inner_text().strip() == "No data"
    assert not errors, errors
    browser.close()
print("PASS: full-page/content/management/dashboard centers; preview/table alignment; mobile, scrolling and dark/light")
