import { useLayoutEffect, type ReactNode } from 'react';
import { BrowserRouter, Navigate, useLocation } from 'react-router-dom';
import { getCurrentProjectDomain, setCurrentProjectDomain, type ProjectDomain } from './services/projectDomains';

const defaultPaths: Record<ProjectDomain, string> = {
  crawler: '/scout', vault: '/vault/catalog', ontology: '/ontology', atlas: '/atlas', graph: '/graph', rag: '/rag',
};

function ProjectLocation({ children }: { children: ReactNode }) {
  const { pathname, search, hash } = useLocation();
  useLayoutEffect(() => { setCurrentProjectDomain(getCurrentProjectDomain()); }, [pathname]);
  if (pathname === '/') return <Navigate to={defaultPaths[getCurrentProjectDomain()] + search + hash} replace />;
  return children;
}

export default function ProjectRouter({ children }: { children: ReactNode }) {
  return <BrowserRouter><ProjectLocation>{children}</ProjectLocation></BrowserRouter>;
}
