import assert from 'node:assert/strict';
import test from 'node:test';
import ts from 'typescript';
import { readFile } from 'node:fs/promises';

const source = await readFile(new URL('../src/pages/Pipeline/explorerSelectionState.ts', import.meta.url), 'utf8');
const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ES2022 } }).outputText;
const { selectedPageRows } = await import('data:text/javascript;base64,' + Buffer.from(code).toString('base64'));
const row = key => ({ key });

test('checking and unchecking a page preserves selections on other pages', () => {
  const other = row('page1');
  assert.deepEqual(selectedPageRows([other, row('page2a')], new Set(['page2a', 'page2b']), [row('page2b')], true, 100), [other, row('page2b')]);
  assert.deepEqual(selectedPageRows([other, row('page2a')], new Set(['page2a', 'page2b']), [], true, 100), [other]);
});
test('row selection replaces the previous selection when requested', () => {
  assert.deepEqual(selectedPageRows([row('page1')], new Set(['page2']), [row('page2')], false, 100), [row('page2')]);
});
test('a cross-page selection exceeding 100 objects fails without discarding references', () => {
  const previous = Array.from({ length: 100 }, (_, index) => row(String(index)));
  assert.throws(() => selectedPageRows(previous, new Set(['next']), [row('next')], true, 100), /up to 100/);
  assert.equal(previous.length, 100);
});
