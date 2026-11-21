import { useEffect, useRef } from 'react';
import { message } from '@/components/ui';
import { getSessionState } from '../../services/session';

export default function useExplorerErrorToast(error: string | undefined, title: string, retry?: () => void) {
  const identity = getSessionState().identity;
  const failure = useRef({ error, identity });
  const retryAction = useRef(retry);
  retryAction.current = retry;
  if (failure.current.error !== error) failure.current = { error, identity };
  useEffect(() => {
    const current = () => {
      const session = getSessionState();
      return session.authenticated && !session.locked && session.identity === identity;
    };
    if (!error || failure.current.identity !== identity || !current()) return;
    return message.error({
      content: title,
      description: <>{error}{retryAction.current && <> <a href={window.location.href} onClick={event => {
        event.preventDefault(); if (current()) retryAction.current?.();
      }}>Retry</a></>}</>,
      className: 'explorer-toast', duration: retryAction.current ? 0 : 5,
    });
  }, [error, title, identity]);
}
