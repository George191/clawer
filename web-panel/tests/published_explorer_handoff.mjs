import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/services/publishedExplorerHandoff.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
const navigationSource = readFileSync(new URL('../src/services/explorerNavigation.ts', import.meta.url), 'utf8');
const navigationCompiled = ts.transpileModule(navigationSource, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
const navigation = {};
vm.runInThisContext('(function(exports){' + navigationCompiled + '\n})')(navigation);
const exports = {};
vm.runInThisContext('(function(exports,require){' + compiled + '\n})')(exports, name => { assert.equal(name, './explorerNavigation'); return navigation; });
const { publishedExplorerHref, publishedCatalogHref, parsePublishedExplorerEntry } = exports;
const id = '463ee113-2b89-4170-977d-1309fe4b3976';
const saved = 'af9b18fc-807f-45f8-b024-57025163228a';
const parseHref = href => { const url = new URL(href, 'https://app.example.test'); return parsePublishedExplorerEntry(url.search, url.hash); };

test('shared workbench search and nested filters roundtrip without replacing the Graph scope', () => {
  const row = { id: 'rule-1', field: 'business-name', operator: '包含', value: '  %_literal  ', active: true, join: 'or' };
  const boundary = { ...row, field: '', value: '', operator: '=', join: 'and' };
  const query = { filters: [{ field: 'amount', op: 'gt', value: 5 }], keys: [{ $recordId: saved }], search: '  name  ', workbenchFilters: [
    { ...boundary, id: 'group-open-1' }, row, { ...row, id: 'rule-2', field: 'amount', operator: '>=', value: '10' }, { ...boundary, id: 'group-close-1' },
  ] };
  assert.deepEqual(parseHref(publishedExplorerHref(id, { query })).query, query);
  assert.deepEqual(parseHref(publishedExplorerHref(id, { query: { filters: [], search: 'First' } })).query, { filters: [], search: 'First' });
  for (const patch of [{ search: 'x'.repeat(1001) }, { workbenchFilters: [{ ...row, operator: 'SQL' }] }, { workbenchFilters: [{ ...row, active: 'true' }] }, { workbenchFilters: [{ ...row, field: 'physical.column' }] }, { workbenchFilters: Array.from({ length: 101 }, () => row) }]) {
    assert.throws(() => publishedExplorerHref(id, { query: { filters: [], ...patch } }), /Invalid published/);
  }
});

test('catalog entry opens Explorer directly and returns to the same filters and page', () => {
  const returnTo = publishedCatalogHref('?catalog=published&publishedQ=%E5%AA%92%E4%BD%93&publishedOffset=25&view=list&publishedHidden=true&publishedDeleted=true&objectType=' + id + '&schema=ts_ods&table=assets&tab=properties');
  const entry = parseHref(publishedExplorerHref(id, { returnTo }));
  const catalog = new URL(entry.returnTo, 'https://app.example.test');
  assert.equal(catalog.pathname, '/ontology');
  assert.equal(catalog.searchParams.get('catalog'), 'published');
  assert.equal(catalog.searchParams.get('publishedQ'), '媒体');
  assert.equal(catalog.searchParams.get('publishedOffset'), '25');
  assert.equal(catalog.searchParams.get('view'), 'list');
  assert.equal(catalog.searchParams.get('publishedHidden'), 'true');
  assert.equal(catalog.searchParams.get('publishedDeleted'), 'true');
  for (const key of ['objectType', 'schema', 'table', 'tab']) assert.equal(catalog.searchParams.has(key), false);
  assert.equal(entry.definitionId, id);
});

test('creating a type from the physical catalog returns to Published without a physical selection', () => {
  const returnTo = publishedCatalogHref('schema=ts_ods&table=assets&tab=properties&view=list');
  assert.equal(returnTo, '/ontology?catalog=published&view=list');
  assert.deepEqual(parseHref(publishedExplorerHref(id, { returnTo })).query, { filters: [] });
});

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
  assert.equal(entry.returnTo, '/ontology?catalog=published');
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
  assert.equal(parsePublishedExplorerEntry('objectType=' + id + '&returnTo=https://example.com').returnTo, '/ontology?catalog=published');
});

test('large transient scopes survive refresh while the server request remains short', () => {
  const query = { filters: [], keys: Array.from({ length: 100 }, (_, index) => ({ business_id: String(index) + 'x'.repeat(1000) })) };
  const href = publishedExplorerHref(id, { query });
  const url = new URL(href, 'https://app.example.test');
  assert.ok(url.href.length > 100000);
  assert.ok((url.pathname + url.search).length < 100);
  assert.deepEqual(parseHref(href).query, query);
});

test('ordinary Published links contain only the object type and omit default query and return state', () => {
  const expected = '/ontology/explorer?objectType=' + id;
  for (const returnTo of [undefined, '/ontology', '/ontology?catalog=published', '/ontology?catalog=published&objectType=' + id + '&view=grid&publishedOffset=0']) {
    assert.equal(publishedExplorerHref(id, { query: { filters: [], sorts: [] }, returnTo }), expected);
  }
  assert.deepEqual(parseHref(expected).query, { filters: [] });
  assert.equal(parseHref(expected).returnTo, '/ontology?catalog=published');
});

test('Saved links retain their identity without expanding an empty query into the fragment', () => {
  assert.equal(publishedExplorerHref(id, { saved, query: { filters: [] }, returnTo: '/ontology?catalog=published' }), '/ontology/explorer?objectType=' + id + '&saved=' + saved);
  assert.equal(publishedExplorerHref(id, { savedSet: saved, returnTo: '/ontology?catalog=published' }), '/ontology/explorer?objectType=' + id + '&savedSet=' + saved);
});
