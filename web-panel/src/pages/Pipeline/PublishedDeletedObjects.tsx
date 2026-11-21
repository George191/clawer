import { useEffect, useRef, useState } from 'react';
import { Button, Input, message } from '@/components/ui';
import { DeleteOutlined, EditOutlined, HistoryOutlined, PlusOutlined, ReloadOutlined, SearchOutlined, UndoOutlined } from '@/components/ui/icons';
import { getSessionState } from '../../services/session';
import {
  listOntologyRecords, listOntologyRecordHistory, ontologyError as describe, undoOntologyEdit,
  type OntologyRecordHistoryPage, type OntologyRecordPage, type PublishedOntologyType,
} from '../../services/ontology';
import useExplorerErrorToast from './useExplorerErrorToast';
import './published-tools.css';
import './explorer-history.css';
import './ontology-tool-workbench.css';

const pageSize = 25;
const maxOffset = 100_000;
const title = (value: unknown) => value == null ? 'Untitled object' : typeof value === 'object' ? JSON.stringify(value) : String(value);

function historyValue(delta: { before?: unknown; after?: unknown }, side: 'before' | 'after') {
  if (!Object.prototype.hasOwnProperty.call(delta, side)) return <span className="explorer-history-unset">Not set</span>;
  const value = delta[side];
  return value === null ? <span className="explorer-history-unset">null</span>
    : typeof value === 'object' ? <code>{JSON.stringify(value, null, 2)}</code> : <span>{String(value)}</span>;
}

export function RecordHistory({ objectType, recordId, onChanged }: { objectType: string; recordId: string; onChanged: () => void }) {
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<OntologyRecordHistoryPage>();
  const [error, setError] = useState('');
  const [version, setVersion] = useState(0);
  const [undoing, setUndoing] = useState(false);
  const active = useRef(true), undoLock = useRef(false);
  const identity = useRef(getSessionState().identity).current;
  const current = () => {
    const session = getSessionState();
    return active.current && session.authenticated && !session.locked && identity === session.identity;
  };
  useExplorerErrorToast(error, 'Unable to load action history', () => setVersion(value => value + 1));
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  useEffect(() => {
    const controller = new AbortController();
    setPage(undefined); setError('');
    listOntologyRecordHistory(objectType, recordId, pageSize, offset, controller.signal).then(
      result => { if (!controller.signal.aborted && current()) setPage(result); },
      failure => { if (!controller.signal.aborted && current()) setError(describe(failure, 'Unable to load this record’s action history.')); },
    );
    return () => controller.abort();
  }, [objectType, recordId, offset, version, identity]);
  return <section className="explorer-history" aria-label="Record action history">
    <header><HistoryOutlined /><h4>Edit history</h4></header>
    {error ? null
      : !page ? <p role="status">Loading action history…</p> : <>
        {page.items.length ? <ol className="explorer-history-events">{page.items.map(event => <li key={event.revision}>
          <span className="explorer-history-marker" aria-hidden="true">{event.kind === 'modify' ? <EditOutlined /> : event.kind === 'delete' ? <DeleteOutlined /> : <PlusOutlined />}</span>
          <details open><summary><span><strong>{{ create: 'Created', modify: 'Modified', delete: 'Deleted' }[event.kind]}</strong><small>Revision {event.revision}</small></span>
            <time dateTime={event.createdAt}>{new Date(event.createdAt).toLocaleString()}</time></summary>
            {event.canUndo && <footer><Button type={event.kind === 'delete' ? 'primary' : 'default'} icon={<UndoOutlined />} disabled={undoing} size="small" onClick={async () => {
              if (!current() || undoLock.current) return;
              undoLock.current = true; setUndoing(true);
              try { await undoOntologyEdit(objectType, recordId, event.revision); if (current()) { message.success('Edit undone.'); setVersion(value => value + 1); onChanged(); } }
              catch (failure) { if (current()) message.error({ content: 'Unable to undo this edit', description: describe(failure, 'Unable to undo this edit.'), className: 'explorer-toast' }); }
              finally { undoLock.current = false; if (current()) setUndoing(false); }
            }}>{event.kind === 'delete' ? 'Restore object' : 'Undo edit'}</Button></footer>}
            {event.changes && Object.keys(event.changes).length ? <div className="explorer-history-changes"><table><thead><tr><th>Property</th><th>Previous value</th><th>Updated value</th></tr></thead>
              <tbody>{Object.entries(event.changes).map(([field, delta]) => <tr key={field}><th scope="row">{field}</th><td>{historyValue(delta, 'before')}</td><td>{historyValue(delta, 'after')}</td></tr>)}</tbody></table></div>
              : <p>{event.changes ? 'No readable property changes.' : 'Property changes are restricted by the current access policy.'}</p>}

          </details>
        </li>)}</ol> : <p>No action history on this page.</p>}
        {(offset > 0 || page.hasMore) && <footer className="flow-published-results-pagination" aria-label="Action history pagination"><div>
          <Button size="small" disabled={!offset || undoing} onClick={() => setOffset(Math.max(0, offset - pageSize))}>Previous page</Button>
          <Button size="small" disabled={!page.hasMore || offset + pageSize > maxOffset || undoing} onClick={() => setOffset(offset + pageSize)}>Next page</Button>
        </div></footer>}
      </>}
  </section>;
}

export default function PublishedDeletedObjects({ objectType, onChanged }: { objectType: PublishedOntologyType; onChanged: () => void }) {
  const [offset, setOffset] = useState(0);
  const [version, setVersion] = useState(0);
  const [page, setPage] = useState<OntologyRecordPage>();
  const [error, setError] = useState('');
  const [selectedId, setSelectedId] = useState(''), [search, setSearch] = useState('');
  const [tab, setTab] = useState<'history' | 'properties'>('history');
  useExplorerErrorToast(error, 'Unable to load deleted objects', () => setVersion(value => value + 1));
  useEffect(() => {
    const controller = new AbortController();
    const identity = getSessionState().identity;
    const current = () => !controller.signal.aborted && identity === getSessionState().identity;
    setPage(undefined); setError('');
    listOntologyRecords(objectType.id, pageSize, offset, true, controller.signal).then(
      result => { if (current()) { setPage(result); setSelectedId(previous => result.items.some(record => record.id === previous) ? previous : result.items[0]?.id ?? ''); } },
      failure => { if (current()) setError(describe(failure, 'Unable to load deleted objects.')); },
    );
    return () => controller.abort();
  }, [objectType.id, offset, version]);
  const visible = page?.items.filter(record => title(record.title).toLowerCase().includes(search.toLowerCase())) ?? [];
  const selected = page?.items.find(record => record.id === selectedId);
  const changePage = (next: number) => { setSelectedId(''); setSearch(''); setOffset(next); };
  return <section className="ontology-tool-workbench ontology-deleted-workbench" aria-label="Deleted objects">
    <header className="ontology-tool-header"><div><h3><DeleteOutlined />Deleted objects</h3><p>Inspect deleted objects and their action history. Restore is available when the deletion can still be undone and your account has permission.</p></div>
      <Button size="small" icon={<ReloadOutlined />} onClick={() => setVersion(value => value + 1)}>Refresh</Button>
    </header>
    <div className="ontology-tool-layout">
      <aside className="ontology-tool-catalog" aria-label="Deleted object list">
        <div className="ontology-tool-search"><Input aria-label="Find deleted objects on this page" placeholder="Find on this page" prefix={<SearchOutlined />} value={search} onChange={event => setSearch(event.target.value)} /></div>
        <div className="ontology-tool-catalog-list">
          {!page && !error && <p role="status">Loading deleted objects…</p>}
          {page && !page.items.length && <p>No deleted objects are available to this account.</p>}
          {!!page?.items.length && !visible.length && <p>No objects on this page match your search.</p>}
          {visible.map(record => <button type="button" key={record.id} aria-pressed={selectedId === record.id} onClick={() => setSelectedId(record.id ?? '')}><DeleteOutlined /><span><strong>{title(record.title)}</strong><small>Deleted · Revision {record.revision}</small></span></button>)}
        </div>
        {(offset > 0 || page?.hasMore) && <footer className="ontology-tool-list-pagination" aria-label="Deleted objects pagination">
          <Button size="small" disabled={!offset || !page} onClick={() => changePage(Math.max(0, offset - pageSize))}>Previous</Button><span>{1 + offset / pageSize}</span>
          <Button size="small" disabled={!page?.hasMore || offset + pageSize > maxOffset} onClick={() => changePage(offset + pageSize)}>Next</Button>
        </footer>}
      </aside>
      <div className="ontology-tool-detail">
        {selected?.id ? <>
          <header className="ontology-tool-detail-header"><div><h4>{title(selected.title)}</h4><code>{objectType.definition.name}</code></div><span>Deleted · Revision {selected.revision}</span></header>
          <nav className="ontology-tool-tabs" aria-label="Deleted object details"><button type="button" aria-pressed={tab === 'history'} onClick={() => setTab('history')}>History and restore</button><button type="button" aria-pressed={tab === 'properties'} onClick={() => setTab('properties')}>Properties</button></nav>
          <div className="ontology-tool-form">
            {tab === 'history' ? <RecordHistory key={selected.id} objectType={objectType.id} recordId={selected.id} onChanged={() => {setSelectedId(''); setVersion(value => value + 1); onChanged();}} />
              : <dl className="ontology-tool-property-list">{Object.entries(selected.properties).map(([id, value]) => <div key={id}><dt>{objectType.definition.properties.find(property => property.id === id)?.name ?? id}</dt><dd>{value === null ? <span className="ontology-tool-note">null</span> : typeof value === 'object' ? <pre>{JSON.stringify(value, null, 2)}</pre> : String(value)}</dd></div>)}</dl>}
          </div>
        </> : <div className="ontology-tool-empty"><DeleteOutlined /><h4>{page && !page.items.length ? 'No deleted objects' : 'Select a deleted object'}</h4><p>Deleted objects are kept separately from current results. Select one to inspect its properties and available restore action.</p></div>}
      </div>
    </div>
  </section>;
}
