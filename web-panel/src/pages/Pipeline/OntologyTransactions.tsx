import { useEffect, useRef, useState } from 'react';
import { Button, message, NativeSelect } from '@/components/ui';
import { graphRequest, type LinkType, type Page } from '../../services/ontology_graph';
import { applyTransaction, listOntologyRecords, listPublishedOntologyTypes, listTransactions, ontologyError, undoTransaction,
  type OntologyRecordPage, type PublishedOntologyType, type TransactionReceipt, type TransactionStep } from '../../services/ontology';
import { parsePropertyValue } from './ontologyPropertyValue';
import { newOntologyRequestId } from '../../services/ontology';
import OntologyWriteback from './OntologyWriteback';
import useExplorerErrorToast from './useExplorerErrorToast';
import { BranchesOutlined, DeleteOutlined, HistoryOutlined, PlusOutlined } from '@/components/ui/icons';
import { getSessionState } from '../../services/session';
import './ontology-tool-workbench.css';
import './ontology-transaction-workbench.css';

type Kind = TransactionStep['kind'];
type Entry = {label: string; step: TransactionStep};
const emptyRecords: OntologyRecordPage = {items: [], hasMore: false, actions: []};
const operationLabels = {create: 'Create object', modify: 'Edit object', delete: 'Delete object', link: 'Add relationship', unlink: 'Remove relationship'};
const description = (error: unknown) => ontologyError(error, 'The transaction request failed.');

function RecordPicker({type, typeName, label, value, aliases = [], change, version}: {
  type: string; typeName?: string; label: string; value: string; aliases?: string[]; version: number;
  change: (id: string, revision?: number, title?: string) => void;
}) {
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState(emptyRecords);
  const [loading, setLoading] = useState(true);
  const [retry, setRetry] = useState(0);
  const [error, setError] = useState('');
  useExplorerErrorToast(error, 'Unable to load records', () => setRetry(value => value + 1));
  useEffect(() => {
    let active = true;
    setLoading(true); setPage(emptyRecords); setError('');
    listOntologyRecords(type, 50, offset).then(v => {if (active) setPage(v);}, e => {if (active) setError(description(e));})
      .finally(() => {if (active) setLoading(false);});
    return () => {active = false;};
  }, [type, offset, version, retry]);
  const records = page.items.filter(r => r.id);
  const pageTo = (next: number) => {change(''); setLoading(true); setOffset(next);};
  return <div><label><span className="ontology-tx-record-label"><span>{label}</span>{typeName && <small>{typeName}</small>}</span><NativeSelect aria-label={label} value={value} disabled={!aliases.length && (loading || !!error || !records.length)} onChange={e => {
    const id = e.target.value; const record = page.items.find(r => r.id === id);
    change(id, record?.revision ?? undefined, record ? String(record.title ?? record.id) : id);
  }}><option value="">{aliases.length || records.length ? 'Select record' : loading ? 'Loading records…' : error ? 'Records unavailable' : 'No visible records'}</option>{aliases.map(ref => <option key={ref} value={ref}>{ref} · New object</option>)}
    {records.map(r => <option key={r.id} value={r.id!}>{String(r.title ?? r.id)} · r{r.revision}</option>)}</NativeSelect></label>
    {(offset > 0 || page.hasMore) && <div><Button disabled={!offset || loading} onClick={() => pageTo(offset-50)}>Previous records</Button>
      <Button disabled={!page.hasMore || loading || offset + 50 > 100_000} onClick={() => pageTo(offset+50)}>Next records</Button></div>}
    </div>;
}

export default function OntologyTransactions({objectType, onCommitted}: {objectType: PublishedOntologyType; onCommitted: () => void}) {
  const project = objectType.definition.projectId;
  const [tab, setTab] = useState<'compose' | 'history'>('compose');
  const [name, setName] = useState('');
  const [entries, setEntries] = useState<Entry[]>([]);
  const [kind, setKind] = useState<Kind>('create');
  const [types, setTypes] = useState([objectType]);
  const [typeId, setTypeId] = useState(objectType.id);
  const [typeOffset, setTypeOffset] = useState(0);
  const [moreTypes, setMoreTypes] = useState(false);
  const [typesLoading, setTypesLoading] = useState(true);
  const [actions, setActions] = useState<OntologyRecordPage['actions']>([]);
  const [links, setLinks] = useState<LinkType[]>([]);
  const [moreLinks, setMoreLinks] = useState(false);
  const [linksLoading, setLinksLoading] = useState(false);
  const [linkOffset, setLinkOffset] = useState(0);
  const [linkId, setLinkId] = useState('');
  const [source, setSource] = useState('');
  const [target, setTarget] = useState('');
  const [record, setRecord] = useState({id: '', revision: 0, title: ''});
  const [values, setValues] = useState<Record<string, string | null>>({});
  const [history, setHistory] = useState<TransactionReceipt[]>([]);
  const [deliveryId, selectDelivery] = useState('');
  const [offset, setOffset] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [version, setVersion] = useState(0);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const [requestError, setRequestError] = useState('');
  useExplorerErrorToast(requestError, 'Unable to load transaction options', () => {
    if (!pending) { setRequestError(''); setVersion(value => value + 1); }
  });
  const locked = useRef(false), alive = useRef(true);
  const identity = useRef(getSessionState().identity).current;
  const current = () => {
    const session = getSessionState();
    return alive.current && session.authenticated && !session.locked && session.identity === identity;
  };
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const retryIds = useRef(new Map<string, string>());
  const nextAlias = useRef(1);
  const selectedType = types.find(t => t.id === typeId);
  const action = actions?.find(a => a.kind === kind);
  const link = links.find(l => l.id === linkId);
  const edge = kind === 'link' || kind === 'unlink';
  useEffect(() => setRequestError(''), [version]);
  useEffect(() => {
    let active = true;
    setTypesLoading(true);
    listPublishedOntologyTypes(50, typeOffset, '', project).then(p => {if (active) {
      const writable = p.items.filter(t => t.definition.source.mode === 'none');
      setTypes(prev => Array.from(new Map([...prev, ...writable].map(item => [item.id,item])).values()));
      setMoreTypes(p.hasMore);
    }}, e => {if (active) {setMoreTypes(false);setRequestError(description(e));}}).finally(() => {if (active) setTypesLoading(false);});
    return () => {active = false;};
  }, [project, typeOffset, version]);
  useEffect(() => {
    let active = true;
    setActions([]);
    listOntologyRecords(typeId, 1, 0).then(p => {if (active) setActions(p.actions);}, e => {if (active) setRequestError(description(e));});
    return () => {active = false;};
  }, [typeId, version]);
  useEffect(() => {
    if (!edge) {setLinksLoading(false); return;}
    let active = true;
    setLinksLoading(true);
    graphRequest<Page<LinkType>>('get', '/object-types/' + typeId + '/link-types',
      {params:{limit:50,offset:linkOffset}}).then(page => {if (active) {
        setLinks(prev => Array.from(new Map([...(linkOffset ? prev : []), ...page.items].map(item => [item.id, item])).values()));
        setMoreLinks(page.hasMore);
      }}, e => {if (active) {setMoreLinks(false);setRequestError(description(e));}}).finally(() => {if (active) setLinksLoading(false);});
    return () => {active = false;};
  }, [edge, typeId, linkOffset, version]);
  useEffect(() => {
    let active = true;
    setHistoryLoading(true);
    listTransactions(project, offset).then(p => {if (active) {setHistory(p.items);setHasMore(p.hasMore);}}, e => {if (active) {setHistory([]);setHasMore(false);setRequestError(description(e));}})
      .finally(() => {if (active) setHistoryLoading(false);});
    return () => {active = false;};
  }, [project, offset, version]);
  const resetStep = () => {setSource('');setTarget('');setRecord({id:'',revision:0,title:''});setValues({});setLinkId('');};
  const resetLinks = () => {setLinks([]);setLinkOffset(0);setMoreLinks(false);};
  const aliasesFor = (id: string) => entries.flatMap(({step}) => step.kind === 'create' && step.definitionId === id ? [step.alias] : []);
  const add = () => {
    setError('');
    try {
      if (entries.length >= 24) throw new Error('A transaction supports up to 24 steps.');
      let entry: Entry;
      if (kind === 'link' || kind === 'unlink') {
        if (!link || !source || !target) throw new Error('Select a relationship and both records.');
        entry = {label: operationLabels[kind] + ' · ' + link.name + ' · ' + source + ' → ' + target,
          step: {kind,linkTypeId:link.id,source,target}};
      } else {
        if (!action || !selectedType) throw new Error('This action is unavailable for this object type.');
        const fields: Record<string, unknown> = {};
        for (const prop of selectedType.definition.properties.filter(p => action.fields.includes(p.id))) {
          if (Object.prototype.hasOwnProperty.call(values,prop.id)) fields[prop.id] = values[prop.id] === null ? null : parsePropertyValue(values[prop.id]!,prop.type);
          else if (kind === 'create' && prop.required) throw new Error(prop.name + ' is required.');
        }
        if (kind === 'create') {
          const alias = '@item' + nextAlias.current++;
          entry = {label:'Create · ' + selectedType.definition.name + ' · ' + alias, step:{kind,definitionId:typeId,alias,values:fields}};
        } else {
          if (!record.id || !record.revision) throw new Error('Select a current record.');
          if (kind === 'modify' && !Object.keys(fields).length) throw new Error('Enter at least one change.');
          entry = {label: operationLabels[kind] + ' · ' + record.title + ' · r' + record.revision,
            step:{kind, definitionId:typeId,recordId:record.id,expectedRevision:record.revision,values:fields}};
        }
      }
      setEntries(prev => [...prev,entry]); resetStep();
    } catch (e) {const detail = description(e);setError(detail);message.error({content:'Unable to add transaction step',description:detail,className:'explorer-toast'});}
  };
  const perform = async (undo?: TransactionReceipt) => {
    if (locked.current || !current()) return;
    locked.current = true; setPending(true);setError('');
    try {
      if (undo) {
        await undoTransaction(project,undo.id); if (!current()) return; message.success({content:'Transaction undone',description:undo.name,className:'explorer-toast'});
      } else {
        const steps = entries.map(entry => entry.step);
        const signature = JSON.stringify([name,steps]);
        const id = retryIds.current.get(signature) ?? newOntologyRequestId();
        retryIds.current.set(signature, id);
        const result = await applyTransaction(project,name,id,steps);
        if (!current()) return;
        message.success({content:'Transaction committed',description:result.name,className:'explorer-toast'});setEntries([]);setName('');retryIds.current.delete(signature);setTab('history');
      }
      resetStep();resetLinks();setOffset(0);setVersion(v => v+1);
      onCommitted();
    } catch (e) {if (!current()) return;const detail = description(e);message.error({content:undo ? 'Unable to undo transaction' : 'Unable to commit transaction',description:detail,className:'explorer-toast'});}
    finally {locked.current = false;if (current()) setPending(false);}
  };
  return <section aria-label="Transaction workbench" className="ontology-tool-workbench ontology-transaction-workbench">
    <header className="ontology-tool-header"><div><h3><BranchesOutlined />Transactions</h3><p>Stage changes across objects and relationships, then commit them together. No changes are applied until you commit.</p></div></header>
    <nav className="ontology-tool-tabs" aria-label="Transaction views"><button type="button" aria-pressed={tab === 'compose'} disabled={pending} onClick={() => setTab('compose')}>New transaction{entries.length ? ' · ' + entries.length : ''}</button><button type="button" aria-pressed={tab === 'history'} disabled={pending} onClick={() => setTab('history')}>History and writeback</button></nav>
    {tab === 'compose' ? <div className="ontology-transaction-layout">
      <section className="flow-tool-section ontology-transaction-editor" aria-label="Transaction steps"><header><h4>Add an operation</h4><p>Choose an object type and supply the values for this step.</p></header><fieldset disabled={pending}>
        <div className="ontology-tx-fields"><label>Step operation<NativeSelect aria-label="Step operation" value={kind} onChange={e => {if (e.target.value === kind) return;setKind(e.target.value as Kind);if (!edge) resetLinks();resetStep();}}>
          {(['create','modify','delete','link','unlink'] as const).map(k => <option key={k} value={k}>{operationLabels[k]}</option>)}</NativeSelect></label>
        <label>Step object type<NativeSelect aria-label="Step object type" value={typeId} onChange={e => {if (e.target.value === typeId) return;setTypeId(e.target.value);setActions([]);resetLinks();resetStep();}}>
          {types.map(t => <option key={t.id} value={t.id}>{t.definition.name}</option>)}</NativeSelect></label></div>
        {moreTypes && <Button disabled={typesLoading || typeOffset + 50 > 100_000} onClick={() => {setTypesLoading(true);setTypeOffset(typeOffset+50);}}>Load more object types</Button>}
        {edge ? <><label>Step relationship<NativeSelect aria-label="Step relationship" value={linkId} onChange={e => {setLinkId(e.target.value);setSource('');setTarget('');}}>
          <option value="">{links.length ? 'Select relationship' : linksLoading ? 'Loading relationships…' : 'No visible relationships'}</option>{links.map(l => <option key={l.id} value={l.id}>{l.name}</option>)}</NativeSelect></label>
          {moreLinks && <Button disabled={linksLoading || linkOffset + 50 > 100_000} onClick={() => {setLinksLoading(true);setLinkOffset(linkOffset+50);}}>Load more relationships</Button>}
          {link && <div className="ontology-tx-fields"><RecordPicker key={'source'+link.id} type={link.sourceDefinitionId} typeName={types.find(type => type.id === link.sourceDefinitionId)?.definition.name || link.sourceDefinitionId} version={version}
            label="Source record" aliases={aliasesFor(link.sourceDefinitionId)} value={source} change={setSource} />
            <RecordPicker key={'target'+link.id} type={link.targetDefinitionId} typeName={types.find(type => type.id === link.targetDefinitionId)?.definition.name || link.targetDefinitionId} version={version}
              label="Target record" aliases={aliasesFor(link.targetDefinitionId)} value={target} change={setTarget} /></div>}</>
          : <>{kind !== 'create' && <RecordPicker key={typeId} type={typeId} label="Step record" value={record.id} version={version}
            change={(id,revision,title) => setRecord({id,revision:revision || 0,title:title || ''})} />}
            <div className="ontology-tx-fields">{selectedType?.definition.properties.filter(p => action?.fields.includes(p.id)).map(p => <div key={p.id}>
              <label>{p.name}<input aria-label={'Step value '+p.id} disabled={values[p.id] === null}
                value={values[p.id] ?? ''} onChange={e => setValues({...values,[p.id]:e.target.value})} /></label>
              {!p.required && <label><input type="checkbox" checked={values[p.id] === null} onChange={e => setValues(prev => {
                const updated = {...prev}; if (e.target.checked) updated[p.id] = null; else delete updated[p.id];return updated;
              })} />Set {p.name} to null</label>}</div>)}</div></>}
        <Button disabled={entries.length >= 24 || (edge ? !link || !source || !target : !action || (kind !== 'create' && !record.id))} onClick={add} icon={<PlusOutlined />}>Add step</Button>
      </fieldset></section>
      <aside className="ontology-transaction-queue" aria-label="Staged changes">
        <header><h4>Staged changes</h4><span>{entries.length} / 24</span></header>
        <label className="ontology-transaction-name">Transaction name<input aria-label="Transaction name" maxLength={120} disabled={pending} placeholder="Describe these changes" value={name} onChange={event => setName(event.target.value)} /></label>
        <div className="ontology-transaction-steps">{entries.length ? <ol>{entries.map((entry,index) => <li key={index}><span className="ontology-transaction-step-number">{index + 1}</span><div><strong>{operationLabels[entry.step.kind]}</strong><p>{entry.label}</p></div>{index === entries.length - 1 && <Button type="text" size="small" icon={<DeleteOutlined />} aria-label="Remove last step" disabled={pending} onClick={() => setEntries(entries.slice(0,-1))} />}</li>)}</ol> : <div className="ontology-tool-empty"><PlusOutlined /><h4>No staged changes</h4><p>Add an operation from the editor. Later steps can reference objects created earlier in the same transaction.</p></div>}</div>
        <footer className="ontology-tool-footer"><p>All steps succeed or none are applied.</p><Button type="primary" loading={pending} disabled={pending || !entries.length || !name.trim()} onClick={() => perform()}>Commit transaction</Button></footer>
      </aside>
    </div> : <section className="flow-tool-section ontology-transaction-history" aria-label="My transactions"><header><h4><HistoryOutlined />My transactions</h4><p>Inspect committed operations, undo eligible transactions and review writeback delivery.</p></header>
      {historyLoading && <p role="status">Loading transactions…</p>}
      {!historyLoading && !history.length && !error && !requestError && <p>No transactions on this page.</p>}
      <ul>{history.map(item => <li key={item.id} className="ontology-transaction-history-item">
        <div className="ontology-transaction-history-summary"><strong>{item.name}</strong>
          <div className="ontology-transaction-history-meta"><span>{item.stepCount} {item.stepCount === 1 ? 'step' : 'steps'}</span><time dateTime={item.createdAt}>{new Date(item.createdAt).toLocaleString()}</time></div>
        </div>
        <div className="ontology-transaction-history-actions"><span className={`ontology-transaction-status${item.undoneAt ? ' is-undone' : ''}`}>{item.undoneAt ? 'Undone' : 'Committed'}</span>
          {!item.undoneAt && <Button size="small" disabled={pending || historyLoading} onClick={() => perform(item)}>Undo transaction</Button>}
          <Button size="small" aria-pressed={deliveryId === item.id} aria-expanded={deliveryId === item.id} onClick={() => selectDelivery(deliveryId === item.id ? '' : item.id)}>Writeback</Button>
        </div>
        {item.undoneAt && <p className="ontology-transaction-undone-at">Undone <time dateTime={item.undoneAt}>{new Date(item.undoneAt).toLocaleString()}</time></p>}
        {deliveryId === item.id && <div className="ontology-transaction-history-writeback"><OntologyWriteback key={item.id} project={project} transaction={item.id}/></div>}
      </li>)}</ul>
      {(offset > 0 || hasMore) && <div><Button disabled={!offset || historyLoading || pending} onClick={() => {setHistoryLoading(true);setOffset(offset-25);}}>Previous transactions</Button>
        <Button disabled={!hasMore || historyLoading || pending || offset + 25 > 100_000} onClick={() => {setHistoryLoading(true);setOffset(offset+25);}}>Next transactions</Button></div>}
    </section>}
  </section>;
}
