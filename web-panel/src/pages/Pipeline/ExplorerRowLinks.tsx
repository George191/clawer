import { useEffect, useState } from 'react';
import { LinkOutlined } from '@/components/ui/icons';
import { PropertyLabel } from './PropertyTypeIcon';
import { ExplorerFieldValue, explorerFields } from './ExplorerObjectOverview';
import { ExplorerValueProvider } from './ExplorerValue';
import { Button } from '@/components/ui';
import { fetchExplorerRowByKey, fetchExplorerRowLinks, type ExplorerRowLinkPage } from '../../services/api';
import { ExplorerRecordLink } from './ExplorerRecordLink';
import { layoutPath, readLayouts, type ResultLayout } from '../../services/explorerLayouts';
import './explorer-row-links.css';

type Props = { schema: string; table: string; primaryKey: Record<string,unknown>; initialPage?: ExplorerRowLinkPage };

export function useExplorerLinkAvailability(schema: string, table: string, identity?: Record<string, unknown>, revision: string | number = 0) {
    const [retry, setRetry] = useState(0);
    const key = JSON.stringify(identity ?? {});
    const scope = JSON.stringify([schema, table, key, revision, retry]);
    const [read, setRead] = useState<{scope: string; page?: ExplorerRowLinkPage; error?: true}>();
    useEffect(() => {
        if (!schema || !table || !Object.keys(JSON.parse(key)).length) {
            setRead({scope}); return;
        }
        const controller = new AbortController();
        async function load() {
            const first = await fetchExplorerRowLinks(schema, table, JSON.parse(key), null, 0, controller.signal);
            let page = first.identities.length ? first : undefined;
            let unavailable = !!first.reason;
            for (const relation of first.relations) {
                if (page || relation.id === first.selected) continue;
                const next = await fetchExplorerRowLinks(schema, table, JSON.parse(key), relation.id, 0, controller.signal);
                if (next.identities.length) page = next;
                unavailable ||= !!next.reason;
            }
            if (!controller.signal.aborted) setRead({scope, page, error: !page && unavailable ? true : undefined});
        }
        load().catch(() => { if (!controller.signal.aborted) setRead({scope, error: true}); });
        return () => controller.abort();
    }, [schema, table, key, scope]);
    return { ...(read?.scope === scope ? read : undefined), retry: () => setRetry(n => n + 1) };
}

function LinkedProperties({ schema, table, identity }: {schema: string; table: string; identity: string}) {
    const [row, setRow] = useState<Awaited<ReturnType<typeof fetchExplorerRowByKey>>>();
    const [layout, setLayout] = useState<ResultLayout | null>();
    const [failed, setFailed] = useState(false);
    useEffect(() => {
        const read = new AbortController();
        Promise.all([fetchExplorerRowByKey(schema, table, JSON.parse(identity), read.signal), readLayouts(layoutPath(schema, table), read.signal)])
            .then(([result, layouts]) => { if (!read.signal.aborted) { setRow(result); setLayout(layouts.personal.layout ?? layouts.default.layout); } })
            .catch(() => { if (!read.signal.aborted) setFailed(true); });
        return () => read.abort();
    }, [schema, table, identity]);
    if (failed) return <p role="alert">This record could not be read. Its access or identity may have changed.</p>;
    if (!row) return <p role="status">Loading record properties…</p>;
    const previewRow = { record: row.row, identity: row.identity, key: identity, title: '', columnTypes: row.columnTypes };
    const presentations = Object.fromEntries((layout?.columns ?? []).map(column => [column.field, column.presentation ?? 'value']));
    return <ExplorerValueProvider schema={schema} table={table}><dl className="explorer-linked-properties" aria-label="Related record properties">
        {explorerFields(previewRow, Object.keys(row.row), undefined, presentations).map(field => <div key={field.key}>
            <dt><PropertyLabel type={field.type}>{field.label}</PropertyLabel></dt><dd><ExplorerFieldValue row={previewRow} field={field} /></dd>
        </div>)}
    </dl></ExplorerValueProvider>;
}

function LinkPage({ schema, table, primaryKey, initialPage }: Props) {
    const [relation, setRelation] = useState<string | null>(initialPage?.selected ?? null);
    const [offset, setOffset] = useState(0);
    const [retry, setRetry] = useState(0);
    const [page, setPage] = useState<ExplorerRowLinkPage | undefined>(initialPage);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [expanded, setExpanded] = useState('');
    const key = JSON.stringify(primaryKey);
    useEffect(() => {
        if (initialPage && relation === initialPage.selected && offset === initialPage.offset && retry === 0) {
            setPage(initialPage); setLoading(false); return;
        }
        const controller = new AbortController();
        setError(''); setLoading(true); setExpanded('');
        fetchExplorerRowLinks(schema, table, JSON.parse(key), relation, offset, controller.signal)
            .then(value => { if (!controller.signal.aborted) { setPage(value); setLoading(false); } })
            .catch(() => { if (!controller.signal.aborted) { setError('Unable to read related records.'); setLoading(false); } });
        return () => controller.abort();
    }, [schema, table, key, relation, offset, retry, initialPage]);
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
