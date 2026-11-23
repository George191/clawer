import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const source = ts.createSourceFile('index.tsx', readFileSync(new URL('../src/pages/Pipeline/index.tsx', import.meta.url), 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
let selection;
function visit(node) {
  if (ts.isVariableDeclaration(node) && node.name.getText(source) === 'selectTable') selection = node.initializer.getText(source);
  ts.forEachChild(node, visit);
}
visit(source);
assert.ok(selection, 'Missing production table selection handler');
const compiled = ts.transpileModule('exports.selectTable = ' + selection, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;

function setup(overrides = {}) {
  const state = { catalogOpen: true, navigation: [], warnings: [] };
  const context = {
    exports: {}, scriptDirty: false, apisDirty: false, mappingDirty: false,
    mutationPending: { current: false }, editingRowKey: undefined, table: { name: 'current' },
    requestedSchema: 'ts_ods', selectedSchema: 'ts_ods', requestedTable: 'current',
    search: '?schema=ts_ods&table=current&saved=old&returnTo=%2Fontology', workspacePath: '/ontology/explorer',
    URLSearchParams, message: { warning: text => state.warnings.push(text) },
    setCatalogOpen: open => { state.catalogOpen = open; }, setTable: table => { state.table = table; },
    navigate: url => state.navigation.push(url), ...overrides,
  };
  vm.runInNewContext(compiled, context);
  return { state, select: context.exports.selectTable };
}

test('selecting another table dismisses the catalog and opens that table', () => {
  const { state, select } = setup();
  const table = { name: 'next' };
  select(table);
  assert.equal(state.catalogOpen, false);
  assert.equal(state.table, table);
  assert.equal(state.table, table);
  assert.equal(state.navigation.length, 1);
  const params = new URL(state.navigation[0], 'http://localhost').searchParams;
  assert.equal(params.get('table'), 'next');
  assert.equal(params.get('returnTo'), '/ontology');
  assert.equal(params.has('saved'), false);
});

test('selecting the current table dismisses the catalog without navigating', () => {
  const { state, select } = setup();
  select({ name: 'current' });
  assert.equal(state.catalogOpen, false);
  assert.equal(state.navigation.length, 0);
});

test('an unsaved edit or pending change keeps the catalog open and blocks switching', () => {
  for (const blocked of [{ scriptDirty: true }, { apisDirty: true }, { mappingDirty: true }, { mutationPending: { current: true } }, { editingRowKey: 'row-1' }]) {
    const { state, select } = setup(blocked);
    select({ name: 'next' });
    assert.equal(state.catalogOpen, true);
    assert.equal(state.navigation.length, 0);
    assert.equal(state.warnings.length, 1);
  }
});
