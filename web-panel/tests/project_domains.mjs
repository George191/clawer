import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import vm from 'node:vm';
import test from 'node:test';
import ts from 'typescript';

const sources = Object.fromEntries(['projectDomains', 'endpoints'].map((name) => [name,
  ts.transpileModule(readFileSync(new URL(`../src/services/${name}.ts`, import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  }).outputText,
]));
const require = createRequire(import.meta.url);
const routerSource = ts.transpileModule(readFileSync(new URL('../src/ProjectRouter.tsx', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.ReactJSX },
}).outputText;

function load(name, frontend, apiOrigin, suffix = 'asiral-helio.internal') {
  const context = { exports: {}, URL, window: {
    location: new URL(frontend), __APP_CONFIG__: { apiOrigin, frontendDomainSuffix: suffix },
  } };
  vm.runInNewContext(sources[name], context);
  return context.exports;
}

test('each project changes the browser origin and API/WSS use its matching label', () => {
  for (const project of ['crawler', 'vault', 'ontology', 'atlas', 'graph', 'rag']) {
    const frontend = `https://${project}.asiral-helio.internal`;
    const domains = load('projectDomains', frontend);
    assert.equal(domains.currentProjectDomain, project);
    assert.equal(domains.projectHref('ontology', '/ontology?view=table#row'),
      'https://ontology.asiral-helio.internal/?view=table#row');
    const endpoints = load('endpoints', frontend, `https://${project}.asiral-helio.api`);
    assert.equal(endpoints.apiUrl('/api/v1/users/me'), `https://${project}.asiral-helio.api/v1/users/me`);
    assert.equal(endpoints.wsUrl('/ws/tasks'), `wss://${project}.asiral-helio.api/ws/tasks`);
  }
});

test('deployment suffix and custom HTTPS port determine the target, development stays same origin', () => {
  const domains = load('projectDomains', 'https://ontology.example.internal:8443', '', 'example.internal');
  assert.equal(domains.projectHref('crawler', '/scout'), 'https://crawler.example.internal:8443/');
  const dev = load('projectDomains', 'http://localhost:3000', '', '');
  assert.equal(dev.currentProjectDomain, undefined);
  assert.equal(dev.projectHref('crawler', '/scout'), 'http://localhost:3000/scout');
});

test('known project routes map between public paths and internal namespaces without changing shared routes', () => {
  const domains = load('projectDomains', 'https://ontology.asiral-helio.internal');
  for (const [project, internal, external] of [
    ['crawler', '/scout', '/'], ['crawler', '/source-strategy', '/source-strategy'],
    ['ontology', '/ontology', '/'], ['ontology', '/ontology/explorer', '/explorer'],
    ['ontology', '/ontology/explorer/record', '/explorer/record'],
    ['atlas', '/atlas/metrics', '/metrics'], ['graph', '/graph/entities', '/entities'],
    ['rag', '/rag/documents', '/documents'], ['vault', '/vault/catalog', '/catalog'],
  ]) {
    assert.equal(domains.publicProjectPath(internal, project), external);
    assert.equal(domains.internalProjectPath(external, project), internal);
  }
  for (const project of ['crawler', 'vault', 'ontology', 'atlas', 'graph', 'rag']) {
    for (const shared of ['/login', '/reset-password', '/automation/runs', '/organization/users/42', '/settings', '/account/settings', '/faq', '/legal/privacy']) {
      assert.equal(domains.internalProjectPath(shared, project), shared);
      assert.equal(domains.publicProjectPath(shared, project), shared);
    }
    assert.equal(domains.internalProjectPath('/unknown', project), '/unknown');
  }
  assert.equal(domains.internalProjectPath('/explorer/'), '/ontology/explorer/');
  assert.equal(domains.publicProjectPath('/ontology/explorer/'), '/explorer/');
  assert.equal(domains.publicProjectPath('/ontology/unknown'), '/ontology/unknown');
  const uppercase = load('projectDomains', 'https://ontology.asiral-helio.internal', '', 'ASIRAL-HELIO.INTERNAL');
  assert.equal(uppercase.projectHref('crawler', '/scout'), 'https://crawler.asiral-helio.internal/');
});

test('navigation links choose the destination project while shared paths retain their current origin', () => {
  const domains = load('projectDomains', 'https://ontology.asiral-helio.internal:8443');
  assert.equal(domains.projectNavigationHref('/ontology/explorer?schema=ts_ods#row'), 'https://ontology.asiral-helio.internal:8443/explorer?schema=ts_ods#row');
  assert.equal(domains.projectNavigationHref('/scout'), 'https://crawler.asiral-helio.internal:8443/');
  assert.equal(domains.projectNavigationHref('/rag/indexes?view=all'), 'https://rag.asiral-helio.internal:8443/indexes?view=all');
  assert.equal(domains.projectNavigationHref('/automation/runs?domain=etl-pipeline'), 'https://ontology.asiral-helio.internal:8443/automation/runs?domain=etl-pipeline');
  assert.equal(domains.projectNavigationHref('https://outside.test/reference'), 'https://outside.test/reference');
  const dev = load('projectDomains', 'http://localhost:3000', '', '');
  assert.equal(dev.internalProjectPath('/ontology'), '/ontology');
  assert.equal(dev.publicProjectPath('/ontology/explorer'), '/ontology/explorer');
  assert.equal(dev.projectNavigationHref('/scout'), 'http://localhost:3000/scout');
});

function browser(frontend, initialState = null, suffix = 'asiral-helio.internal') {
  const entries = [{ url: new URL(frontend), state: initialState }], listeners = new Set(), departures = [];
  let index = 0;
  function location(value) {
    const url = new URL(value);
    url.assign = target => departures.push({ method: 'assign', target });
    url.replace = target => departures.push({ method: 'replace', target });
    return url;
  }
  const window = {
    __APP_CONFIG__: { frontendDomainSuffix: suffix },
    get location() { return location(entries[index].url); },
    addEventListener: (type, listener) => { if (type === 'popstate') listeners.add(listener); },
    removeEventListener: (type, listener) => { if (type === 'popstate') listeners.delete(listener); },
    history: {
      get state() { return entries[index].state; },
      pushState(state, title, path) {
        entries.splice(index + 1);
        entries.push({ url: new URL(path, entries[index].url), state });
        index++;
      },
      replaceState(state, title, path) { entries[index] = { url: path ? new URL(path, entries[index].url) : entries[index].url, state }; },
      go(delta) {
        const next = index + delta;
        if (next < 0 || next >= entries.length) return;
        index = next;
        listeners.forEach(listener => listener({ state: entries[index].state }));
      },
    },
  };
  const domainContext = { exports: {}, URL, window };
  vm.runInNewContext(sources.projectDomains, domainContext);
  const routerContext = { exports: {}, URL, window, require: name => name === './services/projectDomains' ? domainContext.exports : require(name) };
  vm.runInNewContext(routerSource, routerContext);
  return { window, entries, departures, adapter: routerContext.exports.createProjectHistory(window) };
}

test('official browser history canonicalizes legacy URLs and preserves state, key, query and hash', () => {
  const fixture = browser('https://ontology.asiral-helio.internal/ontology?schema=ts_ods#row', { usr: { from: 'saved' }, key: 'existing', idx: 0 });
  assert.equal(fixture.window.location.href, 'https://ontology.asiral-helio.internal/?schema=ts_ods#row');
  assert.equal(fixture.entries.length, 1);
  const logical = fixture.adapter.getState().location;
  assert.equal(logical.pathname, '/ontology');
  assert.equal(logical.search, '?schema=ts_ods');
  assert.equal(logical.hash, '#row');
  assert.equal(logical.state.from, 'saved');
  assert.equal(logical.key, 'existing');
});

test('push, replace and POP keep public URLs and internal route locations synchronized', () => {
  const fixture = browser('https://ontology.asiral-helio.internal/?schema=initial');
  const updates = [], stop = fixture.adapter.listen(update => updates.push(update));
  const { navigator } = fixture.adapter;
  assert.equal(navigator.createHref({ pathname: '/ontology/explorer', search: '?schema=ts_ods', hash: '#row' }), 'https://ontology.asiral-helio.internal/explorer?schema=ts_ods#row');
  navigator.push('/ontology/explorer?schema=ts_ods#row', { pane: 'explorer' });
  assert.equal(fixture.window.location.pathname, '/explorer');
  assert.equal(updates.at(-1).location.pathname, '/ontology/explorer');
  assert.equal(updates.at(-1).location.state.pane, 'explorer');
  assert.equal(updates.at(-1).action, 'PUSH');
  navigator.replace('/ontology/runtime?panel=cdc#status', { pane: 'runtime' });
  const replacementKey = fixture.adapter.getState().location.key;
  assert.equal(fixture.entries.length, 2);
  assert.equal(fixture.window.location.pathname, '/runtime');
  assert.equal(updates.at(-1).action, 'REPLACE');
  navigator.go(-1);
  assert.equal(fixture.window.location.pathname, '/');
  assert.equal(updates.at(-1).action, 'POP');
  assert.equal(updates.at(-1).location.pathname, '/ontology');
  assert.equal(updates.at(-1).location.search, '?schema=initial');
  navigator.go(1);
  assert.equal(updates.at(-1).location.pathname, '/ontology/runtime');
  assert.equal(updates.at(-1).location.key, replacementKey);
  assert.equal(updates.at(-1).location.state.pane, 'runtime');
  navigator.push('?panel=tasks#new', null);
  assert.equal(fixture.window.location.href, 'https://ontology.asiral-helio.internal/runtime?panel=tasks#new');
  stop();
});

test('cross-project href and navigation change origin without pushing cross-origin browser state', () => {
  const fixture = browser('https://ontology.asiral-helio.internal:8443/');
  assert.equal(fixture.adapter.navigator.createHref('/scout'), 'https://crawler.asiral-helio.internal:8443/');
  fixture.adapter.navigator.push('/scout');
  fixture.adapter.navigator.replace('/rag/indexes?view=all');
  assert.deepEqual(fixture.departures, [
    { method: 'assign', target: 'https://crawler.asiral-helio.internal:8443/' },
    { method: 'replace', target: 'https://rag.asiral-helio.internal:8443/indexes?view=all' },
  ]);
  assert.equal(fixture.entries.length, 1);
});

test('React Router matches internal routes while Link exposes the public URL', () => {
  const fixture = browser('https://ontology.asiral-helio.internal/explorer?schema=ts_ods');
  const React = require('react'), { renderToStaticMarkup } = require('react-dom/server');
  const { Link, Route, Router, Routes } = require('react-router-dom');
  const html = renderToStaticMarkup(React.createElement(Router, {
    location: fixture.adapter.getState().location, navigator: fixture.adapter.navigator, static: true,
  }, React.createElement(Routes, null,
    React.createElement(Route, { path: '/ontology/explorer', element: React.createElement(Link, { to: '/ontology/runtime?panel=cdc' }, 'Runtime') }),
  )));
  assert.match(html, /href="https:\/\/ontology\.asiral-helio\.internal\/runtime\?panel=cdc"/);
  assert.match(html, />Runtime<\/a>/);
});

test('production rejects unrelated origins, unknown labels, nested labels, and insecure origins', () => {
  for (const frontend of ['https://ontology.asiral-helio.internal.evil.test',
    'https://unknown.asiral-helio.internal', 'https://deep.ontology.asiral-helio.internal',
    'http://ontology.asiral-helio.internal']) {
    assert.throws(() => load('projectDomains', frontend), /Invalid project hostname/);
  }
});


test('public v1 prefix maps only application API paths, preserving signed and development URLs', () => {
  const production = load('endpoints', 'https://ontology.asiral-helio.internal', 'https://ontology.asiral-helio.api');
  assert.equal(production.apiUrl('/api/v1/etl/ts_ods/local_demo_media/row-links?offset=10'),
    'https://ontology.asiral-helio.api/v1/etl/ts_ods/local_demo_media/row-links?offset=10');
  assert.equal(production.apiUrl('/api/ontology/v1/objects'), 'https://ontology.asiral-helio.api/api/ontology/v1/objects');
  const signed = 'https://files.example.test/api/v1/object?signature=abc';
  assert.equal(production.apiUrl(signed), signed);
  const dev = load('endpoints', 'http://localhost:3000', '', '');
  assert.equal(dev.apiUrl('/api/v1/users/me'), 'http://localhost:3000/api/v1/users/me');
});
