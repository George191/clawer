import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/pages/Pipeline/explorerFilters.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
const exports = {};
vm.runInThisContext('(function(exports){' + compiled + '\n})')(exports);
const { activeExplorerFilters, blankExplorerFilter, hasExplorerFilterInput } = exports;
const rule = (id, patch = {}) => ({ ...blankExplorerFilter(id), ...patch });

test('column filter indicators omit incomplete and disabled conditions but include unary filters', () => {
  const rows = [rule('blank', { field: 'name' }), rule('disabled', { field: 'city', value: 'Shanghai', active: false }),
    rule('value', { field: 'employees', value: '50' }), rule('null', { field: 'founded', operator: '是  null' })];
  assert.deepEqual(activeExplorerFilters(rows).map(row => row.id), ['value', 'null']);
});

test('disabled nested groups do not mark their fields as filtered', () => {
  const rows = [rule('group-open-outer'), rule('outer', { field: 'city', value: 'Shanghai' }),
    rule('group-open-nested-inner', { active: false }), rule('inner', { field: 'name', value: 'Hidden' }),
    rule('group-close-nested-inner'), rule('group-close-outer')];
  assert.deepEqual(activeExplorerFilters(rows), [rows[1]]);
});

test('multiple conditions on one column retain their row identities and order for navigation', () => {
  const rows = [rule('lower', { field: 'employees', operator: '>=', value: '40' }),
    rule('upper', { field: 'employees', operator: '<=', value: '80' })];
  const result = activeExplorerFilters(rows);
  assert.equal(result[0], rows[0]);
  assert.equal(result[1], rows[1]);
});

test('default field-only rows do not mark the query dirty but entered and unary conditions do', () => {
  assert.equal(hasExplorerFilterInput(rule('initial', { field: 'id' })), false);
  assert.equal(hasExplorerFilterInput(rule('unary', { field: 'name', operator: '是空的' })), true);
  assert.equal(hasExplorerFilterInput(rule('range-draft', { field: 'employees', operator: '介于', value: '10,' })), true);
  assert.equal(hasExplorerFilterInput(rule('missing-field', { value: '10' })), false);
});

const workbenchCode = ts.transpileModule(readFileSync(new URL('../src/pages/Pipeline/ExplorerWorkbench.tsx', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
}).outputText;
const jsx = (type, props) => ({ type, props });
const descendants = tree => tree && typeof tree === 'object' ? [tree, ...[tree.props?.children].flat(Infinity).flatMap(descendants)] : [];
const workbench = {};
vm.runInThisContext('(function(require,exports){' + workbenchCode + '\n})')(name => {
  if (name === 'react/jsx-runtime') return { jsx, jsxs: jsx };
  if (name === './explorerFilters') return exports;
  if (name === '@/components/ui') return { Dropdown: 'Dropdown', Button: 'Button' };
  if (name === './ExplorerResultLayout') return { LayoutHeader: 'LayoutHeader' };
  return {};
}, workbench);

test('Published JSON column actions cannot sort but scalar media URLs remain sortable', () => {
  const sorted = [], filtered = [];
  const columns = workbench.explorerResultColumns({
    layout: { columns: ['tags', 'media_url'].map(field => ({ field, visible: true, width: 100 })), layout: { wrap: false }, ready: true },
    sorts: [], sortFields: ['media_url'], addFilter: field => filtered.push(field), openFilter() {},
    setSort: (field, direction) => sorted.push({ field, direction }), clearSorts() {}, filterValue() {},
  });
  const menus = columns.map(column => descendants(column.title).find(node => node.props?.menu).props.menu);
  assert.ok(menus[0].items.filter(item => ['asc', 'desc'].includes(item.key)).every(item => item.disabled));
  assert.ok(menus[1].items.filter(item => ['asc', 'desc'].includes(item.key)).every(item => !item.disabled));
  menus[0].onClick({ key: 'asc' });
  menus[0].onClick({ key: 'filter' });
  assert.deepEqual(sorted, []);
  assert.deepEqual(filtered, ['tags']);
  menus[1].onClick({ key: 'desc' });
  assert.deepEqual(sorted, [{ field: 'media_url', direction: 'desc' }]);
});

test('SQL NULL has a muted marker for scalar, JSON and array fields without changing literal values', () => {
  for (const type of ['text', 'jsonb', 'text[]']) {
    const [column] = workbench.explorerResultColumns({
      layout: { columns: [{ field: 'value', visible: true, width: 100 }], layout: { wrap: false } },
      types: { value: type }, sorts: [], addFilter() {}, openFilter() {}, setSort() {}, clearSorts() {}, filterValue() {},
    });
    const nullMarker = descendants(column.render(null)).find(node => node.props?.className === 'flow-explorer-null');
    assert.equal(nullMarker.props.children, '(Null)');
    for (const value of ['', 'null', '(Null)']) {
      assert.ok(!descendants(column.render(value)).some(node => node.props?.className === 'flow-explorer-null'));
    }
  }
});
