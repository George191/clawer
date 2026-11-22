import { useEffect, useState } from 'react';
import { Button, NativeSelect } from '@/components/ui';
import { graphRequest } from '../../services/ontology_graph';
import { ontologyError } from '../../services/ontology';

type Delivery = {id: string; receiver: string; status: 'pending' | 'failed' | 'delivered';
  receiptId: string | null; failure: string | null; attempts: number};
type Status = {receivers: {key: string; name: string}[]; deliveries: Delivery[]};

export default function OntologyWriteback({project, transaction}: {project: string; transaction: string}) {
  const path = '/projects/' + project + '/transactions/' + transaction + '/writeback';
  const [data, setData] = useState<Status | null>(null);
  const [key, choose] = useState('');
  const [error, fail] = useState('');
  const [pending, sending] = useState(false);
  const [refresh, reload] = useState(0);
  useEffect(() => {
    const cancel = new AbortController();
    graphRequest<Status>('get', path, {signal: cancel.signal}).then(result => {
      if (cancel.signal.aborted) return;
      setData(result);
      choose(old => result.receivers.some(r => r.key === old) ? old : result.receivers[0]?.key || '');
    }).catch(e => {if (!cancel.signal.aborted) fail(ontologyError(e, 'Unable to load writeback status.'));});
    return () => cancel.abort();
  }, [path, refresh]);
  const selected = data?.deliveries.find(r => r.receiver === key);
  async function send() {
    if (!key || pending) return;
    sending(true); fail('');
    try {
      const result = await graphRequest<Delivery>('post', path + '/' + encodeURIComponent(key));
      setData(old => old && {...old, deliveries: [...old.deliveries.filter(r => r.receiver !== key), result]});
    } catch (e) {
      fail(ontologyError(e, 'Delivery failed. Review the receipt before retrying.'));
    } finally {sending(false); reload(v => v + 1);}
  }
  return <section aria-label="External writeback">
    <h4>External writeback</h4>
    {data && !data.receivers.length && <p>No receivers configured for this project.</p>}
    {!!data?.receivers.length && <><label>Business receiver<NativeSelect aria-label="Business receiver" value={key}
      disabled={pending} onChange={e => {choose(e.target.value); fail('');}}>
      {data.receivers.map(r => <option key={r.key} value={r.key}>{r.name}</option>)}</NativeSelect></label>
      <Button disabled={pending || !key || selected?.status === 'delivered'} onClick={send}>
        {selected && selected.status !== 'delivered' ? 'Retry delivery' : 'Send transaction'}</Button></>}
    {selected && <p role="status">Delivery {selected.status} · Attempts {selected.attempts}
      {selected.receiptId && <> · Receipt {selected.receiptId}</>}{selected.failure && <> · {selected.failure}</>}</p>}
    {error && <p role="alert">{error}</p>}
  </section>;
}
