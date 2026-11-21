import { useEffect, useRef, useState } from 'react';
import { Button, message } from '@/components/ui';
import { graphRequest, type LinkType, type Page } from '../../services/ontology_graph';
import { applyTransaction, listOntologyRecords, listPublishedOntologyTypes, listTransactions, ontologyError, undoTransaction,
  type OntologyRecordPage, type PublishedOntologyType, type TransactionReceipt, type TransactionStep } from '../../services/ontology';
import { parsePropertyValue } from './ontologyPropertyValue';
import { newOntologyRequestId } from '../../services/ontology';
import OntologyWriteback from './OntologyWriteback';

type Kind = TransactionStep['kind'];
type Entry = {label: string; step: TransactionStep};
const emptyRecords: OntologyRecordPage = {items: [], hasMore: false, actions: []};
const description = (error: unknown) => ontologyError(error, 'The transaction request failed.');

function RecordPicker({type, label, value, aliases = [], change, version}: {
  type: string; label: string; value: string; aliases?: string[]; version: number;
  change: (id: string, revision?: number, title?: string) => void;
}) {
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState(emptyRecords);
  const [loading, setLoading] = useState(true);
  const [retry, setRetry] = useState(0);
  const [error, setError] = useState('');
  useEffect(() => {
    let active = true;
    setLoading(true); setPage(emptyRecords); setError('');
    listOntologyRecords(type, 50, offset).then(v => {if (active) setPage(v);}, e => {if (active) setError(description(e));})
      .finally(() => {if (active) setLoading(false);});
    return () => {active = false;};
  }, [type, offset, version, retry]);
  const records = page.items.filter(r => r.id);
  const pageTo = (next: number) => {change(''); setLoading(true); setOffset(next);};
  return <div><label>{label}<select aria-label={label} value={value} disabled={!aliases.length && (loading || !!error || !records.length)} onChange={e => {
    const id = e.target.value; const record = page.items.find(r => r.id === id);
    change(id, record?.revision ?? undefined, record ? String(record.title ?? record.id) : id);
  }}><option value="">{aliases.length || records.length ? 'Select record' : loading ? 'Loading records…' : error ? 'Records unavailable' : 'No visible records'}</option>{aliases.map(ref => <option key={ref}>{ref}</option>)}
    {records.map(r => <option key={r.id} value={r.id!}>{String(r.title ?? r.id)} · r{r.revision}</option>)}</select></label>
    {(offset > 0 || page.hasMore) && <div><Button disabled={!offset || loading} onClick={() => pageTo(offset-50)}>Previous records</Button>
      <Button disabled={!page.hasMore || loading || offset + 50 > 100_000} onClick={() => pageTo(offset+50)}>Next records</Button></div>}
    {error && <p role="alert">{error} <Button size="small" onClick={() => setRetry(value => value + 1)}>Retry records</Button></p>}</div>;
}

export default function OntologyTransactions({objectType, onCommitted}: {objectType: PublishedOntologyType; onCommitted: () => void}) {
  const project = objectType.definition.projectId;
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
  const locked = useRef(false);
  const retryIds = useRef(new Map<string, string>());
  const nextAlias = useRef(1);
  const selectedType = types.find(t => t.id === typeId);
  const action = actions?.find(a => a.kind === kind);
  const link = links.find(l => l.id === linkId);
  const edge = kind === 'link' || kind === 'unlink';
  useEffect(() => {
    let active = true;
    setTypesLoading(true);
    listPublishedOntologyTypes(50, typeOffset, '', project).then(p => {if (active) {
      const writable = p.items.filter(t => t.definition.source.mode === 'none');
      setTypes(prev => Array.from(new Map([...prev, ...writable].map(item => [item.id,item])).values()));
      setMoreTypes(p.hasMore);
    }}, e => {if (active) {setMoreTypes(false);setError(description(e));}}).finally(() => {if (active) setTypesLoading(false);});
    return () => {active = false;};
  }, [project, typeOffset, version]);
  useEffect(() => {
    let active = true;
    setActions([]);
    listOntologyRecords(typeId, 1, 0).then(p => {if (active) setActions(p.actions);}, e => {if (active) setError(description(e));});
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
      }}, e => {if (active) {setMoreLinks(false);setError(description(e));}}).finally(() => {if (active) setLinksLoading(false);});
    return () => {active = false;};
  }, [edge, typeId, linkOffset, version]);
  useEffect(() => {
    let active = true;
    setHistoryLoading(true);
    listTransactions(project, offset).then(p => {if (active) {setHistory(p.items);setHasMore(p.hasMore);}}, e => {if (active) {setHistory([]);setHasMore(false);setError(description(e));}})
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
        entry = {label: kind + ' · ' + link.name + ' · ' + source + ' → ' + target,
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
          entry = {label: kind + ' · ' + record.title + ' · r' + record.revision,
            step:{kind, definitionId:typeId,recordId:record.id,expectedRevision:record.revision,values:fields}};
        }
      }
      setEntries(prev => [...prev,entry]); resetStep();
    } catch (e) {const detail = description(e);setError(detail);message.error({content:'Unable to add transaction step',description:detail,className:'explorer-toast'});}
  };
  const perform = async (undo?: TransactionReceipt) => {
    if (locked.current) return;
    locked.current = true; setPending(true);setError('');
    try {
      if (undo) {
        await undoTransaction(project,undo.id); message.success({content:'Transaction undone',description:undo.name,className:'explorer-toast'});
      } else {
        const steps = entries.map(entry => entry.step);
        const signature = JSON.stringify([name,steps]);
        const id = retryIds.current.get(signature) ?? newOntologyRequestId();
        retryIds.current.set(signature, id);
        const result = await applyTransaction(project,name,id,steps);
        message.success({content:'Transaction committed',description:result.name,className:'explorer-toast'});setEntries([]);setName('');retryIds.current.delete(signature);
      }
      resetStep();resetLinks();setOffset(0);setVersion(v => v+1);
      onCommitted();
    } catch (e) {const detail = description(e);setError(detail);message.error({content:undo ? 'Unable to undo transaction' : 'Unable to commit transaction',description:detail,className:'explorer-toast'});}
    finally {locked.current = false;setPending(false);}
  };
  return <section aria-label="Transaction workbench" className="ontology-transaction">
    <section className="flow-tool-section" aria-label="Transaction steps">
      <h3>Cross-object transaction</h3>
      <fieldset disabled={pending}><label>Transaction name<input maxLength={120} value={name} onChange={e => setName(e.target.value)} /></label>
        <div className="ontology-tx-fields"><label>Step operation<select aria-label="Step operation" value={kind} onChange={e => {if (e.target.value === kind) return;setKind(e.target.value as Kind);if (!edge) resetLinks();resetStep();}}>
          {(['create','modify','delete','link','unlink'] as const).map(k => <option key={k} value={k}>{k}</option>)}</select></label>
        <label>Step object type<select aria-label="Step object type" value={typeId} onChange={e => {if (e.target.value === typeId) return;setTypeId(e.target.value);setActions([]);resetLinks();resetStep();}}>
          {types.map(t => <option key={t.id} value={t.id}>{t.definition.name}</option>)}</select></label></div>
        {moreTypes && <Button disabled={typesLoading || typeOffset + 50 > 100_000} onClick={() => {setTypesLoading(true);setTypeOffset(typeOffset+50);}}>Load more object types</Button>}
        {edge ? <><label>Step relationship<select aria-label="Step relationship" value={linkId} onChange={e => {setLinkId(e.target.value);setSource('');setTarget('');}}>
          <option value="">{links.length ? 'Select relationship' : linksLoading ? 'Loading relationships…' : 'No visible relationships'}</option>{links.map(l => <option key={l.id} value={l.id}>{l.name}</option>)}</select></label>
          {moreLinks && <Button disabled={linksLoading || linkOffset + 50 > 100_000} onClick={() => {setLinksLoading(true);setLinkOffset(linkOffset+50);}}>Load more relationships</Button>}
          {link && <div className="ontology-tx-fields"><RecordPicker key={'source'+link.id} type={link.sourceDefinitionId} version={version}
            label="Source record" aliases={aliasesFor(link.sourceDefinitionId)} value={source} change={setSource} />
            <RecordPicker key={'target'+link.id} type={link.targetDefinitionId} version={version}
              label="Target record" aliases={aliasesFor(link.targetDefinitionId)} value={target} change={setTarget} /></div>}</>
          : <>{kind !== 'create' && <RecordPicker key={typeId} type={typeId} label="Step record" value={record.id} version={version}
            change={(id,revision,title) => setRecord({id,revision:revision || 0,title:title || ''})} />}
            <div className="ontology-tx-fields">{selectedType?.definition.properties.filter(p => action?.fields.includes(p.id)).map(p => <div key={p.id}>
              <label>{p.name}<input aria-label={'Step value '+p.id} disabled={values[p.id] === null}
                value={values[p.id] ?? ''} onChange={e => setValues({...values,[p.id]:e.target.value})} /></label>
              {!p.required && <label><input type="checkbox" checked={values[p.id] === null} onChange={e => setValues(prev => {
                const updated = {...prev}; if (e.target.checked) updated[p.id] = null; else delete updated[p.id];return updated;
              })} />Set {p.name} to null</label>}</div>)}</div></>}
        <Button disabled={entries.length >= 24 || (edge ? !link || !source || !target : !action || (kind !== 'create' && !record.id))} onClick={add}>Add step</Button>
        <ol>{entries.map((entry,index) => <li key={index}>{entry.label}{index === entries.length-1 && <Button size="small" onClick={() => setEntries(entries.slice(0,-1))}>Remove last step</Button>}</li>)}</ol>
        <Button type="primary" disabled={!entries.length || !name.trim()} onClick={() => perform()}>Commit transaction</Button>
      </fieldset>
      {error && <p role="alert">{error} <Button size="small" disabled={pending} onClick={() => {setError('');setVersion(v => v+1);}}>Refresh options</Button></p>}
    </section>
    <section className="flow-tool-section" aria-label="My transactions">
      <h4>My transactions</h4>
      {historyLoading && <p role="status">Loading transactions…</p>}
      {!historyLoading && !history.length && !error && <p>No transactions on this page.</p>}
      <ul>{history.map(item => <li key={item.id}>{item.name} · {item.stepCount} steps · {new Date(item.createdAt).toLocaleString()} {' '}
        {item.undoneAt ? <span>Undone</span> : <Button size="small" disabled={pending || historyLoading} onClick={() => perform(item)}>Undo transaction</Button>}
        <Button size="small" aria-pressed={deliveryId === item.id} onClick={() => selectDelivery(deliveryId === item.id ? '' : item.id)}>Writeback</Button>
        {deliveryId === item.id && <OntologyWriteback key={item.id} project={project} transaction={item.id}/>}</li>)}</ul>
      {(offset > 0 || hasMore) && <div><Button disabled={!offset || historyLoading || pending} onClick={() => {setHistoryLoading(true);setOffset(offset-25);}}>Previous transactions</Button>
        <Button disabled={!hasMore || historyLoading || pending || offset + 25 > 100_000} onClick={() => {setHistoryLoading(true);setOffset(offset+25);}}>Next transactions</Button></div>}
    </section>
  </section>;
}
