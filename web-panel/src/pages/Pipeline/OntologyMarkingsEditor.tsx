import { useEffect, useState } from 'react';
import { Button, Input, Select } from '@/components/ui';
import { ontologyError, type OntologyPrincipal, type Principal } from '../../services/ontology';
import { graphRequest, type Page } from '../../services/ontology_graph';

type Marking = { id: string; name: string; readers?: Principal[]; revision?: number };
type Result = Page<Marking> & { canManage: boolean };
type Change = { revision: number; actorId: string; createdAt: string; readers: Principal[] };
const key = (p: Principal) => p.kind + ':' + p.id;

export default function OntologyMarkingsEditor({ project, value, onChange, principals }: {
  project: string; value: string[]; onChange: (ids: string[]) => void; principals: OntologyPrincipal[];
}) {
  const [result, setResult] = useState<Result>({ items: [], hasMore: false, canManage: false });
  const [version, refresh] = useState(0);
  const [editing, setEditing] = useState<Marking>();
  const [name, setName] = useState('');
  const [readers, setReaders] = useState<Principal[]>([]);
  const [history, setHistory] = useState<Change[]>([]);
  const [moreHistory, setMoreHistory] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const path = '/projects/' + project + '/markings';
  useEffect(() => {
    if (!project) return;
    const cancel = new AbortController();
    graphRequest<Result>('get', path, { signal: cancel.signal }).then(res => {
      if (!cancel.signal.aborted) setResult(res);
    }, e => {if (!cancel.signal.aborted) setError(ontologyError(e, 'Unable to load markings.'));});
    return () => cancel.abort();
  }, [project, path, version]);
  if (!project) return null;
  const choices = editing && !result.items.some(item => item.id === editing.id) ? [...result.items, editing] : result.items;
  const options = new Map(principals.map(p => [key(p), p]));
  for (const reader of readers) if (!options.has(key(reader))) options.set(key(reader), { ...reader, name: 'Name unavailable' });
  const selectMark = (mark?: Marking) => {
    setEditing(mark); setReaders(mark?.readers ?? []); setName(mark?.name ?? ''); setError(''); setHistory([]); setMoreHistory(false);
  };
  const run = async (action: () => Promise<void>) => {
    setBusy(true); setError('');
    try { await action(); } catch (e) { setError(ontologyError(e, 'Marking request failed.')); } finally { setBusy(false); }
  };
  const audit = (offset: number) => run(async () => {
    const page = await graphRequest<Page<Change>>('get', path + '/' + editing!.id + '/history', { params: { offset } });
    setHistory(old => offset ? [...old, ...page.items] : page.items); setMoreHistory(page.hasMore);
  });
  return <section aria-label="Security markings" className="flow-semantic-config">
    <h4>Security markings</h4>
    <label className="flow-create-field"><span>Required project markings</span>
      <Select mode="multiple" aria-label="Required project markings" value={value} placeholder={choices.length ? "Select markings" : "No project markings available"}
        options={choices.map(item => ({value:item.id,label:item.name}))}
        onChange={(ids: string[]) => onChange(ids)} /></label>
    <p className="flow-create-help">Readers need every selected clearance to access this type and its records.</p>
    {result.hasMore && <Button disabled={busy} onClick={() => run(async () => {
      const page = await graphRequest<Result>('get', path, { params: { offset: result.items.length } });
      setResult({ ...page, items: [...result.items, ...page.items] });
    })}>Load more markings</Button>}
    {result.canManage && <details><summary>Manage project clearances</summary>
      <fieldset disabled={busy} className="flow-semantic-config">
        <label>Project marking<select aria-label="Project marking" value={editing?.id ?? ''} onChange={e => selectMark(choices.find(item => item.id === e.target.value))}>
          <option value="">New marking</option>{choices.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
        {!editing && <label>Marking name<Input value={name} onChange={e => setName(e.target.value)} /></label>}
        <label>Clearance holders<Select aria-label="Clearance holders" mode="multiple" value={readers.map(key)}
          options={[...options].map(([v,p]) => ({value:v,label:p.name + ' (' + p.kind + ')'}))}
          onChange={(ids: string[]) => setReaders(ids.map(id => {const p=options.get(id)!;return {kind:p.kind,id:p.id};}))} /></label>
        <p className="flow-create-help">Grant changes take effect immediately across this project for types using this marking.</p>
        <Button disabled={!editing && !name.trim()} onClick={() => run(async () => {
          const saved = editing
            ? await graphRequest<Marking>('put', path + '/' + editing.id, {data:{revision:editing.revision,readers}})
            : await graphRequest<Marking>('post', path, {data:{name:name.trim(),readers}});
          selectMark(saved); refresh(v => v + 1);
        })}>{editing ? 'Save clearances' : 'Create marking'}</Button>
        {editing && <Button onClick={() => audit(0)}>View clearance history</Button>}
        {!!history.length && <ol aria-label="Clearance history">{history.map(change => <li key={change.revision}>
          Revision {change.revision} · {new Date(change.createdAt).toLocaleString()} · {principals.find(p => p.kind === 'user' && p.id === change.actorId)?.name ?? 'Name unavailable'} · {change.readers.map(p => options.get(key(p))?.name ?? 'Name unavailable').join(', ') || 'No holders'}
        </li>)}</ol>}{moreHistory && <Button onClick={() => audit(history.length)}>Load older clearance changes</Button>}
      </fieldset></details>}
    {error && <p role="alert">{error} <Button onClick={() => {selectMark(); refresh(v => v + 1);}}>Reload markings</Button></p>}
  </section>;
}
