import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const jsx = (type, props) => ({ type, props });
const nodes = tree => tree && typeof tree === 'object' ? [tree, ...[tree.props?.children].flat(Infinity).flatMap(nodes)] : [];
const text = value => Array.isArray(value) ? value.map(text).join('') : value?.props ? text(value.props.children) : value == null || value === false ? '' : String(value);
const types = ['mysql', 'postgresql'].map(engine => ({ engine, name: engine === 'mysql' ? 'MySQL' : 'PostgreSQL', port: engine === 'mysql' ? 3306 : 5432, category: 'relational', driverAvailable: true, catalogAvailable: false }));
const source = (engine = 'mysql', patch = {}) => ({ id: 'source-a', name: 'Synthetic source', engine,
  config: { host: 'db.synthetic.test', port: engine === 'mysql' ? 3306 : 5432, database: 'analytics', username: 'reader', auth: 'password', tls: true },
  credentialsConfigured: true, catalogAvailable: false, updatedAt: '2026-01-01T00:00:00Z', testedAt: null, ...patch });
let instance = 0;

async function mount({ sources = [], selected = '', overrides = {} } = {}) {
  let cursor = 0, tree, params = new URLSearchParams(selected ? { sourceId: selected } : {}), mounted = true;
  const state = [], refs = [], effects = [], scheduled = [], notices = [], destroyed = [], errorHooks = [], requests = [];
  const session = { identity: 'synthetic-account', authenticated: true, locked: false }, id = 'data-connections-' + ++instance;
  const store = { sources: [...sources] };
  const defaults = {
    fetchCurrentUserContext: async () => ({ user: { is_superuser: true } }),
    fetchDataConnectorTypes: async () => types,
    fetchDataSources: async () => [...store.sources],
    testDataSource: async () => ({ ok: true, message: 'Connected to synthetic source.', latencyMs: 17 }),
    createDataSource: async payload => { const saved = source(payload.engine, { name: payload.name, config: payload.config }); store.sources.push(saved); return saved; },
    updateDataSource: async (identifier, payload) => { const saved = source(payload.engine, { id: identifier, name: payload.name, config: payload.config }); store.sources = store.sources.map(row => row.id === identifier ? saved : row); return saved; },
    deleteDataSource: async identifier => { store.sources = store.sources.filter(row => row.id !== identifier); },
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
    if (name === '@/components/ui') return { Button: 'Button', Checkbox: 'Checkbox', Input: 'Input', Modal: 'Modal', Select: 'Select', message };
    if (name === '@/components/ui/icons') return new Proxy({}, { get: (_, key) => String(key) });
    if (name === '../../services/api') return api;
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
  vm.runInThisContext('(function(require,exports,window){' + code + '\n})')(require, exports, { addEventListener() {}, removeEventListener() {} });
  const render = () => {
    if (!mounted) return tree;
    cursor = 0; scheduled.length = 0; errorHooks.length = 0; tree = exports.default();
    for (const [i, effect, deps] of scheduled) { effects[i]?.cleanup?.(); effects[i] = { deps, cleanup: effect() }; }
    return tree;
  };
  const ui = {
    session, store, implementations, requests, notices, destroyed, errorHooks, render,
    nodes: () => nodes(tree),
    feedback: () => notices.filter(notice => notice.kind !== 'loading'),
    calls: name => requests.filter(request => request.name === name),
    button: name => nodes(tree).find(node => node.type === 'Button' && text(node.props.children) === name),
    field: name => nodes(tree).find(node => node.type === 'Input' && node.props['aria-label'] === name),
    confirmation: () => nodes(tree).find(node => node.type === 'Modal' && node.props.okText),
    wizard: () => nodes(tree).find(node => node.type === 'Modal' && node.props.className.includes('ontology-connectors__wizard')),
    click(name) { const button = this.button(name); assert.ok(button, 'Missing button: ' + name); assert.ok(!button.props.disabled, 'Disabled button: ' + name); const result = button.props.onClick(); render(); return result; },
    fill(name, value) { const field = this.field(name); assert.ok(field, 'Missing input: ' + name); field.props.onChange({ target: { value } }); render(); },
    async settle() { for (let i = 0; i < 4; i++) { await new Promise(resolve => setImmediate(resolve)); render(); } },
    choose(engine = 'mysql') { this.click('New source'); const card = nodes(tree).find(node => node.type === 'button' && node.props['aria-label'] === types.find(type => type.engine === engine).name); card.props.onClick(); render(); this.click('Continue'); },
    valid() { for (const [name, value] of Object.entries({ 'Connection name': 'Synthetic source', Host: 'db.synthetic.test', Database: 'analytics', Username: 'reader', Password: 'synthetic-password' })) this.fill(name, value); },
    noInlineErrors() { assert.equal(nodes(tree).filter(node => node.props?.role === 'alert').length, 0); },
    unmount() { mounted = false; effects.forEach(effect => effect?.cleanup?.()); },
  };
  render(); await ui.settle(); return ui;
}

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
  ui.click('Delete source'); assert.equal(ui.calls('deleteDataSource').length, 0);
  ui.implementations.deleteDataSource = async () => { throw new Error('Deletion rejected.'); };
  await ui.confirmation().props.onOk(); await ui.settle();
  assert.ok(ui.confirmation()); assert.equal(ui.feedback().at(-1).value.content, 'Deletion rejected.'); ui.noInlineErrors();
  ui.implementations.deleteDataSource = async identifier => { ui.store.sources = ui.store.sources.filter(row => row.id !== identifier); };
  await ui.confirmation().props.onOk(); await ui.settle();
  assert.equal(ui.confirmation(), undefined); assert.equal(ui.calls('deleteDataSource').length, 2);
  assert.equal(ui.feedback().at(-1).kind, 'success'); assert.equal(ui.feedback().at(-1).value.content, 'Data source deleted.'); ui.unmount();
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
