import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/pages/Pipeline/explorerFilterDraft.ts', import.meta.url), 'utf8');
const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
const exports = {};
vm.runInThisContext('(function(exports){' + code + '\n})')(exports);
const { missingExplorerFilterParts, firstIncompleteExplorerFilter, nextUnusedExplorerField, availableExplorerSortFields, firstIncompleteExplorerSort } = exports;
const rule = (id, patch = {}) => ({ id, field: 'name', operator: '=', value: 'Acme', active: true, join: 'and', ...patch });

test('adding another condition identifies only the controls still needing input', () => {
  assert.deepEqual(missingExplorerFilterParts(rule('empty', { field: '', operator: '', value: '  ' })), ['field', 'operator', 'value']);
  assert.deepEqual(firstIncompleteExplorerFilter([rule('filled'), rule('pending', { value: '' })]), { id: 'pending', parts: ['value'] });
  assert.equal(firstIncompleteExplorerFilter([rule('filled')]), undefined);
});

test('null and empty operators do not require a value but still require a field', () => {
  for (const operator of ['是  null', '不是  null', '是空的', '不是空的']) {
    assert.deepEqual(missingExplorerFilterParts(rule('unary', { operator, value: '' })), []);
    assert.deepEqual(missingExplorerFilterParts(rule('unary', { field: '', operator, value: '' })), ['field']);
  }
});

test('both range operators require exactly two populated values', () => {
  for (const operator of ['介于', '不介于']) {
    for (const value of ['', '10', '10, ', ',20', '10,20,30']) {
      assert.deepEqual(missingExplorerFilterParts(rule('range', { operator, value })), ['value']);
    }
    assert.deepEqual(missingExplorerFilterParts(rule('range', { operator, value: '10, 20' })), []);
  }
});

test('disabled parents suppress incomplete children and group boundaries are not inputs', () => {
  const rows = [rule('group-open-a', { active: false }), rule('group-open-nested-b'),
    rule('group-filter-nested-b', { value: '' }), rule('group-close-nested-b'), rule('group-close-a'), rule('outside')];
  assert.equal(firstIncompleteExplorerFilter(rows), undefined);
  rows.push(rule('last', { field: '', value: '' }));
  assert.deepEqual(firstIncompleteExplorerFilter(rows), { id: 'last', parts: ['field', 'value'] });
});

test('inactive rows and untouched placeholders permit the first new condition', () => {
  assert.equal(firstIncompleteExplorerFilter([rule('disabled', { field: '', value: '', active: false }),
    rule('placeholder-rule', { field: '', value: '' })]), undefined);
  assert.deepEqual(firstIncompleteExplorerFilter([rule('placeholder-rule', { value: '' })]), { id: 'placeholder-rule', parts: ['value'] });
});

test('new filter fields advance through unused fields and leave selection empty when exhausted', () => {
  const fields = ['id', 'name', 'city', 'employees'];
  assert.equal(nextUnusedExplorerField(fields, []), 'id');
  assert.equal(nextUnusedExplorerField(fields, ['id'], 'id'), 'name');
  assert.equal(nextUnusedExplorerField(fields, ['id', 'name', 'employees'], 'name'), 'city');
  assert.equal(nextUnusedExplorerField(fields, ['employees'], 'employees'), 'id');
  assert.equal(nextUnusedExplorerField(fields, fields, 'city'), '');
  assert.equal(firstIncompleteExplorerFilter([rule('a'), rule('b')]), undefined, 'manual repeated filter fields remain valid');
});

test('sort selectors omit fields used by other entries while retaining their own field', () => {
  const fields = ['id', 'name', 'city'];
  const sorts = [{ field: 'id', direction: 'asc' }, { field: 'city', direction: 'desc' }];
  assert.deepEqual(availableExplorerSortFields(fields, sorts, 0), ['id', 'name']);
  assert.deepEqual(availableExplorerSortFields(fields, sorts, 1), ['name', 'city']);
  assert.equal(nextUnusedExplorerField(fields, sorts.map(sort => sort.field)), 'name');
  assert.equal(nextUnusedExplorerField(fields, [...sorts.map(sort => sort.field), 'name']), '');
});

test('another sort cannot be added before the existing field selection is complete', () => {
  assert.equal(firstIncompleteExplorerSort([]), 0, 'the visible first placeholder also needs a selection');
  assert.equal(firstIncompleteExplorerSort([{ field:'', direction:'asc' }]), 0);
  assert.equal(firstIncompleteExplorerSort([{ field:'name', direction:'asc' }, { field:'  ', direction:'desc' }]), 1);
  assert.equal(firstIncompleteExplorerSort([{ field:'name', direction:'asc' }, { field:'city', direction:'desc' }]), undefined);
});

const editorCode = ts.transpileModule(readFileSync(new URL('../src/pages/Pipeline/ExplorerFilterEditor.tsx', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
}).outputText;
const jsx = (type, props, key) => ({ type, props, key });
const descendants = tree => tree && typeof tree === 'object' ? [tree, ...[tree.props?.children].flat(Infinity).flatMap(descendants)] : [];
const editor = {};
vm.runInThisContext('(function(require,exports){' + editorCode + '\n})')(name => {
  if (name === 'react/jsx-runtime') return { jsx, jsxs: jsx };
  if (name === 'react') return { useEffect() {}, useLayoutEffect() {}, useRef: current => ({ current }), useState: value => [value, () => {}] };
  if (name === './explorerFilterDraft') return exports;
  if (name === '@/components/ui') return { Button: 'Button', Dropdown: 'Dropdown', Input: 'Input' };
  return {};
}, editor);
const renderSortEditor = (sorts, onSortsChange = () => {}, onApply = () => {}, columnMode = true) => editor.default({
  rules: [], setRules() {}, sorts, onSortsChange, fields: ['id', 'name', 'city'], rows: [], formatValue: String, onApply, columnMode,
});

test('both filter join controls support native keyboard activation without submitting a form', () => {
  let rules = ['a', 'group-open-g', 'group-filter-g', 'group-close-g', 'b'].map(id => rule(id));
  const tree = editor.default({ rules, setRules: update => { rules = update(rules); }, fields: ['name'], rows: [], sorts: [], formatValue: String });
  const buttons = descendants(tree).filter(node => node.props?.className?.startsWith?.('flow-filter-join'));
  for (const [selector, index] of [['flow-filter-join', 0], ['flow-filter-join flow-filter-close-join', 3]]) {
    const control = buttons.find(node => node.props.className === selector);
    assert.equal(control.type, 'button');
    assert.equal(control.props.type, 'button');
    control.props.onClick();
    assert.equal(rules[index].join, 'or');
  }
});

test('sort field and direction edits stay drafts until Apply filters is chosen', () => {
  const sorts = [{ field: 'id', direction: 'asc' }, { field: 'name', direction: 'desc' }];
  const calls = [];
  let applied = 0;
  const tree = renderSortEditor(sorts, (next, apply) => calls.push({ next, apply }), () => applied++);
  const pickers = descendants(tree).filter(node => node.props?.labelClassName === 'flow-filter-sort-field-label');
  pickers[0].props.onChange('city');
  pickers[1].props.onChange('city');
  for (const picker of pickers) {
    descendants(picker.props.trailing).find(node => node.props?.className === 'flow-filter-sort-direction-toggle')
      .props.onClick({ stopPropagation() {} });
  }
  assert.equal(calls.length, 4);
  assert.ok(calls.every(call => call.apply === false));
  assert.deepEqual(calls[0].next, [{ field: 'city', direction: 'asc' }, sorts[1]]);
  assert.deepEqual(calls[1].next, [sorts[0], { field: 'city', direction: 'desc' }]);
  assert.equal(applied, 0);
  descendants(tree).find(node => node.props?.className === 'explorer-filter-apply-button').props.onClick();
  assert.equal(applied, 1);
});

test('changing an additional sort field preserves its picker identity', () => {
  const sorts = [{ field: 'id', direction: 'asc' }, { field: 'name', direction: 'desc' }];
  const additionalKey = tree => descendants(tree).filter(node => node.props?.className === 'explorer-filter-sort-field-wrap')[1].key;
  assert.equal(additionalKey(renderSortEditor(sorts)), additionalKey(renderSortEditor([sorts[0], { ...sorts[1], field: 'city' }])));
});

test('the shared editor keeps immediate primary sorting outside Ontology Explorer', () => {
  const applied = [];
  const tree = renderSortEditor([{ field: 'id', direction: 'asc' }], (_next, apply) => applied.push(apply), undefined, false);
  const picker = descendants(tree).find(node => node.props?.labelClassName === 'flow-filter-sort-field-label');
  picker.props.onChange('name');
  descendants(picker.props.trailing).find(node => node.props?.className === 'flow-filter-sort-direction-toggle')
    .props.onClick({ stopPropagation() {} });
  assert.deepEqual(applied, [true, true]);
});

test('Published sorting uses scalar fields while structured fields remain available for filters', () => {
  const fields = ['tags', 'id', 'media_url'];
  const sorts = [{ field: 'id', direction: 'asc' }];
  const calls = [];
  const props = { rules: [rule('tags', { field: 'tags' })], setRules() {}, sorts, onSortsChange: next => calls.push(next),
    fields, sortFields: ['id', 'media_url'], rows: [], formatValue: String, onApply() {} };
  const tree = editor.default(props);
  const filter = descendants(tree).find(node => node.props?.value === 'tags' && node.props?.fields);
  assert.deepEqual(filter.props.fields, fields);
  const picker = descendants(tree).find(node => node.props?.labelClassName === 'flow-filter-sort-field-label');
  assert.deepEqual(picker.props.fields, ['id', 'media_url']);
  descendants(tree).find(node => node.props?.['aria-label'] === 'Add sort field').props.onClick();
  assert.deepEqual(calls, [[sorts[0], { field: 'media_url', direction: 'asc' }]]);
  const full = editor.default({ ...props, sorts: calls[0] });
  assert.ok(descendants(full).find(node => node.props?.['aria-label'] === 'Add sort field').props.disabled);
  const pickers = descendants(full).filter(node => node.props?.labelClassName === 'flow-filter-sort-field-label');
  assert.deepEqual(pickers.map(item => item.props.fields), [['id'], ['media_url']]);
});
