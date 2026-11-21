import { useEffect, useRef, useState } from 'react';
import { Button, Input, message } from '@/components/ui';
import { ArrowRightOutlined, CheckCircleOutlined, CodeOutlined, ReloadOutlined, SearchOutlined } from '@/components/ui/icons';
import { newOntologyRequestId, ontologyError, type OntologyRecord, type TransactionReceipt } from '../../services/ontology';
import { graphRequest } from '../../services/ontology_graph';
import { getSessionState } from '../../services/session';
import { parsePropertyValue } from './ontologyPropertyValue';
import useExplorerErrorToast from './useExplorerErrorToast';
import './ontology-tool-workbench.css';

type Catalog = {revision: number; items: {key: string; name: string; parameters: {key: string; type: string; nullable: boolean}[]}[]};

export default function OntologyFunctions({id, record, onCommitted}: {
  id: string; record?: OntologyRecord; onCommitted: (name: string) => void;
}) {
  const [catalog, setCatalog] = useState<Catalog>();
  const [selected, setSelected] = useState(''), [search, setSearch] = useState('');
  const [values, setValues] = useState<Record<string, string | null>>({});
  const [pending, setPending] = useState(false), [error, setError] = useState('');
  const [requestError, setRequestError] = useState(''), [refresh, setRefresh] = useState(0);
  const [receipt, setReceipt] = useState<TransactionReceipt>();
  const busy = useRef(false), alive = useRef(true);
  const retryIds = useRef(new Map<string, string>());
  const identity = getSessionState().identity;
  const current = () => {
    const session = getSessionState();
    return alive.current && session.authenticated && !session.locked && session.identity === identity;
  };
  const path = '/object-types/' + encodeURIComponent(id) + '/functions';
  useExplorerErrorToast(requestError, 'Unable to load functions', () => { if (!pending) setRefresh(value => value + 1); });
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    const controller = new AbortController();
    setCatalog(undefined); setError(''); setRequestError(''); setReceipt(undefined);
    graphRequest<Catalog>('get', path, {signal: controller.signal}).then(result => {
      if (!controller.signal.aborted && current()) {
        setCatalog(result); setSelected(result.items[0]?.key ?? ''); setValues({});
      }
    }, failure => { if (!controller.signal.aborted && current()) setRequestError(ontologyError(failure, 'Unable to load functions.')); });
    return () => controller.abort();
  }, [path, refresh, identity]);
  const fn = catalog?.items.find(item => item.key === selected);
  const visible = catalog?.items.filter(item => (item.name + ' ' + item.key).toLowerCase().includes(search.toLowerCase())) || [];
  const run = async () => {
    if (!record?.id || !record.revision || !catalog || !fn || busy.current || !current()) return;
    busy.current = true; setPending(true); setError(''); setReceipt(undefined);
    let submitted = false;
    try {
      const args = Object.fromEntries(fn.parameters.map(parameter => {
        if (!(parameter.key in values)) throw new Error('Enter a value for ' + parameter.key + '.');
        const value = values[parameter.key];
        return [parameter.key, value === null ? null : parsePropertyValue(value, parameter.type)];
      }));
      const data = {modelRevision: catalog.revision, recordId: record.id, expectedRevision: record.revision, arguments: args};
      const signature = JSON.stringify([fn.key, data]);
      const requestId = retryIds.current.get(signature) ?? newOntologyRequestId();
      retryIds.current.set(signature, requestId);
      submitted = true;
      const result = await graphRequest<TransactionReceipt>('post', path + '/' + encodeURIComponent(fn.key) + '/invoke', {data: {...data, requestId}});
      if (!current()) return;
      retryIds.current.delete(signature); setReceipt(result); onCommitted(result.name);
    } catch (failure) {
      if (!current()) return;
      const detail = ontologyError(failure, 'Unable to run function.');
      if (!submitted) setError(detail);
      else message.error({content: 'Unable to run function', description: detail, className: 'explorer-toast'});
    } finally {
      busy.current = false;
      if (current()) setPending(false);
    }
  };
  return <section className="ontology-tool-workbench" aria-label="Business function runner">
    <header className="ontology-tool-header"><div><h3><CodeOutlined />Functions</h3><p>Run published business logic on one selected object. Changes are applied together and recorded in its history.</p></div>
      <Button size="small" icon={<ReloadOutlined />} disabled={pending} onClick={() => setRefresh(value => value + 1)}>Refresh</Button>
    </header>
    <div className="ontology-tool-layout">
      <aside className="ontology-tool-catalog" aria-label="Published functions">
        <div className="ontology-tool-search"><Input aria-label="Find functions" placeholder="Find a function" prefix={<SearchOutlined />} value={search} onChange={event => setSearch(event.target.value)} /></div>
        <div className="ontology-tool-catalog-list">
          {!catalog && !requestError && <p role="status">Loading functions…</p>}
          {catalog && !catalog.items.length && <p>No published functions are available to this account.</p>}
          {catalog && !!catalog.items.length && !visible.length && <p>No functions match this search.</p>}
          {visible.map(item => <button type="button" key={item.key} aria-pressed={selected === item.key} disabled={pending} onClick={() => {setSelected(item.key); setValues({}); setError(''); setReceipt(undefined);}}>
            <CodeOutlined /><span><strong>{item.name}</strong><small>{item.parameters.length} parameters</small></span>
          </button>)}
        </div>
      </aside>
      <div className="ontology-tool-detail">
        {fn ? <>
          <header className="ontology-tool-detail-header"><div><h4>{fn.name}</h4><code>{fn.key}</code></div><span>Published revision {catalog?.revision}</span></header>
          <div className="ontology-tool-form">
            <div className="ontology-tool-target"><span>Target object</span><strong>{record?.id ? String(record.title ?? record.id) : 'Select an object in Results'}</strong>{record?.revision && <small>Object revision {record.revision}</small>}</div>
            <div className="ontology-tool-section-heading"><h4>Inputs</h4><span>{fn.parameters.length} parameters</span></div>
            {!fn.parameters.length && <p className="ontology-tool-note">This function does not require input parameters.</p>}
            <div className="ontology-tool-fields">{fn.parameters.map(parameter => <div key={parameter.key}>
              <label><span>{parameter.key}<small>{parameter.type}{parameter.nullable ? ' · Nullable' : ' · Required'}</small></span>
                <Input aria-label={'Argument ' + parameter.key} disabled={pending || values[parameter.key] === null} value={values[parameter.key] ?? ''} onChange={event => setValues(previous => ({...previous, [parameter.key]: event.target.value}))} /></label>
              {parameter.nullable && <label className="ontology-tool-null"><input type="checkbox" disabled={pending} checked={values[parameter.key] === null} onChange={event => setValues(previous => ({...previous, [parameter.key]: event.target.checked ? null : ''}))} />Set to null</label>}
            </div>)}</div>
            {error && <p role="alert" className="ontology-tool-validation">{error}</p>}
            {receipt && <div className="ontology-tool-receipt" role="status"><CheckCircleOutlined /><div><strong>Function completed</strong><p>{receipt.name} · {receipt.stepCount} operations committed</p></div></div>}
          </div>
          <footer className="ontology-tool-footer"><p>Running this function updates the target object.</p><Button type="primary" icon={<ArrowRightOutlined />} disabled={!record?.id || pending || !!requestError} loading={pending} onClick={() => void run()}>Run function</Button></footer>
        </> : catalog && <div className="ontology-tool-empty"><CodeOutlined /><h4>Choose a published function</h4><p>Functions encapsulate a published set of property updates. Select a function to inspect its inputs and run it on an object.</p></div>}
      </div>
    </div>
  </section>;
}
