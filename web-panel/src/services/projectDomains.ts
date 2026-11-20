export type ProjectDomain = 'crawler' | 'vault' | 'ontology' | 'atlas' | 'graph' | 'rag';

const projectNamespaces: Record<ProjectDomain, string> = {
  crawler: '/scout', vault: '/vault', ontology: '/ontology', atlas: '/atlas', graph: '/graph', rag: '/rag',
};
const projectRoutes: Record<ProjectDomain, readonly string[]> = {
  crawler: ['/scout', '/source-strategy', '/anti-crawl', '/field-mapping'],
  vault: ['/vault/catalog', '/vault/metadata', '/vault/quality', '/vault/lineage', '/vault/security', '/vault/market', '/explorer', '/data-api'],
  ontology: ['/ontology', '/ontology/explorer', '/ontology/explorer/record', '/ontology/connections', '/ontology/lineage', '/ontology/runtime', '/ontology/quality', '/ontology/runs', '/ontology/schedules', '/ontology/tasks', '/ontology/templates', '/ontology/schedule'],
  atlas: ['/atlas', '/atlas/metrics', '/atlas/quality'],
  graph: ['/graph', '/graph/entities', '/graph/quality', '/graph/lineage'],
  rag: ['/rag', '/rag/documents', '/rag/indexes', '/rag/evaluation'],
};
const sharedRoutes = ['/login', '/reset-password', '/automation', '/organization', '/settings', '/account', '/faq', '/legal', '/project-users', '/billing', '/project-settings', '/learning'];
const isSharedRoute = (pathname: string) => sharedRoutes.some(route => pathname === route || pathname.startsWith(route + '/'));
const withoutTrailingSlash = (pathname: string) => pathname.endsWith('/') && pathname !== '/' ? pathname.slice(0, -1) : pathname;

const configuredSuffix = window.__APP_CONFIG__?.frontendDomainSuffix;
if (typeof configuredSuffix !== 'string') {
  throw new Error('Missing runtime configuration: frontendDomainSuffix must be defined.');
}
const suffix = configuredSuffix.toLowerCase();

export const currentProjectDomain = suffix
  ? window.location.hostname.slice(0, -(suffix.length + 1)) as ProjectDomain
  : undefined;

if (suffix && (window.location.protocol !== 'https:' ||
    !window.location.hostname.endsWith(`.${suffix}`) ||
    !['crawler', 'vault', 'ontology', 'atlas', 'graph', 'rag'].includes(currentProjectDomain!))) {
  throw new Error('Invalid project hostname for the configured frontend domain suffix.');
}

export function publicProjectPath(pathname: string, project = currentProjectDomain): string {
  if (!suffix || !project || !projectRoutes[project].includes(withoutTrailingSlash(pathname))) return pathname;
  const namespace = projectNamespaces[project];
  if (withoutTrailingSlash(pathname) === namespace) return '/';
  return pathname.startsWith(namespace + '/') ? pathname.slice(namespace.length) : pathname;
}

export function internalProjectPath(pathname: string, project = currentProjectDomain): string {
  if (!suffix || !project || isSharedRoute(pathname)) return pathname;
  if (pathname === '/') return projectRoutes[project][0];
  const normalized = withoutTrailingSlash(pathname);
  const route = projectRoutes[project].find(route => publicProjectPath(route, project) === normalized);
  return route ? route + (pathname.endsWith('/') ? '/' : '') : pathname;
}

export function projectHref(project: ProjectDomain, path: string): string {
  const url = new URL(path, window.location.origin);
  if (suffix) {
    url.hostname = `${project}.${suffix}`;
    url.pathname = publicProjectPath(url.pathname, project);
  }
  return url.href;
}

export function projectNavigationHref(path: string): string {
  const url = new URL(path, window.location.origin);
  if (!suffix || url.origin !== window.location.origin) return url.href;
  const project = isSharedRoute(url.pathname) ? currentProjectDomain :
    (Object.keys(projectRoutes) as ProjectDomain[]).find(project => projectRoutes[project].includes(withoutTrailingSlash(url.pathname))) ?? currentProjectDomain;
  return projectHref(project!, url.pathname + url.search + url.hash);
}
