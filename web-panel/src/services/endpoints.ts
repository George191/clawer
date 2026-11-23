const config = window.__APP_CONFIG__;
const deployed = Boolean(config?.frontendDomainSuffix);
if (!config || ['backendDomainSuffix', 'httpsPort', 'frontendDomainSuffix'].some(key =>
    typeof config[key as keyof typeof config] !== 'string')) {
  throw new Error('Missing runtime configuration: /runtime-config.js must define the deployment domain suffixes and HTTPS port.');
}

const { backendDomainSuffix, httpsPort, frontendDomainSuffix } = config;
const dnsSuffix = /^(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$/i;
if (deployed ? !dnsSuffix.test(frontendDomainSuffix) || !dnsSuffix.test(backendDomainSuffix) ||
    window.location.protocol !== 'https:' || window.location.hostname !== `app.${frontendDomainSuffix.toLowerCase()}` ||
    !/^\d+$/.test(httpsPort) || Number(httpsPort) < 1 || Number(httpsPort) > 65535
    : backendDomainSuffix !== '' || httpsPort !== '') {
  throw new Error('Invalid runtime deployment configuration.');
}

export function apiUrl(path: string): string {
  const origin = deployed
    ? `https://api.${backendDomainSuffix}${Number(httpsPort) === 443 ? '' : `:${httpsPort}`}`
    : window.location.origin;
  const url = new URL(path, origin);
  if (deployed && url.origin === new URL(origin).origin) {
    url.pathname = url.pathname.replace(/^\/api\/v1(?=\/|$)/, '/v1');
  }
  return url.href;
}

export function wsUrl(path: string): string {
  const url = new URL(apiUrl(path));
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
  return url.href;
}
