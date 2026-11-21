import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/services/publishedExplorerHandoff.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
const exports = {};
vm.runInThisContext('(function(exports){' + compiled + '\n})')(exports);
const { publishedExplorerHref, parsePublishedExplorerEntry, publishedRecordIdentity } = exports;
const id = '463ee113-2b89-4170-977d-1309fe4b3976';
const saved = 'af9b18fc-807f-45f8-b024-57025163228a';
const parseHref = href => { const url = new URL(href, 'https://app.example.test'); return parsePublishedExplorerEntry(url.search, url.hash); };

test('dynamic handoff survives a URL roundtrip with semantic filters, aggregates and sorting', () => {
  const query = { filters: [{ field: 'business-name', op: 'contains', value: '图片 & 视频' }, { field: 'amount', op: 'gt', value: 12.5 }], groupBy: 'category', sumField: 'amount', sorts: [{ field: 'business-name', direction: 'desc' }] };
  const href = publishedExplorerHref(id, { query, returnTo: '/ontology?catalog=published&objectType=' + id });
  assert.equal(href.includes('schema='), false);
  assert.equal(href.includes('table='), false);
  const url = new URL(href, 'https://app.example.test');
  assert.equal(url.searchParams.has('objectSet'), false);
  assert.ok(url.hash.startsWith('#objectSet='));
  const entry = parseHref(href);
  assert.deepEqual(entry.query, query);
  assert.equal(entry.definitionId, id);
  assert.equal(entry.returnTo, '/ontology?catalog=published&objectType=' + id);
});

test('fixed object references retain exact large values and an empty selection stays empty', () => {
  for (const keys of [[], [{ 'asset-id': '9007199254741017', region: '中国' }], [{ $recordId: saved }]]) {
    const href = publishedExplorerHref(id, { query: { filters: [], keys } });
    const entry = parseHref(href);
    assert.deepEqual(entry.query.keys, keys);
    assert.ok(Object.hasOwn(entry.query, 'keys'));
  }
  assert.equal(Object.hasOwn(parsePublishedExplorerEntry('objectType=' + id).query, 'keys'), false);
});

test('saved dynamic set and Saved item use separate references without physical scope', () => {
  const entry = parseHref(publishedExplorerHref(id, { savedSet: saved, saved }));
  assert.equal(entry.savedSet, saved);
  assert.equal(entry.saved, saved);
  assert.throws(() => publishedExplorerHref(id, { savedSet: saved, query: { filters: [] } }), /two object set scopes/);
});

test('invalid, mixed and oversized scopes fail explicitly', () => {
  assert.throws(() => parsePublishedExplorerEntry('objectType=physical_table'), /valid published/);
  assert.throws(() => parsePublishedExplorerEntry('objectType=' + id + '&schema=ts_ods&table=assets'), /opened separately/);
  assert.throws(() => parsePublishedExplorerEntry('objectType=' + id, '#objectSet=%7B'), /not valid JSON/);
  assert.throws(() => parsePublishedExplorerEntry('objectType=' + id + '&objectSet=%7B'), /URL fragment/);
  assert.throws(() => publishedExplorerHref(id, { query: { filters: [], schema: 'ts_ods' } }), /Invalid published object set/);
  assert.throws(() => publishedExplorerHref(id, { query: { filters: [{ field: 'name', op: 'rawSql', value: 'x' }] } }), /filters/);
  assert.throws(() => publishedExplorerHref(id, { query: { filters: [], keys: [{ 'physical.column': '1' }] } }), /selection/);
  assert.throws(() => publishedExplorerHref(id, { query: { filters: [], keys: [{ id: 1 }] } }), /selection/);
  assert.throws(() => publishedExplorerHref(id, { query: { filters: [], keys: Array.from({ length: 101 }, (_, index) => ({ id: String(index) })) } }), /selection/);
  assert.throws(() => publishedExplorerHref(id, { query: { filters: [], keys: [{ id: 'x'.repeat(4097) }] } }), /selection/);
  assert.throws(() => publishedExplorerHref(id, { query: { filters: [], sorts: Array.from({ length: 21 }, (_, index) => ({ field: 'field_' + index, direction: 'asc' })) } }), /sorting/);
  assert.throws(() => parsePublishedExplorerEntry('objectType=' + id, '#objectSet=' + 'x'.repeat(131073)), /too large/);
  assert.equal(parsePublishedExplorerEntry('objectType=' + id + '&returnTo=https://example.com').returnTo, '/ontology?catalog=published&objectType=' + id);
});

test('producers consume authoritative identity without rebuilding decimals, large numbers or native IDs', () => {
  assert.deepEqual(publishedRecordIdentity({ id: saved, identity: { $recordId: saved }, properties: { id: 'external' } }), { $recordId: saved });
  assert.equal(publishedRecordIdentity({ id: saved, properties: { id: 'external' } }), null);
  assert.deepEqual(publishedRecordIdentity({ id: null, identity: { amount: '1.0', 'business-id': '9007199254741017' }, properties: { amount: 1, 'business-id': 9007199254741017 } }), { amount: '1.0', 'business-id': '9007199254741017' });
  assert.equal(publishedRecordIdentity({ id: null, identity: null, properties: { id: '1' } }), null);
});

test('large transient scopes survive refresh while the server request remains short', () => {
  const query = { filters: [], keys: Array.from({ length: 100 }, (_, index) => ({ business_id: String(index) + 'x'.repeat(1000) })) };
  const href = publishedExplorerHref(id, { query });
  const url = new URL(href, 'https://app.example.test');
  assert.ok(url.href.length > 100000);
  assert.ok((url.pathname + url.search).length < 100);
  assert.deepEqual(parseHref(href).query, query);
});
