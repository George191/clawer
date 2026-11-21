import { useEffect, useRef, useState } from 'react';
import { Button, message } from '@/components/ui';
import { newOntologyRequestId, ontologyError, type OntologyRecord, type TransactionReceipt } from '../../services/ontology';
import { graphRequest } from '../../services/ontology_graph';
import { parsePropertyValue } from './ontologyPropertyValue';
import useExplorerErrorToast from './useExplorerErrorToast';

type Catalog = {revision: number; items: {key: string; name: string; parameters: {key: string; type: string; nullable: boolean}[]}[]};

export default function OntologyFunctions({id, record, onCommitted}: {
  id: string; record?: OntologyRecord; onCommitted: (name: string) => void;
}) {
  const [catalog,setCatalog] = useState<Catalog>();
  const [selected,setSelected] = useState('');
  const [values,setValues] = useState<Record<string,string|null>>({});
  const [pending,setPending] = useState(false);
  const [error,setError] = useState('');
  const [requestError,setRequestError] = useState('');
  const [refresh,setRefresh] = useState(0);
  useExplorerErrorToast(requestError, 'Unable to load functions', () => { if (!pending) setRefresh(value => value + 1); });
  const busy = useRef(false);
  const retryIds = useRef(new Map<string,string>());
  const path = '/object-types/'+encodeURIComponent(id)+'/functions';
  useEffect(() => {
    const controller = new AbortController();
    setCatalog(undefined); setError(''); setRequestError('');
    graphRequest<Catalog>('get',path,{signal:controller.signal}).then(result => {
      if (!controller.signal.aborted) {setCatalog(result);setSelected(result.items[0]?.key ?? '');setValues({});}
    }, e => {if (!controller.signal.aborted) setRequestError(ontologyError(e,'Unable to load functions.'));});
    return () => controller.abort();
  },[path,refresh]);
  const fn = catalog?.items.find(f => f.key === selected);
  const run = async () => {
    if (!record?.id || !record.revision || !catalog || !fn || busy.current) return;
    busy.current = true; setPending(true);setError('');
    let submitted = false;
    try {
      const args = Object.fromEntries(fn.parameters.map(p => {
        if (!(p.key in values)) throw new Error('Enter a value for '+p.key+'.');
        const value = values[p.key];
        return [p.key,value === null ? null : parsePropertyValue(value,p.type)];
      }));
      const data = {modelRevision:catalog.revision, recordId:record.id, expectedRevision:record.revision, arguments:args};
      const signature = JSON.stringify([fn.key,data]);
      const requestId = retryIds.current.get(signature) ?? newOntologyRequestId();
      retryIds.current.set(signature,requestId);
      submitted = true;
      const receipt = await graphRequest<TransactionReceipt>('post',path+'/'+encodeURIComponent(fn.key)+'/invoke',{data:{...data,requestId}});
      retryIds.current.delete(signature);onCommitted(receipt.name);
    } catch (e) {const detail=ontologyError(e,'Unable to run function.');if (!submitted) setError(detail);message.error({content:'Unable to run function',description:detail,className:'explorer-toast'});}
    finally {busy.current=false;setPending(false);}
  };
  return <section className="flow-tool-section" aria-label="Business function runner"><h4>Business functions</h4>
    {error && <p role="alert">{error} <Button disabled={pending} onClick={() => setRefresh(n=>n+1)}>Reload functions</Button></p>}
    {!catalog && !error && !requestError && <p role="status">Loading functions…</p>}
    {catalog && (catalog.items.length ? <>
      <div className="flow-function-fields">
        <label>Published function<select aria-label="Published function" value={selected} disabled={pending} onChange={e => {setSelected(e.target.value);setValues({});setError('');}}>
          {catalog.items.map(item => <option key={item.key} value={item.key}>{item.name}</option>)}</select></label>
        {fn?.parameters.map(p => <div key={p.key}><label>{p.key} ({p.type})<input aria-label={'Argument '+p.key} disabled={pending || values[p.key] === null} value={values[p.key] ?? ''} onChange={e => setValues(prev=>({...prev,[p.key]:e.target.value}))} /></label>
          {p.nullable && <label><input type="checkbox" disabled={pending} checked={values[p.key] === null} onChange={e=>setValues(prev=>({...prev,[p.key]:e.target.checked ? null : ''}))} />Set {p.key} to null</label>}</div>)}
      </div>
      <p>{record?.id ? 'Selected record: '+String(record.title ?? record.id) : 'Select an active record in Results.'}</p>
      <Button disabled={!record?.id || !fn || pending} onClick={run}>Run function</Button>
    </> : <p>No published functions are available to this account.</p>)}
  </section>;
}
