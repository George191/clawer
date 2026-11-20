export type ProjectDomain = 'crawler' | 'vault' | 'ontology' | 'atlas' | 'graph' | 'rag';
export const PROJECT_CHANGED_EVENT = 'project-domain-changed';
const PROJECT_STORAGE_KEY = 'helio_project_domain';
const projectRoutes: Record<ProjectDomain, readonly string[]> = {
  crawler: ['/scout', '/source-strategy', '/anti-crawl', '/field-mapping'],
  vault: ['/vault', '/explorer', '/data-api'],
  ontology: ['/ontology'],
  atlas: ['/atlas'],
  graph: ['/graph'],
  rag: ['/rag'],
};

export function resolveProjectDomain(pathname: string): ProjectDomain | undefined {
  return (Object.keys(projectRoutes) as ProjectDomain[]).find(project =>
    projectRoutes[project].some(route => pathname === route || pathname.startsWith(route + '/')));
}

function rememberedProjectDomain(): ProjectDomain {
  const value = sessionStorage.getItem(PROJECT_STORAGE_KEY);
  return value && Object.prototype.hasOwnProperty.call(projectRoutes, value) ? value as ProjectDomain : 'ontology';
}

let lastNotifiedDomain = rememberedProjectDomain();

function rememberProjectDomain(project: ProjectDomain) {
  if (sessionStorage.getItem(PROJECT_STORAGE_KEY) !== project) sessionStorage.setItem(PROJECT_STORAGE_KEY, project);
}

export function setCurrentProjectDomain(project: ProjectDomain) {
  rememberProjectDomain(project);
  if (lastNotifiedDomain !== project) {
    lastNotifiedDomain = project;
    window.dispatchEvent(new Event(PROJECT_CHANGED_EVENT));
  }
}

export function getCurrentProjectDomain(): ProjectDomain {
  const project = resolveProjectDomain(window.location.pathname) ?? rememberedProjectDomain();
  rememberProjectDomain(project);
  return project;
}

export function projectHref(path: string): string {
  const url = new URL(path, window.location.origin);
  if (url.origin !== window.location.origin) throw new Error('Project links must use the current frontend origin.');
  return url.href;
}
