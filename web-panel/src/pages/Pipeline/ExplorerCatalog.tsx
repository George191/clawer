import { useEffect, useId, useRef, useState, type ReactNode } from 'react';
import { Button, Input } from '@/components/ui';
import { AppstoreOutlined, DatabaseOutlined, DownOutlined, FolderOutlined, ReloadOutlined, RightOutlined, SearchOutlined, TableOutlined } from '@/components/ui/icons';
import type { LayerNode, LayerTable } from '../../services/types';
import { listPublishedOntologyTypes, ontologyError, type PublishedOntologyPage } from '../../services/ontology';
import { getSessionState } from '../../services/session';
import SemanticTypeGlyph from './SemanticTypeGlyph';
import './explorer-catalog.css';

type PhysicalCatalogProps = {
    published?: never;
    onDismiss?: () => void;
    schemas: LayerNode[];
    tables: LayerTable[];
    selectedSchema: string;
    selectedConnection?: string;
    selectedTable: string;
    catalogLoaded: boolean;
    tablesLoading: boolean;
    requestError: string;
    overlayOpen: boolean;
    onCreateTable?: () => void;
    onRefresh: () => void;
    onSchema: (schema: string, connectionId?: string) => void;
    onTable: (table: LayerTable) => void;
};

export type PublishedCatalogPickerProps = {
    published: { selectedId: string; onSelect: (definitionId: string) => void };
    overlayOpen: boolean;
    onDismiss: () => void;
};
type Props = PhysicalCatalogProps | PublishedCatalogPickerProps;
type CatalogFrameProps = {
    published?: boolean;
    overlayOpen: boolean;
    onDismiss?: () => void;
    onRefresh: () => void;
    search: string;
    onSearch: (value: string) => void;
    createButton?: ReactNode;
    children: ReactNode;
};

function CatalogFrame({ published, overlayOpen, onDismiss, onRefresh, search, onSearch, createButton, children }: CatalogFrameProps) {
    const [mobileOpen, setMobileOpen] = useState(false);
    const catalogRef = useRef<HTMLElement>(null);
    const searchId = useId();
    const contentId = useId();
    useEffect(() => {
        if (overlayOpen) catalogRef.current?.querySelector('input')?.focus();
    }, [overlayOpen]);
    useEffect(() => {
        if (!overlayOpen || !onDismiss) return;
        const outside = (event: PointerEvent) => {
            if (event.target instanceof Node && !catalogRef.current?.contains(event.target)
                && !(event.target instanceof Element && event.target.closest('.wb-catalog-toggle'))) onDismiss();
        };
        const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') onDismiss(); };
        document.addEventListener('pointerdown', outside);
        document.addEventListener('keydown', escape);
        return () => { document.removeEventListener('pointerdown', outside); document.removeEventListener('keydown', escape); };
    }, [overlayOpen, onDismiss]);
    return <aside ref={catalogRef} className={`flow-layer-tree flow-ontology-schema-sidebar flow-explorer-catalog${mobileOpen ? ' is-mobile-open' : ''}`} aria-label="Explorer catalog">
        <header>
            <div className="flow-catalog-title">{published ? <AppstoreOutlined /> : <DatabaseOutlined />}<strong>{published ? 'Object types' : 'Catalog'}</strong></div>
            <button type="button" className="flow-catalog-mobile-toggle" aria-expanded={mobileOpen} aria-controls={contentId} onClick={() => setMobileOpen(!mobileOpen)}>Browse catalog <DownOutlined /></button>
            <button type="button" className="flow-catalog-refresh" aria-label="Refresh catalog" title="Refresh catalog" onClick={onRefresh}><ReloadOutlined /></button>
        </header>
        <div className="flow-catalog-content" id={contentId} onClick={event => {
            if (event.target instanceof Element && event.target.closest('[data-catalog-select]')) setMobileOpen(false);
        }}>
            {createButton}
            <Input id={searchId} className="flow-ontology-schema-search" aria-label="Search catalog" placeholder={published ? 'Search object types' : 'Search catalog'}
                prefix={<label htmlFor={searchId}><SearchOutlined /></label>} value={search} onChange={event => onSearch(event.target.value)} allowClear />
            {children}
        </div>
    </aside>;
}

export default function ExplorerCatalog(props: Props) {
    return props.published ? <PublishedCatalogPicker {...props} /> : <PhysicalCatalog {...props} />;
}

function PhysicalCatalog({ schemas, tables, selectedSchema, selectedConnection, selectedTable, catalogLoaded, tablesLoading, requestError, overlayOpen, onDismiss, onCreateTable, onRefresh, onSchema, onTable }: PhysicalCatalogProps) {
    const [search, setSearch] = useState('');
    const [collapsedSchema, setCollapsedSchema] = useState('');
    const keyword = search.trim().toLowerCase();
    const matchingTables = tables.filter((table) => !keyword || `${selectedSchema}.${table.name}`.toLowerCase().includes(keyword));
    const visibleSchemas = schemas.filter((schema) => !keyword
        || `${schema.key} ${schema.label} ${schema.alias || ''} ${schema.database || ''}`.toLowerCase().includes(keyword)
        || (schema.key === selectedSchema && matchingTables.length > 0));

    return <CatalogFrame overlayOpen={overlayOpen} onDismiss={onDismiss} onRefresh={onRefresh} search={search} onSearch={setSearch}
        createButton={onCreateTable && <Button className="flow-catalog-create-table" size="small" title={`Create a storage table in ${selectedSchema}`} onClick={onCreateTable}>New storage table</Button>}>
                {!!keyword && <p className="flow-catalog-scope">Searches schema names and tables in {selectedSchema || 'the open schema'}.</p>}
                <ul className="flow-catalog-list" aria-label="Schemas">
                    {visibleSchemas.map((schema) => {
                        const selected = schema.key === selectedSchema && schema.connectionId === selectedConnection;
                        const expanded = selected && (!!keyword || collapsedSchema !== schema.key);
                        return (
                            <li key={JSON.stringify([schema.connectionId, schema.key])}>
                                <button type="button" className={`flow-catalog-schema${selected ? ' is-selected' : ''}`} aria-expanded={expanded} onClick={() => {
                                    if (selected) setCollapsedSchema(expanded ? schema.key : '');
                                    else { setCollapsedSchema(''); onSchema(schema.key, schema.connectionId); }
                                }}>
                                    {expanded ? <DownOutlined className="flow-catalog-disclosure" /> : <RightOutlined className="flow-catalog-disclosure" />}
                                    <FolderOutlined className="flow-catalog-resource-icon" />
                                    <strong>{schema.alias || schema.database || schema.label}</strong>
                                    <code>{schema.key}</code>
                                    <span className="flow-catalog-count">{schema.tables ?? 0}</span>
                                </button>
                                {expanded && <ul className="flow-catalog-tables" aria-label={`Tables in ${schema.key}`}>
                                    {tablesLoading ? <li className="flow-catalog-empty" role="status">Loading tables…</li> : matchingTables.length ? matchingTables.map((table) => (
                                        <li key={table.name}>
                                            <button type="button" title={table.name} aria-current={table.name === selectedTable ? 'page' : undefined} data-catalog-select onClick={() => { onDismiss?.(); onTable(table); }}>
                                                <TableOutlined className="flow-catalog-resource-icon" />
                                                <span>{table.name}</span>
                                            </button>
                                        </li>
                                    )) : <li className="flow-catalog-empty">{keyword ? 'No matching tables in this schema' : requestError ? 'Tables are unavailable' : 'No tables in this schema'}</li>}
                                </ul>}
                            </li>
                        );
                    })}
                    {!visibleSchemas.length && <li className="flow-catalog-empty" role="status">{!catalogLoaded && !requestError ? 'Loading catalog…' : keyword ? 'No matches in the current search scope' : 'No schemas available'}</li>}
                </ul>
    </CatalogFrame>;
}

const publishedPageSize = 25;
const maxPublishedOffset = 100_000;
function PublishedCatalogPicker({ published, overlayOpen, onDismiss }: PublishedCatalogPickerProps) {
    const [search, setSearch] = useState('');
    const [offset, setOffset] = useState(0);
    const [version, setVersion] = useState(0);
    const [page, setPage] = useState<{ data: PublishedOntologyPage; keyword: string; offset: number }>();
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');
    const keyword = search.trim();
    const identity = useRef(getSessionState().identity).current;
    useEffect(() => {
        if (!overlayOpen) return;
        const request = new AbortController();
        setLoading(true); setError('');
        const timer = window.setTimeout(() => {
            listPublishedOntologyTypes(publishedPageSize, offset, keyword, undefined, request.signal).then(data => {
                if (!request.signal.aborted && identity === getSessionState().identity) setPage({ data, keyword, offset });
            }, failure => {
                if (!request.signal.aborted && identity === getSessionState().identity) setError(ontologyError(failure, 'Unable to load published object types.'));
            }).finally(() => {
                if (!request.signal.aborted && identity === getSessionState().identity) setLoading(false);
            });
        }, keyword ? 250 : 0);
        return () => { request.abort(); window.clearTimeout(timer); };
    }, [overlayOpen, keyword, offset, version, identity]);
    const currentPage = page?.keyword === keyword && page.offset === offset ? page.data : undefined;
    const pending = loading || !currentPage && !error;
    return <CatalogFrame published overlayOpen={overlayOpen} onDismiss={onDismiss} onRefresh={() => setVersion(value => value + 1)} search={search}
        onSearch={value => { setSearch(value); setOffset(0); }}>
        {error && <p className="flow-catalog-error" role="alert">{error}</p>}
        <ul className="flow-catalog-list flow-catalog-published" aria-label="Published object types" aria-busy={pending}>
            {pending && <li className="flow-catalog-empty" role="status">Loading object types…</li>}
            {currentPage?.items.map(item => <li key={item.id}>
                <button type="button" data-catalog-select title={`${item.definition.name} · ${item.definition.id}`} aria-current={item.id === published.selectedId ? 'page' : undefined}
                    disabled={pending} onClick={() => { onDismiss(); published.onSelect(item.id); }}>
                    <SemanticTypeGlyph definition={item.definition} className="flow-catalog-type-glyph" />
                    <span><strong>{item.definition.name}</strong><code>{item.definition.id}</code></span>
                </button>
            </li>)}
            {!pending && !error && !currentPage?.items.length && <li className="flow-catalog-empty" role="status">{keyword ? 'No matching object types' : 'No published object types available'}</li>}
        </ul>
        {(offset > 0 || currentPage?.hasMore) && <nav className="flow-catalog-pagination" aria-label="Object types pagination">
            <button type="button" disabled={pending || !offset} onClick={() => setOffset(value => Math.max(0, value - publishedPageSize))}>Previous</button>
            <span>{currentPage?.items.length ? `${offset + 1}–${offset + currentPage.items.length}` : '0 shown'}</span>
            <button type="button" disabled={pending || !currentPage?.hasMore || offset + publishedPageSize > maxPublishedOffset} onClick={() => setOffset(value => value + publishedPageSize)}>Next</button>
        </nav>}
    </CatalogFrame>;
}
