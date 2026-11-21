import { useEffect, useState } from 'react';
import { explorerFields } from './ExplorerObjectOverview';
import { ExplorerValueProvider } from './ExplorerValue';
import { fetchExplorerRowByKey, fetchExplorerRowLinks, type ExplorerRowLinkPage } from '../../services/api';
import { ExplorerRecordLink } from './ExplorerRecordLink';
import { layoutPath, readLayouts, type ResultLayout } from '../../services/explorerLayouts';
import ExplorerRelationshipsView, { ExplorerLinkedProperties } from './ExplorerRelationshipsView';
import useExplorerErrorToast from './useExplorerErrorToast';
import { getSessionState } from '../../services/session';

type Props = { schema: string; table: string; primaryKey: Record<string,unknown>; initialPage?: ExplorerRowLinkPage };
const live = (signal: AbortSignal, identity: string) => {
    const session = getSessionState();
    return !signal.aborted && session.authenticated && !session.locked && session.identity === identity;
};

export function useExplorerLinkAvailability(schema: string, table: string, identity?: Record<string, unknown>, revision: string | number = 0) {
    const [retry, setRetry] = useState(0);
    const key = JSON.stringify(identity ?? {});
    const sessionIdentity = getSessionState().identity;
    const scope = JSON.stringify([schema, table, key, revision, retry, sessionIdentity]);
    const [read, setRead] = useState<{scope: string; page?: ExplorerRowLinkPage; error?: true}>();
    useEffect(() => {
        if (!schema || !table || !Object.keys(JSON.parse(key)).length) {
            setRead({scope}); return;
        }
        const controller = new AbortController();
        async function load() {
            const first = await fetchExplorerRowLinks(schema, table, JSON.parse(key), null, 0, controller.signal);
            if (!live(controller.signal, sessionIdentity)) return;
            let page = first.identities.length ? first : undefined;
            let unavailable = !!first.reason;
            for (const relation of first.relations) {
                if (!live(controller.signal, sessionIdentity)) return;
                if (page || relation.id === first.selected) continue;
                const next = await fetchExplorerRowLinks(schema, table, JSON.parse(key), relation.id, 0, controller.signal);
                if (next.identities.length) page = next;
                unavailable ||= !!next.reason;
            }
            if (live(controller.signal, sessionIdentity)) setRead({scope, page, error: !page && unavailable ? true : undefined});
        }
        load().catch(error => { if (live(controller.signal, sessionIdentity) && error?.code !== 'ERR_CANCELED') setRead({scope, error: true}); });
        return () => controller.abort();
    }, [schema, table, key, scope]);
    const current = read?.scope === scope ? read : undefined;
    useExplorerErrorToast(current?.error ? 'Unable to read relationships.' : undefined, 'Relationships unavailable', () => setRetry(n => n + 1));
    return { ...current, retry: () => setRetry(n => n + 1) };
}

function LinkedProperties({ schema, table, identity }: {schema: string; table: string; identity: string}) {
    const [row, setRow] = useState<Awaited<ReturnType<typeof fetchExplorerRowByKey>>>();
    const [layout, setLayout] = useState<ResultLayout | null>();
    const [failed, setFailed] = useState(false);
    const sessionIdentity = getSessionState().identity;
    useEffect(() => {
        const read = new AbortController();
        setFailed(false); setRow(undefined); setLayout(undefined);
        Promise.all([fetchExplorerRowByKey(schema, table, JSON.parse(identity), read.signal), readLayouts(layoutPath(schema, table), read.signal)])
            .then(([result, layouts]) => { if (live(read.signal, sessionIdentity)) { setRow(result); setLayout(layouts.personal.layout ?? layouts.default.layout); } })
            .catch(error => { if (live(read.signal, sessionIdentity) && error?.code !== 'ERR_CANCELED') setFailed(true); });
        return () => read.abort();
    }, [schema, table, identity, sessionIdentity]);
    useExplorerErrorToast(failed ? 'This record could not be read. Its access or identity may have changed.' : undefined, 'Related record unavailable');
    if (failed) return null;
    if (!row) return <p role="status">Loading record properties…</p>;
    const previewRow = { record: row.row, identity: row.identity, key: identity, title: '', columnTypes: row.columnTypes };
    const presentations = Object.fromEntries((layout?.columns ?? []).map(column => [column.field, column.presentation ?? 'value']));
    return <ExplorerValueProvider schema={schema} table={table}>
        <ExplorerLinkedProperties row={previewRow} fields={explorerFields(previewRow, Object.keys(row.row), undefined, presentations)} />
    </ExplorerValueProvider>;
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
    const sessionIdentity = getSessionState().identity;
    useEffect(() => {
        if (initialPage && relation === initialPage.selected && offset === initialPage.offset && retry === 0) {
            setPage(initialPage); setLoading(false); return;
        }
        const controller = new AbortController();
        setError(''); setLoading(true); setExpanded('');
        fetchExplorerRowLinks(schema, table, JSON.parse(key), relation, offset, controller.signal)
            .then(value => { if (live(controller.signal, sessionIdentity)) { setPage(value); setLoading(false); } })
            .catch(error => { if (live(controller.signal, sessionIdentity) && error?.code !== 'ERR_CANCELED') { setError('Unable to read related records.'); setLoading(false); } });
        return () => controller.abort();
    }, [schema, table, key, relation, offset, retry, initialPage, sessionIdentity]);
    useExplorerErrorToast(error, 'Unable to read related records', () => setRetry(v => v + 1));
    if (error) return null;
    if (!page) return <p role="status">Looking up related records…</p>;
    const selected = page.relations.find(item => item.id === page.selected);
    if (page.relations.length && !selected) return null;
    return <ExplorerRelationshipsView loading={loading} selectedId={page.selected}
        relations={page.relations.map(item => ({ id: item.id, name: item.name, direction: item.direction,
            fieldMapping: { label: item.kind === 'foreign_key' ? 'Foreign key' : 'Field mapping',
                fields: item.fields.map((field, index) => ({ source: field, target: item.targetFields[index] })) } }))}
        records={selected ? page.identities.map(identity => {
            const identityKey = JSON.stringify(identity);
            return { key: identityKey,
                link: <ExplorerRecordLink schema={selected.schema} table={selected.table} identity={identity}>
                    {Object.entries(identity).map(([name, value]) => `${name} = ${value}`).join(' · ')}
                </ExplorerRecordLink>,
                properties: <LinkedProperties key={identityKey} schema={selected.schema} table={selected.table} identity={identityKey} /> };
        }) : []}
        emptyReason={page.reason} expanded={expanded} onToggle={key => setExpanded(current => current === key ? '' : key)}
        onSelect={id => { setOffset(0); setRelation(id); }} offset={offset} hasMore={page.hasMore} pageLimit={500}
        onPage={offset => { setRelation(page.selected); setOffset(offset); }} />;
}

export default function ExplorerRowLinks(props: Props) {
    return <LinkPage key={JSON.stringify([props.schema, props.table, props.primaryKey, getSessionState().identity])} {...props} />;
}
