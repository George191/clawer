import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const jsx = (type, props) => ({ type, props });
const nodes = tree => tree && typeof tree === 'object' ? [tree, ...[tree.props?.children].flat(Infinity).flatMap(nodes)] : [];
const text = value => Array.isArray(value) ? value.map(text).join('') : value?.props ? text(value.props.children) : value == null || value === false ? '' : String(value);
const navigation = {};
const navigationCode = ts.transpileModule(readFileSync(new URL('../src/services/explorerNavigation.ts', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
vm.runInThisContext('(function(exports){' + navigationCode + '\n})')(navigation);
const types = [
  ['mysql', 'MySQL', 3306, 'relational'], ['postgresql', 'PostgreSQL', 5432, 'relational'],
  ['s3', 'S3-compatible storage', 443, 'object_storage'], ['redis', 'Redis', 6379, 'key_value'],
  ['clickhouse', 'ClickHouse', 8443, 'relational'], ['mongodb', 'MongoDB', 27017, 'document'],
  ['dameng', 'Dameng', 5236, 'relational'], ['milvus', 'Milvus', 19530, 'vector'], ['elasticsearch', 'Elasticsearch', 9200, 'search'],
].map(([engine, name, port, category]) => ({ engine, name, port, category, driverAvailable: true, catalogAvailable: false,
  cdcSupported: ['postgresql', 'mysql', 'mongodb', 'milvus'].includes(engine),
  captureKind: engine === 's3' ? 'object_events' : engine === 'dameng' ? 'dmdrs' : ['postgresql', 'mysql', 'mongodb', 'milvus'].includes(engine) ? 'cdc' : null,
}));
const source = (engine = 'mysql', patch = {}) => ({ id: 'source-a', name: 'Synthetic source', engine,
  config: { host: 'db.synthetic.test', port: types.find(type => type.engine === engine).port, database: 'analytics', username: 'reader', auth: 'password', tls: true,
    ...(engine === 'mongodb' ? { authDatabase: 'admin' } : {}) },
  credentialsConfigured: true, catalogAvailable: false, updatedAt: '2026-01-01T00:00:00Z', testedAt: null, ...patch });
let instance = 0;

async function mount({ sources = [], selected = '', query = '', overrides = {}, warehouseFailed = false } = {}) {
  let cursor = 0, tree, params = new URLSearchParams(query), mounted = true;
  if (selected) params.set('sourceId', selected);
  const state = [], refs = [], effects = [], scheduled = [], notices = [], destroyed = [], errorHooks = [], requests = [];
  const session = { identity: 'synthetic-account', authenticated: true, locked: false }, id = 'data-connections-' + ++instance;
  const store = { sources: [...sources], closed: false, warehouseRetries: 0 };
  const props = {
    warehouse: warehouseFailed ? undefined : { id: 'warehouse', engine: 'postgresql', database: 'warehouse_fixture', connectionId: 'selected-business', name: 'Selected business source' }, warehouseFailed,
    onRetryWarehouse: () => store.warehouseRetries++, onCancel: () => { store.closed = true; },
  };
  const defaults = {
    fetchCurrentUserContext: async () => ({ user: { is_superuser: true } }),
    fetchDataConnectorTypes: async () => types,
    fetchDataSources: async () => [...store.sources],
    testDataSource: async () => ({ ok: true, message: 'Connected to synthetic source.', latencyMs: 17 }),
    createDataSource: async payload => { const saved = source(payload.engine, { name: payload.name, config: payload.config }); store.sources.push(saved); return saved; },
    updateDataSource: async (identifier, payload) => { const saved = source(payload.engine, { id: identifier, name: payload.name, config: payload.config }); store.sources = store.sources.map(row => row.id === identifier ? saved : row); return saved; },
    deleteDataSource: async identifier => { store.sources = store.sources.filter(row => row.id !== identifier); },
    fetchDataConnectionDependencies: async () => ({ dependencies: [], canDisconnect: true }),
  };
  const implementations = { ...defaults, ...overrides };
  const api = Object.fromEntries(Object.keys(defaults).map(name => [name, (...args) => { requests.push({ name, args }); return implementations[name](...args); }]));
  const message = Object.fromEntries(['success', 'error', 'loading'].map(kind => [kind, value => {
    const notice = { kind, value, closed: false }; notices.push(notice); return () => { notice.closed = true; };
  }]));
  message.destroy = key => { destroyed.push(key); notices.filter(notice => notice.value.key === key).forEach(notice => { notice.closed = true; }); };
  const react = {
    useState(initial) { const i = cursor++; if (!(i in state)) state[i] = typeof initial === 'function' ? initial() : initial; return [state[i], next => { state[i] = typeof next === 'function' ? next(state[i]) : next; }]; },
    useRef(initial) { return refs[cursor++] ||= { current: initial }; },
    useId() { cursor++; return id; },
    useEffect(effect, deps) { const i = cursor++; if (!effects[i] || deps.some((value, j) => !Object.is(value, effects[i].deps[j]))) scheduled.push([i, effect, deps]); },
  };
  const require = name => {
    if (name === 'react') return react;
    if (name === 'react/jsx-runtime') return { jsx, jsxs: jsx, Fragment: 'Fragment' };
    if (name === 'react-router-dom') return { Link: 'Link', useSearchParams: () => [params, next => { params = typeof next === 'function' ? next(params) : next; }] };
    if (name === '@/components/ui') return { Button: 'Button', Checkbox: 'Checkbox', Input: 'Input', Modal: 'Modal', Select: 'Select', NativeSelect: 'NativeSelect', Tabs: 'Tabs', message };
    if (name === '@/components/ui/icons') return new Proxy({}, { get: (_, key) => String(key) });
    if (name === '../../services/api') return api;
    if (name === '../../services/explorerNavigation') return navigation;
    if (name === '../../services/session') return { getSessionState: () => session };
    if (name === './useExplorerErrorToast') return { default: (...args) => errorHooks.push(args) };
    if (name.endsWith('.css')) return {};
    if (name.startsWith('./')) return { default: name.slice(2) };
    throw new Error('Unexpected dependency ' + name);
  };
  const code = ts.transpileModule(readFileSync(new URL('../src/pages/Pipeline/DataConnections.tsx', import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const exports = {};
  vm.runInThisContext('(function(require,exports,window,document){' + code + '\n})')(require, exports, { addEventListener() {}, removeEventListener() {} }, { getElementById: id => ({ focus() { store.focused = id; } }) });
  const render = () => {
    if (!mounted) return tree;
    cursor = 0; scheduled.length = 0; errorHooks.length = 0; tree = exports.default(props);
    for (const [i, effect, deps] of scheduled) { effects[i]?.cleanup?.(); effects[i] = { deps, cleanup: effect() }; }
    return tree;
  };
  const ui = {
    session, store, implementations, requests, notices, destroyed, errorHooks, render,
    nodes: () => nodes(tree),
    feedback: () => notices.filter(notice => notice.kind !== 'loading'),
    calls: name => requests.filter(request => request.name === name),
    button: name => nodes(tree).find(node => ['Button', 'button'].includes(node.type) && text(node.props.children) === name),
    field: name => nodes(tree).find(node => node.type === 'Input' && node.props['aria-label'] === name),
    confirmation: () => nodes(tree).find(node => node.type === 'Modal' && node.props.okText),
    inventory: () => nodes(tree).find(node => node.type === 'Modal' && node.props.title === 'Data Connections'),
    wizard: () => nodes(tree).find(node => node.type === 'Modal' && node.props.className.includes('ontology-connectors__wizard')),
    details: () => nodes(tree).find(node => node.type === 'Modal' && node.props.className.includes('ontology-connections-modal')),
    params: () => new URLSearchParams(params),
    click(name) { const button = this.button(name); assert.ok(button, 'Missing button: ' + name); assert.ok(!button.props.disabled, 'Disabled button: ' + name); const result = button.props.onClick(); render(); return result; },
    fill(name, value) { const field = this.field(name); assert.ok(field, 'Missing input: ' + name); field.props.onChange({ target: { value } }); render(); },
    select(name, value) {
      const field = nodes(tree).find(node => ['Select', 'NativeSelect'].includes(node.type) && node.props['aria-label'] === name);
      assert.ok(field, 'Missing select: ' + name);
      field.props.onChange(field.type === 'Select' ? value : { target: { value } }); render();
    },
    async settle() { for (let i = 0; i < 4; i++) { await new Promise(resolve => setImmediate(resolve)); render(); } },
    choose(engine = 'mysql', category) { this.click('New source'); if (category) this.select('Filter connector category', category); const card = nodes(tree).find(node => node.type === 'button' && node.props['aria-label'] === types.find(type => type.engine === engine).name); card.props.onClick(); render(); this.click('Continue'); },
    valid() { for (const [name, value] of Object.entries({ 'Connection name': 'Synthetic source', Host: 'db.synthetic.test', Database: 'analytics', Username: 'reader', Password: 'synthetic-password' })) this.fill(name, value); },
    noInlineErrors() { assert.equal(nodes(tree).filter(node => node.props?.role === 'alert').length, 0); },
    unmount() { mounted = false; effects.forEach(effect => effect?.cleanup?.()); },
  };
  render(); await ui.settle(); return ui;
}

test('the inventory starts with the connection list and has no standalone storage catalog card', async () => {
  const ui = await mount({ query: 'source=ts_ods&view=list' });
  assert.equal(ui.inventory().props.open, true);
  assert.equal(ui.nodes().find(node => node.props['aria-label'] === 'Workspace warehouse'), undefined);
  assert.equal(ui.nodes().find(node => node.type === 'h2' && text(node) === 'Storage catalog'), undefined);
  assert.ok(ui.nodes().find(node => node.type === 'strong' && text(node) === 'No external sources configured'));
  ui.click('New source');
  assert.equal(ui.inventory().props.open, false);
  assert.equal(ui.wizard().props.title, 'New source');
  ui.click('Cancel');
  assert.equal(ui.inventory().props.open, true);
  assert.equal(ui.params().get('view'), 'list');
  ui.inventory().props.onCancel(); assert.equal(ui.store.closed, true); ui.unmount();
});

test('connections have independent sync actions without a global ETL selector', async () => {
  const postgres = source('postgresql', { id: 'business-target', name: 'Business target' });
  const ui = await mount({ warehouseFailed: true, sources: [postgres, source('mysql', { id: 'mysql-source' }),
    source('postgresql', { id: 'paused-source', pausedAt: '2026-01-01T00:00:00Z' })] });
  assert.equal(ui.nodes().find(node => node.props['aria-label'] === 'ETL business connection'), undefined);
  assert.equal(ui.button('Use for ETL'), undefined);
  assert.equal(ui.nodes().filter(node => node.type === 'Button' && text(node) === 'Syncs').length, 3);
  ui.click('Syncs'); await ui.settle();
  assert.equal(ui.params().get('sourceId'), postgres.id);
  assert.equal(ui.params().get('sourcePanel'), 'sync');
  assert.equal(ui.nodes().find(node => node.type === 'SourceNodeSyncs').props.source.id, postgres.id);
});

test('node sync links retain their connection, scope and kind', async () => {
  const postgres = source('postgresql', { id: 'business-target' });
  const ui = await mount({ sources: [postgres], selected: postgres.id, query: 'sourcePanel=sync&sourceKind=schema&sourceSchema=public' });
  const syncs = ui.nodes().find(node => node.type === 'SourceNodeSyncs');
  assert.equal(syncs.props.source.id, postgres.id);
  assert.equal(syncs.props.kind, 'schema'); assert.equal(syncs.props.scope, 'public');
  ui.details().props.onCancel(); await ui.settle();
  assert.equal(ui.params().has('sourceKind'), false); assert.equal(ui.params().has('sourceSchema'), false);
});

test('catalog access is placed in the actual saved connection row without a duplicate connection', async () => {
  const saved = source('postgresql', { id: 'selected-business', name: 'Business PostgreSQL', config: { ...source('postgresql').config, database: 'warehouse_fixture' } });
  const ui = await mount({ query: 'source=ts_ods&view=list', sources: [saved, source('mysql', { id: 'other-source' })] });
  const inventory = ui.nodes().find(node => node.props['aria-label'] === 'Saved external sources');
  const rows = nodes(inventory).filter(node => node.type === 'tr' && nodes(node).some(child => child.type === 'td'));
  assert.equal(rows.length, 2);
  const row = rows.find(row => text(row).includes('Business PostgreSQL'));
  assert.match(text(row), /warehouse_fixture/);
  const links = nodes(inventory).filter(node => node.type === 'Link' && text(node).includes('Browse tables'));
  assert.equal(links.length, 1); assert.ok(nodes(row).includes(links[0]));
  assert.equal(links[0].props.to, '/ontology?source=ts_ods&view=list');
  assert.equal(nodes(inventory).filter(node => node.type === 'col').length, 5);
  ui.select('Filter sources by connector', 'mysql');
  assert.equal(ui.nodes().some(node => node.type === 'Link' && text(node).includes('Browse tables')), false);
  ui.unmount();
});

test('viewers retain a read-only catalog row without fetching or managing saved connections', async () => {
  const viewer = await mount({ query: 'source=ts_ods&view=list', overrides: { fetchCurrentUserContext: async () => ({ user: { is_superuser: false } }) } });
  assert.ok(viewer.nodes().find(node => node.type === 'code' && text(node) === 'warehouse_fixture'));
  assert.equal(viewer.calls('fetchDataSources').length, 0);
  assert.equal(viewer.calls('fetchDataConnectorTypes').length, 0);
  assert.equal(viewer.button('New source'), undefined);
  assert.equal(viewer.button('Syncs'), undefined); assert.equal(viewer.button('Disconnect'), undefined);
  const catalog = viewer.nodes().find(node => node.props['aria-label'] === 'Available catalog connection');
  assert.match(text(catalog), /Read-only catalog/);
  assert.equal(nodes(catalog).find(node => node.type === 'Link').props.to, '/ontology?source=ts_ods&view=list');
  viewer.unmount();
});

test('catalog errors stay compact and do not block independent connection management', async () => {
  const failing = await mount({ warehouseFailed: true, sources: [source('mysql')] });
  assert.ok(failing.nodes().find(node => node.props.role === 'status' && /Resource catalog access is unavailable/.test(text(node))));
  assert.equal(failing.nodes().some(node => node.type === 'Link' && /Browse tables/.test(text(node))), false);
  failing.click('Retry catalog');
  assert.equal(failing.store.warehouseRetries, 1);
  assert.ok(failing.button('New source'));
  assert.equal(failing.button('Syncs').props.disabled, false);
  assert.equal(failing.button('Disconnect').props.disabled, false);
  failing.unmount();
});

test('saving a new source continues into its details dialog while retaining the inventory', async () => {
  const id = '2e7a9733-daa3-4f04-a788-4fd9f2e2b818';
  const ui = await mount();
  ui.implementations.createDataSource = async payload => {
    const saved = source(payload.engine, { id, name: payload.name, config: payload.config });
    ui.store.sources.push(saved);
    return saved;
  };
  ui.choose(); ui.valid(); ui.click('Save source'); await ui.settle();
  assert.equal(ui.wizard(), undefined); assert.ok(ui.details());
  const linkedCatalog = ui.nodes().find(node => node.type === 'Link' && text(node.props.children).includes('View published object types'));
  assert.equal(linkedCatalog?.props.to, `/ontology?catalog=published&publishedSource=${id}`);
  assert.equal(ui.inventory().props.open, false);
  assert.ok(ui.nodes().find(node => node.type === 'section' && node.props['aria-label'] === 'Connections'));
  assert.ok(ui.button('Configure Continuous CDC'));
  assert.equal(ui.nodes().find(node => node.type === 'SourceCdc'), undefined);
  ui.click('Configure Continuous CDC');
  assert.ok(ui.nodes().find(node => node.type === 'SourceCdc'));
  ui.details().props.onCancel(); await ui.settle();
  assert.equal(ui.details(), undefined); assert.equal(ui.params().get('sourceId'), null);
  assert.equal(ui.inventory().props.open, true);
  assert.equal(ui.calls('createDataSource').length, 1); ui.unmount();
});

test('source capture configuration is opened on demand with the correct capture mode', async () => {
  for (const [engine, label, component] of [['mongodb', 'Continuous CDC', 'SourceCdc'], ['s3', 'Object events', 'SourceObjectEvents'], ['dameng', 'DMDRS CDC', 'SourceDamengCdc']]) {
    const saved = source(engine);
    const ui = await mount({ sources: [saved], selected: saved.id });
    assert.ok(ui.details()); assert.equal(ui.nodes().some(node => ['SourceCdc', 'SourceObjectEvents', 'SourceDamengCdc'].includes(node.type)), false);
    ui.click(label); assert.ok(ui.nodes().find(node => node.type === component));
    ui.click('Overview'); assert.equal(ui.nodes().find(node => node.type === component), undefined);
    ui.unmount();
  }
});

test('capture operations block dialog dismissal and switching sections until settled', async () => {
  const saved = source('mongodb');
  const ui = await mount({ sources: [saved], selected: saved.id }); ui.click('Continuous CDC');
  const capture = ui.nodes().find(node => node.type === 'SourceCdc');
  capture.props.onBusyChange(true); ui.render();
  assert.equal(ui.details().props.closable, false); assert.equal(ui.details().props.keyboard, false);
  assert.equal(ui.button('Overview').props.disabled, true);
  const linkedCatalog = ui.nodes().find(node => node.type === 'Link' && text(node.props.children).includes('View published object types'));
  let prevented = false;
  linkedCatalog.props.onClick({ preventDefault() { prevented = true; } });
  assert.ok(prevented && linkedCatalog.props['aria-disabled']);
  ui.details().props.onCancel(); ui.render(); assert.ok(ui.details());
  capture.props.onBusyChange(false); ui.render(); ui.details().props.onCancel(); await ui.settle();
  assert.equal(ui.details(), undefined); ui.unmount();
});

test('editing swaps the details dialog for the existing wizard and cancellation returns to details', async () => {
  const saved = source('mysql'); const ui = await mount({ sources: [saved], selected: saved.id });
  ui.click('Edit connection'); assert.equal(ui.details(), undefined); assert.ok(ui.wizard());
  ui.click('Cancel'); await ui.settle(); assert.equal(ui.wizard(), undefined); assert.ok(ui.details());
  assert.equal(ui.calls('updateDataSource').length, 0); ui.unmount();
});

test('editing a PostgreSQL name retains credentials and the saved revision', async () => {
  const saved = source('postgresql'); const ui = await mount({ sources: [saved], selected: saved.id });
  ui.click('Edit name');
  assert.equal(ui.field('Connection name').props.autoFocus, true);
  ui.fill('Connection name', 'Renamed source'); ui.click('Save source'); await ui.settle();
  const payload = ui.calls('updateDataSource')[0].args[1];
  assert.equal(payload.name, 'Renamed source');
  assert.equal(payload.expectedUpdatedAt, saved.updatedAt);
  assert.deepEqual(payload.credentials, {});
  assert.equal(ui.store.sources[0].name, 'Renamed source');
  ui.unmount();
});

test('unsupported connectors and paused sources do not expose capture controls', async () => {
  for (const saved of [source('elasticsearch'), source('clickhouse'), source('redis'), source('postgresql', { pausedAt: '2026-11-24T00:00:00Z' })]) {
    const ui = await mount({ sources: [saved], selected: saved.id });
    assert.ok(ui.details()); assert.equal(ui.button('Continuous CDC'), undefined);
    assert.equal(ui.button('Object events'), undefined); assert.equal(ui.button('DMDRS CDC'), undefined);
    ui.unmount();
  }
});

test('catalog table selection opens batch sync configuration with the selected table', async () => {
  const saved = source('postgresql', { catalogAvailable: true });
  const ui = await mount({ sources: [saved], selected: saved.id, overrides: {
    fetchDataConnectorTypes: async () => types.map(item => ({ ...item, catalogAvailable: item.engine === 'postgresql' })),
  } });
  ui.click('Source catalog');
  const catalog = ui.nodes().find(node => typeof node.type === 'function' && node.type.name === 'SourceCatalog');
  assert.ok(catalog);
  const selection = { schema: 'public', table: 'events' };
  catalog.props.onSetUpSync(selection); ui.render();
  assert.equal(ui.nodes().find(node => node.type === 'SourceBatchSyncs').props.selection, selection);
  assert.equal(ui.button('Syncs').props['aria-selected'], true); ui.unmount();
});

test('existing schedule links select the sync section inside the details dialog', async () => {
  const saved = source('postgresql', { pausedAt: '2026-11-24T00:00:00Z' });
  const ui = await mount({ sources: [saved], selected: saved.id, query: 'batchSync=scheduled-plan' });
  assert.ok(ui.details());
  assert.equal(ui.nodes().find(node => node.type === 'SourceBatchSyncs').props.selectedSchedule, 'scheduled-plan');
  assert.equal(ui.button('Syncs').props['aria-selected'], true); ui.unmount();
});

test('unsaved sync setup requires confirmation before changing tabs or closing details', async () => {
  const saved = source('mysql');
  const ui = await mount({ sources: [saved], selected: saved.id, query: 'sourcePanel=sync' });
  ui.nodes().find(node => node.type === 'SourceNodeSyncs').props.onStateChange({ busy: false, dirty: true }); ui.render();
  assert.equal(ui.button('Disconnect connection').props.disabled, true);
  ui.click('Overview');
  assert.equal(ui.button('Syncs').props['aria-selected'], true);
  assert.equal(ui.confirmation().props.title, 'Discard unsaved setup?');
  ui.confirmation().props.onCancel(); ui.render();
  assert.equal(ui.button('Syncs').props['aria-selected'], true);
  ui.details().props.onCancel(); ui.render(); assert.ok(ui.confirmation());
  ui.confirmation().props.onCancel(); ui.render();
  ui.click('Overview'); await ui.confirmation().props.onOk(); await ui.settle();
  assert.equal(ui.button('Overview').props['aria-selected'], true); ui.unmount();
});

test('sync mutations block closing and tab changes until the child request settles', async () => {
  const saved = source('mysql');
  const ui = await mount({ sources: [saved], selected: saved.id, query: 'sourcePanel=sync' });
  const report = ui.nodes().find(node => node.type === 'SourceNodeSyncs').props.onStateChange;
  report({ busy: true, dirty: true }); ui.render();
  assert.equal(ui.details().props.closable, false); assert.equal(ui.details().props.keyboard, false);
  assert.equal(ui.button('Overview').props.disabled, true);
  ui.details().props.onCancel(); ui.render(); assert.equal(ui.confirmation(), undefined);
  assert.equal(ui.params().get('sourceId'), saved.id);
  report({ busy: false, dirty: false }); ui.render(); ui.click('Overview');
  assert.equal(ui.button('Overview').props['aria-selected'], true); ui.unmount();
});

test('unfinished CDC settings are retained when a tab change is cancelled', async () => {
  const saved = source('mysql'); const ui = await mount({ sources: [saved], selected: saved.id });
  ui.click('Continuous CDC');
  ui.nodes().find(node => node.type === 'SourceCdc').props.onDirtyChange(true); ui.render();
  ui.click('Syncs'); assert.ok(ui.confirmation());
  ui.confirmation().props.onCancel(); ui.render();
  assert.equal(ui.button('Continuous CDC').props['aria-selected'], true); ui.unmount();
});

test('connection pause cannot discard an unfinished sync setup', async () => {
  const saved = source('postgresql', { pauseAvailable: true });
  const ui = await mount({ sources: [saved], selected: saved.id, query: 'sourcePanel=sync' });
  ui.nodes().find(node => node.type === 'SourceNodeSyncs').props.onStateChange({ busy: false, dirty: true }); ui.render();
  assert.equal(ui.button('Pause connection').props.disabled, true);
  ui.button('Pause connection').props.onClick(); ui.render();
  assert.equal(ui.confirmation(), undefined); assert.equal(ui.calls('setDataSourcePaused').length, 0);
  ui.unmount();
});

test('source detail tabs expose the active panel and support arrow, Home and End keys', async () => {
  const saved = source('postgresql', { catalogAvailable: true });
  const ui = await mount({ sources: [saved], selected: saved.id, overrides: {
    fetchDataConnectorTypes: async () => types.map(item => ({ ...item, catalogAvailable: item.engine === 'postgresql' })),
  } });
  const key = value => { ui.nodes().find(node => node.props.role === 'tablist').props.onKeyDown({ key: value, preventDefault() {} }); ui.render(); };
  key('ArrowRight'); assert.equal(ui.button('Source catalog').props['aria-selected'], true);
  assert.ok(ui.store.focused.endsWith('-tab-catalog'));
  key('End'); assert.equal(ui.button('Continuous CDC').props['aria-selected'], true);
  key('ArrowLeft'); assert.equal(ui.button('Syncs').props['aria-selected'], true);
  key('Home'); assert.equal(ui.button('Overview').props['aria-selected'], true);
  const activeTab = ui.button('Overview');
  assert.equal(ui.nodes().find(node => node.props.role === 'tabpanel').props['aria-labelledby'], activeTab.props.id);
  ui.unmount();
});

test('repeated invalid connection submissions each show a toast without calling a probe or adding an error row', async () => {
  const ui = await mount(); ui.choose();
  ui.click('Test connection'); ui.click('Test connection'); ui.click('Save source');
  assert.equal(ui.feedback().length, 3);
  for (const notice of ui.feedback()) {
    assert.equal(notice.kind, 'error'); assert.equal(notice.value.className, 'explorer-toast');
    assert.equal(notice.value.content, 'Enter a connection name, host and port between 1 and 65535.');
    assert.equal(notice.value.key, ui.feedback()[0].value.key);
  }
  assert.equal(ui.calls('testDataSource').length, 0); assert.equal(ui.calls('createDataSource').length, 0);
  ui.noInlineErrors(); assert.ok(ui.wizard()); ui.unmount();
});

test('connection tests report successful probes, rejected probes and request failures through toast only', async () => {
  const ui = await mount(); ui.choose(); ui.valid();
  ui.click('Test connection'); await ui.settle();
  assert.equal(ui.feedback().at(-1).kind, 'success'); assert.equal(ui.feedback().at(-1).value.description, '17 ms');
  ui.implementations.testDataSource = async () => ({ ok: false, message: 'Read access rejected.', latencyMs: 9 });
  ui.click('Test connection'); await ui.settle();
  assert.equal(ui.feedback().at(-1).kind, 'error'); assert.equal(ui.feedback().at(-1).value.content, 'Read access rejected.');
  ui.implementations.testDataSource = async () => { throw new Error('Probe service unavailable.'); };
  ui.click('Test connection'); await ui.settle();
  assert.equal(ui.feedback().at(-1).value.content, 'Probe service unavailable.');
  assert.equal(ui.calls('testDataSource').length, 3); ui.noInlineErrors(); ui.unmount();
});

test('save and confirmed delete notify through toast, and a failed delete keeps the confirmation available for retry', async () => {
  const ui = await mount(); ui.choose(); ui.valid();
  ui.click('Save source'); await ui.settle();
  assert.equal(ui.calls('createDataSource').length, 1); assert.equal(ui.wizard(), undefined);
  assert.equal(ui.feedback().at(-1).kind, 'success'); assert.match(ui.feedback().at(-1).value.content, /Source saved/);
  ui.click('Disconnect connection'); await ui.settle(); assert.equal(ui.calls('deleteDataSource').length, 0);
  ui.implementations.deleteDataSource = async () => { throw new Error('Deletion rejected.'); };
  await ui.confirmation().props.onOk(); await ui.settle();
  assert.ok(ui.confirmation()); assert.equal(ui.feedback().at(-1).value.content, 'Deletion rejected.'); ui.noInlineErrors();
  ui.implementations.deleteDataSource = async identifier => { ui.store.sources = ui.store.sources.filter(row => row.id !== identifier); };
  await ui.confirmation().props.onOk(); await ui.settle();
  assert.equal(ui.confirmation(), undefined); assert.equal(ui.calls('deleteDataSource').length, 2);
  assert.equal(ui.feedback().at(-1).kind, 'success'); assert.equal(ui.feedback().at(-1).value.content, 'Connection disconnected.'); ui.unmount();
});

test('disconnect shows dependencies and cannot delete a connection with defined flows', async () => {
  const saved = source('mysql');
  const ui = await mount({ sources: [saved], selected: saved.id, overrides: {
    fetchDataConnectionDependencies: async () => ({ canDisconnect: false, dependencies: [{ type: 'batch_sync', id: 'sync-a', name: 'Orders sync', message: 'Reassign this sync first.' }] }),
  } });
  ui.click('Disconnect connection'); await ui.settle();
  assert.match(text(ui.confirmation()), /Orders sync/);
  assert.equal(ui.confirmation().props.okButtonProps.disabled, true);
  await ui.confirmation().props.onOk(); await ui.settle();
  assert.equal(ui.calls('deleteDataSource').length, 0);
  ui.click('Review syncs'); await ui.settle();
  assert.equal(ui.params().get('sourceId'), saved.id); assert.equal(ui.params().get('sourcePanel'), 'sync');
});

test('disconnect fails closed if dependency lookup fails and refreshes dependencies on a delete race', async () => {
  const saved = source('mysql');
  const ui = await mount({ sources: [saved], selected: saved.id, overrides: {
    fetchDataConnectionDependencies: async () => { throw new Error('Dependency check unavailable'); },
  } });
  ui.click('Disconnect connection'); await ui.settle();
  assert.equal(ui.confirmation(), undefined); assert.equal(ui.calls('deleteDataSource').length, 0);
  ui.implementations.fetchDataConnectionDependencies = async () => ({ dependencies: [], canDisconnect: true });
  ui.click('Disconnect connection'); await ui.settle();
  ui.implementations.deleteDataSource = async () => { throw { response: { data: { detail: { code: 'connection_in_use', message: 'A flow was added', dependencies: [{ type: 'object_type', id: 'object-a', name: 'Orders', message: 'Change this object source first' }] } } } }; };
  await ui.confirmation().props.onOk(); await ui.settle();
  assert.match(text(ui.confirmation()), /Orders/); assert.equal(ui.confirmation().props.okButtonProps.disabled, true);
  assert.equal(ui.details().props.open, true);
});

test('a PostgreSQL revision conflict clears the entered password and locks saving until the saved source is reloaded', async () => {
  const saved = source('postgresql');
  const ui = await mount({ sources: [saved], selected: saved.id,
    overrides: { updateDataSource: async () => { throw { response: { status: 409, data: { detail: { code: 'source_revision_changed' } } } }; } } });
  ui.click('Edit connection'); ui.fill('Password', 'replacement-secret'); ui.click('Save source'); await ui.settle();
  assert.equal(ui.field('Password').props.value, ''); assert.equal(ui.button('Save source').props.disabled, true);
  assert.equal(ui.nodes().find(node => node.type === 'fieldset').props.disabled, true);
  assert.match(ui.feedback().at(-1).value.content, /password was cleared/);
  assert.equal(ui.calls('updateDataSource')[0].args[1].expectedUpdatedAt, saved.updatedAt);
  ui.noInlineErrors(); ui.click('Reload saved source'); await ui.settle();
  assert.equal(ui.wizard(), undefined); assert.ok(ui.calls('fetchDataSources').length >= 2);
  ui.click('Edit connection'); assert.ok(ui.button('Test connection'));
  assert.equal(ui.nodes().find(node => node.type === 'fieldset').props.disabled, false); ui.unmount();
});

test('locked sessions and unmounted pages suppress late probe notifications and clean only their own toast key', async () => {
  for (const mode of ['locked', 'unmounted']) {
    let resolve, reject;
    const ui = await mount({ overrides: { testDataSource: () => new Promise((done, fail) => { resolve = done; reject = fail; }) } });
    ui.choose(); ui.valid(); ui.click('Test connection');
    if (mode === 'locked') { ui.session.locked = true; reject(new Error('Late private failure.')); }
    else { ui.unmount(); resolve({ ok: true, message: 'Late private result.', latencyMs: 2 }); }
    await ui.settle(); assert.equal(ui.feedback().length, 0);
    assert.ok(ui.destroyed.length > 0); assert.ok(ui.destroyed.every(key => typeof key === 'string'));
    ui.unmount();
  }
});

test('registry loading errors retain blocked controls and toast retry, without an inline API alert', async () => {
  let failing = true;
  const ui = await mount({ overrides: { fetchDataSources: async () => { if (failing) throw new Error('Registry unavailable.'); return []; } } });
  const [failure, title, retry] = ui.errorHooks.find(([, title]) => title === 'Unable to load data sources');
  assert.equal(failure, 'Registry unavailable.'); assert.equal(title, 'Unable to load data sources');
  assert.equal(ui.button('New source').props.disabled, true); ui.noInlineErrors();
  failing = false; retry(); await ui.settle();
  assert.equal(ui.calls('fetchDataSources').length, 2); assert.equal(ui.button('New source').props.disabled, false);
  assert.equal(ui.errorHooks.find(([, title]) => title === 'Unable to load data sources')[0], ''); ui.unmount();
});

test('hosting providers pair every name with its icon in the picker, saved details and editor', async () => {
  const labels = {
    self_hosted: 'Self-hosted', aws: 'Amazon Web Services', azure: 'Microsoft Azure', gcp: 'Google Cloud',
    aliyun: 'Alibaba Cloud', tencent: 'Tencent Cloud', huawei: 'Huawei Cloud', oracle_cloud: 'Oracle Cloud', other: 'Other cloud provider',
  };
  const assertLabel = (label, provider) => {
    assert.equal(text(label), labels[provider]);
    assert.deepEqual(nodes(label).filter(n => n.type === 'HostingProviderIcon').map(n => n.props.provider), [provider]);
  };
  const ui = await mount(); ui.choose();
  const picker = () => ui.nodes().find(n => n.type === 'Select' && n.props['aria-label'] === 'Hosting provider').props;
  assert.deepEqual(picker().options.map(option => option.value).sort(), Object.keys(labels).sort());
  for (const option of picker().options) assertLabel(option.label, option.value);
  ui.valid(); ui.select('Hosting provider', 'tencent');
  ui.click('Save source'); await ui.settle();
  const detail = ui.nodes().find(n => n.type === 'dd' && text(n.props.children) === labels.tencent);
  assert.ok(detail); assertLabel(detail, 'tencent');
  ui.click('Edit connection');
  const { options, value } = picker(); assert.equal(value, 'tencent');
  assertLabel(options.find(option => option.value === value).label, 'tencent');
  ui.unmount();
});

test('S3 bucket, signing region and provider survive saving and editing while the secret stays write-only', async () => {
  const ui = await mount(); ui.choose('s3', 'object_storage');
  assert.equal(ui.field('Port').props.value, '443'); assert.equal(ui.field('Database'), undefined);
  ui.fill('Connection name', 'Reports bucket'); ui.fill('Host', 's3.us-east-1.amazonaws.com');
  ui.select('Hosting provider', 'aws'); ui.fill('Access key ID', 'synthetic-reader'); ui.fill('Secret access key', 'synthetic-secret');
  ui.click('Test connection'); assert.equal(ui.calls('testDataSource').length, 0);
  ui.fill('Bucket', 'reports'); ui.fill('Signing region', 'us-east-1');
  assert.equal(ui.nodes().find(node => node.props['aria-label'] === 'Bucket addressing').props.value, 'path');
  ui.select('Bucket addressing', 'virtual');
  ui.click('Save source'); await ui.settle();
  const { config, credentials } = ui.calls('createDataSource')[0].args[0];
  assert.deepEqual(config, { host: 's3.us-east-1.amazonaws.com', port: 443, username: 'synthetic-reader', auth: 'access_key', database: '', tls: true, bucket: 'reports', region: 'us-east-1', provider: 'aws', addressingStyle: 'virtual' });
  assert.deepEqual(credentials, { secretAccessKey: 'synthetic-secret' });
  const summary = ui.nodes().filter(n => n.type === 'dd').map(n => text(n.props.children));
  for (const expected of ['Amazon Web Services', 'reports', 'us-east-1', 'Virtual-hosted style']) assert.ok(summary.includes(expected));
  ui.click('Edit connection'); assert.equal(ui.field('Secret access key').props.value, '');
  assert.equal(ui.nodes().find(node => node.props['aria-label'] === 'Bucket addressing').props.value, 'virtual');
  assert.equal(ui.field('Bucket').props.value, 'reports'); assert.equal(ui.field('Signing region').props.value, 'us-east-1');
  ui.fill('Connection name', 'Renamed bucket'); ui.click('Test connection'); await ui.settle();
  assert.deepEqual(ui.calls('testDataSource')[0].args[0].credentials, {});
  ui.fill('Bucket', 'different-bucket'); ui.click('Test connection'); assert.equal(ui.calls('testDataSource').length, 1);
  ui.unmount();
});

test('Redis validates the database index and ClickHouse starts with its HTTP TLS port and default database', async () => {
  for (const engine of ['redis', 'clickhouse']) {
    const ui = await mount(); ui.choose(engine);
    ui.select('Hosting provider', 'aliyun'); ui.fill('Connection name', 'Hosted source'); ui.fill('Host', 'cloud.synthetic.test'); ui.fill('Password', 'synthetic-password');
    if (engine === 'redis') {
      assert.equal(ui.field('Database index').props.value, '0'); assert.equal(ui.field('Username').props.value, 'default');
      for (const invalid of ['-1', '01', '1.5']) {
        ui.fill('Database index', invalid); ui.click('Test connection'); assert.equal(ui.calls('testDataSource').length, 0);
      }
      ui.fill('Database index', '2');
    } else { assert.equal(ui.field('Database').props.value, 'default'); ui.fill('Username', 'reader'); }
    ui.click('Test connection'); await ui.settle();
    const { config } = ui.calls('testDataSource')[0].args[0];
    assert.equal(config.port, engine === 'redis' ? 6379 : 8443); assert.equal(config.provider, 'aliyun'); assert.equal(config.tls, true); ui.unmount();
  }
});

test('editing an existing database shows self-hosted and changing providers requires fresh credentials', async () => {
  const saved = source(); const ui = await mount({ sources: [saved], selected: saved.id });
  ui.click('Edit connection');
  const provider = ui.nodes().find(node => node.props['aria-label'] === 'Hosting provider');
  assert.equal(provider.props.value, 'self_hosted');
  ui.select('Hosting provider', 'azure'); ui.click('Save source'); assert.equal(ui.calls('updateDataSource').length, 0);
  ui.select('Hosting provider', 'self_hosted'); ui.fill('Connection name', 'Renamed'); ui.click('Save source'); await ui.settle();
  const payload = ui.calls('updateDataSource')[0].args[1];
  assert.equal(payload.config.provider, undefined); assert.deepEqual(payload.credentials, {}); ui.unmount();
});

test('MongoDB requires a target database and tests and saves its own authentication database', async () => {
  const ui = await mount(); ui.choose('mongodb', 'document');
  assert.equal(ui.field('Port').props.value, '27017');
  assert.equal(ui.field('Authentication database').props.value, 'admin');
  ui.valid(); ui.fill('Database', '');
  ui.click('Test connection');
  assert.equal(ui.calls('testDataSource').length, 0);
  assert.match(ui.feedback().at(-1).value.content, /database or service name/);

  ui.fill('Database', 'documents');
  ui.fill('Authentication database', '');
  ui.click('Test connection'); await ui.settle();
  const tested = ui.calls('testDataSource')[0].args[0];
  assert.equal(tested.engine, 'mongodb');
  assert.equal(tested.config.database, 'documents');
  assert.equal(tested.config.authDatabase, 'admin');
  assert.equal(tested.config.port, 27017);
  assert.equal(tested.config.tls, true);

  ui.fill('Authentication database', ' accounts ');
  ui.click('Save source'); await ui.settle();
  assert.equal(ui.calls('createDataSource')[0].args[0].config.authDatabase, 'accounts');
  ui.unmount();
});

test('changing the MongoDB authentication database requires re-entering credentials', async () => {
  const saved = source('mongodb');
  const ui = await mount({ sources: [saved], selected: saved.id });
  ui.click('Edit connection');
  assert.equal(ui.field('Authentication database').props.value, 'admin');
  ui.fill('Authentication database', 'accounts');
  ui.click('Test connection');
  assert.equal(ui.calls('testDataSource').length, 0);
  assert.match(ui.feedback().at(-1).value.content, /Enter credentials again/);
  ui.fill('Authentication database', '');
  ui.click('Test connection'); await ui.settle();
  assert.equal(ui.calls('testDataSource')[0].args[0].config.authDatabase, 'admin');
  assert.deepEqual(ui.calls('testDataSource')[0].args[0].credentials, {});
  ui.unmount();
});

test('MySQL and MongoDB use distinct vector marks rather than database initials', () => {
  const code = ts.transpileModule(readFileSync(new URL('../src/pages/Pipeline/ConnectorMark.tsx', import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const exports = {};
  vm.runInThisContext('(function(require,exports){' + code + '\n})')(name =>
    name === 'react/jsx-runtime' ? { jsx, jsxs: jsx } : { default: 'PostgreSQL' }, exports);
  const marks = ['mysql', 'mongodb'].map(engine => {
    const mark = exports.default({ engine });
    assert.equal(mark.props['aria-hidden'], 'true');
    assert.match(mark.props.className, new RegExp('is-' + engine));
    const glyph = mark.props.children.type();
    assert.equal(glyph.type, 'svg');
    return nodes(glyph).find(node => node.type === 'path').props.d;
  });
  assert.notEqual(...marks);
});
