import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const jsx = (type, props) => ({ type, props });
const nodes = tree => Array.isArray(tree) ? tree.flatMap(nodes) : tree && typeof tree === 'object' ? [tree, ...[tree.props?.children].flat(Infinity).flatMap(nodes)] : [];
const source = file => readFileSync(new URL('../src/' + file, import.meta.url), 'utf8');
function compile(file, require, suffix = '') {
  const code = ts.transpileModule(source(file), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  const exports = {};
  vm.runInThisContext('(function(require,exports){' + code + suffix + '\n})')(require, exports);
  return exports;
}
const relation = (id = '1') => ({ id, name: id === '1' ? 'Works for' : 'Owns', description: 'Initial description', revision: '2026-11-22 01:02:03.123456+00',
  enabled: true, cardinality: 'many_to_one', direction: 'outgoing',
  source: { schema: 'ts_ods', table: 'employees', fields: ['company_id'] }, target: { schema: 'ts_ods', table: 'companies', fields: ['id'] } });
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const objectType = (index = 0) => ({ id: 'ts_ods.type_' + index, schema: 'ts_ods', table: 'type_' + index, displayName: 'Object type ' + index });
const catalogPage = (items = [objectType()], offset = 0, total = items.length) => ({ items, offset, limit: 20, total });
function mount({ canManage = true, read, write, create, catalog, initialRelationId } = {}) {
  const state = [], refs = [], effects = [], scheduled = [], reads = [], writes = [], creates = [], targetReads = [], notifications = [], errors = [], changed = [], directions = [];
  const session = { identity: 'synthetic-user', authenticated: true, locked: false };
  let cursor = 0, tree, closed = 0, data = { items: [relation(), relation('2')], canManage };
  const module = compile('pages/Pipeline/StorageRelationshipTypes.tsx', name => {
    if (name === 'react/jsx-runtime') return { jsx, jsxs: jsx, Fragment: 'Fragment' };
    if (name === 'react') return {
      useState(initial) { const i = cursor++; if (!(i in state)) state[i] = initial; return [state[i], next => { state[i] = typeof next === 'function' ? next(state[i]) : next; }]; },
      useRef(initial) { const i = cursor++; return refs[i] ||= { current: initial }; },
      useEffect(effect, deps) { const i = cursor++; if (!effects[i] || deps.some((value, j) => !Object.is(value, effects[i].deps[j]))) scheduled.push([i, effect, deps]); },
    };
    if (name === '@/components/ui') return { Button: 'Button', Modal: 'Modal', Input: Object.assign(() => null, { TextArea: 'TextArea' }),
      message: { success: value => notifications.push({ kind: 'success', value }), error: value => notifications.push({ kind: 'error', value }) } };
    if (name === '@/components/ui/icons') return new Proxy({}, { get: (_, key) => String(key) });
    if (name === '../../services/session') return { getSessionState: () => session };
    if (name === '../../services/ontology') return { ontologyError: (error, fallback) => error.message || fallback };
    if (name === './useExplorerErrorToast') return { default: (...args) => errors.push(args) };
    if (name === '../../services/storageRelationshipTypes') return {
      fetchStorageRelationshipTypes: async (...args) => { reads.push(args); return read ? read(...args) : data; },
      saveStorageRelationshipType: async (...args) => { writes.push(args); return write ? write(...args) : { ...data.items.find(item => item.id === args[2]), ...args[3], revision: 'next-revision' }; },
      createStorageRelationshipType: async (...args) => {
        creates.push(args);
        if (create) return create(...args);
        const payload = args[2], anchor = { schema: args[0], table: args[1], fields: [] }, peer = { ...payload.target, fields: [] };
        return { ...payload, id: 'new-type', revision: 'created', enabled: true,
          source: payload.direction === 'incoming' ? peer : anchor, target: payload.direction === 'incoming' ? anchor : peer };
      },
    };
    if (name === '../../services/api') return { fetchEtlObjectTypes: async (...args) => { targetReads.push(args); return catalog ? catalog(...args) : catalogPage(); } };
    return {};
  }, '\nexports.Manager=RelationshipManager;');
  const props = { schema: 'ts_ods', table: 'employees', open: true, initialRelationId, onClose: () => closed++, onChanged: (id, direction) => { changed.push(id); directions.push(direction); } };
  const render = () => {
    cursor = 0; scheduled.length = 0; tree = module.Manager(props);
    for (const [i, effect, deps] of scheduled) { effects[i]?.cleanup?.(); effects[i] = { deps, cleanup: effect() }; }
    return tree;
  };
  render();
  return { render, reads, writes, creates, targetReads, notifications, errors, changed, directions, session, closed: () => closed, tree: () => tree,
    main: () => nodes(tree).find(node => node.type === 'Modal' && node.props.title === 'Relationship types'),
    confirm: () => nodes(tree).find(node => node.type === 'Modal' && node.props.title === 'Discard changes?' && node.props.open),
    field: label => nodes(tree).find(node => node.props?.['aria-label'] === label),
    button: label => nodes([tree, ...nodes(tree).filter(node => node.type === 'Modal').map(node => node.props.footer)])
      .find(node => node.type === 'Button' && node.props.children === label),
    chooseOther: () => nodes(tree).find(node => node.type === 'button' && node.props['aria-pressed'] === false).props.onClick(),
    setData: value => { data = value; },
    async flush() { for (let i = 0; i < 12; i++) { await Promise.resolve(); render(); } return tree; },
    unmount: () => effects.forEach(effect => effect?.cleanup?.()),
  };
}
const edit = (ui, name, description = 'Changed description') => {
  ui.field('Relationship display name').props.onChange({ target: { value: name } });
  ui.field('Relationship description').props.onChange({ target: { value: description } }); ui.render();
};
const save = async ui => { nodes(ui.tree()).find(node => node.type === 'form').props.onSubmit({ preventDefault() {} }); await ui.flush(); };

test('storage relationship service addresses the selected scope and preserves opaque reviewed revisions', async () => {
  const calls = [];
  const api = compile('services/storageRelationshipTypes.ts', name => {
    assert.equal(name, './api');
    const invoke = method => async (...args) => { calls.push([method, ...args]); return { data: { data: 'unwrapped' } }; };
    return { default: { get: invoke('get'), patch: invoke('patch'), post: invoke('post') }, unwrap: value => value.data };
  });
  const signal = new AbortController().signal, payload = { name: 'Works for', description: '', expectedRevision: relation().revision };
  assert.equal(await api.fetchStorageRelationshipTypes('ts_ods', 'selected table', signal), 'unwrapped');
  assert.equal(await api.saveStorageRelationshipType('ts_ods', 'selected table', '7', payload, signal), 'unwrapped');
  const createPayload = { name: 'Participates', description: '', cardinality: 'many_to_many', direction: 'incoming', target: { schema: 'ts_dwd', table: 'projects' } };
  assert.equal(await api.createStorageRelationshipType('ts_ods', 'selected table', createPayload, signal), 'unwrapped');
  assert.deepEqual(calls, [
    ['get', '/etl/ts_ods/selected%20table/relationship-types', { signal }],
    ['patch', '/etl/ts_ods/selected%20table/relationship-types/7', payload, { signal }],
    ['post', '/etl/ts_ods/selected%20table/relationship-types', createPayload, { signal }],
  ]);
});

test('metadata saves only name and description against the reviewed revision, then updates its displayed draft', async () => {
  const ui = mount({ initialRelationId: '2' }); await ui.flush();
  assert.equal(ui.field('Relationship display name').props.value, 'Owns');
  edit(ui, '  Owns equipment  ');
  await save(ui); await ui.flush();
  assert.deepEqual(ui.writes[0].slice(0, 4), ['ts_ods', 'employees', '2', { name: 'Owns equipment', description: 'Changed description', expectedRevision: relation().revision }]);
  assert.equal(ui.field('Relationship display name').props.value, 'Owns equipment');
  assert.equal(ui.button('Save changes').props.disabled, true);
  assert.deepEqual(ui.changed, ['2']); assert.equal(ui.closed(), 0);
  assert.equal(ui.notifications[0].value.className, 'explorer-toast');
});

test('closing with a draft uses application confirmation; keeping or discarding never writes metadata', async () => {
  const ui = mount(); await ui.flush(); edit(ui, 'Edited only');
  ui.main().props.onCancel(); ui.render(); assert.ok(ui.confirm());
  ui.confirm().props.onCancel(); ui.render(); assert.equal(ui.confirm(), undefined);
  assert.equal(ui.field('Relationship display name').props.value, 'Edited only');
  ui.main().props.onCancel(); ui.render(); ui.confirm().props.onOk(); ui.render();
  assert.equal(ui.closed(), 1); assert.equal(ui.writes.length, 0);
});

test('switching relationship types reviews unsaved changes and never carries a draft into the next type', async () => {
  const ui = mount(); await ui.flush(); edit(ui, 'Unsaved first type');
  ui.chooseOther(); ui.render(); assert.ok(ui.confirm());
  assert.equal(ui.field('Relationship display name').props.value, 'Unsaved first type');
  ui.confirm().props.onCancel(); ui.render(); assert.equal(ui.confirm(), undefined);
  ui.chooseOther(); ui.render(); ui.confirm().props.onOk(); ui.render();
  assert.equal(ui.field('Relationship display name').props.value, 'Owns'); assert.equal(ui.writes.length, 0);
  assert.equal(ui.button('Save changes').props.disabled, true);
});

test('saving locks duplicate submissions, closing and selection until the request settles', async () => {
  const request = deferred(), ui = mount({ write: () => request.promise }); await ui.flush(); edit(ui, 'Changed');
  const first = save(ui); void save(ui); ui.main().props.onCancel(); ui.render();
  assert.equal(ui.writes.length, 1); assert.equal(ui.closed(), 0); assert.equal(ui.confirm(), undefined);
  assert.equal(ui.main().props.keyboard, false); assert.equal(ui.main().props.maskClosable, false);
  ui.chooseOther(); ui.render(); assert.equal(ui.confirm(), undefined);
  assert.equal(ui.field('Relationship display name').props.disabled, true);
  request.resolve({ ...relation(), name: 'Changed', description: 'Changed description', revision: 'saved' });
  await first; await ui.flush(); assert.equal(ui.changed.length, 1);
});

test('a version conflict keeps the draft and requires confirmed reload; a failed reload cannot unlock stale saving', async () => {
  let loadFails = false;
  const ui = mount({ read: async () => { if (loadFails) throw new Error('Read unavailable'); return { items: [relation()], canManage: true }; },
    write: async () => { throw { response: { data: { errors: { code: 'relation_version_conflict' } } } }; } });
  await ui.flush(); edit(ui, 'My pending name'); await save(ui); ui.render();
  assert.equal(ui.field('Relationship display name').props.value, 'My pending name');
  assert.equal(ui.button('Save changes').props.disabled, true); assert.ok(ui.button('Discard and reload'));
  assert.equal(ui.notifications.at(-1).value.className, 'explorer-toast');
  ui.button('Discard and reload').props.onClick(); ui.render();
  assert.ok(ui.confirm()); assert.equal(ui.reads.length, 1);
  ui.confirm().props.onCancel(); ui.render(); assert.equal(ui.field('Relationship display name').props.value, 'My pending name');
  ui.button('Discard and reload').props.onClick(); ui.render(); loadFails = true;
  ui.confirm().props.onOk(); await ui.flush();
  assert.equal(ui.reads.length, 2); assert.equal(ui.field('Relationship display name').props.value, 'My pending name');
  assert.equal(ui.button('Save changes').props.disabled, true);
  await save(ui); assert.equal(ui.writes.length, 1);
});

test('duplicate names remain editable and a corrected name can be saved without discarding the draft', async () => {
  let denied = true;
  const ui = mount({ write: async (_schema, _table, _id, payload) => {
    if (denied) throw { response: { data: { detail: { code: 'relation_name_conflict' } } }, message: 'Name is already used' };
    return { ...relation(), ...payload, revision: 'saved' };
  } });
  await ui.flush(); edit(ui, 'Duplicate name'); await save(ui); ui.render();
  assert.equal(ui.field('Relationship display name').props.disabled, false);
  assert.equal(ui.field('Relationship display name').props.value, 'Duplicate name');
  denied = false; edit(ui, 'Distinct name'); await save(ui); await ui.flush();
  assert.equal(ui.writes.length, 2); assert.equal(ui.field('Relationship display name').props.value, 'Distinct name');
});

test('read-only metadata cannot be submitted and invalid values never issue a write', async () => {
  const viewer = mount({ canManage: false }); await viewer.flush();
  assert.equal(viewer.field('Relationship display name').props.disabled, true);
  assert.equal(viewer.button('Save changes'), undefined); edit(viewer, 'Forbidden'); await save(viewer);
  assert.equal(viewer.writes.length, 0);
  const ui = mount(); await ui.flush();
  for (const name of ['   ', 'Contains\u0001control', 'x'.repeat(129)]) { edit(ui, name); await save(ui); }
  edit(ui, 'Valid', 'x'.repeat(4001)); await save(ui);
  assert.equal(ui.writes.length, 0); assert.equal(ui.notifications.filter(value => value.kind === 'error').length, 4);
});

test('late reads and saves cannot populate a different session and unmount aborts an in-flight write', async () => {
  const reading = deferred(), lateRead = mount({ read: () => reading.promise });
  lateRead.session.identity = 'another-user'; reading.resolve({ items: [relation()], canManage: true }); await lateRead.flush();
  assert.equal(lateRead.field('Relationship display name'), undefined);
  for (const unmount of [false, true]) {
    const request = deferred(), ui = mount({ write: () => request.promise }); await ui.flush(); edit(ui, 'Changed');
    const saving = save(ui);
    if (unmount) ui.unmount(); else ui.session.identity = 'another-user';
    request.resolve({ ...relation(), name: 'Changed', revision: 'new' }); await saving;
    assert.equal(ui.changed.length, 0); assert.equal(ui.notifications.length, 0);
    if (unmount) assert.equal(ui.writes[0][4].aborted, true);
  }
});

test('an initial read failure offers standard Toast retry and retry loads the actual metadata', async () => {
  let failure = true;
  const ui = mount({ read: async () => { if (failure) throw new Error('Metadata unavailable'); return { items: [relation()], canManage: true }; } });
  await ui.flush(); const readFailureNotice = ui.errors.findLast(args => args[1] === 'Relationship types unavailable');
  assert.equal(readFailureNotice[0], 'Metadata unavailable');
  assert.equal(nodes(ui.tree()).some(node => node.props?.role === 'alert'), false);
  failure = false; ui.errors.findLast(args => args[1] === 'Relationship types unavailable')[2](); await ui.flush();
  assert.equal(ui.field('Relationship display name').props.value, 'Works for');
});

const beginCreate = async ui => { ui.button('New relationship type').props.onClick(); ui.render(); await ui.flush(); };
const pickTarget = (ui, id = objectType().id) => { ui.field('New relationship target type').props.onChange({ target: { value: id } }); ui.render(); };

test('creating an incoming relationship submits its business definition and selects the saved type without creating an import rule', async () => {
  const ui = mount(); await ui.flush(); await beginCreate(ui); edit(ui, '  参与  ');
  ui.field('New relationship direction').props.onChange({ target: { value: 'incoming' } });
  ui.field('New relationship cardinality').props.onChange({ target: { value: 'one_to_many' } }); ui.render();
  pickTarget(ui); await save(ui); await ui.flush();
  assert.deepEqual(ui.creates[0].slice(0, 3), ['ts_ods', 'employees', { name: '参与', description: 'Changed description', cardinality: 'one_to_many', direction: 'incoming', target: { schema: 'ts_ods', table: 'type_0' } }]);
  assert.equal(ui.writes.length, 0); assert.deepEqual(ui.changed, ['new-type']); assert.deepEqual(ui.directions, ['incoming']);
  assert.equal(ui.field('Relationship display name').props.value, '参与');
  assert.equal(ui.button('Create relationship type'), undefined); assert.equal(ui.button('Save changes').props.disabled, true);
  assert.equal(nodes(ui.tree()).some(node => node.props?.className === 'storage-relationship-import'), false);
  const endpoints = nodes(ui.tree()).find(node => node.props?.className === 'storage-relationship-endpoints');
  assert.deepEqual(nodes(endpoints).filter(node => node.type === 'strong').map(node => node.props.children), ['type_0', 'employees']);
  assert.equal(ui.notifications.at(-1).value.content, 'Relationship type created'); assert.equal(ui.closed(), 0);
});

test('starting and cancelling type creation reviews existing edits and never writes an abandoned draft', async () => {
  const ui = mount(); await ui.flush(); edit(ui, 'Pending metadata');
  ui.button('New relationship type').props.onClick(); ui.render(); assert.ok(ui.confirm());
  assert.equal(ui.field('New relationship target type'), undefined);
  ui.confirm().props.onCancel(); ui.render(); assert.equal(ui.field('Relationship display name').props.value, 'Pending metadata');
  ui.button('New relationship type').props.onClick(); ui.render(); ui.confirm().props.onOk(); await ui.flush();
  assert.equal(ui.field('Relationship display name').props.value, ''); edit(ui, 'Unsaved creation');
  ui.button('Cancel creation').props.onClick(); ui.render(); assert.ok(ui.confirm());
  ui.confirm().props.onCancel(); ui.render(); assert.equal(ui.field('Relationship display name').props.value, 'Unsaved creation');
  ui.button('Cancel creation').props.onClick(); ui.render(); ui.confirm().props.onOk(); ui.render();
  assert.equal(ui.field('Relationship display name').props.value, 'Works for'); assert.equal(ui.creates.length, 0);
});

test('target type search is paginated and changing pages or search clears the old target selection', async () => {
  const ui = mount({ catalog: async params => catalogPage(Array.from({ length: params.offset ? 1 : 20 }, (_, i) => objectType(params.offset + i)), params.offset, 21) });
  await ui.flush(); await beginCreate(ui); pickTarget(ui);
  assert.equal(ui.button('Next target types').props.disabled, false);
  ui.button('Next target types').props.onClick(); ui.render(); await ui.flush();
  assert.deepEqual(ui.targetReads.map(call => call[0]), [{ search: '', limit: 20, offset: 0 }, { search: '', limit: 20, offset: 20 }]);
  assert.equal(ui.field('New relationship target type').props.value, '');
  assert.equal(ui.button('Next target types').props.disabled, true); assert.equal(ui.button('Previous target types').props.disabled, false);
  pickTarget(ui, objectType(20).id);
  ui.field('Find target object types').props.onChange({ target: { value: '  new search  ' } }); ui.render(); await ui.flush();
  assert.deepEqual(ui.targetReads.at(-1)[0], { search: 'new search', limit: 20, offset: 0 });
  assert.equal(ui.field('New relationship target type').props.value, ''); assert.equal(ui.button('Previous target types').props.disabled, true);
});

test('older target searches cannot replace current results and a failed catalog read can be retried through Toast', async () => {
  const requests = [], ui = mount({ catalog: (params, signal) => { const request = deferred(); requests.push({ params, signal, request }); return request.promise; } });
  await ui.flush(); await beginCreate(ui);
  ui.field('Find target object types').props.onChange({ target: { value: 'current' } }); ui.render(); await ui.flush();
  assert.equal(requests[0].signal.aborted, true);
  requests[1].request.resolve(catalogPage([objectType(2)])); await ui.flush();
  requests[0].request.resolve(catalogPage([objectType(1)])); await ui.flush();
  assert.deepEqual(nodes(ui.field('New relationship target type')).filter(node => node.type === 'option').map(node => node.props.value), ['', objectType(2).id]);
  ui.field('Find target object types').props.onChange({ target: { value: 'failure' } }); ui.render(); await ui.flush();
  requests[2].request.reject(new Error('Target catalog unavailable')); await ui.flush();
  const notice = ui.errors.findLast(args => args[1] === 'Target object types unavailable');
  assert.equal(notice[0], 'Target catalog unavailable'); assert.equal(ui.field('New relationship target type').props.disabled, true);
  notice[2](); ui.render(); await ui.flush(); requests[3].request.resolve(catalogPage([objectType(3)])); await ui.flush();
  assert.equal(ui.field('New relationship target type').props.disabled, false);
});

test('creation requires a target and valid name, preserving a duplicate-name draft for correction', async () => {
  let denied = true;
  const ui = mount({ create: async (_schema, _table, payload) => {
    if (denied) throw { response: { data: { detail: { code: 'relation_name_conflict' } } }, message: 'Name already used' };
    return { ...relation('3'), ...payload, id: '3', source: { schema: 'ts_ods', table: 'employees', fields: [] }, target: { ...payload.target, fields: [] } };
  } });
  await ui.flush(); await beginCreate(ui); edit(ui, 'Requires peer'); await save(ui); assert.equal(ui.creates.length, 0);
  pickTarget(ui); edit(ui, '\tInvalid control'); await save(ui); assert.equal(ui.creates.length, 0);
  edit(ui, 'Duplicate'); await save(ui); await ui.flush();
  assert.equal(ui.field('Relationship display name').props.value, 'Duplicate'); assert.equal(ui.field('Relationship display name').props.disabled, false);
  assert.ok(ui.field('New relationship target type')); assert.equal(ui.notifications.at(-1).value.className, 'explorer-toast');
  denied = false; edit(ui, 'Distinct role'); await save(ui); await ui.flush();
  assert.equal(ui.creates.length, 2); assert.deepEqual(ui.changed, ['3']);
  const viewer = mount({ canManage: false }); await viewer.flush(); assert.equal(viewer.button('New relationship type'), undefined);
});

test('an in-flight creation locks duplicate submits and closing, and a stale session or unmount cannot report success', async () => {
  for (const unmount of [false, true]) {
    const request = deferred(), ui = mount({ create: () => request.promise }); await ui.flush(); await beginCreate(ui);
    edit(ui, 'New link type'); pickTarget(ui); const first = save(ui); void save(ui); ui.render();
    ui.main().props.onCancel(); ui.button('Cancel creation').props.onClick(); ui.chooseOther(); ui.render();
    assert.equal(ui.creates.length, 1); assert.equal(ui.closed(), 0); assert.equal(ui.confirm(), undefined);
    assert.equal(ui.main().props.keyboard, false); assert.equal(ui.field('New relationship target type').props.disabled, true);
    if (unmount) ui.unmount(); else ui.session.identity = 'another-account';
    request.resolve({ ...relation('3'), name: 'New link type' }); await first; await ui.flush();
    assert.equal(ui.changed.length, 0); assert.equal(ui.notifications.length, 0);
    if (unmount) assert.equal(ui.creates[0][3].aborted, true);
  }
});

test('a self relationship is created once without a field-based import rule', async () => {
  const peer = { ...objectType(), id: 'ts_ods.employees', table: 'employees' };
  const ui = mount({ catalog: async () => catalogPage([peer]) }); await ui.flush(); await beginCreate(ui);
  edit(ui, 'Reports to'); pickTarget(ui, peer.id); await save(ui); await ui.flush();
  assert.equal(ui.creates.length, 1); assert.deepEqual(ui.creates[0][2].target, { schema: 'ts_ods', table: 'employees' });
  assert.equal(nodes(ui.tree()).some(node => node.props?.className === 'storage-relationship-import'), false);
});
