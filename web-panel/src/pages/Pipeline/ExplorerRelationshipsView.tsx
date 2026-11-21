import type { ComponentProps, ReactNode } from 'react';
import { Button } from '@/components/ui';
import { LinkOutlined } from '@/components/ui/icons';
import { ExplorerFieldValue, type explorerFields } from './ExplorerObjectOverview';
import { PropertyLabel } from './PropertyTypeIcon';
import './explorer-row-links.css';

type Relationship = {
  id: string;
  name: string;
  direction: 'incoming' | 'outgoing';
  fieldMapping?: { label: string; fields: { source: string; target: string }[] };
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
  records: { key: string; link: ReactNode; properties: ReactNode }[];
  loading: boolean; emptyReason?: string; expanded: string; onToggle: (key: string) => void; onSelect: (id: string) => void;
  offset: number; hasMore: boolean; pageLimit: number; onPage: (offset: number) => void;
}) {
  const selected = relations.find(item => item.id === selectedId);
  if (!selected) return <section className="explorer-row-links explorer-links-empty" role="status"><LinkOutlined /><strong>No relationships</strong><p>No declared relationships are available for this record.</p></section>;
  return <section className="explorer-row-links" aria-label="Related records" aria-busy={loading}>
    <header className="explorer-links-heading"><LinkOutlined /><label><span>Relationships</span>
      <select aria-label="Relationship" value={selected.id} disabled={loading} onChange={event => onSelect(event.target.value)}>
        {relations.map(item => <option key={item.id} value={item.id}>{item.name} · {item.direction}</option>)}
      </select></label>
    </header>
    {selected.fieldMapping && <div className="explorer-links-mapping">
      <div className="explorer-links-field-mapping">
        <span className="explorer-links-badge">{selected.fieldMapping.label}</span>
        <div className="explorer-links-fields">{selected.fieldMapping.fields.map(field => <span key={field.source}><code>{field.source}</code><span aria-hidden="true">→</span><code>{field.target}</code></span>)}</div>
      </div>
    </div>}
    {emptyReason || !records.length ? <div className="explorer-links-empty" role="status"><LinkOutlined /><strong>No matching records</strong><p>{emptyReason || 'No objects match this relationship for the selected record.'}</p></div> :
      <ul>{records.map(record => <li key={record.key}>
        {record.link}
        <Button size="small" disabled={loading} aria-expanded={expanded === record.key} onClick={() => onToggle(record.key)}>
          {expanded === record.key ? 'Hide properties' : 'Show properties'}
        </Button>
        {expanded === record.key && record.properties}
      </li>)}</ul>}
    {(offset > 0 || hasMore) && <nav aria-label="Related records pages">
      <Button disabled={loading || offset === 0} onClick={() => onPage(offset - 10)}>Previous</Button>
      <span>Page {1 + offset / 10}</span>
      <Button disabled={loading || !hasMore || offset >= pageLimit} onClick={() => onPage(offset + 10)}>Next</Button>
      {hasMore && offset >= pageLimit && <span>Inspection limit reached</span>}
    </nav>}
  </section>;
}
