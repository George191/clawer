import { useLayoutEffect, type ReactNode } from 'react';
import { BrowserRouter, useLocation } from 'react-router-dom';
import { getCurrentProjectDomain, setCurrentProjectDomain } from './services/projectDomains';

function ProjectLocation({ children }: { children: ReactNode }) {
  const { pathname } = useLocation();
  useLayoutEffect(() => { setCurrentProjectDomain(getCurrentProjectDomain()); }, [pathname]);
  return children;
}

export default function ProjectRouter({ children }: { children: ReactNode }) {
  return <BrowserRouter><ProjectLocation>{children}</ProjectLocation></BrowserRouter>;
}
