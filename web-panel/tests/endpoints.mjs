import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const compiled = Object.fromEntries(['endpoints', 'projectDomains'].map(name => [name,
  ts.transpileModule(readFileSync(new URL(`../src/services/${name}.ts`, import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  }).outputText,
]));
const production = { backendDomainSuffix: 'astral-helio.api', frontendDomainSuffix: 'astral-helio.internal', httpsPort: '443' };
function load(config = production, href = 'https://app.astral-helio.internal/ontology') {
  const location = new URL(href), stored = new Map(), cache = {}, events = [];
  const window = { location, __APP_CONFIG__: config, dispatchEvent: event => events.push(event.type) };
  const sessionStorage = { getItem: key => stored.get(key) ?? null, setItem: (key, value) => stored.set(key, value) };
  window.sessionStorage = sessionStorage;
  const require = path => {
    const name = path.replace('./', '');
    if (cache[name]) return cache[name];
    const context = { exports: {}, window, sessionStorage, URL, Event, require };
    vm.runInNewContext(compiled[name], context);
    cache[name] = context.exports;
    return context.exports;
  };
  return { api: require('./endpoints'), location, stored, events };
}

test('the same imported API helpers follow SPA project changes without a document reload', () => {
  const { api, location } = load();
  for (const [path, project] of [['/ontology', 'ontology'], ['/scout', 'crawler'], ['/atlas/metrics', 'atlas'], ['/graph', 'graph'], ['/rag/documents', 'rag'], ['/vault/catalog', 'vault']]) {
    location.pathname = path;
    assert.equal(api.apiUrl('/api/v1/users/me'), `https://${project}.astral-helio.api/v1/users/me`);
    assert.equal(api.wsUrl('/ws'), `wss://${project}.astral-helio.api/ws`);
  }
  location.pathname = '/settings';
  assert.equal(api.apiUrl('/api/v1/users/me'), 'https://vault.astral-helio.api/v1/users/me');
});

test('configured suffix and HTTPS port determine backend addresses; signed external links stay intact', () => {
  const { api } = load({ ...production, backendDomainSuffix: 'example.api', httpsPort: '8443' });
  assert.equal(api.apiUrl('/api/v1/etl/ts_ods/local_demo_media/row-links?offset=10'),
    'https://ontology.example.api:8443/v1/etl/ts_ods/local_demo_media/row-links?offset=10');
  assert.equal(api.apiUrl('/api/ontology/v1/objects'), 'https://ontology.example.api:8443/api/ontology/v1/objects');
  const signed = 'https://files.example.test/api/v1/object?signature=abc';
  assert.equal(api.apiUrl(signed), signed);
});

test('development uses its same-origin API and WebSocket proxy', () => {
  const { api } = load({ backendDomainSuffix: '', frontendDomainSuffix: '', httpsPort: '' }, 'http://localhost:3000/scout');
  assert.equal(api.apiUrl('/api/v1/users/me'), 'http://localhost:3000/api/v1/users/me');
  assert.equal(api.wsUrl('/ws'), 'ws://localhost:3000/ws');
});

test('production rejects insecure hosts, malformed suffixes, invalid ports and incomplete configuration', () => {
  for (const config of [{ ...production, httpsPort: '0' }, { ...production, httpsPort: '65536' },
    { ...production, backendDomainSuffix: 'bad;.api' }, { ...production, frontendDomainSuffix: '', httpsPort: '' }]) {
    assert.throws(() => load(config), /Invalid runtime/);
  }
  assert.throws(() => load(production, 'http://app.astral-helio.internal/ontology'), /Invalid runtime/);
  assert.throws(() => load(production, 'https://ontology.astral-helio.internal/ontology'), /Invalid runtime/);
  assert.throws(() => load({}), /Missing runtime/);
});
