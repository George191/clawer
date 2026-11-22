import { useEffect, useState } from 'react';
import { Button } from '@/components/ui';
import { PlusOutlined, SettingOutlined } from '@/components/ui/icons';
import { ExplorerTypeIcon, explorerFields, explorerRowTitle } from './ExplorerObjectOverview';
import { ExplorerValueProvider } from './ExplorerValue';
import { fetchExplorerRowByKey, fetchExplorerRowLinks, unlinkExplorerRowLink, type ExplorerRowLinkPage } from '../../services/api';
import { ExplorerRecordLink } from './ExplorerRecordLink';
import { layoutPath, readLayouts, type ResultLayout } from '../../services/explorerLayouts';
import { fetchExplorerProperties, type ExplorerPropertyMapping } from '../../services/explorerProperties';
import ExplorerRelationshipsView, { ExplorerLinkedProperties } from './ExplorerRelationshipsView';
import useExplorerErrorToast from './useExplorerErrorToast';
import { getSessionState } from '../../services/session';
import StorageRelationshipTypes from './StorageRelationshipTypes';
import StorageLinkObjectPicker from './StorageLinkObjectPicker';

type Props = { schema: string; table: string; primaryKey: Record<string,unknown>; initialPage?: ExplorerRowLinkPage; revision?: number };
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
            const page = first.relations.length || first.canManage ? first : undefined;
            if (live(controller.signal, sessionIdentity)) setRead({scope, page, error: !page && first.reason ? true : undefined});
        }
        load().catch(error => { if (live(controller.signal, sessionIdentity) && error?.code !== 'ERR_CANCELED') setRead({scope, error: true}); });
        return () => controller.abort();
    }, [schema, table, key, scope]);
    const current = read?.scope === scope ? read : undefined;
    useExplorerErrorToast(current?.error ? 'Unable to read relationships.' : undefined, 'Relationships unavailable', () => setRetry(n => n + 1));
    return { ...current, retry: () => setRetry(n => n + 1) };
}

function LinkedProperties({ schema, table, row, layout, presentation }: {schema: string; table: string;
    row: Awaited<ReturnType<typeof fetchExplorerRowByKey>>; layout: ResultLayout | null; presentation: ExplorerPropertyMapping}) {
    const previewRow = { record: row.row, identity: row.identity, key: JSON.stringify(row.identity), title: '', columnTypes: row.columnTypes };
    const presentations = Object.fromEntries((layout?.columns ?? []).map(column => [column.field, column.presentation ?? 'value']));
    return <ExplorerValueProvider schema={schema} table={table}>
        <ExplorerLinkedProperties row={previewRow} fields={explorerFields(previewRow, Object.keys(row.row), presentation, presentations)} />
    </ExplorerValueProvider>;
}

function LinkPage({ schema, table, primaryKey, initialPage, revision = 0 }: Props) {
    const [relation, setRelation] = useState<string | null>(initialPage?.selected ?? null);
    const [offset, setOffset] = useState(0);
    const [retry, setRetry] = useState(0);
    const [read, setRead] = useState<{ page: ExplorerRowLinkPage; rows: Awaited<ReturnType<typeof fetchExplorerRowByKey>>[];
        presentation?: ExplorerPropertyMapping; layout?: ResultLayout | null }>();
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [expanded, setExpanded] = useState('');
    const [managementOpen, setManagementOpen] = useState(false);
    const [linkPickerOpen, setLinkPickerOpen] = useState(false);
    const key = JSON.stringify(primaryKey);
    const sessionIdentity = getSessionState().identity;
    useEffect(() => {
        const controller = new AbortController();
        setError(''); setLoading(true); setExpanded('');
        fetchExplorerRowLinks(schema, table, JSON.parse(key), relation, offset, controller.signal).then(async page => {
            if (!live(controller.signal, sessionIdentity)) return;
            const target = page.relations.find(item => item.id === page.selected);
            if (!target || !page.links.length) return { page, rows: [] };
            const [presentation, layouts, rows] = await Promise.all([
                fetchExplorerProperties(target.schema, target.table, controller.signal),
                readLayouts(layoutPath(target.schema, target.table), controller.signal),
                Promise.all(page.links.map(item => fetchExplorerRowByKey(target.schema, target.table, item.identity, controller.signal))),
            ]);
            return { page, rows, presentation, layout: layouts.personal.layout ?? layouts.default.layout };
        }).then(value => { if (value && live(controller.signal, sessionIdentity)) { setRead(value); setLoading(false); } })
            .catch(error => { if (live(controller.signal, sessionIdentity) && error?.code !== 'ERR_CANCELED') { setError('Unable to read related records.'); setLoading(false); } });
        return () => controller.abort();
    }, [schema, table, key, relation, offset, retry, revision, sessionIdentity]);
    useExplorerErrorToast(error, 'Unable to read related records', () => setRetry(v => v + 1));
    if (error) return null;
    const page = read?.page;
    if (!page) return <p role="status">Looking up related records…</p>;
    const selected = page.relations.find(item => item.id === page.selected);
    if (page.relations.length && !selected) return null;
    return <ExplorerRelationshipsView loading={loading} selectedId={page.selected}
        management={page.canManage ? <>
            <Button size="small" icon={<PlusOutlined />} disabled={loading || !selected} onClick={() => setLinkPickerOpen(true)}>Add link</Button>
            <Button type="text" size="small" className="explorer-links-manage" icon={<SettingOutlined />} aria-label="Manage relationship types"
                title="Manage relationship types" disabled={loading} onClick={() => setManagementOpen(true)} />
            <StorageRelationshipTypes schema={schema} table={table} open={managementOpen} initialRelationId={selected?.relationId}
                onClose={() => setManagementOpen(false)} onChanged={(id, direction) => { setRelation(selected?.relationId === id ? page.selected : `${id}:${direction}`); setRetry(value => value + 1); }} />
            {selected && <StorageLinkObjectPicker schema={schema} table={table} sourceKey={primaryKey} relation={selected} open={linkPickerOpen}
                onClose={() => setLinkPickerOpen(false)} onCreated={() => { setLinkPickerOpen(false); setRelation(page.selected); setOffset(0); setRetry(value => value + 1); }} />}
        </> : undefined}
        relations={page.relations.map(item => ({ id: item.id, name: item.name, direction: item.direction,
            fieldMapping: item.fields.length ? { label: 'Import rule',
                fields: item.fields.map((field, index) => ({ source: field, target: item.targetFields[index] })) } : undefined }))}
        records={selected ? page.links.map((record, index) => {
            const { identity } = record;
            const identityKey = JSON.stringify(identity);
            const row = read!.rows[index], presentation = read!.presentation!;
            const label = explorerRowTitle(row.row, identity, presentation.titleProperty, presentation.primaryKey);
            return { key: record.linkId, typeName: presentation.displayName, icon: <ExplorerTypeIcon dataType={presentation.dataType} table={selected.table} />,
                link: <ExplorerRecordLink schema={selected.schema} table={selected.table} identity={identity}>
                    {label}
                </ExplorerRecordLink>,
                properties: <LinkedProperties key={identityKey} schema={selected.schema} table={selected.table} row={row} presentation={presentation} layout={read!.layout ?? null} />,
                unlink: record.canUnlink ? {
                    label,
                    description: <>Remove the “{selected.name}” relationship to <strong>{label}</strong>? Both records and their source fields remain available.</>,
                    run: async signal => {
                        await unlinkExplorerRowLink(schema, table, record.linkId, { key: JSON.parse(key), relationId: selected.relationId,
                            direction: selected.direction, targetKey: identity, expectedVersion: record.version }, signal);
                        if (live(signal, sessionIdentity)) { setExpanded(''); setOffset(0); setRelation(page.selected); setRetry(value => value + 1); }
                    },
                } : undefined };
        }) : []}
        emptyReason={page.reason} expanded={expanded} onToggle={key => setExpanded(current => current === key ? '' : key)}
        onSelect={id => { setOffset(0); setRelation(id); }} offset={offset} hasMore={page.hasMore} pageLimit={500}
        onPage={offset => { setRelation(page.selected); setOffset(offset); }} />;
}

export default function ExplorerRowLinks(props: Props) {
    return <LinkPage key={JSON.stringify([props.schema, props.table, props.primaryKey, getSessionState().identity])} {...props} />;
}
