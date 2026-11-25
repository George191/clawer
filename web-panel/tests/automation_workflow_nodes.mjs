import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';
const jsx = (type, props) => ({ type, props });
const exported = {};
const source = readFileSync(new URL('../src/pages/AutomationCenter/WorkflowNodes.tsx', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText;
vm.runInNewContext(compiled, { exports: exported, require(name) {
  if (name === 'react/jsx-runtime') return { jsx, jsxs: jsx };
  return new Proxy({}, { get: (_, key) => String(key) });
} });
const nodes = tree => tree && typeof tree === 'object' ? [tree, ...[tree.props?.children].flat(Infinity).flatMap(nodes)] : [];
const initial = [{ name: 'Collect', type: 'collect', template: 'patents', metadata: { required: true } }, { name: 'Validate', type: 'validate' }];
const click = (tree, label) => nodes(tree).find(node => node.props?.['aria-label'] === label).props.onClick();
test('reordering steps preserves reference and metadata without mutating the saved definition', () => {
  let changed;
  const tree = exported.default({ value: initial, onChange: value => { changed = value; } });
  click(tree, 'Move step 1 down');
  assert.equal(changed[0].name, 'Validate');
  assert.equal(changed[1], initial[0]);
  assert.equal(initial[0].name, 'Collect');
});
test('editing one step preserves its other fields and does not alter other steps', () => {
  let changed;
  const tree = exported.default({ value: initial, onChange: value => { changed = value; } });
  nodes(tree).find(node => node.props?.['aria-label'] === 'Step 1 name').props.onChange({ target: { value: 'Collect patents' } });
  assert.equal(changed[0].name, 'Collect patents');
  assert.equal(changed[0].template, 'patents');
  assert.equal(changed[0].metadata, initial[0].metadata);
  assert.equal(changed[1], initial[1]);
});
test('adding and removing steps operates on the structured definition', () => {
  let changed;
  const tree = exported.default({ value: initial, onChange: value => { changed = value; } });
  nodes(tree).find(node => node.type === 'Button' && node.props.children === 'Add step').props.onClick();
  assert.equal(changed.length, 3);
  assert.equal(changed[2].type, 'task');
  click(tree, 'Remove step 1');
  assert.equal(changed.length, 1);
  assert.equal(changed[0], initial[1]);
});
test('boundary reorder buttons are disabled', () => {
  const tree = exported.default({ value: initial });
  assert.equal(nodes(tree).find(node => node.props?.['aria-label'] === 'Move step 1 up').props.disabled, true);
  assert.equal(nodes(tree).find(node => node.props?.['aria-label'] === 'Move step 2 down').props.disabled, true);
});
