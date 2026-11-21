import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const jsx = (type, props) => ({ type, props });
const nodes = tree => tree && typeof tree === 'object' ? [tree, ...[tree.props?.children].flat(Infinity).flatMap(nodes)] : [];
function compile(file, require) {
  const source = ts.transpileModule(readFileSync(new URL('../src/pages/Pipeline/' + file + (file.endsWith('.ts') ? '' : '.tsx'), import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const exports = {};
  vm.runInThisContext('(function(require,exports){' + source + '\n})')(require, exports);
  return exports;
}
const ui = name => {
  if (name === 'react/jsx-runtime') return { jsx, jsxs: jsx };
  if (name === '@/components/ui') return { Button: 'Button', Checkbox: 'Checkbox', Modal: 'Modal' };
  if (name === '@/components/ui/icons') return new Proxy({}, { get: (_, key) => String(key) });
  if (name === './PropertyTypeIcon') return { PropertyLabel: 'PropertyLabel' };
  return { default: name };
};
const mutation = compile('ExplorerRecordMutation', ui);

function publishedTools(request = async () => {}, actions = [{ kind: 'delete', fields: [] }]) {
  const states = [], refs = [], calls = [], notifications = [];
  let cursor = 0, result;
  const record = { id: 'record', title: 'Synthetic object', revision: 7, properties: { caption: 'Original' } };
  const objectType = { id: 'synthetic-type', definition: { id: 'synthetic', name: 'Synthetic type', source: { mode: 'none' }, properties: [{ id: 'caption', name: 'Caption', type: 'string', required: false }] } };
  const module = compile('PublishedObjectTools', name => {
    if (name === 'react') return {
      useState(initial) { const i = cursor++; if (!(i in states)) states[i] = initial; return [states[i], next => { states[i] = typeof next === 'function' ? next(states[i]) : next; }]; },
      useRef(initial) { return refs[cursor++] ||= { current: initial }; }, useEffect() {},
    };
    if (name === '@/components/ui') return { ...ui(name), Input: 'Input', message: Object.fromEntries(['success', 'error'].map(kind => [kind, value => notifications.push({ kind, value })])) };
    if (name === '../../services/session') return { getSessionState: () => ({ identity: 'synthetic-user', authenticated: true, locked: false }) };
    if (name === '../../services/ontology') return { ontologyError: error => error.message, submitOntologyAction: async (...args) => { calls.push(args); await request(...args); } };
    if (name === './ExplorerRecordMutation') return mutation;
    return ui(name);
  });
  const render = () => { cursor = 0; result = module.usePublishedObjectTools({ objectType, record, actions, returnTo: '/ontology', onChanged() {} }); return result; };
  render();
  return { record, calls, notifications, render, result: () => result, dialog: () => {
    const element = nodes(result.dialogs).find(node => node.type === mutation.default && node.props.mode === 'delete');
    return element?.type(element.props);
  } };
}

function selection(tools, record) {
  const module = compile('ExplorerSelection', name => {
    if (name === 'react') return { useState: initial => [initial, () => {}], useRef: current => ({ current }), useEffect() {}, useId: () => 'panel' };
    if (name === './ExplorerObjectOverview') return { ExplorerObjectOverview: 'Overview', ExplorerTypeIcon: 'TypeIcon' };
    if (name === './ExplorerRowLinks') return { useExplorerLinkAvailability: () => ({}) };
    if (name === './useExplorerErrorToast') return { default() {} };
    return ui(name);
  });
  const row = { record: record.properties, identity: { $recordId: record.id }, key: record.id, title: record.title, columnTypes: { caption: 'string' } };
  return module.default({ source: { kind: 'published', definitionId: 'synthetic-type' }, schema: '', table: 'synthetic', rows: [row.record],
    columns: ['caption'], dataColumns: [], selectedRows: [row], metadata: { presentation: { displayName: 'Synthetic type', groups: [] } },
    rowKey: () => record.id, rowIdentity: () => row.identity, rowVersion: () => '7', loading: false, locked: tools.mutationOpen,
    renderMutation: () => tools.renderMutation(record) });
}

test('object deletion opens an application modal and cancellation never submits a delete', async () => {
  let deleted = 0, cancelled = 0;
  const tree = mutation.default({ mode: 'delete', title: 'Synthetic object', identity: { id: '9007199254740993' },
    onSubmit: () => deleted++, onCancel: () => cancelled++ });
  assert.equal(tree.type, 'Modal');
  assert.equal(tree.props.open, true);
  assert.equal(tree.props.okButtonProps.danger, true);
  assert.equal(nodes(tree).some(node => node.props?.className === 'flow-explorer-row-mutation'), false);
  await tree.props.onCancel();
  assert.equal(deleted, 0); assert.equal(cancelled, 1);
});

test('a busy deletion locks confirmation, escape, mask and cancel until its request settles', async () => {
  let deleted = 0, cancelled = 0;
  const tree = mutation.default({ mode: 'delete', title: 'Synthetic object', identity: { id: 'one' }, busy: true,
    onSubmit: () => deleted++, onCancel: () => cancelled++ });
  assert.equal(tree.props.confirmLoading, true);
  assert.equal(tree.props.closable, false); assert.equal(tree.props.maskClosable, false); assert.equal(tree.props.keyboard, false);
  assert.equal(tree.props.cancelButtonProps.disabled, true);
  await tree.props.onCancel(); await tree.props.onOk();
  assert.equal(deleted, 0); assert.equal(cancelled, 0);
});

test('disabled deletion does not submit and successful confirmation calls only the supplied adapter', async () => {
  let deleted = 0;
  const props = { mode: 'delete', title: 'Synthetic object', identity: { id: 'one' }, onSubmit: () => deleted++, onCancel() {} };
  const blocked = mutation.default({ ...props, disabled: true });
  assert.equal(blocked.props.okButtonProps.disabled, true);
  await blocked.props.onOk(); assert.equal(deleted, 0);
  await mutation.default(props).props.onOk(); assert.equal(deleted, 1);
});

test('shared editors expose null only through field capability and lock nullable controls while saving', () => {
  let nullValue;
  const fields = [
    { key: 'required', label: 'Required field', type: 'text', value: 'Required value', onChange() {} },
    { key: 'nullable', label: 'Optional field', type: 'text', value: 'Previous value', onChange() {}, nullControl: { checked: false, onChange: value => { nullValue = value; } } },
  ];
  const props = { mode: 'edit', title: 'Synthetic object', identity: { id: 'one' }, fields, onSubmit() {}, onCancel() {} };
  const tree = mutation.default(props);
  const controls = nodes(tree).filter(node => node.type === 'Checkbox');
  assert.equal(controls.length, 1); assert.equal(controls[0].props['aria-label'], 'Set Optional field to null');
  controls[0].props.onChange({ target: { checked: true } }); assert.equal(nullValue, true);
  const saving = mutation.default({ ...props, busy: true });
  assert.equal(nodes(saving).find(node => node.type === 'Checkbox').props.disabled, true);
});

test('Published delete keeps the actual selection Overview while using the separate dialog slot', () => {
  const tools = publishedTools();
  tools.result().actionsForRecord(tools.record)[0].onClick(); tools.render();
  assert.equal(tools.result().renderMutation(tools.record), null);
  assert.equal(tools.dialog().type, 'Modal');
  const panel = selection(tools.result(), tools.record);
  assert.ok(nodes(panel).some(node => node.type === 'Overview'));
  assert.ok(nodes(panel).some(node => node.props?.['aria-label'] === 'Selected object view'));
  tools.dialog().props.onCancel(); tools.render();
  assert.equal(tools.dialog(), undefined); assert.equal(tools.calls.length, 0);
});

test('a Published delete preserves its reviewed revision and reports failure through standard Toast', async () => {
  const tools = publishedTools(async () => { throw new Error('Synthetic delete denied'); });
  tools.result().actionsForRecord(tools.record)[0].onClick(); tools.render();
  await tools.dialog().props.onOk(); tools.render();
  assert.deepEqual(tools.calls, [['synthetic-type', 'delete', { recordId: tools.record.id, expectedRevision: 7 }]]);
  assert.ok(tools.dialog()); assert.equal(tools.result().renderMutation(tools.record), null);
  assert.equal(tools.notifications[0].kind, 'error'); assert.equal(tools.notifications[0].value.className, 'explorer-toast');
});

test('Storage null toggles preserve the edited draft and submit a real null rather than the text null', () => {
  const editing = compile('explorerRowEditing.ts', () => ({}));
  const remembered = {}; let draft = 'Edited text';
  const control = () => editing.rowNullControl('caption', draft, remembered, value => { draft = value; });
  control().onChange(true); assert.equal(draft, null);
  assert.deepEqual({ ...editing.rowChanges({ caption: draft }, { caption: 'Original' }, { caption: 'text' }) }, { caption: null });
  control().onChange(false); assert.equal(draft, 'Edited text');
  draft = null;
  const unset = editing.rowNullControl('new_record', draft, {}, value => { draft = value; });
  unset.onChange(false); assert.equal(draft, '');
});

test('Published optional fields preserve the same edited draft across Set to null and reset', () => {
  const tools = publishedTools(undefined, [{ kind: 'modify', fields: ['caption'] }]);
  tools.result().actionsForRecord(tools.record)[0].onClick(); tools.render();
  const field = () => tools.result().renderMutation(tools.record).props.fields[0];
  field().onChange('Edited text'); tools.render();
  field().nullControl.onChange(true); tools.render();
  assert.equal(field().nullControl.checked, true);
  field().nullControl.onChange(false); tools.render();
  assert.equal(field().value, 'Edited text'); assert.equal(field().nullControl.checked, false);
});
