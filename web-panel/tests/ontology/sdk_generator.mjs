import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { basename, dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import test from 'node:test';
import { generateOntologySdk, ontologySdkFilename } from '../../src/services/ontology_sdk.ts';

const id = '123e4567-e89b-12d3-a456-426614174000';
const definition = {
  id: 'test-object', source: { mode: 'none' }, titleKey: 'id',
  properties: [
    { id: 'id', type: 'string', required: true },
    { id: 'line-count', type: 'integer', required: false },
    { id: 'payload', type: 'json', required: true },
  ],
};
const object = { id, revision: 7, definition };
const action = (kind, fields = []) => ({ kind, fields });
const functions = [{
  key: 'set-count', name: 'Set count',
  parameters: [{ key: 'count', type: 'integer', nullable: false }, { key: 'enabled', type: 'boolean', nullable: true }],
}];
const tsc = fileURLToPath(new URL('../../node_modules/typescript/bin/tsc', import.meta.url));

function cleanup(folder) {
  const target = realpathSync(folder);
  assert.equal(dirname(target), realpathSync(tmpdir()));
  assert.match(basename(target), /^(?:compiled-)?ontology-sdk-[a-z0-9]+$/i);
  rmSync(target, { recursive: true, force: true });
}

function compile(files) {
  const folder = mkdtempSync(join(tmpdir(), 'ontology-sdk-'));
  try {
    for (const [name, source] of Object.entries(files)) writeFileSync(join(folder, name), source);
    const artifact = join(folder, 'js');
    execFileSync(process.execPath, [
      tsc, '--strict', '--target', 'ES2020', '--lib', 'ES2020,DOM',
      '--module', 'CommonJS', '--outDir', artifact, ...Object.keys(files),
    ], { cwd: folder, stdio: 'pipe' });
    return readFileSync(join(artifact, 'sdk.js'), 'utf8');
  } finally {
    cleanup(folder);
  }
}

test('generated declarations and typed calls compile with the repository TypeScript compiler', () => {
  const source = generateOntologySdk(object, [
    action('create', ['id', 'line-count', 'payload']), action('modify', ['line-count']), action('delete'),
  ], functions);
  assert.equal(ontologySdkFilename(object), 'test-object-ontology-sdk.ts');
  compile({
    'sdk.ts': source,
    'consumer.ts': `import { createOntologyClient } from './sdk';
const sdk = createOntologyClient({ baseUrl: 'https://api.example.org', getAccessToken: () => 'fixture' });
sdk.listRecords({ limit: 10 });
sdk.getRecord('record-id');
sdk.createRecord({ id: 'n1', payload: false, 'line-count': null });
sdk.modifyRecord('record-id', 4, { 'line-count': 12 });
sdk.functions['set-count']({ recordId: 'record-id', expectedRevision: 4, requestId: 'request', arguments: { count: 1, enabled: false } });
sdk.functions['set-count']({ recordId: 'record-id', expectedRevision: 4, requestId: 'request', arguments: { count: 1, enabled: null } });
// @ts-expect-error a required field must be present
sdk.createRecord({ id: 'n1' });
// @ts-expect-error a required JSON field cannot be null
sdk.createRecord({ id: 'n1', payload: null });
// @ts-expect-error the immutable key is not in the modify action
sdk.modifyRecord('record-id', 4, { id: 'new' });
// @ts-expect-error named function parameters are typed
sdk.functions['set-count']({ recordId: 'record-id', expectedRevision: 4, requestId: 'request', arguments: { count: '1', enabled: false } });
// @ts-expect-error nullable parameters still require a key
sdk.functions['set-count']({ recordId: 'record-id', expectedRevision: 4, requestId: 'request', arguments: { count: 1 } });`,
  });
});

test('read-only source types omit instance detail, actions, functions, and hidden metadata', () => {
  const sourced = { ...object, definition: { ...definition, source: { mode: 'existing' }, properties: [definition.properties[0]] } };
  const source = generateOntologySdk(sourced, [action('create', ['id']), action('delete')], functions);
  assert.doesNotMatch(source, /line-count|set-count|createRecord|deleteRecord|getRecord/);
  compile({
    'sdk.ts': source,
    'consumer.ts': `import { createOntologyClient } from './sdk';
const sdk = createOntologyClient({ baseUrl: 'http://api.example.org', getAccessToken: () => 'fixture' });
sdk.listRecords();
// @ts-expect-error warehouse rows have no stable runtime record ID
sdk.getRecord('record-id');
// @ts-expect-error warehouse sources cannot be written
sdk.createRecord({ id: 'n1' });`,
  });
  const suppressed = generateOntologySdk({ ...object, definition: { ...definition, properties: [definition.properties[0]] } }, [
    action('modify', ['hidden-field']),
  ]);
  assert.doesNotMatch(suppressed, /hidden-field|modifyRecord/);
});

test('compiled client sends scoped HTTP requests and preserves server denial', async () => {
  const source = generateOntologySdk(object, [action('modify', ['line-count'])], functions);
  const js = compile({ 'sdk.ts': source });
  const folder = mkdtempSync(join(tmpdir(), 'compiled-ontology-sdk-'));
  try {
    const filename = join(folder, 'sdk.cjs');
    writeFileSync(filename, js);
    const { createOntologyClient, OntologySdkError } = (await import(pathToFileURL(filename))).default;
    const sent = [];
    let tokenReads = 0;
    const sdk = createOntologyClient({
      baseUrl: 'https://api.example.org',
      getAccessToken: () => 'caller-' + ++tokenReads,
      fetch: async (url, init) => {
        sent.push({ url: url.href, init });
        if (url.pathname.endsWith('/actions/modify')) return Response.json({ errors: 'Record not found' }, { status: 404 });
        if (url.pathname.endsWith('/invoke')) return Response.json({ id: 'transaction', stepCount: 1 });
        return Response.json({ items: [{ id: 'record-id', revision: 1, title: 'visible', properties: { id: 'visible' } }], hasMore: false });
      },
    });
    assert.equal((await sdk.listRecords({ limit: 5, offset: 10 })).items[0].properties.id, 'visible');
    await assert.rejects(sdk.modifyRecord('record-id', 1, { 'line-count': 10 }), error =>
      error instanceof OntologySdkError && error.status === 404 && error.message === 'Record not found');
    assert.equal((await sdk.functions['set-count']({
      recordId: 'record-id', requestId: 'request', expectedRevision: 1, arguments: { count: 2, enabled: true },
    })).id, 'transaction');
    await assert.rejects(async () => sdk.modifyRecord('record-id', 1, { 'line-count': 9007199254740992 }), RangeError);
    assert.equal(tokenReads, 3);
    assert.equal(sent[0].url, 'https://api.example.org/api/ontology/v1/object-types/' + id + '/records?limit=5&offset=10');
    assert.equal(sent[0].init.headers.get('Authorization'), 'Bearer caller-1');
    assert.equal(sent[0].init.redirect, 'error');
    assert.deepEqual(JSON.parse(sent[2].init.body), {
      recordId: 'record-id', requestId: 'request', expectedRevision: 1, modelRevision: 7, arguments: { count: 2, enabled: true },
    });
  } finally {
    cleanup(folder);
  }
});
