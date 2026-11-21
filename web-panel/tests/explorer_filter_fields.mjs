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
