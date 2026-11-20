const config = window.__APP_CONFIG__;
if (!config || typeof config.apiOrigin !== 'string') {
  throw new Error('Missing runtime configuration: /runtime-config.js must define window.__APP_CONFIG__.apiOrigin.');
}

const origin = config.apiOrigin;
let apiOrigin = window.location.origin;
if (origin) {
  const url = new URL(origin);
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.pathname !== '/' || url.search || url.hash) {
    throw new Error('Invalid runtime configuration: apiOrigin must be an HTTP(S) origin without a path.');
  }
  apiOrigin = url.origin;
}

export function apiUrl(path: string): string {
  return new URL(path, apiOrigin).href;
}

export function wsUrl(path: string): string {
  const url = new URL(apiUrl(path));
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
  return url.href;
}
