import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/pages/Pipeline/explorerFilterDraft.ts', import.meta.url), 'utf8');
const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
const exports = {};
vm.runInThisContext('(function(exports){' + code + '\n})')(exports);
const { missingExplorerFilterParts, firstIncompleteExplorerFilter, nextUnusedExplorerField, availableExplorerSortFields } = exports;
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
