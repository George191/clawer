import assert from 'node:assert/strict';
import {execFile, execFileSync} from 'node:child_process';
import {mkdtempSync, realpathSync, rmSync, writeFileSync} from 'node:fs';
import {createServer} from 'node:http';
import {tmpdir} from 'node:os';
import {basename, dirname, join} from 'node:path';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {promisify} from 'node:util';
import test from 'node:test';
import {generatePublicationSdk, sdkFilename} from '../src/services/publicationSdk.ts';
const exec = promisify(execFile);
const require = createRequire(import.meta.url);
const tsc = fileURLToPath(new URL('../node_modules/typescript/bin/tsc', import.meta.url));
const cleanup = directory => {
  const resolved = realpathSync(directory);
  assert.equal(dirname(resolved), realpathSync(tmpdir()));
  assert.match(basename(resolved), /^publication-sdk-[a-zA-Z0-9]+$/);
  rmSync(resolved, {recursive: true, force: true});
};

test('all SDKs retain the endpoint, scoped key header and explicit page bounds', () => {
  for (const language of ['typescript', 'python', 'java', 'c']) {
    const source = generatePublicationSdk('https://api.example.org/api/ontology/v1/publications/test/data', language, 100, 100000);
    assert.ok(source.includes('https://api.example.org/api/ontology/v1/publications/test/data'));
    assert.ok(source.includes('X-API-Key'));
    assert.ok(source.includes('100000'));
    assert.ok(source.includes('100'));
    assert.ok(!source.includes('pk_live_'));
    assert.ok(!source.includes('Authorization'));
    assert.ok(sdkFilename(language));
  }
  for (const url of ['file:///local', 'https://user:secret@example.org/data', 'https://example.org/data?key=secret'])
    assert.throws(() => generatePublicationSdk(url, 'python'));
});

test('TypeScript client compiles and rejects denial, unsafe integers and bad pagination', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'publication-sdk-'));
  try {
    writeFileSync(join(dir, 'sdk.ts'), generatePublicationSdk('https://api.example.org/v1/etl/published/test/data', 'typescript', 100, 100000));
    execFileSync(process.execPath, [tsc, '--strict', '--target', 'ES2020', '--lib', 'ES2020,DOM', '--module', 'CommonJS', '--outDir', join(dir, 'js'), join(dir, 'sdk.ts')], {stdio: 'pipe'});
    const {PublicationClient} = require(join(dir, 'js/sdk.js'));
    const requests = [];
    const client = new PublicationClient('fixture-key', async (url, options) => {
      requests.push({url, options});
      return {ok: true, json: async () => ({rows: [{value: 1}], nextOffset: 2})};
    });
    assert.equal((await client.readPage(2, 0)).nextOffset, 2);
    assert.equal(requests[0].options.headers['X-API-Key'], 'fixture-key');
    assert.equal(requests[0].options.redirect, 'error');
    assert.equal(new URL(requests[0].url).searchParams.get('limit'), '2');
    await assert.rejects(client.readPage(101, 0));
    assert.equal(requests.length, 1);
    const denied = new PublicationClient('fixture-key', async () => ({ok: false, status: 403, text: async () => 'denied'}));
    await assert.rejects(denied.readPage(), /403.*denied/);
    const unsafe = new PublicationClient('fixture-key', async () => ({ok: true, json: async () => ({items: [{id: 9007199254740992}]})}));
    await assert.rejects(unsafe.readPage(), /safe JavaScript/);
    assert.throws(() => new PublicationClient('key\r\nInjected: yes'));
  } finally {cleanup(dir);}
});

test('Python SDK makes authenticated requests, preserves integers and refuses redirects and HTTP errors', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'publication-sdk-'));
  const seen = [];
  const server = createServer((request, response) => {
    seen.push(request);
    const offset = new URL(request.url, 'http://fixture').searchParams.get('offset');
    if (offset === '1') {response.writeHead(302, {Location: '/unexpected'}); response.end();}
    else if (offset === '2') {response.writeHead(403); response.end('denied');}
    else {response.setHeader('Content-Type', 'application/json'); response.end('{"items":[{"id":9007199254740993}],"nextOffset":null}');}
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  try {
    const endpoint = `http://127.0.0.1:${server.address().port}/data`;
    writeFileSync(join(dir, 'publication_client.py'), generatePublicationSdk(endpoint, 'python', 100, 100000));
    writeFileSync(join(dir, 'test_client.py'), `from publication_client import PublicationClient
from urllib.error import HTTPError
client = PublicationClient("fixture-key")
assert client.read_page(2, 0)["items"][0]["id"] == 9007199254740993
for offset in (1, 2):
    try:
        client.read_page(2, offset)
        raise AssertionError("Expected HTTPError")
    except HTTPError as error:
        assert error.code in (302, 403)
try:
    client.read_page(101, 0)
    raise AssertionError("Expected bounds failure")
except ValueError:
    pass
`);
    await exec('python', ['-B', join(dir, 'test_client.py')], {env: {...process.env, PYTHONUTF8: '1'}});
    assert.equal(seen.length, 3, 'no redirect or invalid pagination request escaped');
    assert.ok(seen.every(request => request.headers['x-api-key'] === 'fixture-key'));
  } finally {server.close(); cleanup(dir);}
});
