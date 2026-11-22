import { useEffect } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { getSessionState, SESSION_CHANGED_EVENT } from '../../services/session';
import type { ExplorerDetailSource } from '../../services/publishedExplorerDetails';
import { beginLinkSelection, readLinkSelection, type LinkSelectionContext } from './ExplorerLinkSelection';

export default function useExplorerLinkRoute(source: ExplorerDetailSource) {
  const location = useLocation(), navigate = useNavigate();
  const context = readLinkSelection(location.state, source);
  const finish = () => {
    if (!location.state?.linkSelection) return;
    const { linkSelection: _linkSelection, ...state } = location.state;
    navigate(location.pathname + location.search + location.hash, { replace: true, state });
  };
  useEffect(() => {
    if (!location.state?.linkSelection) return;
    const clearExpired = () => {
      const session = getSessionState();
      if (!session.authenticated || session.locked || session.identity !== location.state.linkSelection.sessionIdentity) finish();
    };
    clearExpired();
    window.addEventListener(SESSION_CHANGED_EVENT, clearExpired);
    return () => window.removeEventListener(SESSION_CHANGED_EVENT, clearExpired);
  }, [location.key]);
  return { context, finish, chooseTarget: (context: LinkSelectionContext) => beginLinkSelection(navigate, context) };
}
