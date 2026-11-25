import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

const nodes = value => !value || typeof value !== 'object' ? [] : Array.isArray(value) ? value.flatMap(nodes) : [value, ...nodes(value.props?.children)];
const text = value => value == null || typeof value === 'boolean' ? '' : typeof value !== 'object' ? String(value) : Array.isArray(value) ? value.map(text).join('') : text(value.props?.children);
const sourceNode = (connectionId = 'mysql-connection', patch = {}) => ({ id: `${connectionId}:database:analytics`, connectionId, kind: 'database', name: 'analytics', engine: 'mysql', connectionName: connectionId, sourceUpdatedAt: '2026-01-01T00:00:00Z', ...patch });
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };

async function mount({ node = sourceNode(), overrides = {}, props = {}, retainedRun = false } = {}) {
  let active;
  const requests = [], session = { identity: 'reviewer', authenticated: true, locked: false }, storage = new Map();
  if (retainedRun) storage.set('ontology-transfer-run:reviewer:saved-plan', JSON.stringify({ requestId: 'retained-request', expectedUpdatedAt: '2026-01-01T00:00:00Z' }));
  const defaults = {
    fetchTransferPlans: async () => [],
    fetchTransferRunCapabilities: async () => ({ enabled: true, weeklyUpsert: true, monthlyUpsert: true, incrementalAppend: true, upstreamUpsert: true, sourceSchema: 'ts_transfer_sandbox', targetSchema: 'ts_transfer_sandbox', maxRows: 100 }),
    fetchStorageNodeRelations: async () => ({ relations: [{ name: 'source_a' }, { name: 'source_b' }], hasMore: false }),
    fetchStorageNodePreview: async () => ({ columns: ['id'], rows: [{ id: 1 }], hasMore: false }),
    fetchEtlObjectTypes: async () => ({ items: [{ table: 'target_rows' }], total: 1 }),
    saveTransferPlan: async draft => ({ ...draft, id: 'saved-plan', updatedAt: '2026-01-01T00:00:00Z' }),
    deleteTransferPlan: async () => {},
    checkTransferPlan: async () => ({ status: 'blocked', checkedColumns: 1, issues: [] }),
    fetchTransferSchedule: async () => ({}), fetchTransferScheduleJobs: async () => [],
    runSandboxTransfer: async () => { throw new Error('External source must not execute'); },
  };
  const api = Object.fromEntries(Object.entries({ ...defaults, ...overrides }).map(([name, implementation]) => [name, (...args) => { requests.push({ name, args }); return implementation(...args); }]));
  const react = {
    useState(initial) { const target = active, i = target.cursor++; if (!(i in target.state)) target.state[i] = typeof initial === 'function' ? initial() : initial; return [target.state[i], next => { target.state[i] = typeof next === 'function' ? next(target.state[i]) : next; }]; },
    useRef(initial) { const target = active, i = target.cursor++; return target.refs[i] ||= { current: initial }; },
    useId() { active.cursor++; return 'transfer-test'; },
    useEffect(effect, deps) { const target = active, i = target.cursor++; if (!target.effects[i] || deps.some((value, j) => !Object.is(value, target.effects[i].deps[j]))) target.scheduled.push([i, effect, deps]); },
  };
  const jsx = (type, props, key) => ({ type, props: props || {}, key });
  const require = name => {
    if (name === 'react') return react;
    if (name === 'react/jsx-runtime') return { jsx, jsxs: jsx, Fragment: 'Fragment' };
    if (name === '@/components/ui') return { Modal: 'Modal', NativeSelect: 'NativeSelect' };
    if (name === '@/components/ui/icons') return new Proxy({}, { get: (_, name) => String(name) });
    if (name === '../../services/api') return api;
    if (name === '../../services/session') return { getSessionState: () => session };
    if (name === '../../services/scheduleTime') return { isoWeekdaysUtc: ['Monday'], isoWeekdayName: day => day === 1 ? 'Monday' : undefined, isUtcMonthDay: day => Number.isInteger(day) && day >= 1 && day <= 28 };
    if (name === './LineageUpstreamTrigger') return { default: 'LineageUpstreamTrigger' };
    throw new Error('Unexpected dependency ' + name);
  };
  const code = ts.transpileModule(readFileSync(new URL('../src/pages/Pipeline/LineageTransferPlans.tsx', import.meta.url), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  const exports = {};
  vm.runInThisContext('(function(require,exports,sessionStorage,crypto){' + code + '\n})')(require, exports, { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key) }, { randomUUID: () => 'new-request' });
  const mountInstance = (component, initialProps) => {
    let componentProps = initialProps, mounted = true;
    const target = { cursor: 0, state: [], refs: [], effects: [], scheduled: [], tree: undefined };
    const render = () => {
      if (!mounted) return;
      active = target; target.cursor = 0; target.scheduled = []; target.tree = component(componentProps);
      for (const [i, effect, deps] of target.scheduled) { target.effects[i]?.cleanup?.(); target.effects[i] = { deps, cleanup: effect() }; }
    };
    const ui = {
      render, requests, nodes: () => nodes(target.tree), text: () => text(target.tree),
      calls: name => requests.filter(request => request.name === name),
      button: label => nodes(target.tree).find(item => item.type === 'button' && text(item).trim() === label),
      picker: label => nodes(target.tree).find(item => typeof item.type === 'function' && item.props.label === label),
      click(label) { const button = this.button(label); assert.ok(button, `Missing ${label}`); assert.ok(!button.props.disabled, `Disabled ${label}`); const result = button.props.onClick(); render(); return result; },
      field(label) { const wrapper = nodes(target.tree).find(item => item.type === 'label' && text(item).startsWith(label)); return nodes(wrapper).find(item => item.type === 'input' || item.type === 'NativeSelect'); },
      fill(label, value) { const field = this.field(label); assert.ok(field, `Missing ${label}`); field.props.onChange({ target: { value } }); render(); },
      chooseSource(value) { const picker = this.picker('Source table') || this.picker('Source index'); picker.props.setTable(value); render(); },
      chooseTarget(value) { this.picker('Destination table').props.setTable(value); render(); },
      submit() { const result = nodes(target.tree).find(item => item.type === 'form').props.onSubmit({ preventDefault() {} }); render(); return result; },
      setProps(next) { componentProps = { ...componentProps, ...next }; render(); },
      async settle() { for (let i = 0; i < 3; i++) { await new Promise(resolve => setImmediate(resolve)); render(); } },
      unmount() { mounted = false; target.effects.forEach(effect => effect?.cleanup?.()); },
    };
    render(); return ui;
  };
  const ui = mountInstance(exports.default, { schema: node.name, schemas: ['ts_rds', 'ts_transfer_sandbox'], sourceNode: node, ...props });
  ui.mountChild = vnode => mountInstance(vnode.type, vnode.props);
  await ui.settle(); return ui;
}

test('saved-source identity and revision reach plan listing, metadata, preview, compatibility and save', async () => {
  const node = sourceNode(); const ui = await mount({ node });
  assert.equal(ui.calls('fetchTransferPlans')[0].args[0], 'analytics'); assert.equal(ui.calls('fetchTransferPlans')[0].args[2].connectionId, node.connectionId);
  ui.click('New plan'); ui.fill('Plan name', 'Source transfer'); ui.chooseSource('source_a'); ui.fill('Destination schema', 'ts_rds'); ui.chooseTarget('target_rows');
  const picker = ui.mountChild(ui.picker('Source table')); await picker.settle();
  const metadata = ui.calls('fetchStorageNodeRelations')[0]; assert.deepEqual(metadata.args[0], { connectionId: node.connectionId, kind: 'database', name: 'analytics' }); assert.equal(metadata.args[1], node.sourceUpdatedAt);
  assert.equal(ui.calls('fetchEtlObjectTypes').length, 0);
  await ui.click('Preview source data'); await ui.settle();
  const preview = ui.calls('fetchStorageNodePreview')[0]; assert.equal(preview.args[0].connectionId, node.connectionId); assert.equal(preview.args[1], 'source_a'); assert.equal(preview.args[2], node.sourceUpdatedAt);
  await ui.click('Check compatibility'); await ui.settle();
  assert.equal(ui.calls('checkTransferPlan')[0].args[0].sourceUpdatedAt, node.sourceUpdatedAt);
  await ui.submit(); await ui.settle();
  const saved = ui.calls('saveTransferPlan')[0].args[0]; assert.deepEqual(saved.sourceNode, { connectionId: node.connectionId, kind: 'database', name: 'analytics' }); assert.equal(saved.sourceUpdatedAt, node.sourceUpdatedAt);
  picker.unmount(); ui.unmount();
});

test('external nodes named like the transfer sandbox cannot run or schedule, including retained requests and schedule links', async () => {
  const node = sourceNode('external', { name: 'ts_transfer_sandbox' });
  const plan = { id: 'saved-plan', name: 'External sandbox names', sourceSchema: node.name, sourceTable: 'source_a', sourceNode: node, sourceUpdatedAt: node.sourceUpdatedAt, targetSchema: node.name, targetTable: 'target_rows', trigger: 'manual', writeMode: 'upsert', updatedAt: node.sourceUpdatedAt };
  const ui = await mount({ node, retainedRun: true, props: { selectedSchedule: plan.id }, overrides: { fetchTransferPlans: async () => [plan] } });
  assert.equal(ui.button('Run sandbox transfer'), undefined); assert.equal(ui.button('Retry same request'), undefined); assert.equal(ui.button('Schedule'), undefined);
  assert.equal(ui.nodes().find(item => typeof item.type === 'function' && item.type.name === 'TransferSchedulePanel'), undefined);
  assert.equal(ui.calls('runSandboxTransfer').length, 0); assert.equal(ui.calls('fetchTransferSchedule').length, 0);
  ui.unmount();
});

test('changing a source table immediately allows a new preview and discards a late result for the old table', async () => {
  const old = deferred();
  const ui = await mount({ overrides: { fetchStorageNodePreview: async (_node, relation) => relation === 'source_a' ? old.promise : { columns: ['result'], rows: [{ result: 'new table data' }], hasMore: false } } });
  ui.click('New plan'); ui.chooseSource('source_a'); const oldRead = ui.click('Preview source data');
  const previousSignal = ui.calls('fetchStorageNodePreview')[0].args[3];
  ui.chooseSource('source_b'); assert.equal(previousSignal.aborted, true);
  await ui.click('Preview source data'); await ui.settle(); assert.match(ui.text(), /new table data/);
  old.resolve({ columns: ['result'], rows: [{ result: 'stale table data' }], hasMore: false }); await oldRead; await ui.settle();
  assert.match(ui.text(), /new table data/); assert.doesNotMatch(ui.text(), /stale table data/); assert.equal(ui.calls('fetchStorageNodePreview').length, 2); ui.unmount();
});

test('source metadata from an old connection cannot replace the new connection catalog', async () => {
  const old = deferred();
  const ui = await mount({ overrides: { fetchStorageNodeRelations: async node => node.connectionId === 'mysql-connection' ? old.promise : { relations: [{ name: 'new_connection_table' }], hasMore: false } } });
  ui.click('New plan'); const vnode = ui.picker('Source table'); const picker = ui.mountChild(vnode);
  const first = ui.calls('fetchStorageNodeRelations')[0];
  picker.setProps({ sourceNode: { connectionId: 'another-connection', kind: 'database', name: 'analytics' }, sourceUpdatedAt: '2026-02-01T00:00:00Z' }); await picker.settle();
  assert.equal(first.args[2].aborted, true); assert.match(picker.text(), /new_connection_table/);
  old.resolve({ relations: [{ name: 'old_connection_table' }], hasMore: false }); await picker.settle();
  assert.doesNotMatch(picker.text(), /old_connection_table/); assert.match(picker.text(), /new_connection_table/); picker.unmount(); ui.unmount();
});

test('unmounting an old source node aborts its preview before another node is opened', async () => {
  const old = deferred(); const first = await mount({ overrides: { fetchStorageNodePreview: () => old.promise } });
  first.click('New plan'); first.chooseSource('source_a'); const pending = first.click('Preview source data');
  const signal = first.calls('fetchStorageNodePreview')[0].args[3]; first.unmount(); assert.equal(signal.aborted, true);
  const second = await mount({ node: sourceNode('another-connection') }); second.click('New plan'); second.chooseSource('source_b'); await second.click('Preview source data'); await second.settle();
  old.resolve({ columns: ['value'], rows: [{ value: 'private old node data' }], hasMore: false }); await pending; await second.settle();
  assert.doesNotMatch(second.text(), /private old node data/); assert.equal(second.calls('fetchStorageNodePreview')[0].args[0].connectionId, 'another-connection'); second.unmount();
});

test('source object catalogs load more pages and reset the offset when the connection changes', async () => {
  const ui = await mount({ overrides: { fetchStorageNodeRelations: async (_node, _revision, _signal, { offset }) => ({ relations: [{ name: offset ? 'later_page_table' : 'first_page_table' }], hasMore: offset === 0 }) } });
  ui.click('New plan'); const vnode = ui.picker('Source table'), picker = ui.mountChild(vnode); await picker.settle();
  picker.click('Load more source objects'); await picker.settle();
  assert.match(picker.text(), /first_page_table/); assert.match(picker.text(), /later_page_table/);
  assert.deepEqual(ui.calls('fetchStorageNodeRelations').map(call => call.args[3].offset), [0, 100]);
  picker.setProps({ sourceNode: { connectionId: 'new-connection', name: 'analytics', kind: 'database' } }); await picker.settle();
  assert.equal(ui.calls('fetchStorageNodeRelations').at(-1).args[3].offset, 0); assert.doesNotMatch(picker.text(), /later_page_table/);
  picker.unmount(); ui.unmount();
});

test('deleting an external transfer draft uses its reviewed revision and preserves the confirmation on failure', async () => {
  const node = sourceNode(), plan = { id: 'saved-plan', name: 'Source draft', sourceSchema: node.name, sourceTable: 'source_a', sourceNode: node, sourceUpdatedAt: node.sourceUpdatedAt, targetSchema: 'ts_rds', targetTable: 'target_rows', trigger: 'manual', writeMode: 'append', updatedAt: '2026-02-01T00:00:00Z' };
  let attempts = 0, rows = [plan];
  const ui = await mount({ node, overrides: {
    fetchTransferPlans: async () => rows,
    deleteTransferPlan: async () => { if (!attempts++) throw { response: { data: { detail: 'Draft changed; reload before deleting' } } }; rows = []; },
  } });
  ui.click('Delete draft');
  const confirmation = () => ui.nodes().find(item => item.type === 'Modal' && item.props.title === 'Delete transfer draft?');
  assert.equal(ui.calls('deleteTransferPlan').length, 0); assert.equal(confirmation().props.open, true);
  confirmation().props.onOk(); await ui.settle();
  assert.match(ui.text(), /Draft changed; reload before deleting/); assert.equal(confirmation().props.open, true); assert.ok(ui.button('Delete draft'));
  assert.deepEqual(ui.calls('deleteTransferPlan')[0].args, [plan.id, plan.updatedAt]);
  confirmation().props.onOk(); await ui.settle();
  assert.equal(confirmation().props.open, false); assert.equal(ui.button('Delete draft'), undefined); assert.match(ui.text(), /Transfer draft deleted/); ui.unmount();
});

test('draft state callbacks ignore pristine drafts and report edits, reverts, cancellation and unmount', async () => {
  const states = [];
  const ui = await mount({ props: { onStateChange: state => states.push(state) } });
  assert.deepEqual(states.at(-1), { busy: false, dirty: false });
  ui.click('New plan'); assert.deepEqual(states.at(-1), { busy: false, dirty: false });
  ui.fill('Plan name', 'Unsaved edit'); assert.deepEqual(states.at(-1), { busy: false, dirty: true });
  ui.fill('Plan name', ''); assert.deepEqual(states.at(-1), { busy: false, dirty: false });
  ui.fill('Write mode', 'upsert'); assert.equal(states.at(-1).dirty, true);
  ui.fill('Write mode', 'append'); assert.equal(states.at(-1).dirty, false);
  ui.fill('Intended trigger', 'scheduled'); assert.equal(states.at(-1).dirty, true);
  ui.fill('Intended trigger', 'manual'); assert.equal(states.at(-1).dirty, false);
  ui.chooseSource('source_a'); assert.equal(states.at(-1).dirty, true);
  ui.chooseSource(''); assert.equal(states.at(-1).dirty, false);
  ui.chooseSource('source_a');
  ui.click('Cancel'); assert.deepEqual(states.at(-1), { busy: false, dirty: false });
  ui.click('New plan'); ui.fill('Plan name', 'Discard on close'); ui.unmount();
  assert.deepEqual(states.at(-1), { busy: false, dirty: false });
});

test('saving reports mutation busy, keeps dirty on failure, and clears it only after success', async () => {
  const states = [], first = deferred(); let attempts = 0;
  const ui = await mount({ props: { onStateChange: state => states.push(state) }, overrides: { saveTransferPlan: async draft => { if (!attempts++) return first.promise; return { ...draft, id: 'saved-plan', updatedAt: '2026-01-01T00:00:00Z' }; } } });
  ui.click('New plan'); ui.fill('Plan name', 'Source transfer'); ui.chooseSource('source_a'); ui.fill('Destination schema', 'ts_rds'); ui.chooseTarget('target_rows');
  const saving = ui.submit(); assert.deepEqual(states.at(-1), { busy: true, dirty: true });
  first.resolve(Promise.reject(new Error('Save rejected'))); await saving; await ui.settle();
  assert.deepEqual(states.at(-1), { busy: false, dirty: true });
  await ui.submit(); await ui.settle(); assert.deepEqual(states.at(-1), { busy: false, dirty: false }); ui.unmount();
});

test('editing a saved draft starts clean and delete mutations report busy', async () => {
  const states = [], deleting = deferred(), node = sourceNode();
  const plan = { id: 'saved-plan', name: 'Existing draft', sourceSchema: node.name, sourceTable: 'source_a', sourceNode: node, sourceUpdatedAt: node.sourceUpdatedAt, targetSchema: 'ts_rds', targetTable: 'target_rows', trigger: 'manual', writeMode: 'append', updatedAt: node.sourceUpdatedAt };
  const ui = await mount({ props: { onStateChange: state => states.push(state) }, overrides: { fetchTransferPlans: async () => [plan], deleteTransferPlan: () => deleting.promise } });
  ui.click('Edit'); assert.deepEqual(states.at(-1), { busy: false, dirty: false });
  ui.fill('Write mode', 'upsert'); assert.equal(states.at(-1).dirty, true);
  ui.fill('Write mode', 'append'); assert.equal(states.at(-1).dirty, false);
  ui.fill('Plan name', 'Changed'); assert.equal(states.at(-1).dirty, true);
  ui.fill('Plan name', plan.name); assert.equal(states.at(-1).dirty, false); ui.click('Cancel');
  ui.click('Delete draft'); const confirmation = ui.nodes().find(item => item.type === 'Modal' && item.props.title === 'Delete transfer draft?'); confirmation.props.onOk(); await ui.settle();
  assert.deepEqual(states.at(-1), { busy: true, dirty: false }); deleting.resolve({ deleted: true }); await ui.settle();
  assert.deepEqual(states.at(-1), { busy: false, dirty: false }); ui.unmount();
});

test('internal sandbox execution reports busy until its request settles', async () => {
  const states = [], request = deferred();
  const plan = { id: 'internal-plan', name: 'Internal sandbox draft', sourceSchema: 'ts_transfer_sandbox', sourceTable: 'source_a', targetSchema: 'ts_transfer_sandbox', targetTable: 'target_rows', trigger: 'manual', writeMode: 'append', updatedAt: '2026-01-01T00:00:00Z' };
  const ui = await mount({ props: { sourceNode: undefined, schema: 'ts_transfer_sandbox', onStateChange: state => states.push(state) }, overrides: { fetchTransferPlans: async () => [plan], runSandboxTransfer: () => request.promise } });
  ui.click('Run sandbox transfer'); ui.nodes().find(item => item.type === 'Modal' && item.props.title === 'Run sandbox transfer?').props.onOk(); await ui.settle();
  assert.deepEqual(states.at(-1), { busy: true, dirty: false }); request.resolve({ planId: plan.id, rowsAffected: 1 }); await ui.settle();
  assert.deepEqual(states.at(-1), { busy: false, dirty: false }); ui.unmount();
});
