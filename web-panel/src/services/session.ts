export const SESSION_CHANGED_EVENT = 'auth-session-changed';
export const IDLE_LOCK_MS = 30 * 60 * 1000;
export const SESSION_KEY = 'auth_session';
type StoredSession = { access_token: string; refresh_token?: string; last_activity: number; locked: boolean };
const SESSION_COOKIE = '__Secure-helio_session';
const configuredSuffix = window.__APP_CONFIG__?.frontendDomainSuffix;
if (typeof configuredSuffix !== 'string') {
  throw new Error('Missing runtime configuration: frontendDomainSuffix is required.');
}
const frontendDomainSuffix = configuredSuffix.toLowerCase();
if (frontendDomainSuffix && (window.location.protocol !== 'https:' ||
    !/^(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$/.test(frontendDomainSuffix) ||
    window.location.hostname !== frontendDomainSuffix && !window.location.hostname.endsWith('.' + frontendDomainSuffix))) {
  throw new Error('Invalid runtime configuration: frontendDomainSuffix must match this HTTPS host.');
}
const cookieAttributes = `Path=/; Domain=${frontendDomainSuffix}; Secure; SameSite=Strict`;

type Claims = { sub: string; exp: number; iat: number; session_exp: number; type: 'access' | 'refresh' };

// These claims drive the UI clock only. The server always verifies the JWT signature.
function claims(token: string | null, type: Claims['type']): Claims | null {
  try {
    if (!token || token.split('.').length !== 3) return null;
    const encoded = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
    const value = JSON.parse(atob(encoded.padEnd(Math.ceil(encoded.length / 4) * 4, '='))) as Claims;
    if (!value || value.type !== type || typeof value.sub !== 'string' || !value.sub ||
        ![value.exp, value.iat, value.session_exp].every((n) => typeof n === 'number' && Number.isFinite(n) && n > 0) ||
        value.exp > value.session_exp || value.session_exp > value.iat + 8 * 60 * 60) return null;
    return value;
  } catch { return null; }
}

function storedSession(): StoredSession | null {
  const value = frontendDomainSuffix
    ? document.cookie.split(';').map(value => value.trim()).find(value => value.startsWith(SESSION_COOKIE + '='))?.slice(SESSION_COOKIE.length + 1)
    : localStorage.getItem(SESSION_KEY);
  try {
    return JSON.parse(value ? frontendDomainSuffix ? decodeURIComponent(value) : value : 'null') as StoredSession | null;
  }
  catch {
    if (frontendDomainSuffix) throw new Error('The browser could not read the shared authentication session.');
    return null;
  }
}

function writeSession(stored: StoredSession) {
  const value = JSON.stringify(stored);
  if (frontendDomainSuffix) {
    const token = stored.refresh_token ? claims(stored.refresh_token, 'refresh') : claims(stored.access_token, 'access');
    if (!token) throw new Error('Cannot save an invalid authentication session.');
    document.cookie = `${SESSION_COOKIE}=${encodeURIComponent(value)}; ${cookieAttributes}; Expires=${new Date(Math.min(token.exp, token.session_exp) * 1000).toUTCString()}`;
    if (JSON.stringify(storedSession()) !== value) throw new Error('The browser could not save the shared authentication session.');
  } else {
    localStorage.setItem(SESSION_KEY, value);
  }
}
export const getAccessToken = () => storedSession()?.access_token || null;
export const getRefreshToken = () => storedSession()?.refresh_token || null;
export const tokenSubject = (token: string | null, type: Claims['type']) => claims(token, type)?.sub;
export const tokenExpiresAt = (token: string | null, type: Claims['type']) => (claims(token, type)?.exp || 0) * 1000;

export function getSessionState() {
  const stored = storedSession();
  const token = stored?.refresh_token;
  const access = claims(stored?.access_token || null, 'access');
  const value = token ? claims(token, 'refresh') : access;
  const expiresAt = value ? Math.min(value.exp, value.session_exp) * 1000 : 0;
  const authenticated = expiresAt > Date.now() && (!access || !value || access.sub === value.sub && access.session_exp === value.session_exp);
  const storedActivity = stored?.last_activity;
  // Missing activity state must never turn an existing token into an unlocked session.
  const lastActivity = typeof storedActivity === 'number' && Number.isFinite(storedActivity) && storedActivity > 0 ? storedActivity : 0;
  const idleAt = lastActivity + IDLE_LOCK_MS;
  return { authenticated, expiresAt, idleAt, locked: authenticated && (stored?.locked === true || Date.now() >= idleAt), identity: value ? `${value.sub}:${value.session_exp}` : '', subject: authenticated ? value?.sub : undefined };
}

const changed = () => window.dispatchEvent(new Event(SESSION_CHANGED_EVENT));

export function clearSession() {
  if (frontendDomainSuffix) {
    document.cookie = `${SESSION_COOKIE}=; ${cookieAttributes}; Max-Age=0`;
    if (storedSession()) throw new Error('The browser could not clear the shared authentication session.');
  } else {
    [SESSION_KEY, 'access_token', 'refresh_token', 'auth_last_activity', 'auth_locked'].forEach((key) => localStorage.removeItem(key));
  }
  changed();
}

export function saveSession(accessToken: string, refreshToken?: string) {
  const access = claims(accessToken, 'access');
  const refresh = refreshToken ? claims(refreshToken, 'refresh') : null;
  if (!access || access.exp * 1000 <= Date.now() || refreshToken && (!refresh || refresh.sub !== access.sub || refresh.session_exp !== access.session_exp || refresh.exp * 1000 <= Date.now())) {
    throw new Error('The sign-in service returned an invalid session. Please sign in again.');
  }
  writeSession({ access_token: accessToken, refresh_token: refreshToken, last_activity: Date.now(), locked: false });
  if (!frontendDomainSuffix) ['access_token', 'refresh_token', 'auth_last_activity', 'auth_locked'].forEach((key) => localStorage.removeItem(key));
  changed();
}

export function updateAccessToken(token: string, expectedRefresh: string) {
  const next = claims(token, 'access');
  const refresh = claims(expectedRefresh, 'refresh');
  if (getRefreshToken() !== expectedRefresh || !getSessionState().authenticated || !next || !refresh || next.sub !== refresh.sub || next.session_exp !== refresh.session_exp || next.exp * 1000 <= Date.now()) return false;
  writeSession({ ...storedSession()!, access_token: token });
  changed();
  return true;
}

export function lockSession(notify = true) {
  const stored = storedSession();
  if (stored && !stored.locked) {
    writeSession({ ...stored, locked: true });
    if (notify) changed();
  }
}

export function recordActivity() {
  const state = getSessionState();
  if (!state.authenticated) return;
  if (state.locked) { lockSession(); return; }
  const stored = storedSession();
  const previous = stored?.last_activity || 0;
  if (Date.now() - previous < 1000) return;
  writeSession({ ...stored!, last_activity: Date.now() });
  changed();
}

export function unlockSession(token: string, expectedRefresh: string) {
  if (!updateAccessToken(token, expectedRefresh)) return false;
  writeSession({ ...storedSession()!, last_activity: Date.now(), locked: false });
  changed();
  return true;
}
