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

function fixture({ refresh } = {}) {
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
  }, async post(url, body, config) {
    refreshes.push(url);
    return refresh ? refresh(url, config) : { data: { access_token: 'access-2' } };
  } };
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

test('the Published catalog sends the saved connection alongside search and pagination', async () => {
  const fixtureState = fixture(), ontology = fixtureState.load('services/ontology');
  const id = '2e7a9733-daa3-4f04-a788-4fd9f2e2b818';
  await ontology.listPublishedOntologyTypes(25, 50, 'Airport', undefined, undefined, true, false, id);
  const request = new URL(fixtureState.requests[0]);
  assert.equal(request.pathname, '/api/ontology/v1/object-types');
  assert.equal(request.searchParams.get('source'), id);
  assert.equal(request.searchParams.get('q'), 'Airport');
  assert.equal(request.searchParams.get('offset'), '50');
  assert.equal(request.searchParams.get('includeHidden'), 'true');
});

test('physical catalog reads use the warehouse Data Connections routes', async () => {
  const fixtureState = fixture(), api = fixtureState.load('services/api');
  await api.fetchWarehouseConnection();
  await api.fetchEtlObjectTypes({ schema: 'ts_ods', limit: 25 }, undefined, 'warehouse');
  await api.fetchLayerTables('ts_ods');
  await api.fetchEtlObjectPreview('ts_ods', 'airports');
  assert.deepEqual(fixtureState.requests.map(url => new URL(url).pathname), [
    '/v1/etl/data-connections/warehouse',
    '/v1/etl/data-connections/warehouse/object-types',
    '/v1/etl/data-connections/warehouse/layers/ts_ods/tables',
    '/v1/etl/data-connections/warehouse/object-types/ts_ods/airports/preview',
  ]);
  assert.equal(new URL(fixtureState.requests[1]).searchParams.get('schema'), 'ts_ods');
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

test('a new session refreshes independently while an old session refresh is still pending', async () => {
  const pending = [];
  const f = fixture({ refresh: (url, config) => new Promise(resolve => pending.push({ url, config, resolve })) });
  let identity = 'old-session', token = 'old-access', refresh = 'old-refresh', cleared = 0;
  Object.assign(f.session, {
    getSessionState: () => ({ authenticated: true, locked: false, identity }),
    getAccessToken: () => token, getRefreshToken: () => refresh,
    updateAccessToken: (next, expectedRefresh) => {
      if (refresh !== expectedRefresh) return false;
      token = next;
      return true;
    },
    clearSession: () => { cleared++; token = null; refresh = null; },
  });
  f.window.location.replace = () => {};
  const api = f.load('services/api');
  api.default.defaults.adapter = async config => {
    if (config.headers.Authorization !== 'Bearer new-access-refreshed') {
      throw new axios.AxiosError('Unauthorized', 'ERR_BAD_REQUEST', config, {}, { ...f.response(config), status: 401 });
    }
    return f.response(config);
  };
  const flush = async () => { for (let index = 0; index < 12; index++) await Promise.resolve(); };
  const oldRequest = api.fetchLayers().then(() => null, error => error);
  await flush();
  assert.equal(pending.length, 1);
  // Signing out and signing in replaces both the session identity and refresh token.
  identity = 'new-session'; token = 'new-access'; refresh = 'new-refresh';
  const newRequest = api.fetchLayers().then(value => ({ value }), error => ({ error }));
  await flush();
  assert.equal(pending.length, 2);
  assert.deepEqual(pending.map(item => item.config.params.refresh_token), ['old-refresh', 'new-refresh']);
  assert.equal(pending[0].url, pending[1].url);
  pending[0].resolve({ data: { access_token: 'old-access-refreshed' } });
  assert.equal((await oldRequest).response.status, 401);
  assert.equal(cleared, 0);
  assert.equal(refresh, 'new-refresh');
  pending[1].resolve({ data: { access_token: 'new-access-refreshed' } });
  assert.equal((await newRequest).error, undefined);
  assert.equal(token, 'new-access-refreshed');
  assert.equal(cleared, 0);
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
