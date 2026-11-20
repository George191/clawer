import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/services/session.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
}).outputText;
const suffix = 'asiral-helio.internal';
const cookieName = '__Secure-helio_session';
const origin = project => `https://${project}.${suffix}`;

class CookieJar {
  records = new Map();
  writes = [];
  acceptWrites = true;
  constructor(clock) { this.clock = clock; }
  read(host, protocol) {
    return [...this.records.values()].filter(record =>
      (!record.expires || record.expires > this.clock.now) &&
      (!record.attributes.secure || protocol === 'https:') &&
      (host === record.domain || host.endsWith('.' + record.domain)),
    ).map(record => `${record.name}=${record.value}`).join('; ');
  }
  write(text, host, protocol) {
    this.writes.push(text);
    if (!this.acceptWrites) return;
    const [pair, ...parts] = text.split(';').map(part => part.trim());
    const position = pair.indexOf('=');
    const name = pair.slice(0, position), value = pair.slice(position + 1);
    const attributes = Object.fromEntries(parts.map(part => {
      const [key, ...rest] = part.split('=');
      return [key.toLowerCase(), rest.length ? rest.join('=') : true];
    }));
    const domain = attributes.domain || host;
    if (host !== domain && !host.endsWith('.' + domain)) return;
    if (name.startsWith('__Secure-') && (protocol !== 'https:' || !attributes.secure)) return;
    const key = `${name}:${domain}`;
    if (attributes['max-age'] === '0') { this.records.delete(key); return; }
    this.records.set(key, { name, value, domain, attributes, expires: Date.parse(attributes.expires) });
  }
}

function load({ currentOrigin = origin('ontology'), config = { frontendDomainSuffix: suffix }, clock = { now: 1800000000000 }, jar = new CookieJar(clock), local = new Map() } = {}) {
  const location = new URL(currentOrigin), events = [];
  const document = {
    get cookie() { return jar.read(location.hostname, location.protocol); },
    set cookie(value) { jar.write(value, location.hostname, location.protocol); },
  };
  const context = {
    window: { __APP_CONFIG__: config, location, dispatchEvent: event => events.push(event.type) },
    document, exports: {},
    localStorage: { getItem: key => local.get(key) ?? null, setItem: (key, value) => local.set(key, value), removeItem: key => local.delete(key) },
    Date: class extends Date { static now() { return clock.now; } },
    Event, atob,
  };
  vm.runInNewContext(compiled, context);
  return { session: context.exports, clock, jar, local, events };
}

function tokens(clock, sessionExpires = Math.floor(clock.now / 1000) + 8 * 60 * 60) {
  const iat = Math.floor(clock.now / 1000);
  const jwt = (type, exp) => `header.${Buffer.from(JSON.stringify({ sub: 'user-1', iat, exp, session_exp: sessionExpires, type })).toString('base64url')}.signature`;
  return { access: jwt('access', Math.min(iat + 15 * 60, sessionExpires)), refresh: jwt('refresh', sessionExpires), sessionExpires };
}

test('production sessions use a secure parent-domain cookie shared by sibling projects', () => {
  const first = load(), token = tokens(first.clock);
  first.session.saveSession(token.access, token.refresh);
  const record = [...first.jar.records.values()][0];
  assert.equal(record.name, cookieName);
  assert.deepEqual(record.attributes, { path: '/', domain: suffix, secure: true, samesite: 'Strict', expires: new Date(token.sessionExpires * 1000).toUTCString() });
  assert.equal(first.local.size, 0);
  const sibling = load({ currentOrigin: origin('crawler'), jar: first.jar, clock: first.clock });
  assert.equal(sibling.session.getAccessToken(), token.access);
  assert.equal(sibling.session.getRefreshToken(), token.refresh);
  assert.equal(sibling.session.getSessionState().authenticated, true);
  assert.equal(first.jar.read('unrelated.internal', 'https:'), '');
  assert.equal(first.jar.read('ontology.asiral-helio.internal.attacker.test', 'https:'), '');
  assert.equal(first.jar.read('ontology.' + suffix, 'http:'), '');
});

test('production never restores an old localStorage session when the cookie is absent', () => {
  const first = load(), token = tokens(first.clock);
  first.local.set('auth_session', JSON.stringify({ access_token: token.access, refresh_token: token.refresh, last_activity: first.clock.now, locked: false }));
  assert.equal(first.session.getAccessToken(), null);
  assert.equal(first.session.getSessionState().authenticated, false);
});

test('shared activity, idle locking and unlock preserve the absolute eight-hour deadline', () => {
  const first = load(), token = tokens(first.clock);
  first.session.saveSession(token.access, token.refresh);
  const sibling = load({ currentOrigin: origin('atlas'), jar: first.jar, clock: first.clock });
  first.clock.now += 20 * 60 * 1000;
  sibling.session.recordActivity();
  first.clock.now += 20 * 60 * 1000;
  assert.equal(first.session.getSessionState().locked, false);
  first.clock.now += 10 * 60 * 1000;
  assert.equal(first.session.getSessionState().locked, true);
  first.session.lockSession();
  assert.equal(sibling.session.getSessionState().locked, true);
  first.clock.now = (token.sessionExpires - 60) * 1000;
  const renewed = tokens(first.clock, token.sessionExpires);
  assert.equal(sibling.session.unlockSession(renewed.access, token.refresh), true);
  assert.equal(first.session.getSessionState().locked, false);
  assert.equal([...first.jar.records.values()][0].expires, token.sessionExpires * 1000);
  first.clock.now = token.sessionExpires * 1000;
  assert.equal(first.session.getSessionState().authenticated, false);
  assert.equal(sibling.session.getAccessToken(), null);
});

test('logout clears the shared session and a stale refresh cannot restore it', () => {
  const first = load(), token = tokens(first.clock);
  first.session.saveSession(token.access, token.refresh);
  const sibling = load({ currentOrigin: origin('graph'), jar: first.jar, clock: first.clock });
  sibling.session.clearSession();
  assert.equal(first.session.getAccessToken(), null);
  assert.equal(first.session.getSessionState().authenticated, false);
  assert.equal(first.session.updateAccessToken(token.access, token.refresh), false);
  assert.equal(sibling.events.at(-1), 'auth-session-changed');
  assert.match(first.jar.writes.at(-1), /Max-Age=0/);
});

test('runtime suffix configuration must be explicit and match an HTTPS hostname boundary', () => {
  assert.throws(() => load({ config: {} }), /Missing runtime configuration/);
  assert.throws(() => load({ currentOrigin: 'http://ontology.' + suffix }), /Invalid runtime configuration/);
  assert.throws(() => load({ currentOrigin: 'https://not' + suffix }), /Invalid runtime configuration/);
  assert.throws(() => load({ currentOrigin: 'https://ontology.' + suffix + '.attacker.test' }), /Invalid runtime configuration/);
  assert.throws(() => load({ config: { frontendDomainSuffix: 'internal' } }), /Invalid runtime configuration/);
  assert.throws(() => load({ config: { frontendDomainSuffix: suffix + ';Path=/' } }), /Invalid runtime configuration/);
});

test('development explicitly uses localStorage and retains idle lock and logout behavior', () => {
  const dev = load({ currentOrigin: 'http://localhost:3000', config: { frontendDomainSuffix: '' } }), token = tokens(dev.clock);
  dev.session.saveSession(token.access, token.refresh);
  assert.equal(dev.session.getAccessToken(), token.access);
  assert.ok(dev.local.has('auth_session'));
  assert.equal(dev.jar.writes.length, 0);
  dev.clock.now += dev.session.IDLE_LOCK_MS;
  assert.equal(dev.session.getSessionState().locked, true);
  dev.session.clearSession();
  assert.equal(dev.local.has('auth_session'), false);
  assert.equal(dev.session.getSessionState().authenticated, false);
});

test('cookie write, deletion and malformed read failures are explicit', () => {
  const first = load(), token = tokens(first.clock);
  first.jar.acceptWrites = false;
  assert.throws(() => first.session.saveSession(token.access, token.refresh), /could not save/);
  first.jar.acceptWrites = true;
  first.session.saveSession(token.access, token.refresh);
  first.jar.acceptWrites = false;
  assert.throws(() => first.session.clearSession(), /could not clear/);
  first.jar.acceptWrites = true;
  first.jar.write(`${cookieName}=%invalid; Domain=${suffix}; Secure`, 'ontology.' + suffix, 'https:');
  assert.throws(() => first.session.getAccessToken(), /could not read/);
});
