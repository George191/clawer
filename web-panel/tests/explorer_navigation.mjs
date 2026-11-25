import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/services/explorerNavigation.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
const exports = {};
vm.runInThisContext('(function(exports){' + compiled + '\n})')(exports);
const { ontologyCatalogHref, catalogReturnHref, compactExplorerSearch, backedExplorerHref, dataConnectionHref, closeDataConnectionsHref } = exports;
const params = href => new URL(href, 'https://app.example.test').searchParams;

test('ordinary physical entry has only schema and table despite a redundant selected-card return URL', () => {
  assert.equal(backedExplorerHref('ts_ods', 'local_demo_media', '/ontology?schema=ts_ods&table=local_demo_media&tab=properties'), '/ontology/explorer?schema=ts_ods&table=local_demo_media');
  assert.equal(backedExplorerHref('ts_ods', 'local_demo_media', '/ontology?view=grid&group=all'), '/ontology/explorer?schema=ts_ods&table=local_demo_media');
  assert.equal(catalogReturnHref(undefined, 'backed'), '/ontology');
});

test('physical entry preserves actual directory filters without repeating the selected table', () => {
  const href = backedExplorerHref('ts_ods', 'local_demo_media', '/ontology?schema=ts_ods&table=local_demo_media&source=ts_ods&group=favorites&q=%E5%AA%92%E4%BD%93&view=list&tab=properties');
  const returnTo = params(href).get('returnTo');
  assert.equal(returnTo, '/ontology?q=%E5%AA%92%E4%BD%93&group=favorites&source=ts_ods&view=list');
  assert.equal(params(returnTo).has('table'), false);
  assert.equal(params(returnTo).has('schema'), false);
  assert.equal(catalogReturnHref(returnTo, 'backed'), returnTo);
});

test('Published return state includes its search, page and visibility without physical catalog filters', () => {
  const href = ontologyCatalogHref('?catalog=published&publishedQ=%E5%AA%92%E4%BD%93&publishedOffset=25&publishedHidden=true&publishedDeleted=true&view=list&q=physical&source=ts_ods&group=ingestion&schema=ts_ods&table=media&objectType=selected&saved=x&returnTo=ignored', 'published');
  assert.equal(href, '/ontology?catalog=published&publishedQ=%E5%AA%92%E4%BD%93&publishedOffset=25&publishedHidden=true&publishedDeleted=true&view=list');
  assert.equal(catalogReturnHref(href, 'published'), href);
  assert.equal(ontologyCatalogHref('catalog=published&publishedOffset=0&publishedHidden=false&publishedDeleted=false&view=grid', 'published'), '/ontology?catalog=published');
});

test('a saved connection survives the Published return URL without leaking into the warehouse catalog', () => {
  const id = '2e7a9733-daa3-4f04-a788-4fd9f2e2b818';
  const href = `/ontology?catalog=published&publishedSource=${id}`;
  assert.equal(catalogReturnHref(`/ontology?publishedSource=${id.toUpperCase()}&objectType=selected`, 'published'), href);
  assert.equal(params(compactExplorerSearch(new URLSearchParams({ returnTo: href }).toString(), 'published')).get('returnTo'), href);
  assert.equal(catalogReturnHref(href, 'backed'), '/ontology');
  assert.equal(ontologyCatalogHref('publishedSource=/outside', 'published'), '/ontology?catalog=published');
  assert.equal(params(dataConnectionHref(id)).get('sourceId'), id);
});

test('opening and dismissing Data Connections retains the underlying catalog selection and filters', () => {
  const id = '2e7a9733-daa3-4f04-a788-4fd9f2e2b818';
  const search = 'catalog=published&publishedQ=Airport&objectType=selected&tab=properties&view=list';
  const path = dataConnectionHref(id, search);
  assert.equal(path, `/ontology/connections?${search}&sourceId=${id}`);
  assert.equal(closeDataConnectionsHref(params(path).toString() + '&batchSync=active&tableExport=1&sourcePanel=sync&sourceKind=database&sourceSchema=orders'), `/ontology?${search}`);
  assert.equal(dataConnectionHref(undefined, 'source=ts_ods&q=news'), '/ontology/connections?source=ts_ods&q=news');
  assert.equal(closeDataConnectionsHref('sourceId=' + id), '/ontology');
});

test('returning from a storage-backed record preserves the catalog page', () => {
  const href = backedExplorerHref('ts_ods', 'media', '/ontology?group=ingestion&backedOffset=100');
  assert.equal(params(href).get('returnTo'), '/ontology?backedOffset=100&group=ingestion');
  assert.equal(ontologyCatalogHref('?backedOffset=-1', 'backed'), '/ontology');
});

test('existing long links compact once while keeping Saved, source view and return behavior', () => {
  const search = '?schema=ts_ods&table=media&saved=selection&view=evidence&returnTo=' + encodeURIComponent('/ontology?schema=ts_ods&table=media&tab=properties&source=ts_ods&q=media&view=list');
  const compact = compactExplorerSearch(search, 'backed');
  assert.equal(params(compact).get('saved'), 'selection');
  assert.equal(params(compact).get('view'), 'evidence');
  assert.equal(params(compact).get('returnTo'), '/ontology?q=media&source=ts_ods&view=list');
  assert.equal(compactExplorerSearch(compact, 'backed'), compact);
  assert.equal(compactExplorerSearch('?schema=ts_ods&table=media&view=preview&returnTo=%2Fontology%3Fschema%3Dts_ods%26table%3Dmedia', 'backed'), '?schema=ts_ods&table=media');
});

test('return targets remain local to the correct catalog and never retain nested navigation', () => {
  for (const value of ['https://outside.example/ontology', '//outside.example/ontology', '/ontology/explorer?schema=ts_ods&table=media', '/ontology?catalog=published&objectType=x&returnTo=%2Fontology%3Fq%3Dhidden']) {
    assert.equal(catalogReturnHref(value, 'backed'), '/ontology');
    assert.equal(catalogReturnHref(value, 'published'), '/ontology?catalog=published');
  }
});
