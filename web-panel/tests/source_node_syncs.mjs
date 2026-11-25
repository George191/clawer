import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

const nodes = value => !value || typeof value !== 'object' ? [] : Array.isArray(value) ? value.flatMap(nodes) : [value, ...nodes(value.props?.children)];
const text = value => value == null || typeof value === 'boolean' ? '' : typeof value !== 'object' ? String(value) : Array.isArray(value) ? value.map(text).join('') : text(value.props?.children);
const node = (name, kind = 'database') => ({ id: JSON.stringify(['mysql-source', kind, name]), connectionId: 'mysql-source', connectionName: 'Business MySQL', engine: 'mysql', kind, name, sourceUpdatedAt: '2026-01-01T00:00:00Z' });

async function mount({ overrides = {}, props = {}, paused = false } = {}) {
  const state = [], effects = [], callbacks = []; let cursor = 0, scheduled = [], tree;
  const session = { identity: 'user', authenticated: true, locked: false }, requests = [];
  const defaults = {
    fetchCurrentUserContext: async () => ({ user: { is_superuser: true } }),
    fetchDataSourceNodes: async () => ({ nodes: [node('sales'), node('finance')], hasMore: false }),
    fetchLayers: async () => [{ key: 'rds' }, { key: 'ts_ods', schemaName: 'ts_ods' }, { key: 'ts_meta' }],
  };
  const api = Object.fromEntries(Object.entries({ ...defaults, ...overrides }).map(([name, implementation]) => [name, (...args) => { requests.push({ name, args }); return implementation(...args); }]));
  const react = {
    useCallback(callback, deps) { const i = cursor++; if (!callbacks[i] || deps.some((value, j) => !Object.is(value, callbacks[i].deps[j]))) callbacks[i] = { callback, deps }; return callbacks[i].callback; },
    useState(initial) { const i = cursor++; if (!(i in state)) state[i] = typeof initial === 'function' ? initial() : initial; return [state[i], next => { state[i] = typeof next === 'function' ? next(state[i]) : next; }]; },
    useEffect(effect, deps) { const i = cursor++; if (!effects[i] || deps.some((value, j) => !Object.is(value, effects[i].deps[j]))) scheduled.push([i, effect, deps]); },
  };
  const jsx = (type, props, key) => ({ type, props: props || {}, key });
  const require = name => {
    if (name === 'react') return react;
    if (name === 'react/jsx-runtime') return { jsx, jsxs: jsx, Fragment: 'Fragment' };
    if (name === '@/components/ui') return { Button: 'Button', NativeSelect: 'NativeSelect' };
    if (name === '../../services/api') return api;
    if (name === '../../services/session') return { getSessionState: () => session };
    if (name === './SandboxMarkings') return { useSandboxIdentity: () => session.authenticated && !session.locked ? session.identity : '' };
    if (name === './LineageTransferPlans') return { default: 'LineageTransferPlans' };
    throw new Error('Unexpected dependency ' + name);
  };
  const code = ts.transpileModule(readFileSync(new URL('../src/pages/Pipeline/SourceNodeSyncs.tsx', import.meta.url), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  const exports = {}; vm.runInThisContext('(function(require,exports){' + code + '\n})')(require, exports);
  const entry = exports.default({ source: { id: 'mysql-source', name: 'Business MySQL', engine: 'mysql', updatedAt: '2026-01-01T00:00:00Z', pausedAt: paused ? '2026-01-02T00:00:00Z' : null }, ...props });
  const render = () => { cursor = 0; scheduled = []; tree = entry.type(entry.props); for (const [i, effect, deps] of scheduled) { effects[i]?.cleanup?.(); effects[i] = { deps, cleanup: effect() }; } };
  const ui = {
    requests, session, nodes: () => nodes(tree), text: () => text(tree), plan: () => nodes(tree).find(item => item.type === 'LineageTransferPlans'),
    async settle() { for (let i = 0; i < 3; i++) { await new Promise(resolve => setImmediate(resolve)); render(); } },
    select(value) { nodes(tree).find(item => item.type === 'NativeSelect').props.onChange({ target: { value } }); render(); },
    search(value) { nodes(tree).find(item => item.type === 'input' && item.props['aria-label'] === 'Search source storage scopes').props.onChange({ target: { value } }); render(); nodes(tree).find(item => item.type === 'form').props.onSubmit({ preventDefault() {} }); render(); },
    reportState(value) { this.plan().props.onStateChange(value); render(); },
    unmount() { effects.forEach(effect => effect?.cleanup?.()); },
  };
  render(); await ui.settle(); return ui;
}

test('source scope deep links select the exact MySQL node and preserve warehouse destination scopes', async () => {
  const ui = await mount({ props: { kind: 'database', scope: 'finance' } });
  assert.equal(ui.plan().props.sourceNode.name, 'finance');
  assert.equal(ui.plan().props.sourceNode.connectionId, 'mysql-source');
  assert.equal(ui.requests.find(item => item.name === 'fetchDataSourceNodes').args[2], 'finance');
  assert.equal(ui.plan().props.schema, 'finance'); assert.deepEqual(ui.plan().props.schemas, ['ts_rds', 'ts_ods']);
  ui.select(node('sales').id); assert.equal(ui.plan().props.sourceNode.name, 'sales'); ui.unmount();
});

test('missing requested scopes show an explicit warning and do not select a different node', async () => {
  const ui = await mount({ props: { kind: 'index', scope: 'finance' } });
  assert.match(ui.text(), /requested storage node is not visible/); assert.equal(ui.plan(), undefined); ui.unmount();
});

test('paused connections issue no catalog or source requests', async () => {
  const ui = await mount({ paused: true }); assert.equal(ui.requests.length, 0); assert.match(ui.text(), /connection is paused/); ui.unmount();
});

test('administrator verification precedes reading source nodes and destination schemas', async () => {
  const ui = await mount({ overrides: { fetchCurrentUserContext: async () => ({ user: { is_superuser: false } }) } });
  assert.deepEqual(ui.requests.map(item => item.name), ['fetchCurrentUserContext']); assert.match(ui.text(), /Only administrators/); ui.unmount();
});

test('source and destination failures are explicit and cannot open a misleading plan editor', async () => {
  const sourceFailure = await mount({ overrides: { fetchDataSourceNodes: async () => { throw { response: { data: { detail: 'This connector cannot enumerate storage scopes' } } }; } } });
  assert.match(sourceFailure.text(), /cannot enumerate storage scopes/); assert.equal(sourceFailure.plan(), undefined); sourceFailure.unmount();
  const targetFailure = await mount({ overrides: { fetchLayers: async () => { throw new Error('warehouse down'); } } });
  assert.match(targetFailure.text(), /Unable to load destination schemas/); assert.equal(targetFailure.plan(), undefined); targetFailure.unmount();
});

test('truncated node catalogs are disclosed', async () => {
  const ui = await mount({ overrides: { fetchDataSourceNodes: async () => ({ nodes: [node('sales')], hasMore: true }) } });
  assert.match(ui.text(), /Additional storage scopes are not listed/); ui.unmount();
});

test('source scope search reaches resources outside the initial catalog', async () => {
  const ui = await mount({ overrides: { fetchDataSourceNodes: async (_id, _signal, search) => ({ nodes: [node(search || 'sales')], hasMore: !search }) } });
  ui.search('database-151'); await ui.settle();
  assert.equal(ui.requests.filter(item => item.name === 'fetchDataSourceNodes').at(-1).args[2], 'database-151');
  assert.equal(ui.plan().props.sourceNode.name, 'database-151'); ui.unmount();
});

test('unsaved and busy transfer setup locks source selection and catalog refresh', async () => {
  const activity = [];
  const ui = await mount({ props: { onStateChange: state => activity.push(state) } });
  const original = ui.plan().props.sourceNode.id;
  ui.reportState({ busy: false, dirty: true });
  assert.equal(ui.nodes().find(item => item.type === 'NativeSelect').props.disabled, true);
  ui.select(node('finance').id); assert.equal(ui.plan().props.sourceNode.id, original);
  assert.match(ui.text(), /Save or cancel the open draft/);
  assert.deepEqual(activity.at(-1), { busy: false, dirty: true });
  ui.reportState({ busy: true, dirty: false });
  assert.match(ui.text(), /being saved or processed/);
  ui.reportState({ busy: false, dirty: false });
  ui.select(node('finance').id); assert.equal(ui.plan().props.sourceNode.name, 'finance');
  ui.unmount();
});
