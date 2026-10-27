import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { afterEach, beforeEach, mock, test } from 'node:test';
import axios from 'axios';
import ts from 'typescript';

const source = await readFile(new URL('./src/services/api.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 },
}).outputText.replace("from 'axios'", `from '${import.meta.resolve('axios')}'`);
const originalAdapter = axios.defaults.adapter;
let moduleId = 0;

beforeEach(() => { mock.method(console, 'error', () => {}); });
afterEach(() => { axios.defaults.adapter = originalAdapter; mock.restoreAll(); });

async function setup(adapter) {
  const tokens = new Map([['access_token', 'expired'], ['refresh_token', 'refresh']]);
  const redirects = [];
  globalThis.localStorage = {
    getItem: (key) => tokens.get(key) ?? null,
    setItem: (key, value) => tokens.set(key, value),
    removeItem: (key) => tokens.delete(key),
  };
  globalThis.location = {
    protocol: 'http:', host: 'localhost:3000', pathname: '/flow/layers',
    search: '?schema=ts_ods', hash: '#data', replace: (url) => redirects.push(url),
  };
  axios.defaults.adapter = adapter;
  const api = await import(`data:text/javascript;base64,${Buffer.from(`${compiled}\n// ${moduleId++}`).toString('base64')}`);
  const loading = [];
  api.onGlobalLoading((value) => loading.push(value));
  return { api, tokens, redirects, loading };
}

function response(config, data) {
  return { config, data, status: 200, statusText: 'OK', headers: {} };
}

function fail(config, status, data = { detail: 'Not authenticated' }) {
  throw new axios.AxiosError('Request failed', 'ERR_BAD_RESPONSE', config, null, {
    config, data, status, statusText: 'Error', headers: {},
  });
}

test('concurrent authentication failures share one refresh and replay with the new token', async () => {
  let refreshes = 0;
  let requests = 0;
  const { api, tokens, redirects, loading } = await setup(async (config) => {
    if (config.url === '/api/v1/login/refresh-token') {
      refreshes++;
      assert.equal(config.params.refresh_token, 'refresh');
      await new Promise(setImmediate);
      return response(config, { access_token: 'fresh' });
    }
    requests++;
    if (config.headers.Authorization === 'Bearer expired') return fail(config, 401);
    assert.equal(config.headers.Authorization, 'Bearer fresh');
    return response(config, []);
  });
  assert.deepEqual(await Promise.all([api.fetchLayers(), api.fetchEtlConnections(), api.fetchCurrentUserContext()]), [[], [], []]);
  assert.equal(refreshes, 1);
  assert.equal(requests, 6);
  assert.equal(tokens.get('access_token'), 'fresh');
  assert.deepEqual(redirects, []);
  assert.equal(loading.at(-1), false);
});

test('a late failure reuses a token already refreshed by another request', async () => {
  let refreshes = 0;
  let finishLate;
  const late = new Promise((resolve) => { finishLate = resolve; });
  const { api, redirects, loading } = await setup(async (config) => {
    if (config.url === '/api/v1/login/refresh-token') {
      refreshes++;
      return response(config, { access_token: 'fresh' });
    }
    if (config.headers.Authorization === 'Bearer expired') {
      if (config.url === '/etl/connections') await late;
      return fail(config, 401);
    }
    return response(config, []);
  });
  const lateRequest = api.fetchEtlConnections();
  await api.fetchLayers();
  finishLate();
  await lateRequest;
  assert.equal(refreshes, 1);
  assert.deepEqual(redirects, []);
  assert.equal(loading.at(-1), false);
});

test('legacy credential 403 responses refresh successfully', async () => {
  let refreshes = 0;
  const { api } = await setup(async (config) => {
    if (config.url === '/api/v1/login/refresh-token') {
      refreshes++;
      return response(config, { access_token: 'fresh' });
    }
    if (config.headers.Authorization === 'Bearer expired') {
      return fail(config, 403, { errors: 'Could not validate credentials' });
    }
    return response(config, []);
  });
  await api.fetchLayers();
  assert.equal(refreshes, 1);
});

test('permission 403 preserves the session and never refreshes', async () => {
  let requests = 0;
  const { api, tokens, redirects, loading } = await setup(async (config) => {
    requests++;
    return fail(config, 403, { errors: "The user doesn't have enough privileges" });
  });
  await assert.rejects(api.fetchEtlApis('ts_ods', 'records'), (error) => error.response.status === 403);
  assert.equal(requests, 1);
  assert.equal(tokens.get('access_token'), 'expired');
  assert.deepEqual(redirects, []);
  assert.deepEqual(loading, [true, false]);
});

for (const failure of ['network', 503]) {
  test(`temporary refresh failure (${failure}) preserves the session`, async () => {
    const { api, tokens, redirects, loading } = await setup(async (config) => {
      if (config.url !== '/api/v1/login/refresh-token') return fail(config, 401);
      if (failure === 'network') throw new axios.AxiosError('Network Error', 'ERR_NETWORK', config);
      return fail(config, failure, { detail: 'Service unavailable' });
    });
    await assert.rejects(api.fetchLayers());
    assert.equal(tokens.get('access_token'), 'expired');
    assert.equal(tokens.get('refresh_token'), 'refresh');
    assert.deepEqual(redirects, []);
    assert.equal(loading.at(-1), false);
  });
}

test('rejected refresh clears credentials and redirects once with the full return address', async () => {
  const { api, tokens, redirects, loading } = await setup(async (config) => fail(config, 401));
  await Promise.allSettled([api.fetchLayers(), api.fetchEtlConnections()]);
  assert.equal(tokens.size, 0);
  assert.deepEqual(redirects, ['/login?next=%2Fflow%2Flayers%3Fschema%3Dts_ods%23data']);
  assert.equal(loading.at(-1), false);
});

test('a replayed request failing authentication is not retried again', async () => {
  let refreshes = 0;
  let requests = 0;
  const { api, tokens, redirects, loading } = await setup(async (config) => {
    if (config.url === '/api/v1/login/refresh-token') {
      refreshes++;
      return response(config, { access_token: 'fresh' });
    }
    requests++;
    return fail(config, 401);
  });
  await assert.rejects(api.fetchLayers());
  assert.equal(refreshes, 1);
  assert.equal(requests, 2);
  assert.equal(tokens.size, 0);
  assert.equal(redirects.length, 1);
  assert.deepEqual(loading, [true, false, true, false]);
});

test('signing out during refresh cannot restore the session', async () => {
  let releaseRefresh;
  let refreshStarted;
  const started = new Promise((resolve) => { refreshStarted = resolve; });
  const pending = new Promise((resolve) => { releaseRefresh = resolve; });
  const { api, tokens, redirects } = await setup(async (config) => {
    if (config.url !== '/api/v1/login/refresh-token') return fail(config, 401);
    refreshStarted();
    await pending;
    return response(config, { access_token: 'fresh' });
  });
  const request = api.fetchLayers();
  await started;
  tokens.clear();
  releaseRefresh();
  await assert.rejects(request);
  assert.equal(tokens.size, 0);
  assert.deepEqual(redirects, []);
});
