import { useEffect, useRef, useState } from 'react';
import { message } from '@/components/ui';
import { issuePublicationKey, revokePublicationKey, type EtlPublishedApi } from '../../services/api';
import { ontologyError } from '../../services/ontology';
import { getSessionState } from '../../services/session';
import PublicationKeyDialog from './PublicationKeyDialog';
import { useSandboxIdentity } from './SandboxMarkings';

type KeyItem = Pick<EtlPublishedApi, 'id' | 'keyStatus'>;

export default function useStoragePublicationKey(scope: string, onChanged: (id: string, status: KeyItem['keyStatus']) => void) {
  const identity = useSandboxIdentity();
  const context = scope && identity ? JSON.stringify([scope, identity]) : '';
  const currentContext = useRef(context);
  currentContext.current = context;
  const [state, setState] = useState<{ context: string; item: KeyItem; issuedKey: string; busy: boolean }>();
  const pending = useRef<AbortController>();
  const selected = state?.context === context ? state : undefined;

  useEffect(() => {
    setState(undefined);
    return () => { pending.current?.abort(); pending.current = undefined; };
  }, [context]);

  const isCurrent = () => {
    const session = getSessionState();
    return !!context && currentContext.current === context && session.identity === identity && session.authenticated && !session.locked;
  };
  const open = (item: KeyItem) => {
    if (isCurrent() && !pending.current) setState({ context, item, issuedKey: '', busy: false });
  };
  const change = async (revoke: boolean) => {
    if (!selected || !isCurrent() || pending.current || (revoke && selected.item.keyStatus !== 'active')) return;
    const controller = new AbortController();
    pending.current = controller;
    const {id} = selected.item;
    setState({...selected, busy: true});
    try {
      const result = revoke ? await revokePublicationKey(id, controller.signal) : await issuePublicationKey(id, controller.signal);
      if (!isCurrent() || controller.signal.aborted) return;
      setState({...selected, item: {id, keyStatus: result.keyStatus}, issuedKey: 'key' in result ? result.key : ''});
      onChanged(id, result.keyStatus);
    } catch (error) {
      if (isCurrent() && !controller.signal.aborted) message.error({
        content: 'Unable to update the publication key', description: ontologyError(error, 'Try again from the publication panel.'), className: 'explorer-toast',
      });
    } finally {
      if (pending.current === controller) {
        pending.current = undefined;
        if (isCurrent()) setState(previous => previous && {...previous, busy: false});
      }
    }
  };

  return { open, busy: !!selected?.busy, dialog: <PublicationKeyDialog item={selected?.item} issuedKey={selected?.issuedKey || ''}
    busy={!!selected?.busy} onClose={() => { if (!pending.current) setState(undefined); }} onChange={change} /> };
}
