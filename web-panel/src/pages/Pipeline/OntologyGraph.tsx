import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Button, Modal, NativeSelect, Select } from '@/components/ui';
import {
  fetchPublishedOntologyType,
  listOntologyRecords,
  ontologyError,
  type PublishedOntologyType,
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
import { ApartmentOutlined, ArrowRightOutlined, LinkOutlined, PlusOutlined } from '@/components/ui/icons';
import './ontology-tool-workbench.css';
import './ontology-graph-workbench.css';

const size = 20;
const cardinalities = { one_to_one: 'One to one', one_to_many: 'One to many', many_to_one: 'Many to one', many_to_many: 'Many to many' } as const;
const cardinalityLabel = (value: string) => cardinalities[value as keyof typeof cardinalities] ?? value;
const operatorLabels = { eq: 'Equals', contains: 'Contains', gt: 'Greater than', lt: 'Less than' };
const objectPath = (id: string) => '/object-types/' + encodeURIComponent(id);
function display(x: unknown) {
  return x == null ? '—' : typeof x === 'object' ? JSON.stringify(x) : String(x);
}
const reason = (error: unknown) => ontologyError(error, 'The operation failed.');
function usePage<T>(key: string | null, load: (signal: AbortSignal) => Promise<T>) {
  const session = getSessionState();
  const { identity } = session;
  const requestKey = session.authenticated && !session.locked ? key : null;
  const [state, setState] = useState<{ key: string; identity: typeof identity; data?: T; error?: string }>();
  useEffect(() => {
    if (requestKey === null) return;
    const controller = new AbortController();
    const active = () => {
      const current = getSessionState();
      return !controller.signal.aborted && current.authenticated && !current.locked && current.identity === identity;
    };
    load(controller.signal).then(
      (data) => {
        if (active()) setState({ key: requestKey, identity, data });
      },
      (err) => {
        if (active()) setState({ key: requestKey, identity, error: reason(err) });
      },
    );
    return () => controller.abort();
    // requestKey includes every input of this request.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [requestKey, identity]);
  return requestKey !== null && state?.key === requestKey && state.identity === identity ? state : undefined;
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

function NewRelationship({ object, onSaved }: { object: PublishedOntologyType; onSaved: (relation: LinkType) => void }) {
  const [term, search] = useState(''),
    [off, page] = useState(0),
    [target, choose] = useState(''),
    [name, setName] = useState(''),
    [key, setKey] = useState(''),
    [direction, setDirection] = useState<Direction>('outgoing');
  const [card, cardinality] = useState('many_to_many'),
    [error, fail] = useState(''),
    [busy, pending] = useState(false);
  const types = usePage('types:' + term + ':' + off, (signal) =>
    api<Page<{id:string;name:string}>>('get','/projects/'+object.definition.projectId+'/link-targets',
      {params:{limit:size, offset:off,q:term},signal}));
  const {locked, current} = useGraphOperation();
  const endpoint = direction === 'outgoing' ? 'Target' : 'Source';
  async function save() {
    if (locked.current || !current()) return;
    if (!target) { fail('Choose the other object type.'); return; }
    locked.current = true; pending(true);
    fail('');
    try {
      const relation = await api<LinkType>('post', '/projects/' + object.definition.projectId + '/link-types', {
        data: { sourceDefinitionId: direction === 'outgoing' ? object.id : target,
          targetDefinitionId: direction === 'outgoing' ? target : object.id, key, name, cardinality: card },
      });
      if (current()) onSaved(relation);
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
      <div className="ontology-tool-target ontology-graph-new-source"><span>{direction === 'outgoing' ? 'Source' : 'Target'} object type</span><strong>{object.definition.name}</strong></div>
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
        Direction
        <NativeSelect aria-label="New relationship direction" value={direction} disabled={busy}
          onChange={event => setDirection(event.target.value as Direction)}>
          <option value="outgoing">Outgoing · current type → selected type</option>
          <option value="incoming">Incoming · selected type → current type</option>
        </NativeSelect>
      </label>
      <label>
        {endpoint} type
        <Select showSearch filterOption={false} placeholder="Search and select type" aria-label={`New relationship ${endpoint.toLowerCase()} type`}
          value={target || undefined} disabled={busy} loading={!types} notFoundContent={types ? 'No matching object types' : 'Loading object types…'}
          onSearch={value => { search(value); page(0); }}
          onChange={(value: string) => choose(value || '')}
          options={(types?.data?.items ?? []).map(t => ({ value: t.id, title: t.name,
            label: <span className="ontology-graph-target-option"><span>{t.name}</span>{t.id === object.id && <small className="ontology-graph-current">current</small>}</span> }))}
        />
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
          {Object.entries(cardinalities).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </NativeSelect>
      </label>
      <Button type="primary" htmlType="submit" loading={busy}>
        Create relationship
      </Button>
      <Message text={error || types?.error} />
    </form>
  );
}

type Direction = 'incoming' | 'outgoing';
type Anchor = { type: string; record: string; title: string };
type TrailEntry = Anchor & { relationshipId: string; direction: Direction };

function Links({ anchor, relationship, direction, move }: { anchor: Anchor; relationship: LinkType;
    direction: Direction; move: (a: Anchor) => void }) {
  const [off, page] = useState(0), [version, reload] = useState(0);
  const [error, fail] = useState(''), [busy, pending] = useState(false);
  const target = direction === 'incoming' ? relationship.sourceDefinitionId : relationship.targetDefinitionId;
  const base = '/link-types/' + encodeURIComponent(relationship.id);
  const relations = usePage(JSON.stringify([anchor.record, relationship.id, direction, off, version]), signal =>
    api<Page<Neighbor>>('get', base + '/records/' + encodeURIComponent(anchor.record) + '/neighbors', {
      params: { limit: size, offset: off, direction }, signal,
    }));
  const {locked, current} = useGraphOperation();
  const [removing, setRemoving] = useState<Neighbor>();
  async function removeLink(id: string, linkId: string) {
    if (locked.current || !current()) return;
    locked.current = true;
    const pair =
      direction === 'outgoing'
        ? { sourceRecordId: anchor.record, targetRecordId: id }
        : { sourceRecordId: id, targetRecordId: anchor.record };
    pending(true);
    fail('');
    try {
      await api('delete', base + '/links/' + encodeURIComponent(linkId), { params: pair });
      if (!current()) return;
      setRemoving(undefined);
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
    <div aria-label="Linked records" aria-busy={!relations}>
      {!relations && <p className="ontology-tool-note" role="status">Loading linked records…</p>}
      <ul className="ontology-graph-neighbors">
        {relations?.data?.items.map(({ linkId, record, canUnlink }) => (
          <li key={linkId}>
            <Button
              onClick={() =>
                move({ type: target, record: record.id, title: display(record.title ?? record.id) })
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
      {relations?.data && !relations.data.items.length && <p className="ontology-tool-note">No linked records.</p>}
      <Pages offset={off} more={relations?.data?.hasMore} change={page} />
      <Message text={error || relations?.error} />
      <Modal open={!!removing} title="Remove relationship" okText="Remove relationship" confirmLoading={busy} closable={!busy} maskClosable={!busy} keyboard={!busy} okButtonProps={{danger: true}} onCancel={() => { if (!busy) setRemoving(undefined); }} onOk={() => removing?.canUnlink ? removeLink(removing.record.id, removing.linkId) : undefined}>
        <p>Remove the link between <strong>{anchor.title}</strong> and <strong>{display(removing?.record.title ?? removing?.record.id)}</strong>?</p><p>The two objects and their property values will be preserved.</p>
      </Modal>
    </div>
  );
}

type Props = { objectType: PublishedOntologyType; recordId?: string; recordTitle?: string; returnTo: string; onOpenExplorer: () => void; onRelationsChanged?: () => void };
export default function OntologyGraph(props: Props) {
  return props.objectType.definition.source.mode === 'none' ? (
    <Panel key={props.objectType.id + ':' + (props.recordId ?? '')} {...props} />
  ) : null;
}
function Panel({ objectType, recordId, recordTitle, returnTo, onOpenExplorer, onRelationsChanged }: Props) {
  const [tab, setTab] = useState<'relationships' | 'sets'>('relationships');
  const [visit, walk] = useState<{ current?: TrailEntry; previous: TrailEntry[]; serial: number }>({ previous: [], serial: 0 });
  return <section className="ontology-tool-workbench ontology-graph-workbench" aria-label="Object relationships and sets">
    <header className="ontology-tool-header"><div><h3><ApartmentOutlined />Relationships and object sets</h3><p>Manage relationships between object types or define a reusable object query.</p></div></header>
    <nav className="ontology-tool-tabs" aria-label="Graph tools"><button type="button" aria-pressed={tab === 'relationships'} onClick={() => setTab('relationships')}>Relationships</button><button type="button" aria-pressed={tab === 'sets'} onClick={() => setTab('sets')}>Dynamic object sets</button></nav>
    {tab === 'relationships' ? <RelationshipWorkbench key={visit.serial}
      objectType={objectType} entry={visit.current} recordId={recordId} recordTitle={recordTitle}
      goBack={visit.previous.length ? () => walk(state => ({
        current: state.previous[state.previous.length - 1], previous: state.previous.slice(0, -1), serial: state.serial + 1,
      })) : undefined}
      move={(next, from) => walk(state => ({ current: next, previous: [...state.previous, from], serial: state.serial + 1 }))} onRelationsChanged={onRelationsChanged} />
      : <div className="ontology-graph-set-body"><ObjectSets object={objectType} returnTo={returnTo} onOpenExplorer={onOpenExplorer} /></div>}
  </section>;
}

function RelationshipWorkbench({ objectType, entry, recordId, recordTitle, move, goBack, onRelationsChanged }: {
    objectType: PublishedOntologyType; entry?: TrailEntry; recordId?: string; recordTitle?: string;
    move: (entry: TrailEntry, from: TrailEntry) => void; goBack?: () => void; onRelationsChanged?: () => void;
}) {
  const definitionId = entry?.type ?? objectType.id;
  const [anchor, setAnchor] = useState<Anchor | undefined>(() =>
    entry ?? (recordId ? { type: definitionId, record: recordId, title: recordTitle ?? recordId } : undefined));
  const [picked, select] = useState(entry?.relationshipId ?? '');
  const [selfDirection, setSelfDirection] = useState<Direction>(entry?.direction ?? 'outgoing');
  const [offset, page] = useState(0), [version, reload] = useState(0);
  const [creating, setCreating] = useState(false), [created, setCreated] = useState<LinkType>();
  const types = usePage([definitionId, offset, version].join(':'), signal =>
    api<Page<LinkType> & { canManage: boolean }>('get', objectPath(definitionId) + '/link-types', {
      params: { limit: size, offset }, signal,
    }));
  const items = types?.data?.items ?? [];
  const rows = types?.data?.canManage && created && !items.some(item => item.id === created.id) ? [created, ...items] : items;
  const selected = rows.find(item => item.id === picked) ?? rows[0];
  const directionLabel = (item: LinkType) => item.sourceDefinitionId === item.targetDefinitionId
    ? 'Self relationship' : item.sourceDefinitionId === definitionId ? 'Outgoing' : 'Incoming';
  const self = selected && selected.sourceDefinitionId === selected.targetDefinitionId;
  const direction = self ? selfDirection : selected?.sourceDefinitionId === definitionId ? 'outgoing' : 'incoming';
  return <div className="ontology-tool-layout">
    <aside className="ontology-tool-catalog" aria-label="Ontology relationships">
      <div className="ontology-graph-catalog-heading">Relationships</div>
      <div className="ontology-tool-catalog-list">
        {!types && <p role="status">Loading relationships…</p>}
        {types?.error && <p>Relationships are unavailable.</p>}
        {types?.data && !rows.length && <p>No relationships are defined for this object type.</p>}
        {rows.map(relation => <button type="button" key={relation.id}
          aria-pressed={!creating && relation.id === selected?.id}
          onClick={() => { if (relation.id !== selected?.id) setSelfDirection('outgoing'); select(relation.id); setCreating(false); }}>
          <LinkOutlined /><span><strong>{relation.name}</strong><small>{directionLabel(relation)} · {cardinalityLabel(relation.cardinality)}</small></span>
        </button>)}
      </div>
      <Pages offset={offset} more={types?.data?.hasMore} change={value => { page(value); select(''); setSelfDirection('outgoing'); setCreated(undefined); }} />
    </aside>
    <div className="ontology-tool-detail">
      <header className="ontology-tool-detail-header"><div>
        <h4>{creating ? 'New relationship' : selected?.name ?? 'Relationship management'}</h4>
        <span>{creating ? 'Choose the other object type, direction and cardinality.' : selected ? directionLabel(selected) : 'Choose a relationship to inspect its definition.'}</span>
      </div><div className="ontology-graph-detail-actions">
        {goBack && <Button size="small" onClick={goBack}>Back</Button>}
        {types?.data?.canManage && definitionId === objectType.id && <Button size="small"
          icon={creating ? undefined : <PlusOutlined />} aria-expanded={creating}
          onClick={() => setCreating(value => !value)}>{creating ? 'Cancel creation' : 'New relationship'}</Button>}
      </div></header>
      <div className="ontology-tool-form">
        {creating ? <NewRelationship object={objectType} onSaved={relationship => {
          setCreated(relationship); select(relationship.id); setSelfDirection('outgoing');
          setCreating(false); page(0); reload(value => value + 1); onRelationsChanged?.();
        }} /> : selected ? <>
          <RelationshipDefinition selected={selected} objectType={objectType} />
          {self && <label>Relationship direction
            <NativeSelect value={direction} onChange={event => setSelfDirection(event.target.value as Direction)}>
              <option value="outgoing">Outgoing</option><option value="incoming">Incoming</option>
            </NativeSelect>
          </label>}
          <RelationshipRecords anchor={anchor} select={setAnchor} definitionId={definitionId}
            relationship={selected} direction={direction}
            move={next => { if (anchor) move(
              { ...next, relationshipId: selected.id, direction: direction === 'incoming' ? 'outgoing' : 'incoming' },
              { ...anchor, relationshipId: selected.id, direction },
            ); }} />
        </> : <div className="ontology-tool-empty"><LinkOutlined /><h4>{types?.error ? 'Unable to load relationships' : 'Select a relationship'}</h4>
          <p>{types?.error ? 'Try reopening this tool.' : 'The definition and connected objects appear here.'}</p></div>}
        <Message text={types?.error} />
      </div>
    </div>
  </div>;
}

function RelationshipDefinition({ selected, objectType }: { selected: LinkType; objectType: PublishedOntologyType }) {
  const ids = [...new Set([selected.sourceDefinitionId, selected.targetDefinitionId])].filter(id => id !== objectType.id);
  const endpoints = usePage(ids.length ? ids.join(':') : null, signal =>
    Promise.all(ids.map(id => fetchPublishedOntologyType(id, signal))));
  const name = (id: string) => id === objectType.id ? objectType.definition.name
    : endpoints?.data?.find(type => type.id === id)?.definition.name ?? (endpoints?.error ? 'Object type unavailable' : 'Loading object type…');
  return <section className="ontology-graph-definition" aria-label="Relationship definition">
    <h4>Connected object types</h4>
    <div className="ontology-graph-endpoints">
      <div className="ontology-tool-target"><span>Source</span><strong>{name(selected.sourceDefinitionId)}</strong></div>
      <ArrowRightOutlined />
      <div className="ontology-tool-target"><span>Target</span><strong>{name(selected.targetDefinitionId)}</strong></div>
    </div>
    <dl><div><dt>Cardinality</dt><dd>{cardinalityLabel(selected.cardinality)}</dd></div><div><dt>Relationship key</dt><dd><code>{selected.key}</code></dd></div></dl>
    <Message text={endpoints?.error} />
  </section>;
}

function RelationshipRecords({ anchor, select, definitionId, relationship, direction, move }: {
    anchor?: Anchor; select: (anchor: Anchor) => void; definitionId: string; relationship: LinkType;
    direction: Direction; move: (anchor: Anchor) => void;
}) {
  const [picker, open] = useState(!anchor);
  const [offset, page] = useState(0);
  const records = usePage(picker ? definitionId + ':' + offset : null, signal =>
    listOntologyRecords(definitionId, size, offset, false, signal));
  const eligible = records?.data?.items.filter(record => record.id) ?? [];
  return <section aria-label="Connected objects">
    <div className="ontology-tool-section-heading">
      <h4>Connected objects</h4>
      {anchor && <Button size="small" aria-expanded={picker} onClick={() => open(value => !value)}>{picker ? 'Close picker' : 'Change starting object'}</Button>}
    </div>
    {picker ? <>
      <label>Starting object
        <NativeSelect value={anchor?.record ?? ''} disabled={!records?.data} onChange={event => {
          const record = eligible.find(item => item.id === event.target.value);
          if (record?.id) { select({ type: definitionId, record: record.id, title: display(record.title ?? record.id) }); open(false); }
        }}>
          <option value="" disabled>{!records ? 'Loading objects…' : 'Select an object'}</option>
          {anchor && !eligible.some(record => record.id === anchor.record) && <option value={anchor.record}>{anchor.title}</option>}
          {eligible.map(record => <option key={record.id} value={record.id!}>{display(record.title ?? record.id)}</option>)}
        </NativeSelect>
      </label>
      <Pages offset={offset} more={records?.data?.hasMore} change={page} />
      {records?.data && !eligible.length && <p className="ontology-tool-note">No visible objects on this page.</p>}
      <Message text={records?.error} />
    </> : anchor && <p className="ontology-tool-note ontology-graph-anchor">Starting object: <strong>{anchor.title}</strong></p>}
    {anchor && <Links key={JSON.stringify([anchor.record, relationship.id, direction])}
      anchor={anchor} relationship={relationship} direction={direction} move={move} />}
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
