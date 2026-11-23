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
type CatalogLoad = { scope: string; version: number; catalog?: Catalog; error?: string };

export default function OntologyFunctions({id, record, onCommitted}: {
  id: string; record?: OntologyRecord; onCommitted: (name: string) => void;
}) {
  const [load, setLoad] = useState<CatalogLoad>();
  const [selected, setSelected] = useState(''), [search, setSearch] = useState('');
  const [values, setValues] = useState<Record<string, string | null>>({});
  const [pending, setPending] = useState(false), [error, setError] = useState('');
  const [refresh, setRefresh] = useState(0);
  const [completion, setCompletion] = useState<{ scope: string; receipt: TransactionReceipt }>();
  const invocation = useRef<{ scope: string }>(), alive = useRef(true);
  const retryIds = useRef(new Map<string, string>());
  const { identity, authenticated, locked } = getSessionState();
  const path = '/object-types/' + encodeURIComponent(id) + '/functions';
  const scope = JSON.stringify([identity, path]);
  const liveScope = useRef(scope);
  liveScope.current = scope;
  const callScope = JSON.stringify([scope, record?.id]);
  const liveCallScope = useRef(callScope);
  liveCallScope.current = callScope;
  const receipt = completion?.scope === callScope ? completion.receipt : undefined;
  const allowed = authenticated && !locked;
  const current = () => {
    const session = getSessionState();
    return alive.current && liveScope.current === scope && session.authenticated && !session.locked && session.identity === identity;
  };
  const scopedLoad = allowed && load?.scope === scope ? load : undefined;
  const catalog = scopedLoad?.catalog;
  const loading = allowed && scopedLoad?.version !== refresh;
  const requestError = !loading ? scopedLoad?.error : undefined;
  const reload = () => { if (!invocation.current && !loading && current()) setRefresh(value => value + 1); };
  useExplorerErrorToast(requestError, 'Unable to load functions', reload);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    if (!allowed) {
      setLoad(undefined); setError(''); setValues({}); setCompletion(undefined);
      return;
    }
    const controller = new AbortController();
    graphRequest<Catalog>('get', path, {signal: controller.signal}).then(result => {
      if (!controller.signal.aborted && current()) {
        setLoad({ scope, version: refresh, catalog: result });
        setSelected(previous => catalog && result.items.some(item => item.key === previous) ? previous : result.items[0]?.key ?? '');
        if (catalog?.revision !== result.revision || !result.items.some(item => item.key === selected)) { setValues({}); setError(''); setCompletion(undefined); }
      }
    }, failure => {
      if (!controller.signal.aborted && current()) {
        setLoad({ scope, version: refresh, error: ontologyError(failure, 'Unable to load functions.') });
        setValues({}); setError(''); setCompletion(undefined);
      }
    });
    return () => controller.abort();
    // The catalog read here is a snapshot from the start of this refresh.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, refresh, scope, allowed]);
  const fn = catalog?.items.find(item => item.key === selected);
  const visible = catalog?.items.filter(item => (item.name + ' ' + item.key).toLowerCase().includes(search.toLowerCase())) || [];
  const run = async () => {
    if (!record?.id || !record.revision || !catalog || !fn || loading || requestError || invocation.current || !current()) return;
    const operation = { scope: callScope };
    invocation.current = operation; setPending(true); setError(''); setCompletion(undefined);
    const currentCall = () => current() && liveCallScope.current === callScope && invocation.current === operation;
    let submitted = false;
    try {
      const args = Object.fromEntries(fn.parameters.map(parameter => {
        if (!(parameter.key in values)) throw new Error('Enter a value for ' + parameter.key + '.');
        const value = values[parameter.key];
        return [parameter.key, value === null ? null : parsePropertyValue(value, parameter.type)];
      }));
      const data = {modelRevision: catalog.revision, recordId: record.id, expectedRevision: record.revision, arguments: args};
      const signature = JSON.stringify([callScope, fn.key, data]);
      const requestId = retryIds.current.get(signature) ?? newOntologyRequestId();
      retryIds.current.set(signature, requestId);
      submitted = true;
      const result = await graphRequest<TransactionReceipt>('post', path + '/' + encodeURIComponent(fn.key) + '/invoke', {data: {...data, requestId}});
      if (!currentCall()) return;
      retryIds.current.delete(signature); setCompletion({ scope: callScope, receipt: result }); onCommitted(result.name);
    } catch (failure) {
      if (!currentCall()) return;
      const detail = ontologyError(failure, 'Unable to run function.');
      if (!submitted) setError(detail);
      else message.error({content: 'Unable to run function', description: detail, className: 'explorer-toast'});
    } finally {
      if (invocation.current === operation) {
        invocation.current = undefined;
        if (alive.current) setPending(false);
      }
    }
  };
  return <section className="ontology-tool-workbench" aria-label="Business function runner">
    <header className="ontology-tool-header"><div><h3><CodeOutlined />Functions</h3><p>Run published business logic on one selected object. Changes are applied together and recorded in its history.</p></div>
      <Button size="small" icon={<ReloadOutlined />} disabled={pending || !allowed} loading={loading} onClick={reload}>Refresh</Button>
    </header>
    <div className="ontology-tool-layout" aria-busy={loading}>
      <aside className="ontology-tool-catalog" aria-label="Published functions">
        <div className="ontology-tool-search"><Input aria-label="Find functions" placeholder="Find a function" prefix={<SearchOutlined />} value={search} onChange={event => setSearch(event.target.value)} /></div>
        <div className="ontology-tool-catalog-list">
          {!catalog && loading && <p role="status">Loading functions…</p>}
          {catalog && !catalog.items.length && <p>No published functions are available to this account.</p>}
          {catalog && !!catalog.items.length && !visible.length && <p>No functions match this search.</p>}
          {visible.map(item => <button type="button" key={item.key} aria-pressed={selected === item.key} disabled={pending || loading} onClick={() => {setSelected(item.key); setValues({}); setError(''); setCompletion(undefined);}}>
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
                <Input aria-label={'Argument ' + parameter.key} disabled={pending || loading || values[parameter.key] === null} value={values[parameter.key] ?? ''} onChange={event => setValues(previous => ({...previous, [parameter.key]: event.target.value}))} /></label>
              {parameter.nullable && <label className="ontology-tool-null"><input type="checkbox" disabled={pending || loading} checked={values[parameter.key] === null} onChange={event => setValues(previous => ({...previous, [parameter.key]: event.target.checked ? null : ''}))} />Set to null</label>}
            </div>)}</div>
            {error && <p role="alert" className="ontology-tool-validation">{error}</p>}
            {receipt && <div className="ontology-tool-receipt" role="status"><CheckCircleOutlined /><div><strong>Function completed</strong><p>{receipt.name} · {receipt.stepCount} operations committed</p></div></div>}
          </div>
          <footer className="ontology-tool-footer"><p>Running this function updates the target object.</p><Button type="primary" icon={<ArrowRightOutlined />} disabled={!record?.id || pending || loading || !!requestError} loading={pending} onClick={() => void run()}>Run function</Button></footer>
        </> : catalog && <div className="ontology-tool-empty"><CodeOutlined /><h4>Choose a published function</h4><p>Functions encapsulate a published set of property updates. Select a function to inspect its inputs and run it on an object.</p></div>}
      </div>
    </div>
  </section>;
}
