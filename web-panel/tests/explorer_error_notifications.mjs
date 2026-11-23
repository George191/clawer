import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const jsx = (type, props) => ({ type, props });
const nodes = tree => tree && typeof tree === 'object' ? [tree, ...[tree.props?.children].flat(Infinity).flatMap(nodes)] : [];
function hooks() {
  let cursor = 0;
  const states = [], refs = [], effects = [], pending = [];
  return {
    react: {
      useState(initial) { const i = cursor++; if (!(i in states)) states[i] = initial; return [states[i], next => { states[i] = typeof next === 'function' ? next(states[i]) : next; }]; },
      useRef(initial) { return refs[cursor++] ||= { current: initial }; },
      useEffect(effect, deps) { const i = cursor++; if (!effects[i] || deps.some((value, j) => !Object.is(value, effects[i].deps[j]))) pending.push([i, effect, deps]); },
    },
    render(component) { cursor = 0; pending.length = 0; const tree = component(); for (const [i, effect, deps] of pending) { effects[i]?.cleanup?.(); effects[i] = { deps, cleanup: effect() }; } return tree; },
    unmount() { effects.forEach(effect => effect?.cleanup?.()); },
  };
}
function load(path, require, fetch = () => { throw new Error('Unexpected request'); }, expose = 'exports.default') {
  const code = ts.transpileModule(readFileSync(new URL('../src/pages/' + path, import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const exports = {};
  return vm.runInThisContext('(function(require,exports,fetch,window){' + code + '\nreturn ' + expose + ';})')(require, exports, fetch,
    { location: { href: 'https://app.example.test/ontology/explorer', origin: 'https://app.example.test' }, setInterval, clearInterval });
}
function notifications() {
  const calls = [];
  const message = Object.fromEntries(['error', 'success', 'info'].map(kind => [kind, value => {
    const call = { kind, value, closed: false }; calls.push(call); return () => { call.closed = true; };
  }]));
  return { calls, message };
}
function toast() {
  const runtime = hooks(), notices = notifications(), session = { identity: 'account-a', authenticated: true, locked: false };
  const useToast = load('Pipeline/useExplorerErrorToast.tsx', name => {
    if (name === 'react') return runtime.react;
    if (name === 'react/jsx-runtime') return { jsx, jsxs: jsx, Fragment: 'Fragment' };
    if (name === '@/components/ui') return { message: notices.message };
    if (name === '../../services/session') return { getSessionState: () => session };
    throw new Error('Unexpected dependency ' + name);
  });
  return { ...notices, session, render: (error, retry) => runtime.render(() => useToast(error, 'Query failed', retry)), unmount: runtime.unmount };
}

test('request error toast does not repeat on rerender and Retry uses the latest callback', () => {
  const ui = toast(); let retried = '';
  ui.render('Service unavailable', () => { retried = 'old'; });
  ui.render('Service unavailable', () => { retried = 'current'; });
  assert.equal(ui.calls.length, 1);
  assert.equal(ui.calls[0].value.className, 'explorer-toast');
  assert.equal(ui.calls[0].value.duration, 5);
  nodes(ui.calls[0].value.description).find(node => node.type === 'a').props.onClick({ preventDefault() {} });
  assert.equal(retried, 'current');
  ui.render(''); assert.equal(ui.calls[0].closed, true);
  ui.render('Service unavailable'); assert.equal(ui.calls.length, 2);
  ui.unmount(); assert.equal(ui.calls[1].closed, true);
});

test('old request errors close without replaying into another account and stale Retry is ignored', () => {
  const ui = toast(); let retries = 0;
  ui.render('Private error', () => retries++);
  const link = nodes(ui.calls[0].value.description).find(node => node.type === 'a');
  ui.session.identity = 'account-b';
  link.props.onClick({ preventDefault() {} }); assert.equal(retries, 0);
  ui.render('Private error', () => retries++);
  assert.equal(ui.calls.length, 1); assert.equal(ui.calls[0].closed, true);
  ui.render(''); ui.session.locked = true; ui.render('Another failure');
  assert.equal(ui.calls.length, 1);
});

function auth(file, fetch, initial = {}) {
  const runtime = hooks(), notices = notifications(), saved = [], routes = [];
  const session = { identity: '', authenticated: false, locked: false, ...initial };
  const Form = Object.assign(function Form() {}, { Item: 'Form.Item', useForm: () => [{ setFieldsValue() {} }] });
  const component = load('Login/' + file + '.tsx', name => {
    if (name === 'react') return runtime.react;
    if (name === 'react/jsx-runtime') return { jsx, jsxs: jsx, Fragment: 'Fragment' };
    if (name === '@/components/ui') return { App: { useApp: () => ({ message: notices.message }) }, message: notices.message,
      Form, Button: 'Button', Input: Object.assign(function Input() {}, { Password: 'Password' }), Checkbox: 'Checkbox', Typography: { Title: 'Title', Text: 'Text', Paragraph: 'Paragraph' } };
    if (name === '@/components/ui/icons') return new Proxy({}, { get: (_, key) => String(key) });
    if (name === 'react-router-dom') return { Link: 'Link', useNavigate: () => (...args) => routes.push(args), useSearchParams: () => [new URLSearchParams()] };
    if (name === '../../services/session') return { getSessionState: () => session, getRefreshToken: () => session.refresh,
      clearSession: () => { session.authenticated = false; session.identity = ''; }, unlockSession: () => true,
      saveSession: (...args) => { saved.push(args); session.identity = 'signed-in'; session.authenticated = true; } };
    if (name === '../../services/endpoints') return { apiUrl: value => value };
    if (name.endsWith('.css')) return {};
    throw new Error('Unexpected dependency ' + name);
  }, fetch);
  let tree = runtime.render(component);
  return { ...notices, saved, routes, session, unmount: runtime.unmount,
    submit: values => nodes(tree).find(node => node.type === Form).props.onFinish(values),
    render: () => (tree = runtime.render(component)),
  };
}

test('failed sign-in uses one standard toast and keeps the form free of an API alert row', async () => {
  const ui = auth('index', async () => ({ ok: false, status: 401, json: async () => ({ detail: 'Incorrect password' }) }));
  await ui.submit({ account: 'synthetic@example.test', password: 'wrong' });
  assert.equal(ui.calls.length, 1); assert.equal(ui.calls[0].kind, 'error');
  assert.equal(ui.calls[0].value.className, 'explorer-toast');
  assert.match(ui.calls[0].value.description, /Incorrect account or password/);
  assert.equal(ui.saved.length, 0); assert.equal(ui.routes.length, 0);
  assert.equal(nodes(ui.render()).some(node => node.type === 'Alert' || node.props?.role === 'alert'), false);
});

test('unmounted and superseded sign-in failures do not produce notifications or persist sessions', async () => {
  for (const dispose of [ui => ui.unmount(), ui => { ui.session.identity = 'another-account'; }]) {
    let reject, signal;
    const ui = auth('index', (_url, options) => { signal = options.signal; return new Promise((_resolve, fail) => { reject = fail; }); });
    const pending = ui.submit({ account: 'synthetic@example.test', password: 'synthetic' });
    dispose(ui); reject(new TypeError('Network interrupted')); await pending;
    assert.equal(ui.calls.length, 0); assert.equal(ui.saved.length, 0);
    if (!ui.session.identity) assert.equal(signal.aborted, true);
  }
});

test('successful sign-in retains its existing session and navigation behavior', async () => {
  const ui = auth('index', async () => ({ ok: true, json: async () => ({ access_token: 'synthetic-access', refresh_token: 'synthetic-refresh' }) }));
  await ui.submit({ account: 'synthetic@example.test', password: 'synthetic' });
  assert.deepEqual(ui.saved, [['synthetic-access', 'synthetic-refresh']]);
  assert.deepEqual(ui.routes, [['/', { replace: true }]]);
  assert.equal(ui.calls[0].kind, 'success'); assert.equal(ui.calls[0].value.className, 'explorer-toast');
});

test('unlock and password recovery API failures use the same toast contract', async () => {
  const response = async () => ({ ok: false, status: 400, json: async () => ({ detail: 'Incorrect password' }) });
  const locked = auth('LockScreen', response, { identity: 'account-a', authenticated: true, locked: true, refresh: 'synthetic-refresh' });
  await locked.submit({ password: 'wrong' });
  assert.equal(locked.calls.length, 1); assert.equal(locked.calls[0].value.className, 'explorer-toast');
  assert.match(locked.calls[0].value.description, /still locked/);
  assert.equal(locked.session.locked, true);
  const recovery = auth('ResetPassword', response);
  await recovery.submit({ email: 'synthetic@example.test' });
  assert.equal(recovery.calls.length, 1); assert.equal(recovery.calls[0].value.className, 'explorer-toast');
  assert.equal(recovery.routes.length, 0);
});

function sharedView(file, props, api = {}, expose = 'exports.default') {
  const runtime = hooks(), errors = [], session = { identity: 'account-a', authenticated: true, locked: false };
  const context = { source: { kind: 'backed', schema: 'ts_ods', table: 'media' }, config: {
    bucket: 'fixtures', baseUrl: 'https://files.example.test', origins: ['https://files.example.test'],
  } };
  const component = load('Pipeline/' + file + '.tsx', name => {
    if (name === 'react') return { ...runtime.react, createContext: () => ({ Provider: 'StorageProvider' }), useContext: () => context };
    if (name === 'react/jsx-runtime') return { jsx, jsxs: jsx, Fragment: 'Fragment' };
    if (name === './useExplorerErrorToast') return { default: (...args) => errors.push(args) };
    if (name === '../../services/session') return { getSessionState: () => session };
    if (name === '../../services/api') return api;
    if (name === '../../services/explorerLayouts') return { readLayouts: async () => ({ personal: { layout: null }, default: { layout: null } }), layoutPath: () => '/layout' };
    if (name === './ExplorerValue') return { ExplorerValueProvider: 'ValueProvider' };
    if (name === './ExplorerRelationshipsView') return { default: 'RelationshipsView', ExplorerLinkedProperties: 'LinkedPropertiesView' };
    if (name === './ExplorerObjectOverview') return { explorerFields: () => [], explorerRowTitle: () => 'Related object', ExplorerTypeIcon: 'TypeIcon' };
    if (name === '../../services/explorerProperties') return { fetchExplorerProperties: async () => ({ properties: [], titleProperty: null, displayName: 'Related type', primaryKey: ['id'] }) };
    if (name === '@/components/ui') return { Button: 'Button', theme: { useToken: () => ({ token: {} }) } };
    if (name === '@/components/ui/icons') return new Proxy({}, { get: (_, key) => String(key) });
    if (name === '../../stores/settings') return { useThemeStore: select => select({ mode: 'dark' }) };
    if (name.endsWith('.css') || name.endsWith('?raw')) return {};
    return {};
  }, undefined, expose);
  const render = () => runtime.render(() => component(props));
  return { errors, props, render, async flush() { for (let i = 0; i < 12; i++) await Promise.resolve(); return render(); } };
}

test('related objects recover after a failed read when the target scope changes', async () => {
  const props = { schema: 'ts_ods', table: 'missing', primaryKey: { id: 'one' } };
  const ui = sharedView('ExplorerRowLinks', props, { fetchExplorerRowByKey: async (_schema, table) => {
    if (table === 'missing') throw new Error('Read failed');
    return { row: { id: 'one' }, identity: { id: 'one' }, columnTypes: { id: 'text' } };
  }, fetchExplorerRowLinks: async (schema, table) => ({ selected: 'relation', relations: [{ id: 'relation', name: 'Has child', direction: 'outgoing', schema, table, fields: [], targetFields: [] }], links: [{ linkId: 'edge', identity: { id: 'one' }, canUnlink: false }], offset: 0, hasMore: false }) }, 'LinkPage');
  ui.render(); assert.equal(await ui.flush(), null);
  assert.match(ui.errors.at(-1)[0], /Unable to read related records/);
  props.table = 'available'; ui.render();
  assert.equal((await ui.flush()).type, 'RelationshipsView');
  assert.equal(ui.errors.at(-1)[0], '');
});

test('media request failures notify without an inline error or a false empty preview', async () => {
  const props = { value: 'https://files.example.test/fixtures/media/image.png', identity: { id: 'one' }, field: 'asset', presentation: 'media' };
  const ui = sharedView('ExplorerValue', props, { fetchExplorerFile: async () => { throw new Error('Request failed'); } }, 'exports.ExplorerValue');
  ui.render(); const tree = await ui.flush();
  assert.equal(ui.errors.at(-1)[0], 'Unable to preview this file.');
  assert.equal(nodes(tree).some(node => node.props?.role === 'alert' || node.type === 'img' && node.props?.src !== '/minio-logo.svg'), false);
  assert.equal(tree.props.className, 'explorer-file explorer-file--media');
});

test('media primary-key validation remains inline and does not issue a request', async () => {
  let reads = 0;
  const props = { value: 'https://files.example.test/fixtures/media/image.png', identity: {}, field: 'asset', presentation: 'media' };
  const ui = sharedView('ExplorerValue', props, { fetchExplorerFile: async () => { reads++; } }, 'exports.ExplorerValue');
  ui.render(); const tree = await ui.flush();
  assert.equal(reads, 0); assert.equal(ui.errors.at(-1)[0], '');
  assert.ok(nodes(tree).some(node => node.props?.role === 'alert' && node.props.children === 'A primary key is required to preview this file.'));
});

test('analysis request errors use Toast while its local inspection limit stays visible', () => {
  const props = { fields: ['category'], types: { category: 'string' }, rows: [], field: 'category', select() {}, onFilter() {}, onFilterValue() {},
    aggregation: { error: 'Service unavailable', groups: [], onRetry() {} } };
  const ui = sharedView('ExplorerExploreView', props);
  assert.equal(nodes(ui.render()).some(node => node.props?.role === 'alert'), false);
  assert.equal(ui.errors.at(-1)[0], 'Service unavailable');
  props.aggregation.limitReached = true;
  assert.equal(nodes(ui.render()).some(node => node.props?.role === 'alert'), true);
  assert.equal(ui.errors.at(-1)[0], undefined);
});
