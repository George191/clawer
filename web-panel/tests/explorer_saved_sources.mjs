import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/services/explorerSaved.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
const calls = [];
const client = Object.fromEntries(['get', 'post', 'put', 'delete'].map(method => [method, async (...args) => {
  calls.push({ method, args });
  return { data: { id: 'saved-id', kind: 'list' } };
}]));
const exports = {};
vm.runInThisContext('(function(exports,require){' + compiled + '\n})')(exports, () => ({ default: client, unwrap: data => data }));

test('Saved requests retain separate Published definition and Backed table scopes', async () => {
  for (const endpoint of [exports.savedPath('ts ods', 'assets/table'), exports.publishedSavedPath('semantic-id')]) {
    calls.length = 0;
    const signal = new AbortController().signal;
    const meta = { id: 'saved-id', version: 'revision' };
    const content = { keys: [{ '$recordId': 'record-id' }] };
    await exports.listSaved(endpoint, 'list', 25, signal, 'Review', 'shared');
    await exports.openSaved(endpoint, 'saved/id', signal);
    await exports.createSaved(endpoint, 'list', 'Review', content);
    await exports.sharing(endpoint, meta, true);
    await exports.removeSaved(endpoint, meta);
    assert.equal(calls.length, 5);
    for (const call of calls) {
      assert.ok(call.args[0].startsWith(endpoint.path));
      const config = call.args[call.method === 'post' || call.method === 'put' ? 2 : 1];
      assert.equal(config.baseURL, endpoint.baseURL);
    }
    assert.equal(calls[1].args[0], endpoint.path + '/saved%2Fid');
    assert.deepEqual(calls[2].args[1], { kind: 'list', name: 'Review', content });
    assert.deepEqual(calls[3].args[1], { version: 'revision', shared: true });
    assert.deepEqual(calls[4].args[1].params, { version: 'revision' });
  }
  assert.deepEqual(exports.savedPath('ts ods', 'assets/table'), { path: '/etl/ts%20ods/assets%2Ftable/saved-items' });
  assert.deepEqual(exports.publishedSavedPath('semantic-id'), { path: '/object-types/semantic-id/explorer/saved-items', baseURL: '/api/ontology/v1' });
});
