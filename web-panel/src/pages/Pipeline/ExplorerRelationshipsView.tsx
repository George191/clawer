import type { ComponentProps, ReactNode } from 'react';
import { useEffect, useRef, useState } from 'react';
import { Button, Modal, message } from '@/components/ui';
import { DeleteOutlined, LinkOutlined } from '@/components/ui/icons';
import { ExplorerFieldValue, type explorerFields } from './ExplorerObjectOverview';
import { PropertyLabel } from './PropertyTypeIcon';
import { ontologyError } from '../../services/ontology';
import { getSessionState } from '../../services/session';
import './explorer-row-links.css';

type Relationship = {
  id: string;
  name: string;
  direction: 'incoming' | 'outgoing';
  fieldMapping?: { label: string; fields: { source: string; target: string }[] };
};
type LinkedRecord = {
  key: string; link: ReactNode; properties: ReactNode; icon?: ReactNode; typeName?: string;
  unlink?: { label: string; description: ReactNode; run: (signal: AbortSignal) => Promise<void> };
};

export function ExplorerLinkedProperties({ row, fields }: {
  row: ComponentProps<typeof ExplorerFieldValue>['row']; fields: ReturnType<typeof explorerFields>;
}) {
  return <dl className="explorer-linked-properties" aria-label="Related record properties">
    {fields.map(field => <div key={field.key}>
      <dt><PropertyLabel type={field.type}>{field.label}</PropertyLabel></dt><dd><ExplorerFieldValue row={row} field={field} /></dd>
    </div>)}
  </dl>;
}

export default function ExplorerRelationshipsView({ relations, selectedId, records, loading, emptyReason, expanded, onToggle, onSelect,
  offset, hasMore, pageLimit, onPage }: {
  relations: Relationship[]; selectedId?: string | null;
  records: LinkedRecord[];
  loading: boolean; emptyReason?: string; expanded: string; onToggle: (key: string) => void; onSelect: (id: string) => void;
  offset: number; hasMore: boolean; pageLimit: number; onPage: (offset: number) => void;
}) {
  const [confirmation, setConfirmation] = useState<{ relation: string; record: string }>();
  const [busy, setBusy] = useState(false);
  const pending = useRef<AbortController>();
  const alive = useRef(true);
  const selectedScope = useRef(selectedId); selectedScope.current = selectedId;
  useEffect(() => { alive.current = true; return () => { alive.current = false; pending.current?.abort(); }; }, []);
  const selected = relations.find(item => item.id === selectedId);
  const removal = confirmation?.relation === selected?.id ? records.find(record => record.key === confirmation?.record)?.unlink : undefined;
  async function unlink() {
    if (!removal || loading || pending.current) return;
    const controller = new AbortController(); pending.current = controller;
    const identity = getSessionState().identity, relation = selectedId;
    const current = () => {
      const session = getSessionState();
      return alive.current && !controller.signal.aborted && selectedScope.current === relation && session.authenticated && !session.locked && session.identity === identity;
    };
    if (!current()) { pending.current = undefined; return; }
    setBusy(true);
    try {
      await removal.run(controller.signal);
      if (current()) { setConfirmation(undefined); message.success({ content: 'Relationship removed', className: 'explorer-toast' }); }
    } catch (error) {
      if (current() && (error as { code?: string })?.code !== 'ERR_CANCELED') message.error({ content: 'Unable to remove relationship', description: ontologyError(error, 'The relationship could not be removed.'), className: 'explorer-toast' });
    } finally { if (pending.current === controller) pending.current = undefined; if (alive.current) setBusy(false); }
  }
  if (!selected) return <section className="explorer-row-links explorer-links-empty" role="status"><LinkOutlined /><strong>No relationships</strong><p>No declared relationships are available for this record.</p></section>;
  return <section className="explorer-row-links" aria-label="Related records" aria-busy={loading || busy}>
    <header className="explorer-links-heading"><LinkOutlined /><label><span>Relationships</span>
      <select aria-label="Relationship" value={selected.id} disabled={loading || busy} onChange={event => onSelect(event.target.value)}>
        {relations.map(item => <option key={item.id} value={item.id}>{item.name} · {item.direction}</option>)}
      </select></label>
    </header>
    {selected.fieldMapping && <details className="explorer-links-mapping"><summary>{selected.fieldMapping.label}</summary>
      <div className="explorer-links-field-mapping">
        <div className="explorer-links-fields">{selected.fieldMapping.fields.map(field => <span key={field.source}><code>{field.source}</code><span aria-hidden="true">→</span><code>{field.target}</code></span>)}</div>
      </div>
    </details>}
    {emptyReason || !records.length ? <div className="explorer-links-empty" role="status"><LinkOutlined /><strong>No matching records</strong><p>{emptyReason || 'No objects match this relationship for the selected record.'}</p></div> :
      <ul>{records.map(record => <li key={record.key}>
        <div className="explorer-linked-object">{record.icon}<div className="explorer-linked-object__identity">
          {record.typeName && <span className="explorer-linked-object__type">{record.typeName}</span>}{record.link}
        </div></div>
        <Button size="small" disabled={loading || busy} aria-expanded={expanded === record.key} onClick={() => onToggle(record.key)}>
          {expanded === record.key ? 'Hide properties' : 'Show properties'}
        </Button>
        {record.unlink && <Button size="small" type="text" danger className="explorer-links-unlink" icon={<DeleteOutlined />}
          aria-label={`Remove relationship to ${record.unlink.label}`} title="Remove relationship" disabled={loading || busy}
          onClick={() => setConfirmation({ relation: selected.id, record: record.key })} />}
        {expanded === record.key && record.properties}
      </li>)}</ul>}
    {(offset > 0 || hasMore) && <nav aria-label="Related records pages">
      <Button disabled={loading || busy || offset === 0} onClick={() => onPage(offset - 10)}>Previous</Button>
      <span>Page {1 + offset / 10}</span>
      <Button disabled={loading || busy || !hasMore || offset >= pageLimit} onClick={() => onPage(offset + 10)}>Next</Button>
      {hasMore && offset >= pageLimit && <span>Inspection limit reached</span>}
    </nav>}
    <Modal open={!!removal} centered destroyOnHidden className="flow-surface-modal explorer-unlink-dialog" title="Remove relationship?" width={440}
      onCancel={() => { if (!busy) setConfirmation(undefined); }} onOk={unlink} okText="Remove relationship" cancelText="Cancel"
      confirmLoading={busy} okButtonProps={{ danger: true, disabled: loading || !removal }} cancelButtonProps={{ disabled: busy }} closable={!busy} maskClosable={!busy} keyboard={!busy}>
      <p>{removal?.description}</p>
    </Modal>
  </section>;
}
