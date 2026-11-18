import { useEffect, useState } from 'react';
import { Button } from '@/components/ui';
import { fetchExplorerRowByKey, fetchExplorerRowLinks, type ExplorerRowLinkPage } from '../../services/api';
import { ExplorerRecordLink } from './ExplorerRecordLink';
import './explorer-row-links.css';

type Props = { schema: string; table: string; primaryKey: Record<string,unknown> };

function LinkedProperties({ schema, table, identity }: {schema: string; table: string; identity: string}) {
    const [row, setRow] = useState<Record<string, unknown>>();
    const [failed, setFailed] = useState(false);
    useEffect(() => {
        const read = new AbortController();
        fetchExplorerRowByKey(schema, table, JSON.parse(identity), read.signal)
            .then(result => { if (!read.signal.aborted) setRow(result.row); })
            .catch(() => { if (!read.signal.aborted) setFailed(true); });
        return () => read.abort();
    }, [schema, table, identity]);
    if (failed) return <p role="alert">This record could not be read. Its access or identity may have changed.</p>;
    if (!row) return <p role="status">Loading record properties…</p>;
    return <dl className="explorer-linked-properties" aria-label="Related record properties">
        {Object.entries(row).map(([name, value]) => <div key={name}>
            <dt>{name}</dt><dd>{value === null ? 'Null' : typeof value === 'object' ? JSON.stringify(value) : String(value)}</dd>
        </div>)}
    </dl>;
}

function LinkPage({ schema, table, primaryKey }: Props) {
    const [relation, setRelation] = useState<string | null>(null);
    const [offset, setOffset] = useState(0);
    const [retry, setRetry] = useState(0);
    const [page, setPage] = useState<ExplorerRowLinkPage>();
    const [error, setError] = useState('');
    const [expanded, setExpanded] = useState('');
    const key = JSON.stringify(primaryKey);
    useEffect(() => {
        const controller = new AbortController();
        setError(''); setPage(undefined); setExpanded('');
        fetchExplorerRowLinks(schema, table, JSON.parse(key), relation, offset, controller.signal)
            .then(value => { if (!controller.signal.aborted) setPage(value); })
            .catch(() => { if (!controller.signal.aborted) setError('Unable to read related records.'); });
        return () => controller.abort();
    }, [schema, table, key, relation, offset, retry]);
    if (error) return <p role="alert">{error} <Button onClick={() => setRetry(v => v + 1)}>Retry</Button></p>;
    if (!page) return <p role="status">Looking up related records…</p>;
    if (!page.relations.length) return <p>No declared relationships are available for this record.</p>;
    const selected = page.relations.find(item => item.id === page.selected);
    if (!selected) return null;
    return <section className="explorer-row-links" aria-label="Related records">
        <label>Relationship <select value={selected.id} onChange={event => { setOffset(0); setRelation(event.target.value); }}>
            {page.relations.map(item => <option key={item.id} value={item.id}>{item.name} · {item.direction} · {item.schema}.{item.table}</option>)}
        </select></label>
        <p>{selected.kind === 'foreign_key' ? 'PostgreSQL foreign key' : 'Registered field mapping'} · {selected.fields.join(', ')} → {selected.targetFields.join(', ')}</p>
        {page.reason ? <p role="status">{page.reason}</p> : !page.identities.length ? <p>No matching records.</p> :
            <ul>{page.identities.map(identity => <li key={JSON.stringify(identity)}>
                <ExplorerRecordLink schema={selected.schema} table={selected.table} identity={identity}>
                    {Object.entries(identity).map(([name, value]) => `${name} = ${value}`).join(' · ')}
                </ExplorerRecordLink>
                <Button size="small" aria-expanded={expanded === JSON.stringify(identity)}
                    onClick={() => setExpanded(current => current === JSON.stringify(identity) ? '' : JSON.stringify(identity))}>
                    {expanded === JSON.stringify(identity) ? 'Hide properties' : 'Show properties'}
                </Button>
                {expanded === JSON.stringify(identity) && <LinkedProperties key={expanded}
                    schema={selected.schema} table={selected.table} identity={expanded} />}
            </li>)}</ul>}
        {(offset > 0 || page.hasMore) && <nav aria-label="Related records pages">
            <Button disabled={offset === 0} onClick={() => { setRelation(selected.id); setOffset(v => v - 10); }}>Previous</Button>
            <span>Page {1 + offset / 10}</span>
            <Button disabled={!page.hasMore || offset >= 500} onClick={() => { setRelation(selected.id); setOffset(v => v + 10); }}>Next</Button>
            {page.hasMore && offset >= 500 && <span>Inspection limit reached</span>}
        </nav>}
    </section>;
}

export default function ExplorerRowLinks(props: Props) {
    return <LinkPage key={JSON.stringify([props.schema, props.table, props.primaryKey])} {...props} />;
}
