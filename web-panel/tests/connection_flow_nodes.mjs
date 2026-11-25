import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

const nodes = value => !value || typeof value !== 'object' ? [] : Array.isArray(value) ? value.flatMap(nodes) : [value, ...nodes(value.props?.children)];
const text = value => value == null || typeof value === 'boolean' ? '' : typeof value !== 'object' ? String(value) : Array.isArray(value) ? value.map(text).join('') : text(value.props?.children);
const source = (id, extra = {}) => ({ id, name: id, engine: 'postgresql', config: { database: 'business' }, catalogAvailable: true, updatedAt: '2026-01-01T00:00:00Z', ...extra });
const storageNode = (id, name = 'public', kind = 'schema', engine = 'postgresql') => ({ id: JSON.stringify([id, kind, name]), connectionId: id, connectionName: id, name, kind, engine });

function setup(overrides = {}) {
  let active;
  const session = { identity: 'reviewer', authenticated: true, locked: false };
  const requests = [];
  const defaults = {
    fetchCurrentUserContext: async () => ({ user: { is_superuser: true } }),
    fetchDataSources: async () => [source('first'), source('second')],
    fetchDataSourceNodes: async id => ({ nodes: [storageNode(id)], hasMore: false }),
  };
  const implementations = { ...defaults, ...overrides };
  const api = Object.fromEntries(Object.keys(defaults).map(name => [name, (...args) => { requests.push({ name, args }); return implementations[name](...args); }]));
  const react = {
    useState(initial) { const target = active, i = target.cursor++; if (!(i in target.state)) target.state[i] = typeof initial === 'function' ? initial() : initial; return [target.state[i], next => { target.state[i] = typeof next === 'function' ? next(target.state[i]) : next; }]; },
    useRef(initial) { const target = active, i = target.cursor++; return target.refs[i] ||= { current: initial }; },
    useEffect(effect, deps) { const target = active, i = target.cursor++; if (!target.effects[i] || deps.some((value, j) => !Object.is(value, target.effects[i].deps[j]))) target.scheduled.push([i, effect, deps]); },
  };
  const jsx = (type, props, key) => ({ type, props: props || {}, key });
  const require = name => {
    if (name === 'react') return react;
    if (name === 'react/jsx-runtime') return { jsx, jsxs: jsx, Fragment: 'Fragment' };
    if (name === 'react-router-dom') return { Link: 'Link' };
    if (name === '../../services/api') return api;
    if (name === '../../services/session') return { getSessionState: () => session };
    if (name === './SandboxMarkings') return { useSandboxIdentity: () => session.authenticated && !session.locked ? session.identity : '' };
    if (name === './ConnectorMark') return { default: 'ConnectorMark' };
    if (name === './LineageTransferPlans') return { default: 'LineageTransferPlans' };
    if (name.endsWith('.css')) return {};
    throw new Error('Unexpected dependency ' + name);
  };
  const code = ts.transpileModule(readFileSync(new URL('../src/pages/Pipeline/ConnectionFlowNodes.tsx', import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const exports = {};
  vm.runInThisContext('(function(require,exports){' + code + '\n})')(require, exports);
  const mount = (component, props) => {
    const target = { state: [], refs: [], effects: [], scheduled: [], cursor: 0, tree: null };
    const render = () => {
      active = target; target.cursor = 0; target.scheduled = []; target.tree = component(props);
      for (const [i, effect, deps] of target.scheduled) { target.effects[i]?.cleanup?.(); target.effects[i] = { deps, cleanup: effect() }; }
    };
    const ui = {
      nodes: () => nodes(target.tree), text: () => text(target.tree),
      async settle() { for (let i = 0; i < 3; i++) { await new Promise(resolve => setImmediate(resolve)); render(); } },
      click(label) { const button = this.nodes().find(node => node.type === 'button' && text(node) === label); assert.ok(button, `Missing ${label}`); assert.ok(!button.props.disabled); button.props.onClick(); render(); },
      unmount() { target.effects.forEach(effect => effect?.cleanup?.()); },
    };
    render(); return ui;
  };
  const entry = exports.default({ version: 0, schemas: ['ts_rds'] });
  return { session, requests, mount, workspace: () => mount(entry.type, entry.props), entry: () => exports.default({ version: 0, schemas: ['ts_rds'] }) };
}

test('connection inventory is requested only after administrator access is verified', async () => {
  const env = setup({ fetchCurrentUserContext: async () => ({ user: { is_superuser: false } }) });
  const ui = env.workspace(); await ui.settle();
  assert.match(ui.text(), /available to administrators/);
  assert.equal(env.requests.filter(row => row.name === 'fetchDataSources').length, 0);
  ui.unmount();
});

test('same scope names on different connections retain distinct nodes and transfer identities', async () => {
  const env = setup(); const ui = env.workspace(); await ui.settle();
  const cards = ui.nodes().filter(node => typeof node.type === 'function');
  assert.equal(cards.length, 2);
  const first = env.mount(cards[0].type, cards[0].props), second = env.mount(cards[1].type, cards[1].props);
  assert.equal(env.requests.filter(row => row.name === 'fetchDataSourceNodes').length, 0);
  first.click('Browse nodes'); second.click('Browse nodes'); await Promise.all([first.settle(), second.settle()]);
  const scopeNode = card => card.nodes().find(node => node.type === 'button' && node.props['data-node-id']);
  const a = scopeNode(first), b = scopeNode(second);
  assert.notEqual(a.props['data-node-id'], b.props['data-node-id']);
  a.props.onClick();
  await ui.settle();
  let plan = ui.nodes().find(node => node.type === 'LineageTransferPlans');
  assert.equal(plan.props.sourceNode.connectionId, 'first');
  assert.deepEqual(plan.props.schemas, ['ts_rds']);
  const link = ui.nodes().find(node => node.type === 'Link' && text(node) === 'Manage syncs for this node');
  assert.equal(new URL(link.props.to, 'https://local').searchParams.get('sourceId'), 'first');
  assert.equal(new URL(link.props.to, 'https://local').searchParams.get('sourceSchema'), 'public');
  assert.equal(new URL(link.props.to, 'https://local').searchParams.get('sourceKind'), 'schema');
  assert.equal(new URL(link.props.to, 'https://local').searchParams.get('sourcePanel'), 'sync');
  b.props.onClick(); await ui.settle(); plan = ui.nodes().find(node => node.type === 'LineageTransferPlans');
  assert.equal(plan.props.sourceNode.connectionId, 'second');
  assert.deepEqual(env.requests.filter(row => row.name === 'fetchDataSourceNodes').map(row => row.args[0]), ['first', 'second']);
  first.unmount(); second.unmount(); ui.unmount();
});

test('discovery errors preserve the connection node and allow an explicit retry', async () => {
  let calls = 0;
  const env = setup({ fetchDataSourceNodes: async id => { if (!calls++) throw new Error('unavailable'); return { nodes: [storageNode(id, 'sales / reporting')], hasMore: true }; } });
  const ui = env.workspace(); await ui.settle(); const card = ui.nodes().find(node => typeof node.type === 'function');
  const sourceUi = env.mount(card.type, card.props); sourceUi.click('Browse nodes'); await sourceUi.settle();
  assert.ok(sourceUi.nodes().find(node => node.props.role === 'alert'));
  assert.ok(sourceUi.nodes().find(node => node.props['aria-label'] === 'Manage first'));
  sourceUi.click('Retry nodes'); await sourceUi.settle();
  const node = sourceUi.nodes().find(node => node.props['aria-label'] === 'Select first: schema sales / reporting');
  node.props.onClick(); await ui.settle();
  const link = ui.nodes().find(node => node.type === 'Link' && text(node) === 'Manage syncs for this node');
  assert.equal(new URL(link.props.to, 'https://local').searchParams.get('sourceSchema'), 'sales / reporting');
  assert.match(sourceUi.text(), /More storage scopes are available/);
  sourceUi.unmount(); ui.unmount();
});

test('disconnected sources cannot start schema catalog reads', async () => {
  const env = setup({ fetchDataSources: async () => [source('paused', { pausedAt: '2026-01-01T00:00:00Z' })] });
  const ui = env.workspace(); await ui.settle(); const card = ui.nodes().find(node => typeof node.type === 'function');
  const sourceUi = env.mount(card.type, card.props);
  assert.match(sourceUi.text(), /Paused/);
  assert.equal(sourceUi.nodes().filter(node => node.type === 'button').length, 0);
  assert.equal(env.requests.filter(row => row.name === 'fetchDataSourceNodes').length, 0);
  sourceUi.unmount(); ui.unmount();
});

test('locked sessions stop exposing the workspace and discard pending schema responses', async () => {
  let resolve;
  const env = setup({ fetchDataSourceNodes: () => new Promise(done => { resolve = done; }) });
  const ui = env.workspace(); await ui.settle(); const card = ui.nodes().find(node => typeof node.type === 'function');
  const sourceUi = env.mount(card.type, card.props); sourceUi.click('Browse nodes');
  env.session.locked = true; resolve({ nodes: [storageNode('first', 'secret_schema')], hasMore: false }); await sourceUi.settle();
  assert.equal(env.entry(), null); assert.doesNotMatch(sourceUi.text(), /secret_schema/);
  sourceUi.unmount();
  assert.equal(env.requests.find(row => row.name === 'fetchDataSourceNodes').args[1].aborted, true);
  ui.unmount();
});

test('MySQL databases and Elasticsearch indices use the same real node discovery path', async () => {
  const env = setup({
    fetchDataSources: async () => [source('mysql', { engine: 'mysql', catalogAvailable: false }), source('es', { engine: 'elasticsearch', catalogAvailable: false })],
    fetchDataSourceNodes: async id => ({ nodes: id === 'mysql' ? [storageNode(id, 'sales', 'database', 'mysql'), storageNode(id, 'finance', 'database', 'mysql')] : [storageNode(id, 'documents-2026', 'index', 'elasticsearch')], hasMore: false }),
  });
  const ui = env.workspace(); await ui.settle(); const cards = ui.nodes().filter(node => typeof node.type === 'function');
  const mysql = env.mount(cards[0].type, cards[0].props), es = env.mount(cards[1].type, cards[1].props);
  mysql.click('Browse nodes'); es.click('Browse nodes'); await Promise.all([mysql.settle(), es.settle()]);
  assert.equal(mysql.nodes().filter(node => node.type === 'button' && node.props['data-node-id']).length, 2);
  assert.ok(es.nodes().find(node => node.props['aria-label'] === 'Select es: index documents-2026'));
  const esNode = es.nodes().find(node => node.props['aria-label'] === 'Select es: index documents-2026'); esNode.props.onClick(); await ui.settle();
  const plan = ui.nodes().find(node => node.type === 'LineageTransferPlans');
  assert.equal(plan.props.sourceNode.kind, 'index'); assert.equal(plan.props.sourceNode.engine, 'elasticsearch');
  mysql.unmount(); es.unmount(); ui.unmount();
});
