import { useLayoutEffect, useState, type ComponentProps, type ReactNode } from 'react';
import { createBrowserHistory, createPath, parsePath, type To } from '@remix-run/router';
import { BrowserRouter, Router } from 'react-router-dom';
import { currentProjectDomain, internalProjectPath, projectNavigationHref, publicProjectPath } from './services/projectDomains';

export function createProjectHistory(browserWindow: Window = window) {
  const history = createBrowserHistory({ window: browserWindow, v5Compat: true });
  const initial = history.location;
  const canonical = publicProjectPath(initial.pathname);
  if (canonical !== initial.pathname) history.replace({ ...initial, pathname: canonical }, initial.state);
  const internalLocation = () => ({ ...history.location, pathname: internalProjectPath(history.location.pathname) });
  const target = (to: To) => {
    const path = typeof to === 'string' ? parsePath(to) : to;
    return new URL(projectNavigationHref(createPath({ ...path, pathname: path.pathname || internalLocation().pathname })));
  };
  const navigate = (to: To, state: unknown, replace: boolean) => {
    const url = target(to);
    if (url.origin !== browserWindow.location.origin) {
      if (replace) browserWindow.location.replace(url.href);
      else browserWindow.location.assign(url.href);
      return;
    }
    history[replace ? 'replace' : 'push'](url.pathname + url.search + url.hash, state);
  };
  const navigator: ComponentProps<typeof Router>['navigator'] = {
    createHref: to => target(to).href,
    encodeLocation: to => history.encodeLocation(to),
    go: delta => history.go(delta),
    push: (to, state) => navigate(to, state, false),
    replace: (to, state) => navigate(to, state, true),
  };
  return {
    navigator,
    getState: () => ({ action: history.action, location: internalLocation() }),
    listen: (listener: (state: { action: typeof history.action; location: ReturnType<typeof internalLocation> }) => void) =>
      history.listen(() => listener({ action: history.action, location: internalLocation() })),
  };
}

function HostedProjectRouter({ children }: { children: ReactNode }) {
  const [history] = useState(() => createProjectHistory());
  const [state, setState] = useState(history.getState);
  useLayoutEffect(() => history.listen(setState), [history]);
  return <Router location={state.location} navigationType={state.action} navigator={history.navigator}>{children}</Router>;
}

export default function ProjectRouter({ children }: { children: ReactNode }) {
  return currentProjectDomain ? <HostedProjectRouter>{children}</HostedProjectRouter> : <BrowserRouter>{children}</BrowserRouter>;
}
