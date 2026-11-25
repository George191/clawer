import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

const nodes = value => !value || typeof value !== 'object' ? [] : Array.isArray(value) ? value.flatMap(nodes) : [value, ...nodes(value.props?.children)];
const text = value => value == null || typeof value === 'boolean' ? '' : typeof value !== 'object' ? String(value) : Array.isArray(value) ? value.map(text).join('') : text(value.props?.children);
const revision = '2026-01-01T00:00:00Z';
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };
const plan = { id: 'batch-plan', name: 'Saved batch', connectionId: 'source', sourceSchema: 'public', sourceTable: 'source_rows', sourceKeyColumn: 'id', sourceUpdatedAt: revision, updatedAt: revision, targetTable: 'target_rows', mapping: [{ sourceColumn: 'id', targetColumn: 'id' }, { sourceColumn: 'value', targetColumn: 'value' }] };

async function mount({ overrides = {}, props = {} } = {}) {
  let active;
  const requests = [], states = [], session = { identity: 'reviewer', authenticated: true, locked: false };
  const defaults = {
    fetchBatchSyncCapabilities: async () => ({ enabled: true, maxRows: 100, maxBytes: 4096, targetSchema: 'ts_batch_sandbox', scheduledBatchSync: true }),
    fetchBatchSyncs: async () => [],
    fetchBatchSyncReceipts: async () => [], fetchBatchSyncAttempts: async () => [],
    fetchEtlObjectTypes: async () => ({ items: [{ table: 'target_rows' }], total: 1 }),
    fetchDataSourceColumns: async () => ({ schema: 'public', table: 'source_rows', hasMore: false, columns: [{ name: 'id', type: 'bigint', primaryKey: true }, { name: 'value', type: 'text', primaryKey: false }] }),
    fetchEtlTableColumns: async () => [{ name: 'id', type: 'bigint', primary: true }, { name: 'value', type: 'text', primary: false }],
    saveBatchSync: async input => ({ ...plan, ...input, sourceUpdatedAt: input.expectedSourceUpdatedAt }),
    runBatchSync: async () => ({ id: 'receipt', planId: plan.id, status: 'committed', rowsAffected: 1, recordedAt: revision }),
    fetchBatchSyncSchedule: async () => ({ available: true, enabled: false, configuredEnabled: false, generation: null, plannedTimeUtc: null }),
    fetchBatchSyncScheduleJobs: async () => [], updateBatchSyncSchedule: async () => ({}),
  };
  const api = Object.fromEntries(Object.entries({ ...defaults, ...overrides }).map(([name, implementation]) => [name, (...args) => { requests.push({ name, args }); return implementation(...args); }]));
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
    if (name === '@/components/ui') return { Button: 'Button', Input: 'Input', Modal: 'Modal', NativeSelect: 'NativeSelect' };
    if (name === '@/components/ui/icons') return new Proxy({}, { get: (_, name) => String(name) });
    if (name === '../../services/api') return api;
    if (name === '../../services/session') return { getSessionState: () => session };
    if (name === './SourceIdentity') return { sameSourceRevision: (a, b) => a === b };
    if (name === '../../services/scheduleTime') return { nextLocalTimeForUtcClock: value => value };
    if (name === './ManagedBatchLineage') return { MANAGED_BATCH_ASSET: 'committed-batch' };
    if (name === './BatchIncrementalPreflight') return { default: 'BatchIncrementalPreflight' };
    throw new Error('Unexpected dependency ' + name);
  };
  const code = ts.transpileModule(readFileSync(new URL('../src/pages/Pipeline/SourceBatchSyncs.tsx', import.meta.url), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  const exports = {}, storage = new Map();
  vm.runInThisContext('(function(require,exports,sessionStorage,crypto){' + code + '\n})')(require, exports, { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key) }, { randomUUID: () => 'request' });
  const mountInstance = (component, initialProps) => {
    let componentProps = initialProps, mounted = true;
    const target = { cursor: 0, state: [], refs: [], effects: [], scheduled: [], tree: undefined };
    const render = () => {
      if (!mounted) return;
      active = target; target.cursor = 0; target.scheduled = []; target.tree = component(componentProps);
      for (const [i, effect, deps] of target.scheduled) { target.effects[i]?.cleanup?.(); target.effects[i] = { deps, cleanup: effect() }; }
    };
    const ui = {
      states, requests, session, render, nodes: () => nodes(target.tree), text: () => text(target.tree), calls: name => requests.filter(row => row.name === name),
      button(label) { return this.nodes().find(item => item.type === 'Button' && text(item).trim() === label); },
      click(label) { const button = this.button(label); assert.ok(button, `Missing ${label}`); assert.ok(!button.props.disabled, `Disabled ${label}`); const result = button.props.onClick(); render(); return result; },
      field(label) { return this.nodes().find(item => ['Input', 'NativeSelect'].includes(item.type) && item.props['aria-label'] === label) || nodes(this.nodes().find(item => item.type === 'label' && text(item).startsWith(label))).find(item => ['Input', 'NativeSelect'].includes(item.type)); },
      fill(label, value) { const field = this.field(label); assert.ok(field, `Missing ${label}`); field.props.onChange({ target: { value } }); render(); },
      submit() { this.nodes().find(item => item.type === 'form' && item.props['aria-label'] === 'Configure batch sync').props.onSubmit({ preventDefault() {} }); render(); },
      setProps(next) { componentProps = { ...componentProps, ...next }; render(); },
      async settle() { for (let i = 0; i < 4; i++) { await new Promise(resolve => setImmediate(resolve)); render(); } },
      unmount() { mounted = false; target.effects.forEach(effect => effect?.cleanup?.()); },
    };
    render(); return ui;
  };
  const ui = mountInstance(exports.default, { source: { id: 'source', engine: 'postgresql', updatedAt: revision }, sourceProof: { state: 'verified', sourceUpdatedAt: revision, verifiedAt: revision }, selection: undefined, onComposerClosed() {}, onStateChange: state => states.push(state), ...props });
  ui.mountChild = vnode => mountInstance(vnode.type, vnode.props);
  await ui.settle(); return ui;
}

test('batch forms distinguish pristine creation, user edits, revert, cancellation and unmount', async () => {
  const ui = await mount({ props: { selection: { schema: 'public', table: 'source_rows' } } });
  assert.deepEqual(ui.states.at(-1), { busy: false, dirty: false });
  ui.fill('Sync name', 'New snapshot'); assert.equal(ui.states.at(-1).dirty, true);
  ui.fill('Sync name', ''); assert.equal(ui.states.at(-1).dirty, false);
  ui.fill('Sync name', 'Unsaved'); ui.click('Cancel'); assert.deepEqual(ui.states.at(-1), { busy: false, dirty: false });
  ui.unmount(); assert.deepEqual(ui.states.at(-1), { busy: false, dirty: false });
});

test('batch edits start clean and report pending saves without changing the payload contract', async () => {
  const saving = deferred();
  const ui = await mount({ overrides: { fetchBatchSyncs: async () => [plan], saveBatchSync: () => saving.promise } });
  ui.click('Edit sync'); await ui.settle(); assert.deepEqual(ui.states.at(-1), { busy: false, dirty: false });
  ui.fill('Sync name', 'Changed name'); ui.submit(); await ui.settle();
  assert.deepEqual(ui.states.at(-1), { busy: true, dirty: true });
  assert.equal(ui.calls('saveBatchSync')[0].args[0].expectedSourceUpdatedAt, revision);
  assert.equal(ui.calls('saveBatchSync')[0].args[0].sourceKeyColumn, 'id');
  saving.resolve({ ...plan, name: 'Changed name' }); await ui.settle();
  assert.deepEqual(ui.states.at(-1), { busy: false, dirty: false }); ui.unmount();
});

test('sandbox runs remain busy until the confirmed request settles', async () => {
  const running = deferred();
  const ui = await mount({ overrides: { fetchBatchSyncs: async () => [plan], runBatchSync: () => running.promise } });
  ui.click('Run sandbox sync'); ui.nodes().find(item => item.type === 'Modal' && item.props.title === 'Run sandbox batch sync?').props.onOk(); await ui.settle();
  assert.deepEqual(ui.states.at(-1), { busy: true, dirty: false });
  running.resolve({ id: 'receipt', planId: plan.id, status: 'committed', rowsAffected: 1, recordedAt: revision }); await ui.settle();
  assert.deepEqual(ui.states.at(-1), { busy: false, dirty: false }); ui.unmount();
});

test('schedule time edits and mutations are included in the parent state', async () => {
  const changing = deferred(); let registered = { available: true, enabled: false, configuredEnabled: false, generation: null, plannedTimeUtc: null };
  const ui = await mount({ overrides: { fetchBatchSyncs: async () => [plan], fetchBatchSyncSchedule: async () => registered, updateBatchSyncSchedule: async () => { await changing.promise; registered = { ...registered, configuredEnabled: true, enabled: true, generation: 1, plannedTimeUtc: '09:00' }; } } });
  ui.click('Schedule'); const vnode = ui.nodes().find(item => typeof item.type === 'function' && item.type.name === 'BatchSyncSchedulePanel');
  const schedule = ui.mountChild(vnode); await schedule.settle(); await ui.settle(); assert.equal(ui.states.at(-1).dirty, false);
  schedule.fill('Daily time (UTC)', '09:00'); await ui.settle(); assert.equal(ui.states.at(-1).dirty, true);
  schedule.fill('Daily time (UTC)', ''); await ui.settle(); assert.equal(ui.states.at(-1).dirty, false);
  schedule.fill('Daily time (UTC)', '09:00'); schedule.click('Arm schedule'); schedule.nodes().find(item => item.type === 'Modal').props.onOk(); await schedule.settle(); await ui.settle();
  assert.deepEqual(ui.states.at(-1), { busy: true, dirty: true }); assert.equal(ui.button('Schedule').props.disabled, true); assert.equal(ui.button('Edit sync').props.disabled, true);
  changing.resolve(); await schedule.settle(); await ui.settle();
  assert.deepEqual(ui.states.at(-1), { busy: false, dirty: false }); schedule.unmount(); ui.unmount();
});

test('sandbox guidance states its real key types and separates saving from execution', async () => {
  const ui = await mount({ overrides: { fetchBatchSyncCapabilities: async () => ({ enabled: false, maxRows: 100, maxBytes: 4096, targetSchema: 'ts_batch_sandbox', scheduledBatchSync: false }) } });
  assert.match(ui.text(), /smallint, integer, bigint or UUID/); assert.match(ui.text(), /Saving a draft does not copy rows or arm a schedule/); assert.match(ui.text(), /Drafts only/);
  ui.unmount();
});
