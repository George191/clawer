import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const compiled = ts.transpileModule(readFileSync(new URL('../src/services/session.ts', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
}).outputText;

function load(local = new Map(), clock = { now: 1800000000000 }) {
  const events = [];
  const context = { exports: {},
    window: { dispatchEvent: event => events.push(event.type) },
    localStorage: { getItem: key => local.get(key) ?? null, setItem: (key, value) => local.set(key, value), removeItem: key => local.delete(key) },
    Date: class extends Date { static now() { return clock.now; } }, Event, atob,
  };
  vm.runInNewContext(compiled, context);
  return { session: context.exports, local, clock, events };
}

function tokens(clock, sessionExpires = Math.floor(clock.now / 1000) + 8 * 60 * 60, subject = 'user-1') {
  const iat = Math.floor(clock.now / 1000);
  const jwt = (type, exp) => `header.${Buffer.from(JSON.stringify({ sub: subject, iat, exp, session_exp: sessionExpires, type })).toString('base64url')}.signature`;
  return { access: jwt('access', Math.min(iat + 15 * 60, sessionExpires)), refresh: jwt('refresh', sessionExpires), sessionExpires };
}

test('one frontend origin retains the same login across project views and reloads', () => {
  const first = load(), token = tokens(first.clock);
  first.session.saveSession(token.access, token.refresh);
  const reloaded = load(first.local, first.clock);
  assert.equal(reloaded.session.getAccessToken(), token.access);
  assert.equal(reloaded.session.getRefreshToken(), token.refresh);
  assert.equal(reloaded.session.getSessionState().authenticated, true);
  assert.equal(first.events.at(-1), 'auth-session-changed');
});

test('activity, idle lock and unlock preserve the absolute eight-hour deadline', () => {
  const first = load(), token = tokens(first.clock);
  first.session.saveSession(token.access, token.refresh);
  first.clock.now += 20 * 60 * 1000;
  first.session.recordActivity();
  first.clock.now += 20 * 60 * 1000;
  assert.equal(first.session.getSessionState().locked, false);
  first.clock.now += 10 * 60 * 1000;
  assert.equal(first.session.getSessionState().locked, true);
  first.session.lockSession();
  first.clock.now = (token.sessionExpires - 60) * 1000;
  const renewed = tokens(first.clock, token.sessionExpires);
  assert.equal(first.session.unlockSession(renewed.access, token.refresh), true);
  assert.equal(first.session.getSessionState().locked, false);
  assert.equal(first.session.getSessionState().expiresAt, token.sessionExpires * 1000);
  first.clock.now = token.sessionExpires * 1000;
  assert.equal(first.session.getSessionState().authenticated, false);
});

test('logout clears login for the origin and a stale refresh cannot restore it', () => {
  const first = load(), token = tokens(first.clock);
  first.session.saveSession(token.access, token.refresh);
  const anotherView = load(first.local, first.clock);
  anotherView.session.clearSession();
  assert.equal(first.session.getAccessToken(), null);
  assert.equal(first.session.getSessionState().authenticated, false);
  assert.equal(first.session.updateAccessToken(token.access, token.refresh), false);
});

test('invalid or mismatched tokens cannot establish a login, missing activity stays locked', () => {
  const first = load(), token = tokens(first.clock);
  assert.throws(() => first.session.saveSession('invalid', token.refresh), /invalid session/);
  assert.throws(() => first.session.saveSession(token.access, tokens(first.clock, undefined, 'other-user').refresh), /invalid session/);
  first.local.set('auth_session', JSON.stringify({ access_token: token.access, refresh_token: token.refresh, locked: false }));
  assert.equal(first.session.getSessionState().locked, true);
});
