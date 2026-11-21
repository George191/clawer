import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/services/explorerCapabilities.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
const exports = {};
vm.runInThisContext('(function(exports){' + compiled + '\n})')(exports);
const { explorerResourceCapabilities } = exports;

test('native Published objects expose analysis without physical source tools', () => {
  assert.deepEqual(explorerResourceCapabilities({ mode: 'none' }, false), ['preview']);
  assert.deepEqual(explorerResourceCapabilities({ mode: 'none' }, true), ['preview']);
});

test('mapped Published objects expose source inspection based on binding, independent of catalog', () => {
  const source = { mode: 'existing', schema: 'ts_ods', table: 'local_demo_media' };
  assert.deepEqual(explorerResourceCapabilities(source, false), ['preview', 'stream', 'evidence', 'schema']);
  assert.deepEqual(explorerResourceCapabilities(source, true), ['preview', 'stream', 'evidence', 'schema', 'api']);
});

test('a mode without a complete physical source cannot open physical inspection', () => {
  for (const source of [{ mode: 'existing', schema: 'ts_ods' }, { mode: 'existing', table: 'media' }, { mode: 'none', schema: 'ts_ods', table: 'media' }]) {
    assert.deepEqual(explorerResourceCapabilities(source, true), ['preview']);
  }
});
