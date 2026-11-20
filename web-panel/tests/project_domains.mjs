import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import vm from 'node:vm';
import test from 'node:test';
import ts from 'typescript';

const require = createRequire(import.meta.url);
const compile = path => ts.transpileModule(readFileSync(new URL(path, import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.ReactJSX },
}).outputText;
const domainSource = compile('../src/services/projectDomains.ts');
const routerSource = compile('../src/ProjectRouter.tsx');

function browser(frontend = 'https://app.astral-helio.internal/ontology', storage = new Map()) {
  const entries = [{ url: new URL(frontend), state: null }], listeners = new Map(), events = [];
  let index = 0;
  const window = {
    get location() { return entries[index].url; },
    addEventListener(type, listener) {
      if (!listeners.has(type)) listeners.set(type, new Set());
      listeners.get(type).add(listener);
    },
    removeEventListener: (type, listener) => listeners.get(type)?.delete(listener),
    dispatchEvent(event) {
      events.push(event.type);
      listeners.get(event.type)?.forEach(listener => listener(event));
    },
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
        window.dispatchEvent({ type: 'popstate', state: entries[index].state });
      },
    },
  };
  const sessionStorage = { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value) };
  const context = { exports: {}, URL, Event, window, sessionStorage };
  vm.runInNewContext(domainSource, context);
  return { window, storage, entries, events, domains: context.exports };
}

test('six project namespaces and existing collection/lake routes resolve at path boundaries', () => {
  const { domains } = browser();
  for (const [path, project] of [
    ['/scout', 'crawler'], ['/source-strategy', 'crawler'], ['/anti-crawl', 'crawler'], ['/field-mapping', 'crawler'],
    ['/vault/catalog', 'vault'], ['/explorer', 'vault'], ['/data-api', 'vault'],
    ['/ontology', 'ontology'], ['/ontology/explorer/record', 'ontology'],
    ['/atlas/metrics', 'atlas'], ['/graph/entities', 'graph'], ['/rag/indexes', 'rag'],
  ]) assert.equal(domains.resolveProjectDomain(path), project);
  for (const path of ['/', '/login', '/automation/runs', '/organization/users/42', '/settings', '/account/settings', '/faq', '/legal/privacy', '/ontology-other', '/unknown']) {
    assert.equal(domains.resolveProjectDomain(path), undefined);
  }
});

test('the synchronous resolver follows the real browser path before any React effect runs', () => {
  const fixture = browser(), { domains } = fixture;
  assert.equal(domains.getCurrentProjectDomain(), 'ontology');
  fixture.window.history.pushState(null, '', '/scout?task=one#logs');
  assert.equal(domains.getCurrentProjectDomain(), 'crawler');
  assert.equal(fixture.window.location.pathname, '/scout');
  assert.equal(fixture.events.filter(event => event === domains.PROJECT_CHANGED_EVENT).length, 0);
  domains.getCurrentProjectDomain();
  domains.setCurrentProjectDomain('crawler');
  assert.equal(fixture.events.filter(event => event === domains.PROJECT_CHANGED_EVENT).length, 1);
});

test('project change notifications expose the new project and repeated getters never loop', () => {
  const fixture = browser(), { domains } = fixture, observed = [];
  domains.getCurrentProjectDomain();
  fixture.window.addEventListener(domains.PROJECT_CHANGED_EVENT, () => observed.push(domains.getCurrentProjectDomain()));
  fixture.window.history.pushState(null, '', '/atlas');
  assert.equal(domains.getCurrentProjectDomain(), 'atlas');
  assert.deepEqual(observed, []);
  domains.setCurrentProjectDomain('atlas');
  assert.deepEqual(observed, ['atlas']);
  domains.setCurrentProjectDomain(domains.getCurrentProjectDomain());
  assert.deepEqual(observed, ['atlas']);
  fixture.window.history.pushState(null, '', '/automation/runs');
  assert.equal(domains.getCurrentProjectDomain(), 'atlas');
  assert.deepEqual(observed, ['atlas']);
});

test('shared pages retain the recent project across refresh and a fresh session defaults to ontology', () => {
  const fixture = browser('https://app.astral-helio.internal/graph');
  fixture.domains.getCurrentProjectDomain();
  for (const path of ['/automation/runs', '/settings', '/organization', '/login']) {
    const refreshed = browser('https://app.astral-helio.internal' + path, fixture.storage);
    assert.equal(refreshed.domains.getCurrentProjectDomain(), 'graph');
  }
  const fresh = browser('https://app.astral-helio.internal/login');
  assert.equal(fresh.domains.getCurrentProjectDomain(), 'ontology');
  for (const value of ['unknown', 'toString']) {
    const invalid = browser('https://app.astral-helio.internal/settings', new Map([['helio_project_domain', value]]));
    assert.equal(invalid.domains.getCurrentProjectDomain(), 'ontology');
  }
});

test('project links retain the fixed frontend origin, internal path, query, hash and custom port', () => {
  for (const origin of ['https://app.astral-helio.internal', 'https://app.example.internal:8443', 'http://localhost:3000']) {
    const { domains } = browser(origin + '/ontology');
    assert.equal(domains.projectHref('/scout'), origin + '/scout');
    assert.equal(domains.projectHref('/ontology/explorer?schema=ts_ods#row'), origin + '/ontology/explorer?schema=ts_ods#row');
    assert.throws(() => domains.projectHref('https://crawler.astral-helio.internal/scout'), /current frontend origin/);
  }
});

test('standard browser history keeps URLs, state, keys and POP behavior while projects change', () => {
  const fixture = browser(), { domains } = fixture;
  const { createBrowserHistory } = require('@remix-run/router');
  const history = createBrowserHistory({ window: fixture.window, v5Compat: true }), updates = [];
  const stop = history.listen(update => updates.push(update));
  history.push('/scout?task=one#logs', { project: 'crawler' });
  assert.equal(domains.getCurrentProjectDomain(), 'crawler');
  assert.equal(fixture.window.location.origin, 'https://app.astral-helio.internal');
  assert.equal(history.location.pathname, '/scout');
  assert.equal(history.location.search, '?task=one');
  assert.equal(history.location.hash, '#logs');
  assert.equal(history.location.state.project, 'crawler');
  history.replace('/ontology/explorer?schema=ts_ods#row', { project: 'ontology' });
  const replacementKey = history.location.key;
  assert.equal(domains.getCurrentProjectDomain(), 'ontology');
  assert.equal(fixture.entries.length, 2);
  history.push('/automation/runs', null);
  assert.equal(domains.getCurrentProjectDomain(), 'ontology');
  history.go(-1);
  assert.equal(updates.at(-1).action, 'POP');
  assert.equal(history.location.key, replacementKey);
  assert.equal(history.location.state.project, 'ontology');
  assert.equal(history.location.pathname, '/ontology/explorer');
  stop();
});

test('the app uses BrowserRouter directly and Links retain their normal same-origin route paths', () => {
  const fixture = browser(), routerContext = { exports: {}, require: name => name === './services/projectDomains' ? fixture.domains : require(name) };
  vm.runInNewContext(routerSource, routerContext);
  const React = require('react'), { renderToStaticMarkup } = require('react-dom/server');
  const { BrowserRouter, Link, Route, Router, Routes } = require('react-router-dom');
  const provider = routerContext.exports.default({ children: 'Application' });
  assert.equal(provider.type, BrowserRouter);
  const { createBrowserHistory } = require('@remix-run/router');
  const history = createBrowserHistory({ window: fixture.window });
  const html = renderToStaticMarkup(React.createElement(Router, {
    location: history.location, navigator: history, static: true,
  }, React.createElement(Routes, null,
    React.createElement(Route, { path: '/ontology', element: React.createElement(Link, { to: '/scout?task=one' }, 'Crawler') }),
  )));
  assert.match(html, /href="\/scout\?task=one"/);
  assert.match(html, />Crawler<\/a>/);
});

test('the frontend root redirects before rendering App and keeps query, hash and the remembered project', () => {
  for (const [previousPath, expectedPath] of [['/ontology', '/ontology'], ['/scout', '/scout']]) {
    const fixture = browser('https://app.astral-helio.internal' + previousPath), effects = [];
    fixture.domains.getCurrentProjectDomain();
    fixture.window.history.pushState(null, '', '/?schema=ts_ods#row');
    const routerDom = require('react-router-dom');
    const routerContext = { exports: {}, require: name => {
      if (name === './services/projectDomains') return fixture.domains;
      if (name === 'react') return { ...require('react'), useLayoutEffect: effect => effects.push(effect) };
      if (name === 'react-router-dom') return { ...routerDom, useLocation: () => fixture.window.location };
      return require(name);
    } };
    vm.runInNewContext(routerSource, routerContext);
    const provider = routerContext.exports.default({ children: 'App subtree' });
    const decision = provider.props.children.type({ children: 'App subtree' });
    assert.equal(decision.type, routerDom.Navigate);
    assert.equal(decision.props.to, expectedPath + '?schema=ts_ods#row');
    assert.equal(decision.props.replace, true);
    assert.equal(effects.length, 1);
  }
});
