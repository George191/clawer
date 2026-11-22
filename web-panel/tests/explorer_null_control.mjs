import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const jsx = (type, props) => ({ type, props });
const nodes = tree => tree && typeof tree === 'object' ? [tree, ...[tree.props?.children].flat(Infinity).flatMap(nodes)] : [];
function compile(file, require) {
  const source = ts.transpileModule(readFileSync(new URL('../src/' + file, import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const exports = {};
  vm.runInThisContext('(function(require,exports){' + source + '\n})')(require, exports);
  return exports;
}
const propertyTypes = compile('services/explorerProperties.ts', () => ({}));
const editing = compile('pages/Pipeline/explorerRowEditing.ts', () => ({}));
const field = compile('pages/Pipeline/ExplorerFieldEditor.tsx', name => {
  if (name === 'react/jsx-runtime') return { jsx, jsxs: jsx };
  if (name === '../../services/explorerProperties') return propertyTypes;
  if (name === '@/components/ui') return { NativeSelect: 'NativeSelect' };
  return {};
}).default;
const mutation = compile('pages/Pipeline/ExplorerRecordMutation.tsx', name => {
  if (name === 'react/jsx-runtime') return { jsx, jsxs: jsx };
  if (name === './ExplorerFieldEditor') return { default: field };
  if (name === './PropertyTypeIcon') return { PropertyLabel: 'PropertyLabel' };
  if (name === '@/components/ui') return { Button: 'Button', Modal: 'Modal' };
  if (name === '@/components/ui/icons') return new Proxy({}, { get: (_, key) => String(key) });
  return {};
}).default;
const native = tree => nodes(tree).find(node => ['input', 'textarea', 'NativeSelect'].includes(node.type));
const toggle = tree => nodes(tree).find(node => node.type === 'button');
const event = value => ({ target: { value } });

test('nullable controls live beside the actual field inside the shared editor without a separate checkbox row', () => {
  const props = { mode: 'edit', title: 'Object', onSubmit() {}, onCancel() {}, fields: [
    { key: 'required', label: 'Required', type: 'text', value: 'Value', onChange() {} },
    { key: 'optional', label: 'Optional', type: 'text', value: 'Value', onChange() {}, nullControl: { checked: false, onChange() {} } },
  ] };
  const editors = nodes(mutation(props)).filter(node => node.type === field);
  assert.equal(toggle(field(editors[0].props)), undefined);
  const optional = field(editors[1].props), button = toggle(optional);
  const control = nodes(optional).find(node => node.props?.className?.includes('explorer-field-editor__control'));
  assert.ok(control.props.children.includes(native(optional)));
  assert.ok(control.props.children.includes(button));
  assert.equal(button.props.type, 'button');
  assert.equal(button.props['aria-label'], 'Set Optional to null');
  assert.equal(button.props['aria-pressed'], false);
});

test('setting null disables only the value input and restoring it retains the last edited draft', () => {
  const remembered = {}; let draft = 'Original';
  const render = () => field({ name: 'Caption', type: 'text', value: draft, onChange: value => { draft = value; },
    nullControl: editing.rowNullControl('caption', draft, remembered, value => { draft = value; }) });
  native(render()).props.onChange(event('Edited text'));
  toggle(render()).props.onClick();
  assert.equal(draft, null);
  const empty = render();
  assert.equal(native(empty).props.disabled, true);
  assert.equal(native(empty).props.value, '');
  assert.equal(toggle(empty).props.disabled, undefined);
  assert.equal(toggle(empty).props['aria-pressed'], true);
  assert.equal(toggle(empty).props.title, 'Restore previous value');
  native(empty).props.onChange(event('Ignored while null'));
  assert.equal(draft, null);
  toggle(empty).props.onClick();
  assert.equal(draft, 'Edited text');
  assert.equal(native(render()).props.disabled, false);
});

test('the shared native and null controls both reject edits while their save is busy or disabled', () => {
  for (const type of ['text', 'boolean', 'jsonb', 'integer', 'date']) {
    let changes = 0;
    const editor = field({ name: 'Value', type, value: 'true', disabled: true,
      onChange: () => changes++, nullControl: { checked: false, onChange: () => changes++ } });
    assert.equal(native(editor).props.disabled, true);
    assert.equal(toggle(editor).props.disabled, true);
    native(editor).props.onChange(event('false')); toggle(editor).props.onClick();
    assert.equal(changes, 0);
  }
});

test('inline null controls preserve empty string, scalar clearing, JSON and exact numeric input semantics', () => {
  for (const [type, expected] of [['text', ''], ['numeric', null], ['integer', null], ['date', null], ['timestamp with time zone', null], ['jsonb', null], ['boolean', null]]) {
    let value;
    const editor = field({ name: 'Value', type, value: '', onChange: next => { value = next; }, nullControl: { checked: false, onChange() {} } });
    native(editor).props.onChange(event(''));
    assert.equal(value, expected, type);
  }
  let exact;
  native(field({ name: 'Identifier', type: 'bigint', value: '', onChange: value => { exact = value; } })).props.onChange(event('9007199254740993'));
  assert.equal(exact, '9007199254740993');
  let boolean;
  native(field({ name: 'Active', type: 'boolean', value: '', onChange: value => { boolean = value; } })).props.onChange(event('false'));
  assert.equal(boolean, 'false');
});

test('textarea null actions share its control and datetime guidance remains separate from the field action', () => {
  for (const type of ['jsonb', 'timestamp with time zone']) {
    let restored;
    const editor = field({ name: 'Details', type, value: 'Previous draft', onChange() {}, nullControl: { checked: true, onChange: next => { restored = next; } } });
    assert.equal(native(editor).props.value, '');
    assert.equal(native(editor).props.placeholder, 'Null');
    toggle(editor).props.onClick(); assert.equal(restored, false);
    const control = nodes(editor).find(node => node.props?.className.includes('explorer-field-editor__control'));
    assert.ok(control.props.children.includes(toggle(editor)));
    assert.equal(nodes(control).some(node => node.type === 'small'), false);
    assert.equal(nodes(editor).some(node => node.type === 'small'), type.startsWith('timestamp'));
  }
});
