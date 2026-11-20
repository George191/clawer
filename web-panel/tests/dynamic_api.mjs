import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const require = createRequire(import.meta.url);
const axios = require('axios');
const source = name => ts.transpileModule(readFileSync(new URL(`../src/${name}.ts`, import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
}).outputText;
const compiled = Object.fromEntries(['services/api', 'services/aiApi', 'services/endpoints', 'services/ontology',
  'services/ontology_graph', 'services/ontology_revisions', 'hooks/useWebSocket'].map(name => [name, source(name)]));
const changedEvent = 'project-domain-changed';

function fixture() {
  const events = new EventTarget(), cache = new Map(), requests = [], refreshes = [];
  let project = 'ontology', detectedProject = project, token = 'access-1', locked = false;
  const window = {
    location: new URL('https://app.astral-helio.internal/ontology'),
    __APP_CONFIG__: { frontendDomainSuffix: 'astral-helio.internal', backendDomainSuffix: 'astral-helio.api', httpsPort: '443' },
    addEventListener: events.addEventListener.bind(events), removeEventListener: events.removeEventListener.bind(events),
    dispatchEvent: events.dispatchEvent.bind(events),
  };
  const domains = { PROJECT_CHANGED_EVENT: changedEvent, getCurrentProjectDomain() {
    if (detectedProject !== project) {
      detectedProject = project;
      window.dispatchEvent(new Event(changedEvent));
    }
    return project;
  } };
  const session = {
    getSessionState: () => ({ authenticated: true, locked, identity: 'session-1' }),
    getAccessToken: () => token, getRefreshToken: () => 'refresh-1', tokenExpiresAt: () => Date.now() + 60000,
    updateAccessToken: next => { token = next; return true; }, clearSession() {}, lockSession() {},
  };
  const response = config => ({ config, data: { items: [] }, status: 200, statusText: 'OK', headers: {} });
  const facade = { ...axios, create(config) {
    const client = axios.create(config);
    client.defaults.adapter = async config => { requests.push(axios.getUri(config)); return response(config); };
    return client;
  }, async post(url) { refreshes.push(url); return { data: { access_token: 'access-2' } }; } };
  function load(name, additions = {}) {
    if (cache.has(name)) return cache.get(name);
    const exports = {};
    const context = { exports, URL, window, location: window.location, console, ...additions, require: name => {
      if (name === 'axios') return { default: facade };
      if (name.endsWith('/session') || name === './session') return session;
      if (name.endsWith('/projectDomains') || name === './projectDomains') return domains;
      if (name.startsWith('./')) return load('services/' + name.slice(2));
      if (name === '../services/api') return load('services/api');
      throw new Error(`Unexpected import: ${name}`);
    } };
    vm.runInNewContext(compiled[name], context);
    cache.set(name, exports);
    return exports;
  }
  return { load, window, requests, refreshes, session, response, select(value) { project = value; },
    lock() { locked = true; }, domains };
}

test('already imported HTTP clients resolve the current project for every new request', async () => {
  const f = fixture(), api = f.load('services/api'), ontology = f.load('services/ontology'), graph = f.load('services/ontology_graph');
  await api.fetchLayers();
  f.select('crawler');
  await api.fetchLayers();
  await ontology.listOntologyProjects();
  f.select('atlas');
  await ontology.listOntologyProjects();
  await graph.graphRequest('get', '/link-types');
  assert.deepEqual(f.requests, [
    'https://ontology.astral-helio.api/v1/etl/layers',
    'https://crawler.astral-helio.api/v1/etl/layers',
    'https://crawler.astral-helio.api/api/ontology/v1/projects',
    'https://atlas.astral-helio.api/api/ontology/v1/projects',
    'https://atlas.astral-helio.api/api/ontology/v1/link-types',
  ]);
  const signed = 'https://files.example.test/api/v1/object?signature=abc';
  await api.default.get(signed);
  assert.equal(f.requests.at(-1), signed);
});

test('a delayed 401 refreshes and retries its original project after a project switch', async () => {
  const f = fixture(), api = f.load('services/api');
  let attempts = 0;
  api.default.defaults.adapter = async config => {
    f.requests.push(axios.getUri(config));
    if (++attempts === 1) {
      f.select('crawler');
      throw new axios.AxiosError('Unauthorized', 'ERR_BAD_REQUEST', config, {}, { ...f.response(config), status: 401 });
    }
    return f.response(config);
  };
  await api.fetchLayers();
  await api.fetchLayers();
  assert.deepEqual(f.refreshes, ['https://ontology.astral-helio.api/v1/login/refresh-token']);
  assert.deepEqual(f.requests, [
    'https://ontology.astral-helio.api/v1/etl/layers',
    'https://ontology.astral-helio.api/v1/etl/layers',
    'https://crawler.astral-helio.api/v1/etl/layers',
  ]);
});

test('revision requests queued before a switch resolve their URL and refresh on the same project', async () => {
  const f = fixture(), api = f.load('services/api'), revisions = f.load('services/ontology_revisions');
  let attempts = 0;
  api.default.defaults.adapter = async config => {
    f.requests.push(axios.getUri(config));
    if (++attempts === 1) {
      f.select('atlas');
      throw new axios.AxiosError('Unauthorized', 'ERR_BAD_REQUEST', config, {}, { ...f.response(config), status: 401 });
    }
    return f.response(config);
  };
  const pending = revisions.readRevisionWorkspace('type/1');
  // Axios dispatches its async request interceptor after this synchronous project switch.
  f.select('crawler');
  await pending;
  assert.deepEqual(f.requests, [
    'https://crawler.astral-helio.api/api/ontology/v1/object-types/type%2F1/revision-workspace',
    'https://crawler.astral-helio.api/api/ontology/v1/object-types/type%2F1/revision-workspace',
  ]);
  assert.deepEqual(f.refreshes, ['https://crawler.astral-helio.api/v1/login/refresh-token']);
});

test('locked sessions never dispatch an HTTP request', async () => {
  const f = fixture(), api = f.load('services/api');
  f.lock();
  await assert.rejects(api.fetchLayers(), error => axios.isCancel(error));
  assert.equal(f.requests.length, 0);
});

test('WebSocket URL getters follow project changes without reimporting consumers', () => {
  const f = fixture(), api = f.load('services/api'), ai = f.load('services/aiApi');
  assert.equal(api.getDashboardWsUrl(), 'wss://ontology.astral-helio.api/ws');
  f.select('crawler');
  assert.equal(ai.getAiAnalyzeWsUrl(), 'wss://crawler.astral-helio.api/ws');
  assert.equal(api.getMonitorWsUrl(), 'wss://crawler.astral-helio.api/api/monitor/ws');
});

function hooks() {
  const slots = [], effects = [];
  let cursor = 0, pending = false, render;
  const equal = (a, b) => a && b && a.length === b.length && a.every((value, index) => Object.is(value, b[index]));
  const react = {
    useState(initial) {
      const index = cursor++;
      if (!(index in slots)) slots[index] = typeof initial === 'function' ? initial() : initial;
      return [slots[index], value => {
        const next = typeof value === 'function' ? value(slots[index]) : value;
        if (!Object.is(next, slots[index])) { slots[index] = next; pending = true; }
      }];
    },
    useRef(initial) { const index = cursor++; return slots[index] ??= { current: initial }; },
    useCallback(fn, deps) {
      const index = cursor++;
      if (!equal(slots[index]?.deps, deps)) slots[index] = { value: fn, deps };
      return slots[index].value;
    },
    useEffect(effect, deps) {
      const index = cursor++, previous = slots[index];
      if (!equal(previous?.deps, deps)) {
        slots[index] = { ...previous, deps };
        effects.push(() => { previous?.cleanup?.(); slots[index].cleanup = effect(); });
      }
    },
  };
  function flush() {
    do {
      pending = false; cursor = 0; render();
      effects.splice(0).forEach(effect => effect());
    } while (pending);
  }
  return { react, mount(fn) { render = fn; flush(); }, flush,
    unmount() { slots.forEach(slot => slot?.cleanup?.()); } };
}

test('a mounted WebSocket closes the previous project and reconnects once, including nested change detection', async () => {
  const f = fixture(), runtime = hooks(), timers = new Map(), sockets = [];
  let timerId = 0;
  class Socket {
    static CONNECTING = 0; static OPEN = 1; static CLOSED = 3;
    readyState = Socket.CONNECTING;
    constructor(url, protocols) { this.url = url; this.protocols = protocols; sockets.push(this); }
    open() { this.readyState = Socket.OPEN; this.onopen?.(); }
    close() { this.readyState = Socket.CLOSED; this.onclose?.(); }
  }
  const exports = {};
  vm.runInNewContext(compiled['hooks/useWebSocket'], { exports, window: f.window, WebSocket: Socket,
    setTimeout: fn => { timers.set(++timerId, fn); return timerId; }, clearTimeout: id => timers.delete(id),
    require: name => name === 'react' ? runtime.react : name.endsWith('/api') ? f.load('services/api') :
      name.endsWith('/session') ? f.session : f.domains,
  });
  const tick = async () => {
    const queued = [...timers.entries()];
    for (const [id, callback] of queued) { if (timers.delete(id)) callback(); }
    await Promise.resolve(); runtime.flush();
  };
  const api = f.load('services/api');
  runtime.mount(() => exports.useWebSocket(api.getDashboardWsUrl, { onMessage() {} }));
  await tick();
  assert.equal(sockets.length, 1);
  assert.equal(sockets[0].url, 'wss://ontology.astral-helio.api/ws');
  assert.deepEqual(Array.from(sockets[0].protocols), ['bearer', 'access-1']);
  sockets[0].open(); runtime.flush();
  f.select('crawler');
  // The getter detects the new project inside this event and emits one nested event.
  f.window.dispatchEvent(new Event(changedEvent));
  runtime.flush(); await tick();
  assert.equal(sockets[0].readyState, Socket.CLOSED);
  assert.equal(sockets.length, 2);
  assert.equal(sockets[1].url, 'wss://crawler.astral-helio.api/ws');
  sockets[1].open(); runtime.flush();
  f.select('ontology'); f.domains.getCurrentProjectDomain();
  runtime.flush(); await tick();
  assert.equal(sockets.length, 3);
  assert.equal(sockets[1].readyState, Socket.CLOSED);
  assert.equal(sockets[2].url, 'wss://ontology.astral-helio.api/ws');
  sockets[2].open(); runtime.flush();
  f.lock(); f.select('graph'); f.domains.getCurrentProjectDomain();
  runtime.flush(); await tick();
  assert.equal(sockets[2].readyState, Socket.CLOSED);
  assert.equal(sockets.length, 3);
  runtime.unmount();
  assert.equal(timers.size, 0);
});
