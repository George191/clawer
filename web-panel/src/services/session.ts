export const SESSION_CHANGED_EVENT = 'auth-session-changed';
export const IDLE_LOCK_MS = 30 * 60 * 1000;
export const SESSION_KEY = 'auth_session';
type StoredSession = { access_token: string; refresh_token?: string; last_activity: number; locked: boolean };

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
  try { return JSON.parse(localStorage.getItem(SESSION_KEY) || 'null') as StoredSession | null; }
  catch { return null; }
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
  return { authenticated, expiresAt, idleAt, locked: authenticated && (stored?.locked === true || Date.now() >= idleAt), identity: value ? `${value.sub}:${value.session_exp}` : '' };
}

const changed = () => window.dispatchEvent(new Event(SESSION_CHANGED_EVENT));

export function clearSession() {
  [SESSION_KEY, 'access_token', 'refresh_token', 'auth_last_activity', 'auth_locked'].forEach((key) => localStorage.removeItem(key));
  changed();
}

export function saveSession(accessToken: string, refreshToken?: string) {
  const access = claims(accessToken, 'access');
  const refresh = refreshToken ? claims(refreshToken, 'refresh') : null;
  if (!access || access.exp * 1000 <= Date.now() || refreshToken && (!refresh || refresh.sub !== access.sub || refresh.session_exp !== access.session_exp || refresh.exp * 1000 <= Date.now())) {
    throw new Error('The sign-in service returned an invalid session. Please sign in again.');
  }
  localStorage.setItem(SESSION_KEY, JSON.stringify({ access_token: accessToken, refresh_token: refreshToken, last_activity: Date.now(), locked: false }));
  ['access_token', 'refresh_token', 'auth_last_activity', 'auth_locked'].forEach((key) => localStorage.removeItem(key));
  changed();
}

export function updateAccessToken(token: string, expectedRefresh: string) {
  const next = claims(token, 'access');
  const refresh = claims(expectedRefresh, 'refresh');
  if (getRefreshToken() !== expectedRefresh || !getSessionState().authenticated || !next || !refresh || next.sub !== refresh.sub || next.session_exp !== refresh.session_exp || next.exp * 1000 <= Date.now()) return false;
  localStorage.setItem(SESSION_KEY, JSON.stringify({ ...storedSession(), access_token: token }));
  changed();
  return true;
}

export function lockSession(notify = true) {
  const stored = storedSession();
  if (stored && !stored.locked) {
    localStorage.setItem(SESSION_KEY, JSON.stringify({ ...stored, locked: true }));
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
  localStorage.setItem(SESSION_KEY, JSON.stringify({ ...stored, last_activity: Date.now() }));
  changed();
}

export function unlockSession(token: string, expectedRefresh: string) {
  if (!updateAccessToken(token, expectedRefresh)) return false;
  localStorage.setItem(SESSION_KEY, JSON.stringify({ ...storedSession(), last_activity: Date.now(), locked: false }));
  changed();
  return true;
}
