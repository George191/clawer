import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const jsx = (type, props) => ({ type, props });
const nodes = tree => tree && typeof tree === 'object' ? [tree, ...[tree.props?.children].flat(Infinity).flatMap(nodes)] : [];
const text = tree => typeof tree === 'string' || typeof tree === 'number' ? String(tree) : tree && typeof tree === 'object' ? [tree.props?.children].flat(Infinity).map(text).join('') : '';
function compile(name, require) {
  const source = readFileSync(new URL('../src/pages/Pipeline/' + name, import.meta.url), 'utf8');
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  const exports = {};
  vm.runInThisContext('(function(require,exports){' + code + '\n})')(require, exports);
  return exports;
}
const values = compile('ontologyPropertyValue.ts', () => ({}));
const mutations = compile('ExplorerRecordMutation.tsx', name => {
  if (name === 'react/jsx-runtime') return { jsx, jsxs: jsx };
  if (name === '@/components/ui') return { Button: 'Button', Checkbox: 'Checkbox' };
  if (name === '@/components/ui/icons') return new Proxy({}, { get: (_, key) => String(key) });
  if (name === './PropertyTypeIcon') return { PropertyLabel: 'PropertyLabel' };
  return { default: name };
});
const objectType = { id: 'qa-type', revision: 2, definition: { id: 'qa-object', name: 'QA object', source: { mode: 'none' }, properties: [
  { id: 'id', name: 'Id', type: 'string', required: true }, { id: 'metadata', name: 'Metadata', type: 'json', required: true },
  { id: 'count', name: 'Count', type: 'number', required: false },
] } };
const createAction = { kind: 'create', fields: ['id', 'metadata', 'count'] };

function mountTools(patch = {}, request = async () => {}) {
  const states = [], refs = [], calls = [], notifications = [];
  const session = { identity: 'qa', authenticated: true, locked: false };
  let cursor = 0, result, changed = 0;
  const input = Object.assign(() => null, { TextArea: 'TextArea' });
  const module = compile('PublishedObjectTools.tsx', name => {
    if (name === 'react/jsx-runtime') return { jsx, jsxs: jsx };
    if (name === 'react') return {
      useState(initial) { const index = cursor++; if (!(index in states)) states[index] = initial; return [states[index], next => { states[index] = typeof next === 'function' ? next(states[index]) : next; }]; },
      useRef(initial) { const index = cursor++; return refs[index] ||= { current: initial }; }, useEffect() {},
    };
    if (name === '@/components/ui') return { Button: 'Button', Checkbox: 'Checkbox', Input: input, Modal: 'Modal', message: {
      success: value => notifications.push(value), error: value => notifications.push(value),
    } };
    if (name === '@/components/ui/icons') return new Proxy({}, { get: (_, key) => String(key) });
    if (name === '../../services/session') return { getSessionState: () => session };
    if (name === '../../services/ontology') return { ontologyError: (error, fallback) => error.message || fallback,
      submitOntologyAction: async (...args) => { calls.push(args); await request(...args); },
    };
    if (name === './ontologyPropertyValue') return values;
    if (name === './ExplorerRecordMutation') return mutations;
    if (name === './useExplorerErrorToast') return { default() {} };
    return { default: name };
  });
  const render = () => { cursor = 0; result = module.usePublishedObjectTools({ objectType, actions: [createAction], onChanged: () => changed++, ...patch }); return result; };
  render();
  return { render, result: () => result, calls, notifications, session, changed: () => changed,
    dialog: () => nodes(result.dialogs).find(node => node.type === 'Modal' && node.props.open && node.props.title === 'Create object'),
  };
}

test('creation remains restricted to permitted native object types and respects busy state', () => {
  assert.ok(mountTools().result().createButton);
  assert.equal(mountTools({ actions: [] }).result().createButton, null);
  assert.equal(mountTools({ objectType: { ...objectType, definition: { ...objectType.definition, source: { mode: 'existing' } } } }).result().createButton, null);
  const busy = mountTools({ busy: true });
  assert.equal(busy.result().createButton.props.disabled, true);
  busy.result().createButton.props.onClick(); busy.render();
  assert.equal(busy.dialog(), undefined);
});

test('the create form preserves required validation, parses JSON and sends explicit nulls', async () => {
  const ui = mountTools();
  ui.result().createButton.props.onClick(); ui.render();
  assert.ok(ui.dialog().props.className.includes('published-create-object-dialog'));
  assert.equal(ui.dialog().props.okText, 'Create object');
  const field = label => nodes(ui.dialog()).find(node => node.props?.['aria-label'] === label);
  assert.equal(field('Metadata').type, 'TextArea');
  assert.equal(field('Id').props['aria-required'], true);
  await ui.dialog().props.onOk(); ui.render();
  assert.ok(nodes(ui.dialog()).some(node => node.props?.role === 'alert' && text(node).includes('Id is required')));
  assert.equal(ui.calls.length, 0);
  field('Id').props.onChange({ target: { value: 'A-17' } });
  field('Metadata').props.onChange({ target: { value: '{bad' } }); ui.render();
  await ui.dialog().props.onOk(); ui.render();
  assert.ok(nodes(ui.dialog()).some(node => node.props?.role === 'alert' && text(node).includes('valid JSON')));
  assert.equal(ui.calls.length, 0);
  field('Metadata').props.onChange({ target: { value: '{"tags":["photo","pdf"]}' } });
  field('Set Count to null').props.onChange({ target: { checked: true } }); ui.render();
  await ui.dialog().props.onOk(); ui.render();
  assert.deepEqual(ui.calls, [[objectType.id, 'create', { values: { id: 'A-17', metadata: { tags: ['photo', 'pdf'] }, count: null } }]]);
  assert.equal(ui.dialog(), undefined);
  assert.equal(ui.changed(), 1);
});

test('the create control follows Open in without occupying the left toolbar', () => {
  const module = compile('ExplorerWorkbenchHeader.tsx', name => {
    if (name === 'react/jsx-runtime') return { jsx, jsxs: jsx };
    if (name === 'react') return { useState: initial => [initial, () => {}], useRef: current => ({ current }), useEffect() {}, useMemo: read => read(), useId: () => 'test' };
    if (name === '@/components/ui') return { Button: 'Button', Dropdown: 'Dropdown', Modal: 'Modal' };
    if (name === '@/components/ui/icons') return new Proxy({}, { get: (_, key) => String(key) });
    if (name === '../../services/explorerSaved') return { publishedSavedPath: () => ({ path: '/saved' }) };
    if (name === './ExplorerExploreView') return { chooseExploreField: () => '' };
    if (name === './useExplorerAggregation') return { default: () => ({}) };
    return { default: name };
  });
  const control = jsx('Button', { children: 'Create object' });
  const tree = module.default({ source: { kind: 'published', definitionId: objectType.id }, label: 'QA object', available: true,
    fields: [], rows: [], selected: [], query: {}, resource: 'preview', availableResources: ['preview'], mode: 'results', createControl: control,
  });
  const right = nodes(tree).find(node => node.props?.className === 'wb-right');
  assert.equal(right.props.children[1], control);
  assert.ok(text(right.props.children[0]).includes('Open in'));
  assert.ok(!nodes(nodes(tree).find(node => node.props?.className === 'wb-left')).includes(control));
});

test('edit and delete use the common inline editor and confirmation without a second modal flow', () => {
  const record = { id: 'record', revision: 1, title: 'Photo', properties: { metadata: { tags: ['photo'] } } };
  for (const kind of ['modify', 'delete']) {
    const ui = mountTools({ actions: [{ kind, fields: kind === 'modify' ? ['metadata'] : [] }] });
    const action = ui.result().actionsForRecord(record)[0];
    assert.equal(action.label, kind === 'modify' ? 'Edit' : 'Delete');
    assert.equal(action.icon.type, kind === 'modify' ? 'EditOutlined' : 'DeleteOutlined');
    action.onClick(); ui.render();
    assert.equal(nodes(ui.result().dialogs).some(node => node.type === 'Modal' && node.props.open), false);
    const mutation = ui.result().renderMutation(record);
    assert.equal(mutation.type, mutations.default);
    assert.equal(ui.result().mutationOpen, true);
    assert.equal(ui.result().renderMutation({ ...record, id: 'another-record' }), null);
    const tree = mutation.type(mutation.props);
    assert.equal(tree.props['aria-label'], kind === 'modify' ? 'Edit object' : 'Confirm object deletion');
    assert.ok(nodes(tree).some(node => node.type === 'Button' && text(node) === 'Cancel'));
    if (kind === 'modify') {
      const editor = nodes(tree).find(node => node.type === './ExplorerFieldEditor');
      assert.equal(editor.props.type, 'json');
      assert.equal(editor.props.value, '{"tags":["photo"]}');
      assert.ok(nodes(tree).some(node => node.type === 'footer' && nodes(node).some(button => text(button) === 'Save changes')));
    } else assert.ok(text(tree).includes('Delete Photo?'));
  }
});

const record = { id: 'record', revision: 7, title: 'Photo', properties: { id: 'immutable', metadata: { tags: ['photo'] }, count: 1 } };
const editAction = { kind: 'modify', fields: ['metadata', 'count'] };
function startEdit(ui) {
  ui.result().actionsForRecord(record).find(action => action.key === 'edit').onClick();
  ui.render();
  return () => ui.result().renderMutation(record);
}

test('common edit fields preserve the server action grant, native parsing and reviewed revision', async () => {
  const ui = mountTools({ actions: [editAction] });
  const editor = startEdit(ui);
  assert.deepEqual(editor().props.fields.map(field => field.key), ['metadata', 'count']);
  editor().props.fields[0].onChange('{"tags":["pdf"]}');
  editor().props.fields[1].onChange('2.5'); ui.render();
  await editor().props.onSubmit(); ui.render();
  assert.deepEqual(ui.calls, [[objectType.id, 'modify', { recordId: record.id, expectedRevision: 7, values: { metadata: { tags: ['pdf'] }, count: 2.5 } }]]);
  assert.equal(ui.result().mutationOpen, false);
  assert.equal(ui.changed(), 1);
});

test('no-change save closes the editor without a write or fabricated refresh', async () => {
  const ui = mountTools({ actions: [editAction] }), editor = startEdit(ui);
  await editor().props.onSubmit(); ui.render();
  assert.equal(ui.result().mutationOpen, false);
  assert.equal(ui.calls.length, 0); assert.equal(ui.changed(), 0); assert.equal(ui.notifications.length, 0);
});

test('native field validation stays inline while null controls preserve the previous typed value', async () => {
  const ui = mountTools({ actions: [editAction] }), editor = startEdit(ui);
  editor().props.fields[0].onChange('{bad'); ui.render();
  await editor().props.onSubmit(); ui.render();
  assert.match(editor().props.error, /valid JSON/);
  assert.equal(ui.calls.length, 0); assert.equal(ui.notifications.length, 0);
  editor().props.fields[0].onChange(null); ui.render();
  await editor().props.onSubmit(); ui.render();
  assert.match(editor().props.error, /Metadata is required/);
  assert.equal(ui.calls.length, 0);
  editor().props.fields[0].onChange('{"tags":["photo"]}');
  editor().props.fields[1].onChange('6.5'); ui.render();
  editor().props.fields[1].nullControl.onChange(true); ui.render();
  assert.equal(editor().props.fields[1].value, '6.5');
  editor().props.fields[1].nullControl.onChange(false); ui.render();
  assert.equal(editor().props.fields[1].value, '6.5');
  editor().props.fields[1].nullControl.onChange(true); ui.render();
  await editor().props.onSubmit(); ui.render();
  assert.deepEqual(ui.calls[0][2], { recordId: record.id, expectedRevision: 7, values: { count: null } });
});

test('read-only, mapped, missing-version and busy records cannot open the common edit flow', () => {
  assert.deepEqual(mountTools({ actions: [] }).result().actionsForRecord(record), []);
  assert.deepEqual(mountTools({ actions: [editAction], objectType: { ...objectType, definition: { ...objectType.definition, source: { mode: 'existing' } } } }).result().actionsForRecord(record), []);
  assert.deepEqual(mountTools({ actions: [editAction] }).result().actionsForRecord({ ...record, revision: undefined }), []);
  const busy = mountTools({ actions: [editAction], busy: true });
  const action = busy.result().actionsForRecord(record)[0];
  assert.equal(action.disabled, true); action.onClick(); busy.render();
  assert.equal(busy.result().mutationOpen, false);
});

test('failed writes retain the reviewed edit and use standard Toast while duplicate submissions are blocked', async () => {
  let fail;
  const ui = mountTools({ actions: [editAction] }, () => new Promise((_resolve, reject) => { fail = reject; }));
  const editor = startEdit(ui);
  editor().props.fields[1].onChange('9'); ui.render();
  const apply = editor().props.onSubmit;
  const first = apply(), second = apply();
  assert.equal(ui.calls.length, 1);
  ui.render(); assert.equal(editor().props.busy, true);
  editor().props.onCancel(); ui.render(); assert.equal(ui.result().mutationOpen, true);
  fail(new Error('Record changed. Refresh, review and try again.'));
  await Promise.all([first, second]); ui.render();
  assert.equal(editor().props.fields[1].value, '9');
  assert.equal(editor().props.error, '');
  assert.equal(ui.changed(), 0);
  assert.equal(ui.notifications.length, 1);
  assert.equal(ui.notifications[0].className, 'explorer-toast');
  assert.equal(ui.notifications[0].description, 'Record changed. Refresh, review and try again.');
});

test('delete submits the object revision only after common confirmation and stale sessions cannot submit', async () => {
  const ui = mountTools({ actions: [{ kind: 'delete', fields: [] }] });
  ui.result().actionsForRecord(record)[0].onClick(); ui.render();
  assert.equal(ui.calls.length, 0);
  await ui.result().renderMutation(record).props.onSubmit(); ui.render();
  assert.deepEqual(ui.calls, [[objectType.id, 'delete', { recordId: record.id, expectedRevision: 7 }]]);
  for (const change of [session => { session.identity = 'another'; }, session => { session.locked = true; }]) {
    const stale = mountTools({ actions: [editAction] }), editor = startEdit(stale);
    editor().props.fields[1].onChange('3'); stale.render();
    change(stale.session); await editor().props.onSubmit();
    assert.equal(stale.calls.length, 0); assert.equal(stale.notifications.length, 0);
  }
});

test('the shared editor blocks disabled submissions for both physical and semantic field descriptors', async () => {
  for (const type of ['bigint', 'number']) {
    let writes = 0;
    const tree = mutations.default({ mode: 'edit', title: 'Record', identity: { id: '1' }, disabled: true,
      fields: [{ key: 'count', label: 'Count', type, value: '2', onChange() {} }], onSubmit: () => writes++, onCancel() {} });
    const field = nodes(tree).find(node => node.type === './ExplorerFieldEditor');
    assert.equal(field.props.disabled, true);
    await nodes(tree).find(node => node.type === 'form').props.onSubmit({ preventDefault() {} });
    assert.equal(writes, 0);
  }
});
