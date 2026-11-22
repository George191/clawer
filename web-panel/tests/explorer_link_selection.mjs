import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const jsx = (type, props) => ({ type, props });
const nodes = tree => Array.isArray(tree) ? tree.flatMap(nodes) : tree && typeof tree === 'object' ? [tree, ...[tree.props?.children].flat(Infinity).flatMap(nodes)] : [];
const physical = (table = 'employees') => ({ kind: 'backed', schema: 'ts_ods', table });
const native = definitionId => ({ kind: 'published', definitionId });
const row = (id, title = id) => ({ key: id, identity: { tenant: 'a', id }, title, record: { id: Number(id), title }, columnTypes: {} });
const publishedRow = id => ({ ...row(id), identity: { $recordId: id } });
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const physicalRelation = (target = 'employees', direction = 'outgoing', canLink = true) => ({
  id: '18:' + direction, relationId: '18', name: 'Works with', direction, kind: 'logical', canLink,
  schema: 'ts_ods', table: target, fields: [], targetFields: [],
});
const nativeRelation = (target = 'companies', direction = 'outgoing') => ({
  id: 'link-type', key: 'works-for', name: 'Works for', direction, sourceDefinitionId: 'employees', targetDefinitionId: target, cardinality: 'many_to_one',
});

function mount({ source = physical(), rows = [row('9007199254740993', 'Alice'), row('9007199254740995', 'Bob')], context,
  relation = physicalRelation(), canManage = true, read, write = async () => {}, nativeWrite = async () => {} } = {}) {
  const state = [], refs = [], effects = [], scheduled = [], reads = [], writes = [], nativeWrites = [], notifications = [], errors = [], busy = [], handoffs = [];
  let cursor = 0, tree, closed = 0, created = 0;
  const session = { identity: 'synthetic-account', authenticated: true, locked: false };
  const props = { source, choices: rows, active: rows[0], context,
    onChooseTarget: value => handoffs.push(value), onCreated: () => created++, onCancel: () => closed++, onBusyChange: value => busy.push(value) };
  const code = ts.transpileModule(readFileSync(new URL('../src/pages/Pipeline/ExplorerLinkSelection.tsx', import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const exports = {};
  vm.runInThisContext('(function(require,exports){' + code + '\n})')(name => {
    if (name === 'react/jsx-runtime') return { jsx, jsxs: jsx, Fragment: 'Fragment' };
    if (name === 'react') return {
      useState(initial) { const i = cursor++; if (!(i in state)) state[i] = initial; return [state[i], next => { state[i] = typeof next === 'function' ? next(state[i]) : next; }]; },
      useRef(initial) { return refs[cursor++] ||= { current: initial }; },
      useEffect(effect, deps) { const i = cursor++; if (!effects[i] || deps.some((value, j) => !Object.is(value, effects[i].deps[j]))) scheduled.push([i, effect, deps]); },
    };
    if (name === '@/components/ui') return { Button: 'Button', Select: 'Select', message: Object.fromEntries(['success', 'error'].map(kind => [kind, value => notifications.push({ kind, value })])) };
    if (name === '@/components/ui/icons') return { LinkOutlined: 'LinkIcon' };
    if (name === '../../services/session') return { getSessionState: () => session };
    if (name === '../../services/ontology') return { ontologyError: (error, fallback) => error.message || fallback };
    if (name === './useExplorerErrorToast') return { default: (...args) => errors.push(args) };
    if (name === '../../services/api') return {
      fetchExplorerRowLinks: async (...args) => { reads.push(args); return read ? read(...args) : { relations: [relation], canManage }; },
      createExplorerRowLink: async (...args) => { writes.push(args); return write(...args); },
    };
    if (name === '../../services/publishedExplorerDetails') return {
      fetchPublishedRelationships: async (...args) => { reads.push(args); return read ? read(...args) : { relations: [relation], canManage }; },
      createPublishedRelationship: async (...args) => { nativeWrites.push(args); return nativeWrite(...args); },
    };
    if (name === '../../services/explorerNavigation') return { backedExplorerHref: (schema, table) => `/ontology/explorer?schema=${schema}&table=${table}` };
    if (name === '../../services/publishedExplorerHandoff') return { publishedExplorerHref: id => '/ontology/explorer?objectType=' + id };
    if (name.endsWith('.css')) return {};
    throw new Error('Unexpected dependency ' + name);
  }, exports);
  const render = () => {
    cursor = 0; scheduled.length = 0; tree = exports.ExplorerLinkForm(props);
    for (const [i, effect, deps] of scheduled) { effects[i]?.cleanup?.(); effects[i] = { deps, cleanup: effect() }; }
    return tree;
  };
  render();
  return { props, session, reads, writes, nativeWrites, notifications, errors, busy, handoffs, render, exports,
    closed: () => closed, created: () => created,
    all: () => nodes(tree),
    button: label => nodes(tree).find(node => node.type === 'Button' && node.props.children === label),
    select: label => nodes(tree).find(node => node.type === 'Select' && node.props['aria-label'] === label),
    async flush() { for (let i = 0; i < 12; i++) { await Promise.resolve(); render(); } },
    unmount: () => effects.forEach(effect => effect?.cleanup?.()),
  };
}

test('same-type linking uses the selected pair and canonical composite keys in both directions', async () => {
  for (const direction of ['incoming', 'outgoing']) {
    const ui = mount({ relation: physicalRelation('employees', direction) }); await ui.flush();
    assert.equal(ui.select('Relationship source').props.options.length, 2);
    assert.deepEqual(ui.select('Relationship target').props.options, [{ value: '9007199254740995', label: 'Bob' }]);
    await ui.button('Add link').props.onClick();
    assert.deepEqual(ui.writes[0].slice(0, 3), ['ts_ods', 'employees', {
      key: { tenant: 'a', id: '9007199254740993' }, relationId: '18', direction,
      targetKey: { tenant: 'a', id: '9007199254740995' },
    }]);
    assert.equal(ui.created(), 1); assert.equal(ui.notifications[0].kind, 'success');
  }
});

test('cross-type linking pins the selected source and hands off to the main table without an object picker', async () => {
  const ui = mount({ relation: physicalRelation('companies'), rows: [row('9007199254740993', 'Alice')] }); await ui.flush();
  assert.equal(ui.select('Relationship target'), undefined);
  assert.equal(ui.all().some(node => node.type === 'Modal' || node.type === 'Search' || node.type === 'Table'), false);
  ui.button('Choose target objects').props.onClick();
  const context = ui.handoffs[0];
  assert.deepEqual(context.sourceRow, { key: '9007199254740993', identity: { tenant: 'a', id: '9007199254740993' }, title: 'Alice' });
  assert.deepEqual(context.relation.target, physical('companies'));
  const navigations = [];
  ui.exports.beginLinkSelection((...args) => navigations.push(args), context);
  assert.deepEqual(navigations, [['/ontology/explorer?schema=ts_ods&table=companies', { state: { linkSelection: context } }]]);
  assert.equal(ui.writes.length, 0);
  ui.button('Cancel').props.onClick(); assert.equal(ui.closed(), 1);
});

test('a pinned source cannot be replaced by a same-key object from another type', async () => {
  const initial = mount({ relation: physicalRelation('companies'), rows: [row('1', 'Employee')] }); await initial.flush();
  initial.button('Choose target objects').props.onClick();
  const ui = mount({ source: physical('companies'), rows: [row('1', 'Company')], context: initial.handoffs[0], relation: physicalRelation('companies') }); await ui.flush();
  assert.deepEqual(ui.reads[0].slice(0, 3), ['ts_ods', 'employees', { tenant: 'a', id: '1' }]);
  assert.equal(ui.select('Relationship source'), undefined);
  assert.equal(ui.select('Relationship target').props.options[0].label, 'Company');
  await ui.button('Add link').props.onClick();
  assert.equal(ui.writes[0][1], 'employees'); assert.deepEqual(ui.writes[0][2].targetKey, { tenant: 'a', id: '1' });
});

test('fresh permission and relation availability are checked before a pinned link can be submitted', async () => {
  const initial = mount({ relation: physicalRelation('companies'), rows: [row('1')] }); await initial.flush(); initial.button('Choose target objects').props.onClick();
  for (const page of [{ relations: [], canManage: true }, { relations: [physicalRelation('companies')], canManage: false }, { relations: [physicalRelation('different')], canManage: true }]) {
    const ui = mount({ source: physical('companies'), context: initial.handoffs[0], read: async () => page }); await ui.flush();
    assert.equal(ui.button('Add link').props.disabled, true);
    await ui.button('Add link').props.onClick(); assert.equal(ui.writes.length, 0);
  }
});

test('inexact or missing source/target identities never use rounded displayed values', async () => {
  for (const badKey of [{}, { id: 9007199254740992 }, { tenant: 'a', id: undefined }]) {
    const source = mount({ rows: [{ ...row('1'), identity: badKey }, row('2')] }); await source.flush();
    assert.equal(source.button('Choose target objects').props.disabled, true); assert.equal(source.writes.length, 0);
    const target = mount({ rows: [row('1'), { ...row('2'), identity: badKey }] }); await target.flush();
    assert.equal(target.button('Add link').props.disabled, true);
    await target.button('Add link').props.onClick(); assert.equal(target.writes.length, 0);
  }
});

test('a pending link blocks double submit, source changes, cancellation and releases the workspace lock', async () => {
  const pending = deferred(), ui = mount({ write: () => pending.promise }); await ui.flush();
  const request = ui.button('Add link').props.onClick(); ui.render();
  assert.equal(ui.button('Cancel').props.disabled, true);
  assert.equal(ui.select('Relationship source').props.disabled, true);
  await ui.button('Add link').props.onClick();
  ui.button('Cancel').props.onClick(); ui.select('Relationship source').props.onChange('9007199254740995'); ui.render();
  assert.equal(ui.writes.length, 1); assert.equal(ui.closed(), 0); assert.equal(ui.select('Relationship source').props.value, '9007199254740993');
  pending.resolve(); await request;
  assert.equal(ui.created(), 1); assert.deepEqual(ui.busy, [true, false]);
});

test('outer loading or mutation locks prevent stale selected objects from being linked or handed off', async () => {
  for (const relation of [physicalRelation(), physicalRelation('companies')]) {
    const ui = mount({ relation }); await ui.flush(); ui.props.disabled = true; ui.render();
    const action = ui.button(relation.table === 'employees' ? 'Add link' : 'Choose target objects');
    assert.equal(action.props.disabled, true); await action.props.onClick();
    ui.select('Relationship source').props.onChange('9007199254740995'); ui.render();
    assert.equal(ui.select('Relationship source').props.value, '9007199254740993');
    assert.equal(ui.writes.length, 0); assert.equal(ui.handoffs.length, 0);
  }
});

test('cardinality and duplicate denials retain selected objects and use the standard Toast', async () => {
  for (const reason of ['These objects are already linked', 'Relationship would exceed its declared cardinality']) {
    const ui = mount({ write: async () => { throw new Error(reason); } }); await ui.flush();
    await ui.button('Add link').props.onClick(); ui.render();
    assert.equal(ui.created(), 0); assert.equal(ui.select('Relationship target').props.value, '9007199254740995');
    assert.deepEqual(ui.notifications, [{ kind: 'error', value: { content: 'Unable to add relationship', description: reason, className: 'explorer-toast' } }]);
    assert.equal(ui.all().some(node => node.props?.role === 'alert'), false);
  }
});

test('late reads and completed writes after lock, account change or unmount cannot publish stale success', async () => {
  for (const stop of ['locked', 'account', 'unmount']) {
    const pending = deferred(), ui = mount({ write: () => pending.promise }); await ui.flush();
    const request = ui.button('Add link').props.onClick();
    if (stop === 'locked') ui.session.locked = true;
    if (stop === 'account') ui.session.identity = 'another-account';
    if (stop === 'unmount') ui.unmount();
    pending.resolve(); await request;
    assert.equal(ui.created(), 0); assert.equal(ui.notifications.length, 0);
    if (stop === 'unmount') assert.equal(ui.writes[0][3].aborted, true);
    assert.equal(ui.busy.at(-1), false);
  }
  const pending = deferred(), ui = mount({ read: () => pending.promise });
  ui.session.locked = true; pending.resolve({ relations: [physicalRelation()], canManage: true }); await ui.flush();
  assert.equal(ui.select('Relationship and direction').props.options.length, 0);
});

test('native Published linking uses record IDs and preserves incoming/outgoing orientation', async () => {
  for (const direction of ['incoming', 'outgoing']) {
    const ui = mount({ source: native('employees'), relation: nativeRelation('employees', direction), rows: [publishedRow('record-a'), publishedRow('record-b')] }); await ui.flush();
    await ui.button('Add link').props.onClick();
    assert.deepEqual(ui.nativeWrites[0].slice(0, 4), ['link-type', direction, 'record-a', 'record-b']);
    assert.equal(ui.writes.length, 0); assert.equal(ui.created(), 1);
  }
});

test('route handoff is scoped to the active session, valid identity and exact peer type', async () => {
  const ui = mount(); await ui.flush();
  const context = ui.exports.createLinkSelection(physical(), ui.props.active, ui.exports.storageLinkRelation(physicalRelation('companies')));
  assert.equal(ui.exports.readLinkSelection({ linkSelection: context }, physical('companies')), context);
  assert.equal(ui.exports.readLinkSelection({ linkSelection: context }, physical('other')), undefined);
  assert.equal(ui.exports.readLinkSelection({ linkSelection: { ...context, sourceRow: { ...context.sourceRow, identity: { id: 9007199254740992 } } } }, physical('companies')), undefined);
  assert.equal(ui.exports.readLinkSelection({ linkSelection: { ...context, relation: { ...context.relation, direction: 'sideways' } } }, physical('companies')), undefined);
  ui.session.locked = true; assert.equal(ui.exports.readLinkSelection({ linkSelection: context }, physical('companies')), undefined);
  ui.session.locked = false; ui.session.identity = 'other'; assert.equal(ui.exports.readLinkSelection({ linkSelection: context }, physical('companies')), undefined);
  assert.equal(ui.exports.readLinkSelection(undefined, physical()), undefined);
});

test('Published relationship service carries management capability and orients native create requests', async () => {
  const requests = [], exports = {};
  const code = ts.transpileModule(readFileSync(new URL('../src/services/publishedExplorerDetails.ts', import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  vm.runInThisContext('(function(require,exports){' + code + '\n})')(name => name === './endpoints' ? { apiUrl: value => value } : {
    graphRequest: async (...args) => { requests.push(args); return args[1].endsWith('/link-types') ? { items: [], hasMore: false, canManage: true } : {}; },
  }, exports);
  assert.equal((await exports.fetchPublishedRelationships('employees', 'record-a', undefined, 0, new AbortController().signal)).canManage, true);
  for (const direction of ['incoming', 'outgoing']) {
    await exports.createPublishedRelationship('link/type', direction, 'record-a', 'record-b', new AbortController().signal);
    assert.equal(requests.at(-1)[1], '/link-types/link%2Ftype/links');
    assert.deepEqual(requests.at(-1)[2].data, direction === 'outgoing' ? { sourceRecordId: 'record-a', targetRecordId: 'record-b' } : { sourceRecordId: 'record-b', targetRecordId: 'record-a' });
  }
});
