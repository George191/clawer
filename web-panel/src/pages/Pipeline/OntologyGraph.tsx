import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Button, Modal, NativeSelect } from '@/components/ui';
import {
  listOntologyRecords,
  ontologyError,
  type PublishedOntologyType,
  type OntologyRecord,
} from '../../services/ontology';
import {
  graphRequest as api,
  type Page,
  type LinkType,
  type Neighbor,
  type NamedSet,
  type SetQuery,
  type SetResult,
} from '../../services/ontology_graph';
import { getSessionState } from '../../services/session';
import { publishedExplorerHref } from '../../services/publishedExplorerHandoff';
import useExplorerErrorToast from './useExplorerErrorToast';
import { ApartmentOutlined, DatabaseOutlined, PlusOutlined } from '@/components/ui/icons';
import './ontology-tool-workbench.css';
import './ontology-graph-workbench.css';

const size = 20;
const operatorLabels = { eq: 'Equals', contains: 'Contains', gt: 'Greater than', lt: 'Less than' };
const objectPath = (id: string) => '/object-types/' + encodeURIComponent(id);
function display(x: unknown) {
  return x == null ? '—' : typeof x === 'object' ? JSON.stringify(x) : String(x);
}
const reason = (error: unknown) => ontologyError(error, 'The operation failed.');
function usePage<T>(key: string, load: (signal: AbortSignal) => Promise<T>) {
  const [state, setState] = useState<{ key: string; data?: T; error?: string }>();
  useEffect(() => {
    const controller = new AbortController(),
      identity = getSessionState().identity;
    const active = () => !controller.signal.aborted && getSessionState().identity === identity;
    load(controller.signal).then(
      (data) => {
        if (active()) setState({ key, data });
      },
      (err) => {
        if (active()) setState({ key, error: reason(err) });
      },
    );
    return () => controller.abort();
    // key includes every input of this request.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  return state?.key === key ? state : undefined;
}
function Pages({ offset, more, change }: { offset: number; more?: boolean; change: (n: number) => void }) {
  return offset > 0 || more ? (
    <div className="ontology-graph-pages">
      <Button disabled={!offset} onClick={() => change(Math.max(0, offset - size))}>
        Previous
      </Button>
      <Button disabled={!more} onClick={() => change(offset + size)}>
        Next
      </Button>
    </div>
  ) : null;
}
function Message({ text }: { text?: string }) {
  useExplorerErrorToast(text, 'Graph request failed');
  return null;
}

function useGraphOperation() {
  const locked = useRef(false), alive = useRef(true);
  const identity = useRef(getSessionState().identity).current;
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const current = () => {
    const session = getSessionState();
    return alive.current && session.authenticated && !session.locked && session.identity === identity;
  };
  return {locked, current};
}

function NewType({ object, onSaved }: { object: PublishedOntologyType; onSaved: () => void }) {
  const [term, search] = useState(''),
    [off, page] = useState(0),
    [target, choose] = useState(''),
    [name, setName] = useState(''),
    [key, setKey] = useState('');
  const [card, cardinality] = useState('many_to_many'),
    [error, fail] = useState(''),
    [busy, pending] = useState(false);
  const types = usePage('types:' + term + ':' + off, (signal) =>
    api<Page<{id:string;name:string}>>('get','/projects/'+object.definition.projectId+'/link-targets',
      {params:{limit:size, offset:off,q:term},signal}));
  const {locked, current} = useGraphOperation();
  async function save() {
    if (locked.current || !current()) return;
    locked.current = true; pending(true);
    fail('');
    try {
      await api('post', '/projects/' + object.definition.projectId + '/link-types', {
        data: { sourceDefinitionId: object.id, targetDefinitionId: target, key, name, cardinality: card },
      });
      if (current()) onSaved();
    } catch (e) {
      if (current()) fail(reason(e));
    } finally {
      locked.current = false; if (current()) pending(false);
    }
  }
  return (
    <form className="flow-tool-fields"
      onSubmit={(e) => {
        e.preventDefault();
        void save();
      }}
    >
      <label>
        Relationship name
        <input required value={name} maxLength={120} onChange={(e) => setName(e.target.value)} />
      </label>
      <label>
        Relationship key
        <input
          required
          pattern="[a-z][a-z0-9_\-]{0,62}"
          value={key}
          onChange={(e) => setKey(e.target.value)}
        />
      </label>
      <label>
        Find target type
        <input
          value={term}
          onChange={(e) => {
            search(e.target.value);
            page(0);
            choose('');
          }}
        />
      </label>
      <label>
        Target type
        <NativeSelect required value={target} onChange={(e) => choose(e.target.value)}>
          <option value="">Select type</option>
          {types?.data?.items.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
        </NativeSelect>
      </label>
      <Pages
        offset={off}
        more={types?.data?.hasMore}
        change={(n) => {
          page(n);
          choose('');
        }}
      />
      <label>
        Cardinality
        <NativeSelect value={card} onChange={(e) => cardinality(e.target.value)}>
          {['one_to_one', 'one_to_many', 'many_to_one', 'many_to_many'].map((c) => (
            <option key={c} value={c}>
              {c.replace(/_/g, ' ')}
            </option>
          ))}
        </NativeSelect>
      </label>
      <Button type="primary" htmlType="submit" disabled={busy}>
        Save relationship type
      </Button>
      <Message text={error || types?.error} />
    </form>
  );
}

type Anchor = { type: string; record: string; title: string };
function Links({ anchor, move }: { anchor: Anchor; move: (a: Anchor) => void }) {
  const [typeId, pickType] = useState(''),
    [direction, setDirection] = useState<'incoming' | 'outgoing'>('outgoing');
  const [toff, typePage] = useState(0),
    [off, page] = useState(0),
    [coff, choicesPage] = useState(0),
    [choice, pick] = useState('');
  const [version, reload] = useState(0),
    [error, fail] = useState(''),
    [busy, pending] = useState(false);
  const types = usePage('relations:' + anchor.type + ':' + toff, (signal) =>
    api<Page<LinkType> & { canManage: boolean }>('get', objectPath(anchor.type) + '/link-types', {
      params: { limit: size, offset: toff },
      signal,
    }),
  );
  const selected = types?.data?.items.find((t) => t.id === typeId);
  const target =
    selected && (direction === 'incoming' ? selected.sourceDefinitionId : selected.targetDefinitionId);
  const base = '/link-types/' + typeId;
  const relations = usePage([anchor.record, typeId, direction, off, version].join('/'), (signal) =>
    selected
      ? api<Page<Neighbor>>('get', base + '/records/' + anchor.record + '/neighbors', {
          params: { limit: size, offset: off, direction },
          signal,
        })
      : Promise.resolve({ items: [], hasMore: false }),
  );
  const choices = usePage([target, coff, version].join('/'), () =>
    target && types?.data?.canManage ? listOntologyRecords(target, size, coff) : Promise.resolve({ items: [], hasMore: false }),
  );
  const {locked, current} = useGraphOperation();
  const [removing, setRemoving] = useState<Neighbor>();
  async function mutate(id: string, linkId?: string) {
    if (locked.current || !current()) return;
    locked.current = true;
    const pair =
      direction === 'outgoing'
        ? { sourceRecordId: anchor.record, targetRecordId: id }
        : { sourceRecordId: id, targetRecordId: anchor.record };
    pending(true);
    fail('');
    try {
      if (linkId) await api('delete', base + '/links/' + linkId, { params: pair });
      else await api('post', base + '/links', { data: pair });
      if (!current()) return;
      setRemoving(undefined);
      pick('');
      page(0);
      reload((n) => n + 1);
    } catch (e) {
      if (current()) fail(reason(e));
    } finally {
      locked.current = false;
      if (current()) pending(false);
    }
  }
  return (
    <div>
      <label>
        Relationship and direction
        <NativeSelect
          value={selected ? typeId + ':' + direction : ''}
          onChange={(e) => {
            const [id, dir] = e.target.value.split(':');
            pickType(id);
            setDirection(dir as 'incoming' | 'outgoing');
            page(0);
            choicesPage(0);
            pick('');
          }}
        >
          <option value="">Select relationship</option>
          {types?.data?.items.flatMap((t) =>
            (['outgoing', 'incoming'] as const)
              .filter((d) => (d === 'outgoing' ? t.sourceDefinitionId : t.targetDefinitionId) === anchor.type)
              .map((d) => (
                <option key={t.id + d} value={t.id + ':' + d}>
                  {t.name} · {d}
                </option>
              )),
          )}
        </NativeSelect>
      </label>
      <Pages
        offset={toff}
        more={types?.data?.hasMore}
        change={(n) => {
          typePage(n);
          pickType('');
        }}
      />
      {selected && (
        <>
          <ul className="ontology-graph-neighbors">
            {relations?.data?.items.map(({ linkId, record, canUnlink }) => (
              <li key={linkId}>
                <Button
                  onClick={() =>
                    move({ type: target!, record: record.id!, title: display(record.title ?? record.id) })
                  }
                >
                  {display(record.title ?? record.id)}
                </Button>
                {canUnlink && (
                  <Button
                    disabled={busy}
                    aria-label={'Remove link to ' + display(record.title ?? record.id)}
                    onClick={() => setRemoving({linkId, record, canUnlink})}
                  >
                    Remove link
                  </Button>
                )}
              </li>
            ))}
          </ul>
          {relations?.data && !relations.data.items.length && <p>No linked records.</p>}
          <Pages offset={off} more={relations?.data?.hasMore} change={page} />
          {types?.data?.canManage && (
            <>
              <label>
                Record to link
                <NativeSelect value={choice} onChange={(e) => pick(e.target.value)}>
                  <option value="">Select record</option>
                  {choices?.data?.items.map((r: OntologyRecord) => (
                    <option value={r.id!} key={r.id}>
                      {display(r.title ?? r.id)}
                    </option>
                  ))}
                </NativeSelect>
              </label>
              <Pages
                offset={coff}
                more={choices?.data?.hasMore}
                change={(n) => {
                  choicesPage(n);
                  pick('');
                }}
              />
              <Button disabled={!choice || busy} onClick={() => void mutate(choice)}>
                Add link
              </Button>
            </>
          )}
        </>
      )}
      <Message text={error || types?.error || choices?.error || relations?.error} />
      <Modal open={!!removing} title="Remove relationship" okText="Remove relationship" confirmLoading={busy} closable={!busy} maskClosable={!busy} okButtonProps={{danger: true}} onCancel={() => { if (!busy) setRemoving(undefined); }} onOk={() => removing?.canUnlink ? mutate(removing.record.id, removing.linkId) : undefined}>
        <p>Remove the link between <strong>{anchor.title}</strong> and <strong>{display(removing?.record.title)}</strong>?</p><p>The two objects and their property values will be preserved.</p>
      </Modal>
    </div>
  );
}

type Props = { objectType: PublishedOntologyType; recordId?: string; recordTitle?: string; returnTo: string; onOpenExplorer: () => void };
export default function OntologyGraph(props: Props) {
  return props.objectType.definition.source.mode === 'none' ? (
    <Panel key={props.objectType.id + ':' + (props.recordId ?? '')} {...props} />
  ) : null;
}
function Panel({ objectType, recordId, recordTitle, returnTo, onOpenExplorer }: Props) {
  const [tab, setTab] = useState<'relationships' | 'sets'>('relationships');
  const [create, toggle] = useState(false), [version, reload] = useState(0), [recordOffset, setRecordOffset] = useState(0);
  const [trail, walk] = useState<Anchor[]>([]);
  const rights = usePage(objectType.id, (signal) =>
    api<{ canManage: boolean }>('get', objectPath(objectType.id) + '/link-types', {params: { limit: 1 }, signal}),
  );
  const records = usePage('graph-records:' + objectType.id + ':' + recordOffset, signal => listOntologyRecords(objectType.id, size, recordOffset, false, signal));
  const anchor = trail[trail.length - 1] ?? (recordId ? { type: objectType.id, record: recordId, title: recordTitle || records?.data?.items.find(item => item.id === recordId)?.title?.toString() || 'Selected object' } : undefined);
  return <section className="ontology-tool-workbench ontology-graph-workbench" aria-label="Object relationships and sets">
    <header className="ontology-tool-header"><div><h3><ApartmentOutlined />Relationships and object sets</h3><p>Browse linked objects or define a reusable query. Relationship links are stored independently of object properties.</p></div></header>
    <nav className="ontology-tool-tabs" aria-label="Graph tools"><button type="button" aria-pressed={tab === 'relationships'} onClick={() => setTab('relationships')}>Relationships</button><button type="button" aria-pressed={tab === 'sets'} onClick={() => setTab('sets')}>Dynamic object sets</button></nav>
    <Message text={rights?.error || records?.error} />
    {tab === 'relationships' ? <div className="ontology-tool-layout">
      <aside className="ontology-tool-catalog" aria-label="Objects to inspect"><div className="ontology-graph-catalog-heading">{objectType.definition.name}</div><div className="ontology-tool-catalog-list">
        {!records && <p role="status">Loading objects…</p>}
        {records?.data && !records.data.items.length && <p>No visible objects.</p>}
        {records?.data?.items.map(record => <button type="button" key={record.id} aria-pressed={anchor?.record === record.id} onClick={() => { toggle(false); walk([{type: objectType.id, record: record.id!, title: display(record.title ?? record.id)}]); }}><DatabaseOutlined /><span><strong>{display(record.title ?? record.id)}</strong><small>Revision {record.revision}</small></span></button>)}
      </div><Pages offset={recordOffset} more={records?.data?.hasMore} change={setRecordOffset} /></aside>
      <div className="ontology-tool-detail">
        <header className="ontology-tool-detail-header"><div><h4>{create ? 'New relationship type' : anchor?.title || 'Select an object'}</h4><span>{create ? 'Define the two object types and link cardinality.' : 'Choose a relationship to inspect connected objects.'}</span></div>
          {rights?.data?.canManage && <Button size="small" icon={!create ? <PlusOutlined /> : undefined} aria-expanded={create} onClick={() => toggle(value => !value)}>{create ? 'Cancel' : 'New type'}</Button>}
        </header>
        <div className="ontology-tool-form">
          {create ? <NewType object={objectType} onSaved={() => { toggle(false); reload(value => value + 1); }} /> : anchor ? <>
            {!!trail.length && <div className="ontology-graph-trail"><span>{trail.map(item => item.title).join(' / ')}</span><Button size="small" onClick={() => walk(previous => previous.slice(0, -1))}>Back</Button></div>}
            <Links key={anchor.record + ':' + version} anchor={anchor} move={next => walk(previous => [...previous, next])} />
          </> : <div className="ontology-tool-empty"><ApartmentOutlined /><h4>Choose an object to browse its links</h4><p>Linked objects can be opened without changing the current result set.</p></div>}
        </div>
      </div>
    </div> : <div className="ontology-graph-set-body"><ObjectSets object={objectType} returnTo={returnTo} onOpenExplorer={onOpenExplorer} /></div>}
  </section>;
}

function ObjectSets({ object, returnTo, onOpenExplorer }: { object: PublishedOntologyType; returnTo: string; onOpenExplorer: () => void }) {
  const navigate = useNavigate();
  const props = object.definition.properties;
  const [field, setField] = useState(''),
    [op, setOp] = useState<SetQuery['filters'][number]['op']>('eq'),
    [raw, setRaw] = useState('');
  const fieldType = props.find(p => p.id === field)?.type;
  const operators: (keyof typeof operatorLabels)[] = fieldType === 'string' ? ['eq', 'contains']
    : fieldType === 'integer' || fieldType === 'number' ? ['eq', 'gt', 'lt'] : ['eq'];
  const [filters, change] = useState<SetQuery['filters']>([]),
    [group, groupBy] = useState(''),
    [sum, sumField] = useState('');
  const [name, setName] = useState(''),
    [chosen, choose] = useState(''),
    [version, reload] = useState(0),
    [off, page] = useState(0);
  const [resultOff, resultPage] = useState(0),
    [groupOff, groupPage] = useState(0),
    [busy, pending] = useState(false);
  const [active, run] = useState<{ query: SetQuery; id?: string; nonce: number }>(),
    [error, fail] = useState('');
  const [validationError, setValidationError] = useState('');
  const path = objectPath(object.id) + '/sets';
  const saved = usePage('sets:' + version + ':' + off, (signal) =>
    api<Page<NamedSet>>('get', path, { params: { limit: size, offset: off }, signal }),
  );
  const result = usePage(JSON.stringify([active, resultOff, groupOff]), (signal) =>
    active
      ? api<SetResult>(active.id ? 'get' : 'post', path + '/' + (active.id ?? 'preview'), {
          data: active.id ? undefined : active.query,
          params: { limit: size, offset: resultOff, groupOffset: groupOff },
          signal,
        })
      : Promise.resolve(undefined),
  );
  const query = { filters, groupBy: group || undefined, sumField: sum || undefined };
  function add() {
    try {
      const p = props.find((f) => f.id === field)!;
      let value: unknown = raw;
      if (p.type === 'number' || p.type === 'integer' || p.type === 'boolean' || p.type === 'json')
        value = JSON.parse(raw);
      if (typeof value === 'number' && (!Number.isFinite(value) || (Number.isInteger(value) && !Number.isSafeInteger(value)))) {
        setValidationError('Enter a number that can be represented exactly by this browser.');
        return;
      }
      change((f) => [...f, { field, op, value }]);
      setRaw('');
      setValidationError('');
      fail('');
    } catch {
      setValidationError('Enter a valid value for this property.');
    }
  }
  function execute(id?: string) {
    resultPage(0);
    groupPage(0);
    run((prev) => ({ id, query, nonce: (prev?.nonce ?? 0) + 1 }));
  }
  const {locked, current} = useGraphOperation();
  const [confirmDelete, setConfirmDelete] = useState(false);
  async function mutate(remove = false) {
    if (locked.current || !current()) return;
    locked.current = true; pending(true);
    fail('');
    setValidationError('');
    try {
      if (remove) {
        await api('delete', path + '/' + chosen);
        if (!current()) return;
        setConfirmDelete(false); choose('');
        run(undefined);
      } else {
        const item = await api<NamedSet>('post', path, { data: { name, query } });
        if (!current()) return;
        choose(item.id);
        setName('');
        execute(item.id);
      }
      reload((n) => n + 1);
      page(0);
    } catch (e) {
      if (current()) fail(reason(e));
    } finally {
      locked.current = false; if (current()) pending(false);
    }
  }
  const data = result?.data;
  return (
    <section className="flow-tool-section" aria-label="Dynamic object sets">
      <header className="flow-tool-heading"><div><h3>Dynamic object sets</h3><p>Save a query that is re-evaluated against current objects. Preview the results, then continue in Explorer.</p></div></header>
      <div className="flow-tool-fields">
        <label>
          Filter property
          <NativeSelect
            value={field}
            onChange={(e) => {
              setField(e.target.value);
              setOp('eq');
            }}
          >
            <option value="">Select property</option>
            {props.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </NativeSelect>
        </label>
        <label>
          Operator
          <NativeSelect value={op} onChange={(e) => setOp(e.target.value as typeof op)}>
            {operators.map(o => <option key={o} value={o}>{operatorLabels[o]}</option>)}
          </NativeSelect>
        </label>
        <label>
          Filter value
          <input value={raw} onChange={(e) => setRaw(e.target.value)} />
        </label>
        <Button disabled={!field || filters.length >= 20} onClick={add}>
          Add filter
        </Button>
      </div>
      {!!filters.length && <ul className="flow-set-filters">
        {filters.map((f, i) => (
          <li key={i}>
            {props.find(property => property.id === f.field)?.name ?? f.field} {operatorLabels[f.op]} {display(f.value)}{' '}
            <Button
              aria-label={'Remove filter ' + (i + 1)}
              onClick={() => change((list) => list.filter((_, n) => n !== i))}
            >
              Remove
            </Button>
          </li>
        ))}
      </ul>}
      <div className="flow-tool-fields">
        <label>
          Group by
          <NativeSelect value={group} onChange={(e) => groupBy(e.target.value)}>
            <option value="">None</option>
            {props
              .filter((p) => p.type !== 'json')
              .map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
          </NativeSelect>
        </label>
        <label>
          Sum property
          <NativeSelect value={sum} onChange={(e) => sumField(e.target.value)}>
            <option value="">None</option>
            {props
              .filter((p) => ['number', 'integer'].includes(p.type))
              .map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
          </NativeSelect>
        </label>
        <Button type="primary" onClick={() => execute()}>Preview set</Button>
      </div>
      <div className="flow-tool-fields flow-set-save">
        <label>
          Set name
          <input maxLength={120} value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <Button disabled={!name.trim() || busy} onClick={() => void mutate()}>
          Save object set
        </Button>
      </div>
      <div className="flow-tool-fields">
        <label>
          Saved set
          <NativeSelect value={chosen} onChange={(e) => choose(e.target.value)}>
            <option value="">Select set</option>
            {saved?.data?.items.map((v) => (
              <option key={v.id} value={v.id}>
                {v.name}
              </option>
            ))}
          </NativeSelect>
        </label>
        <Pages
          offset={off}
          more={saved?.data?.hasMore}
          change={(n) => {
            page(n);
            choose('');
          }}
        />
        <Button disabled={!chosen} onClick={() => execute(chosen)}>
          Run saved set
        </Button>
        <Button disabled={!chosen || busy} onClick={() => setConfirmDelete(true)}>
          Delete saved set
        </Button>
      </div>
      {active && !result && <p role="status">Loading object set…</p>}
      {data && (
        <div aria-label="Object set results">
          <p>
            Matching records: {data.count}
            {data.sum != null ? ' · Sum: ' + data.sum : ''}
          </p>
          {active && <Button size="small" onClick={() => {
            const href = publishedExplorerHref(object.id, {
              ...(active.id ? { savedSet: active.id } : { query: active.query }), returnTo,
            });
            onOpenExplorer();
            navigate(href);
          }}>Open in Object Explorer</Button>}
          <ul>
            {data.items.map((row) => (
              <li key={row.id}>{display(row.title ?? row.id)}</li>
            ))}
          </ul>
          <Pages offset={resultOff} more={data.hasMore} change={resultPage} />
          {data.groups.length > 0 && (
            <table>
              <thead>
                <tr>
                  <th>Group</th>
                  <th>Count</th>
                  <th>Sum</th>
                </tr>
              </thead>
              <tbody>
                {data.groups.map((g, i) => (
                  <tr key={i}>
                    <td>{display(g.value)}</td>
                    <td>{g.count}</td>
                    <td>{display(g.sum)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {(groupOff > 0 || data.groupsHasMore) && (
            <div>
              <Button disabled={!groupOff} onClick={() => groupPage((n) => n - 50)}>
                Previous groups
              </Button>
              <Button disabled={!data.groupsHasMore} onClick={() => groupPage((n) => n + 50)}>
                Next groups
              </Button>
            </div>
          )}
        </div>
      )}
      {validationError && <p role="alert">{validationError}</p>}
      <Message text={error || saved?.error || result?.error} />
      <Modal open={confirmDelete} title="Delete object set" okText="Delete object set" okButtonProps={{danger:true}} confirmLoading={busy} closable={!busy} maskClosable={!busy} onCancel={() => { if (!busy) setConfirmDelete(false); }} onOk={() => mutate(true)}>
        <p>Delete <strong>{saved?.data?.items.find(item => item.id === chosen)?.name}</strong>?</p><p>This removes the saved query. Object records will be preserved.</p>
      </Modal>
    </section>
  );
}
