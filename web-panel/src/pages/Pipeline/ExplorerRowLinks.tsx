import { useEffect, useState } from 'react';
import { LinkOutlined } from '@/components/ui/icons';
import { PropertyLabel } from './PropertyTypeIcon';
import { ExplorerPropertyValue } from './ExplorerObjectOverview';
import { ExplorerValueProvider } from './ExplorerValue';
import { Button } from '@/components/ui';
import { fetchExplorerRowByKey, fetchExplorerRowLinks, type ExplorerRowLinkPage } from '../../services/api';
import { ExplorerRecordLink } from './ExplorerRecordLink';
import './explorer-row-links.css';

type Props = { schema: string; table: string; primaryKey: Record<string,unknown> };

function LinkedProperties({ schema, table, identity }: {schema: string; table: string; identity: string}) {
    const [row, setRow] = useState<Awaited<ReturnType<typeof fetchExplorerRowByKey>>>();
    const [failed, setFailed] = useState(false);
    useEffect(() => {
        const read = new AbortController();
        fetchExplorerRowByKey(schema, table, JSON.parse(identity), read.signal)
            .then(result => { if (!read.signal.aborted) setRow(result); })
            .catch(() => { if (!read.signal.aborted) setFailed(true); });
        return () => read.abort();
    }, [schema, table, identity]);
    if (failed) return <p role="alert">This record could not be read. Its access or identity may have changed.</p>;
    if (!row) return <p role="status">Loading record properties…</p>;
    return <ExplorerValueProvider schema={schema} table={table}><dl className="explorer-linked-properties" aria-label="Related record properties">
        {Object.entries(row.row).map(([name, value]) => <div key={name}>
            <dt><PropertyLabel type={row.columnTypes[name] || 'string'}>{name}</PropertyLabel></dt><dd><ExplorerPropertyValue value={value} identity={row.identity} field={name} /></dd>
        </div>)}
    </dl></ExplorerValueProvider>;
}

function LinkPage({ schema, table, primaryKey }: Props) {
    const [relation, setRelation] = useState<string | null>(null);
    const [offset, setOffset] = useState(0);
    const [retry, setRetry] = useState(0);
    const [page, setPage] = useState<ExplorerRowLinkPage>();
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [expanded, setExpanded] = useState('');
    const key = JSON.stringify(primaryKey);
    useEffect(() => {
        const controller = new AbortController();
        setError(''); setLoading(true); setExpanded('');
        fetchExplorerRowLinks(schema, table, JSON.parse(key), relation, offset, controller.signal)
            .then(value => { if (!controller.signal.aborted) { setPage(value); setLoading(false); } })
            .catch(() => { if (!controller.signal.aborted) { setError('Unable to read related records.'); setLoading(false); } });
        return () => controller.abort();
    }, [schema, table, key, relation, offset, retry]);
    if (error) return <p role="alert">{error} <Button onClick={() => setRetry(v => v + 1)}>Retry</Button></p>;
    if (!page) return <p role="status">Looking up related records…</p>;
    if (!page.relations.length) return <section className="explorer-row-links explorer-links-empty" role="status"><LinkOutlined /><strong>No relationships</strong><p>No declared relationships are available for this record.</p></section>;
    const selected = page.relations.find(item => item.id === page.selected);
    if (!selected) return null;
    return <section className="explorer-row-links" aria-label="Related records" aria-busy={loading}>
        <header className="explorer-links-heading"><LinkOutlined /><label>Relationship
            <select aria-label="Relationship" value={selected.id} disabled={loading} onChange={event => { setOffset(0); setRelation(event.target.value); }}>
                {page.relations.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
            </select></label>
        </header>
        <div className="explorer-links-mapping">
            <div><span className="explorer-links-direction">{selected.direction === 'outgoing' ? 'Outgoing' : 'Incoming'}</span>
                <strong>{selected.schema}.{selected.table}</strong></div>
            <p>{selected.kind === 'foreign_key' ? 'Foreign key' : 'Field mapping'}</p>
            <div className="explorer-links-fields">{selected.fields.map((field, index) => <span key={field}><code>{field}</code><span aria-hidden="true">→</span><code>{selected.targetFields[index]}</code></span>)}</div>
        </div>
        {page.reason || !page.identities.length ? <div className="explorer-links-empty" role="status"><LinkOutlined /><strong>No matching records</strong><p>{page.reason || 'No objects match this relationship for the selected record.'}</p></div> :
            <ul>{page.identities.map(identity => <li key={JSON.stringify(identity)}>
                <ExplorerRecordLink schema={selected.schema} table={selected.table} identity={identity}>
                    {Object.entries(identity).map(([name, value]) => `${name} = ${value}`).join(' · ')}
                </ExplorerRecordLink>
                <Button size="small" disabled={loading} aria-expanded={expanded === JSON.stringify(identity)}
                    onClick={() => setExpanded(current => current === JSON.stringify(identity) ? '' : JSON.stringify(identity))}>
                    {expanded === JSON.stringify(identity) ? 'Hide properties' : 'Show properties'}
                </Button>
                {expanded === JSON.stringify(identity) && <LinkedProperties key={expanded}
                    schema={selected.schema} table={selected.table} identity={expanded} />}
            </li>)}</ul>}
        {(offset > 0 || page.hasMore) && <nav aria-label="Related records pages">
            <Button disabled={loading || offset === 0} onClick={() => { setRelation(selected.id); setOffset(v => v - 10); }}>Previous</Button>
            <span>Page {1 + offset / 10}</span>
            <Button disabled={loading || !page.hasMore || offset >= 500} onClick={() => { setRelation(selected.id); setOffset(v => v + 10); }}>Next</Button>
            {page.hasMore && offset >= 500 && <span>Inspection limit reached</span>}
        </nav>}
    </section>;
}

export default function ExplorerRowLinks(props: Props) {
    return <LinkPage key={JSON.stringify([props.schema, props.table, props.primaryKey])} {...props} />;
}
