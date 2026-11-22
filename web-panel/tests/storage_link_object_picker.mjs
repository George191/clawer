import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const jsx = (type, props) => ({ type, props });
const nodes = tree => Array.isArray(tree) ? tree.flatMap(nodes) : tree && typeof tree === 'object' ? [tree, ...[tree.props?.children].flat(Infinity).flatMap(nodes)] : [];
function compile(file, require, suffix = '') {
  const code = ts.transpileModule(readFileSync(new URL('../src/pages/Pipeline/' + file + '.tsx', import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const exports = {};
  vm.runInThisContext('(function(require,exports){' + code + suffix + '\n})')(require, exports);
  return exports;
}
const overview = compile('ExplorerObjectOverview', name => name === 'react' ? { lazy: () => 'Preview' }
  : name === 'react/jsx-runtime' ? { jsx, jsxs: jsx } : {});
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const results = (name = 'Company') => ({
  primaryKey: ['tenant', 'id'], columns: ['tenant', 'id', 'name'],
  rows: [{ tenant: 'a', id: 9007199254740992, name: name + ' A' }, { tenant: 'b', id: 9007199254740992, name: name + ' B' }],
  rowIdentities: [{ tenant: 'a', id: '9007199254740993' }, { tenant: 'b', id: '9007199254740995' }],
  hasMore: true, canPageFurther: true,
});
const definition = { displayName: 'Companies', titleProperty: 'name', dataType: 'company' };

function mount({ read = async () => results(), write = async () => {}, properties = async () => definition, canLink = true, direction = 'incoming' } = {}) {
  const state = [], refs = [], effects = [], scheduled = [], reads = [], propertyReads = [], writes = [], notifications = [], errors = [];
  let cursor = 0, tree, closed = 0, created = 0;
  const session = { identity: 'synthetic-account', authenticated: true, locked: false };
  const relation = { id: '18:' + direction, relationId: '18', name: 'Works for', direction, kind: 'logical', canLink,
    schema: 'ts_dwd', table: 'companies', fields: ['company_id'], targetFields: ['id'] };
  const props = { open: true, schema: 'ts_ods', table: 'employees', sourceKey: { tenant: 'anchor', id: '9007199254740997' }, relation,
    onClose: () => closed++, onCreated: () => created++ };
  const module = compile('StorageLinkObjectPicker', name => {
    if (name === 'react/jsx-runtime') return { jsx, jsxs: jsx, Fragment: 'Fragment' };
    if (name === 'react') return {
      useState(initial) { const i = cursor++; if (!(i in state)) state[i] = initial; return [state[i], next => { state[i] = typeof next === 'function' ? next(state[i]) : next; }]; },
      useRef(initial) { return refs[cursor++] ||= { current: initial }; },
      useEffect(effect, deps) { const i = cursor++; if (!effects[i] || deps.some((value, j) => !Object.is(value, effects[i].deps[j]))) scheduled.push([i, effect, deps]); },
    };
    if (name === '@/components/ui') return { Button: 'Button', Modal: 'Modal', Input: { Search: 'Search' },
      message: Object.fromEntries(['success', 'error'].map(kind => [kind, value => notifications.push({ kind, value })])) };
    if (name === '@/components/ui/icons') return { LinkOutlined: 'LinkIcon' };
    if (name === '../../services/session') return { getSessionState: () => session };
    if (name === '../../services/ontology') return { ontologyError: (error, fallback) => error.message || fallback };
    if (name === './ExplorerObjectOverview') return { ...overview, ExplorerTypeIcon: 'TypeIcon' };
    if (name === './useExplorerErrorToast') return { default: (...args) => errors.push(args) };
    if (name === '../../services/api') return {
      queryExplorerPreview: async (...args) => { reads.push(args); return read(...args); },
      createExplorerRowLink: async (...args) => { writes.push(args); return write(...args); },
    };
    if (name === '../../services/explorerProperties') return { fetchExplorerProperties: async (...args) => { propertyReads.push(args); return properties(...args); } };
    if (name.endsWith('.css')) return {};
    throw new Error('Unexpected dependency ' + name);
  }, '\nexports.Picker=ObjectPicker;');
  const render = () => {
    cursor = 0; scheduled.length = 0; tree = module.Picker(props);
    for (const [i, effect, deps] of scheduled) { effects[i]?.cleanup?.(); effects[i] = { deps, cleanup: effect() }; }
    return tree;
  };
  render();
  const all = () => nodes([tree, tree.props.footer]);
  return { props, session, reads, propertyReads, writes, notifications, errors, render, closed: () => closed, created: () => created,
    modal: () => tree, choices: () => nodes(tree).filter(node => node.type === 'button'),
    search: () => nodes(tree).find(node => node.type === 'Search'),
    button: label => all().find(node => node.type === 'Button' && node.props.children === label),
    choose(index = 0) { this.choices()[index].props.onClick(); render(); },
    async flush() { for (let i = 0; i < 14; i++) { await Promise.resolve(); render(); } },
    unmount: () => effects.forEach(effect => effect?.cleanup?.()),
  };
}

test('available objects come only from the peer type and links preserve canonical composite identities in both directions', async () => {
  for (const direction of ['incoming', 'outgoing']) {
    const ui = mount({ direction }); await ui.flush();
    assert.deepEqual(ui.reads[0].slice(0, 3), ['ts_dwd', 'companies', { search: '', offset: 0, limit: 10, filters: [], sorts: [], withIdentity: true }]);
    assert.deepEqual(ui.propertyReads[0].slice(0, 2), ['ts_dwd', 'companies']);
    assert.equal(ui.choices().length, 2);
    assert.ok(nodes(ui.choices()[1]).some(node => node.type === 'strong' && node.props.children === 'Company B'));
    assert.equal(ui.button('Add link').props.disabled, true);
    ui.choose(1);
    await ui.button('Add link').props.onClick(); ui.render();
    assert.deepEqual(ui.writes[0].slice(0, 3), ['ts_ods', 'employees', {
      key: { tenant: 'anchor', id: '9007199254740997' }, relationId: '18', direction,
      targetKey: { tenant: 'b', id: '9007199254740995' },
    }]);
    assert.equal(ui.created(), 1);
    assert.equal(ui.notifications[0].kind, 'success');
    assert.equal(ui.notifications[0].value.className, 'explorer-toast');
  }
});

test('unselected or read-only objects never create links and cancelling performs no write', async () => {
  for (const canLink of [true, false]) {
    const ui = mount({ canLink }); await ui.flush();
    await ui.button('Add link').props.onClick();
    assert.equal(ui.writes.length, 0);
    if (!canLink) {
      ui.choose(); assert.equal(ui.button('Add link').props.disabled, true);
      await ui.button('Add link').props.onClick();
    }
    ui.modal().props.onCancel();
    assert.equal(ui.writes.length, 0); assert.equal(ui.closed(), 1); assert.equal(ui.created(), 0);
  }
});

test('a pending add blocks duplicate submission, closing, searching and paging until the reviewed request settles', async () => {
  const pending = deferred(), ui = mount({ write: () => pending.promise }); await ui.flush(); ui.choose();
  const first = ui.button('Add link').props.onClick(); ui.render();
  assert.equal(ui.modal().props.closable, false); assert.equal(ui.modal().props.keyboard, false); assert.equal(ui.modal().props.maskClosable, false);
  assert.equal(ui.button('Cancel').props.disabled, true);
  await ui.button('Add link').props.onClick(); ui.modal().props.onCancel();
  ui.search().props.onSearch('Later query'); ui.button('Next').props.onClick(); ui.render(); await ui.flush();
  assert.equal(ui.writes.length, 1); assert.equal(ui.closed(), 0); assert.equal(ui.reads.length, 1);
  pending.resolve(); await first; ui.render();
  assert.equal(ui.created(), 1); assert.equal(ui.notifications.length, 1);
});

test('search and page changes clear the old selection while late reads cannot restore a previous query', async () => {
  const oldSearch = deferred(); let calls = 0;
  const ui = mount({ read: async () => ++calls === 2 ? oldSearch.promise : results(calls === 1 ? 'Company' : 'Newest') });
  await ui.flush(); ui.choose();
  ui.search().props.onSearch(' Old search '); ui.render();
  await ui.button('Add link').props.onClick(); assert.equal(ui.writes.length, 0);
  ui.search().props.onSearch(' Newest search '); ui.render(); await ui.flush();
  oldSearch.resolve(results('Stale result')); await ui.flush();
  assert.ok(nodes(ui.choices()[0]).some(node => node.props?.children === 'Newest A'));
  assert.equal(ui.choices().some(node => node.props['aria-pressed']), false);
  assert.equal(ui.button('Add link').props.disabled, true);
  assert.deepEqual(ui.reads[2][2], { search: 'Newest search', offset: 0, limit: 10, filters: [], sorts: [], withIdentity: true });
  ui.choose(); ui.button('Next').props.onClick(); ui.render();
  await ui.button('Add link').props.onClick(); assert.equal(ui.writes.length, 0);
  await ui.flush();
  assert.equal(ui.reads[3][2].offset, 10);
  assert.equal(ui.choices().some(node => node.props['aria-pressed']), false);
  assert.equal(ui.button('Add link').props.disabled, true);
});

test('missing or inexact primary-key metadata fails explicitly instead of linking the displayed row values', async () => {
  const cases = [
    { ...results(), primaryKey: [] },
    { ...results(), rowIdentities: undefined },
    { ...results(), rowIdentities: [{ tenant: 'a', id: 9007199254740992 }, { tenant: 'b', id: '9007199254740995' }] },
    { ...results(), rowIdentities: [{ id: '9007199254740993' }, { tenant: 'b', id: '9007199254740995' }] },
  ];
  for (const page of cases) {
    const ui = mount({ read: async () => page }); await ui.flush();
    assert.equal(ui.choices().length, 0); assert.equal(ui.button('Add link').props.disabled, true);
    assert.ok(ui.errors.some(([error, title]) => error?.includes('stable primary key') && title === 'Objects unavailable'));
    await ui.button('Add link').props.onClick(); assert.equal(ui.writes.length, 0);
  }
});

test('duplicate and cardinality denials keep the reviewed selection and report standard Toast without claiming success', async () => {
  for (const message of ['These objects are already linked', 'Relationship would exceed its declared cardinality']) {
    const failure = Object.assign(new Error(message), { response: { status: 409 } });
    const ui = mount({ write: async () => { throw failure; } }); await ui.flush(); ui.choose(1);
    await ui.button('Add link').props.onClick(); ui.render();
    assert.equal(ui.created(), 0); assert.equal(ui.closed(), 0);
    assert.equal(ui.choices()[1].props['aria-pressed'], true);
    assert.equal(ui.notifications.length, 1); assert.equal(ui.notifications[0].kind, 'error');
    assert.equal(ui.notifications[0].value.description, message); assert.equal(ui.notifications[0].value.className, 'explorer-toast');
    assert.equal(ui.button('Add link').props.disabled, false);
  }
});

test('a limited preview offers no page beyond its supported bound and an empty peer type never enables add', async () => {
  const limited = mount({ read: async () => ({ ...results(), canPageFurther: false }) }); await limited.flush();
  assert.equal(limited.button('Next').props.disabled, true);
  assert.ok(nodes(limited.modal()).some(node => node.props?.className === 'storage-link-object-limit'));
  const empty = mount({ read: async () => ({ ...results(), rows: [], rowIdentities: [], hasMore: false }) }); await empty.flush();
  assert.equal(empty.choices().length, 0); assert.equal(empty.button('Add link').props.disabled, true);
  assert.ok(nodes(empty.modal()).some(node => node.props?.children === 'No objects match this search.'));
});

test('initial read failures use Toast Retry and cannot fabricate available objects', async () => {
  let reads = 0;
  const ui = mount({ read: async () => { if (++reads === 1) throw new Error('Peer read denied'); return results(); } }); await ui.flush();
  assert.equal(ui.choices().length, 0); assert.equal(ui.writes.length, 0);
  const failure = ui.errors.findLast(([error]) => error);
  assert.equal(failure[0], 'Peer read denied'); assert.equal(failure[1], 'Objects unavailable');
  failure[2](); ui.render(); await ui.flush();
  assert.equal(ui.choices().length, 2); assert.equal(ui.button('Add link').props.disabled, true);
});

test('late reads and saves cannot update another session, and unmount aborts the pending add', async () => {
  const reading = deferred(), staleRead = mount({ read: () => reading.promise });
  staleRead.session.identity = 'another-account'; reading.resolve(results()); await staleRead.flush();
  assert.equal(staleRead.choices().length, 0); assert.equal(staleRead.notifications.length, 0);
  for (const leave of ['session', 'unmount']) {
    const writing = deferred(), ui = mount({ write: () => writing.promise }); await ui.flush(); ui.choose();
    const pending = ui.button('Add link').props.onClick(); ui.render();
    if (leave === 'session') ui.session.locked = true; else ui.unmount();
    if (leave === 'unmount') assert.equal(ui.writes[0][3].aborted, true);
    writing.resolve(); await pending; ui.render();
    assert.equal(ui.created(), 0); assert.equal(ui.notifications.length, 0);
  }
});
