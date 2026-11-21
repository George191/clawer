import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/services/publishedExplorerDetails.ts', import.meta.url), 'utf8');
const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
const type = { id: 'relationship', key: 'assigned', name: 'Assigned', sourceDefinitionId: 'source-type', targetDefinitionId: 'target-type', cardinality: 'many_to_many' };
const related = { id: 'related-record', revision: 2, title: 'Related', properties: { name: 'Related', tags: ['visible'] } };
function setup(canUnlink = true) {
  const calls = [], exports = {};
  let rejection;
  vm.runInThisContext('(function(require,exports){' + code + '\n})')(name => {
    if (name === './endpoints') return { apiUrl: path => path };
    assert.equal(name, './ontology_graph');
    return { graphRequest: async (method, path, options) => {
      calls.push({ method, path, options });
      if (method === 'delete') { if (rejection) throw rejection; return; }
      if (path.endsWith('/link-types')) return { items: [type], hasMore: false, canManage: true };
      if (path.endsWith('/neighbors')) return { items: [{ linkId: 'edge-instance', canUnlink, record: related }], hasMore: false };
      throw new Error('Unexpected request: ' + path);
    } };
  }, exports);
  return { ...exports, calls, reject: error => { rejection = error; } };
}

test('relationship reads retain the exact edge identity, record values and server unlink capability', async () => {
  for (const capability of [true, false]) {
    const api = setup(capability), signal = new AbortController().signal;
    const page = await api.fetchPublishedRelationships('source-type', 'anchor-record', undefined, 0, signal);
    assert.equal(page.selected, 'relationship:outgoing');
    assert.deepEqual(page.items, [{ ...related, linkId: 'edge-instance', canUnlink: capability }]);
    assert.equal(api.calls.at(-1).options.signal, signal);
    assert.equal(api.calls.at(-1).options.params.direction, 'outgoing');
  }
});

test('unlink sends the original source and target identities for outgoing and incoming views', async () => {
  for (const [definition, direction, sourceRecordId, targetRecordId] of [
    ['source-type', 'outgoing', 'anchor-record', related.id],
    ['target-type', 'incoming', related.id, 'anchor-record'],
  ]) {
    const api = setup(), signal = new AbortController().signal;
    const page = await api.fetchPublishedRelationships(definition, 'anchor-record', undefined, 0, signal);
    const relation = page.relations.find(item => item.id + ':' + item.direction === page.selected);
    assert.equal(relation.direction, direction);
    await api.unlinkPublishedRelationship(relation, 'anchor-record', page.items[0], signal);
    assert.deepEqual(api.calls.at(-1), { method: 'delete', path: '/link-types/relationship/links/edge-instance',
      options: { params: { sourceRecordId, targetRecordId }, signal } });
  }
});

test('unlink preserves server permission and stale edge denials instead of reporting success', async () => {
  for (const status of [403, 409]) {
    const api = setup(), failure = { response: { status } };
    api.reject(failure);
    await assert.rejects(api.unlinkPublishedRelationship({ ...type, direction: 'outgoing' }, 'anchor-record', { ...related, linkId: 'old-edge', canUnlink: true }), error => error === failure);
    assert.equal(api.calls.at(-1).path, '/link-types/relationship/links/old-edge');
  }
});
