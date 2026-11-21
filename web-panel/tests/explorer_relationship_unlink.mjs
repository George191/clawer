import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const jsx = (type, props) => ({ type, props });
const nodes = tree => tree && typeof tree === 'object' ? [tree, ...[tree.props?.children].flat(Infinity).flatMap(nodes)] : [];
function mount(run) {
  let cursor = 0, tree;
  const state = [], refs = [], effects = [], scheduled = [], notifications = [];
  const session = { identity: 'synthetic-account', authenticated: true, locked: false };
  const record = { key: 'edge-a', link: jsx('a', { children: 'Related object' }), properties: 'Properties',
    ...(run ? { unlink: { label: 'Related object', description: 'Disconnect this pair of objects.', run } } : {}) };
  const props = { relations: [{ id: 'owns:outgoing', name: 'Owns', direction: 'outgoing' }], selectedId: 'owns:outgoing', records: [record],
    loading: false, expanded: 'edge-a', onToggle() {}, onSelect() {}, offset: 10, hasMore: true, pageLimit: 100_000, onPage() {} };
  const require = name => {
    if (name === 'react/jsx-runtime') return { jsx, jsxs: jsx, Fragment: 'Fragment' };
    if (name === 'react') return {
      useState(initial) { const i = cursor++; if (!(i in state)) state[i] = initial; return [state[i], next => { state[i] = typeof next === 'function' ? next(state[i]) : next; }]; },
      useRef(initial) { return refs[cursor++] ||= { current: initial }; },
      useEffect(effect, deps) { const i = cursor++; if (!effects[i] || deps.some((value, j) => !Object.is(value, effects[i].deps[j]))) scheduled.push([i, effect, deps]); },
    };
    if (name === '@/components/ui') return { Button: 'Button', Modal: 'Modal', message: Object.fromEntries(['success', 'error'].map(kind => [kind, value => notifications.push({ kind, value })])) };
    if (name === '@/components/ui/icons') return new Proxy({}, { get: (_, key) => String(key) });
    if (name === '../../services/session') return { getSessionState: () => session };
    if (name === '../../services/ontology') return { ontologyError: (error, fallback) => error.message || fallback };
    if (name.endsWith('.css')) return {};
    return {};
  };
  const source = readFileSync(new URL('../src/pages/Pipeline/ExplorerRelationshipsView.tsx', import.meta.url), 'utf8');
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  const exports = {};
  vm.runInThisContext('(function(require,exports){' + code + '\n})')(require, exports);
  const render = () => {
    cursor = 0; scheduled.length = 0; tree = exports.default(props);
    for (const [i, effect, deps] of scheduled) { effects[i]?.cleanup?.(); effects[i] = { deps, cleanup: effect() }; }
    return tree;
  };
  render();
  return { props, session, notifications, render, nodes: () => nodes(tree),
    modal: () => nodes(tree).find(node => node.type === 'Modal'),
    remove: () => nodes(tree).find(node => node.type === 'Button' && node.props['aria-label']?.startsWith('Remove relationship')),
    unmount: () => effects.forEach(effect => effect?.cleanup?.()),
  };
}

test('read-only relationship rows retain properties and never expose unlink', () => {
  const ui = mount();
  assert.equal(ui.remove(), undefined);
  assert.ok(ui.nodes().some(node => node.type === 'Button' && node.props.children === 'Hide properties'));
  assert.equal(ui.modal().props.open, false);
});

test('unlink requires an application confirmation and cancellation makes no request', () => {
  let calls = 0;
  const ui = mount(async () => calls++);
  const rowButtons = ui.nodes().filter(node => node.type === 'Button');
  assert.equal(rowButtons[0].props.children, 'Hide properties');
  assert.equal(rowButtons[1].props['aria-label'], 'Remove relationship to Related object');
  ui.remove().props.onClick(); ui.render();
  assert.equal(calls, 0); assert.equal(ui.modal().props.open, true);
  assert.equal(ui.modal().props.okText, 'Remove relationship');
  ui.modal().props.onCancel(); ui.render();
  assert.equal(ui.modal().props.open, false); assert.equal(calls, 0);
});

test('confirmed unlink is submitted once, locks controls and closes only after success', async () => {
  let resolve, calls = 0, requestSignal;
  const ui = mount(signal => { requestSignal = signal; calls++; return new Promise(done => { resolve = done; }); });
  ui.remove().props.onClick(); ui.render();
  const pending = ui.modal().props.onOk(); ui.render();
  assert.equal(calls, 1); assert.equal(ui.modal().props.confirmLoading, true);
  assert.equal(ui.modal().props.closable, false);
  assert.equal(ui.nodes().find(node => node.type === 'select').props.disabled, true);
  assert.equal(ui.remove().props.disabled, true);
  await ui.modal().props.onOk(); assert.equal(calls, 1);
  ui.modal().props.onCancel(); ui.render(); assert.equal(ui.modal().props.open, true);
  resolve(); await pending; ui.render();
  assert.equal(requestSignal.aborted, false); assert.equal(ui.modal().props.open, false);
  assert.equal(ui.notifications.length, 1); assert.equal(ui.notifications[0].kind, 'success');
  assert.equal(ui.notifications[0].value.className, 'explorer-toast');
});

test('failed unlink preserves the reviewed connection and requires an explicit retry', async () => {
  let calls = 0;
  const ui = mount(async () => { calls++; throw new Error('Permission changed'); });
  ui.remove().props.onClick(); ui.render();
  await ui.modal().props.onOk(); ui.render();
  assert.equal(calls, 1); assert.equal(ui.modal().props.open, true);
  assert.equal(ui.props.records.length, 1); assert.equal(ui.modal().props.confirmLoading, false);
  assert.equal(ui.notifications[0].kind, 'error');
  assert.equal(ui.notifications[0].value.description, 'Permission changed');
  assert.equal(ui.notifications[0].value.className, 'explorer-toast');
  ui.render(); assert.equal(calls, 1);
  await ui.modal().props.onOk(); assert.equal(calls, 2);
});

test('changed edge or revoked capability invalidates the confirmation', async () => {
  for (const change of [record => { record.key = 'replacement-edge'; }, record => { delete record.unlink; }]) {
    let calls = 0;
    const ui = mount(async () => calls++);
    ui.remove().props.onClick(); ui.render();
    change(ui.props.records[0]); ui.render();
    assert.equal(ui.modal().props.open, false);
    await ui.modal().props.onOk(); assert.equal(calls, 0);
  }
});

test('locked sessions cannot submit and unmount suppresses late mutation notifications', async () => {
  let calls = 0;
  const locked = mount(async () => calls++);
  locked.remove().props.onClick(); locked.render(); locked.session.locked = true;
  await locked.modal().props.onOk(); assert.equal(calls, 0);
  let reject, signal;
  const ui = mount(request => { signal = request; return new Promise((_resolve, failure) => { reject = failure; }); });
  ui.remove().props.onClick(); ui.render(); const pending = ui.modal().props.onOk();
  ui.unmount(); reject(new Error('Read failed')); await pending;
  assert.equal(signal.aborted, true); assert.equal(ui.notifications.length, 0);
});
