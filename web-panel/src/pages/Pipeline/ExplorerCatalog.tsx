import { useState } from 'react';
import type { CSSProperties } from 'react';
import { Button, Input } from '@/components/ui';
import { DatabaseOutlined, DownOutlined, ReloadOutlined, RightOutlined, SearchOutlined } from '@/components/ui/icons';
import type { LayerNode, LayerTable } from '../../services/types';
import { TemplateGlyph, inferSiteKind } from '../AICollect/WorkspaceDock';
import { schemaColor } from './OntologyExplorer';

type Props = {
    schemas: LayerNode[];
    tables: LayerTable[];
    selectedSchema: string;
    selectedTable: string;
    catalogLoaded: boolean;
    tablesLoading: boolean;
    requestError: string;
    onCreateTable?: () => void;
    onRefresh: () => void;
    onSchema: (schema: string) => void;
    onTable: (table: LayerTable) => void;
};

export default function ExplorerCatalog({ schemas, tables, selectedSchema, selectedTable, catalogLoaded, tablesLoading, requestError, onCreateTable, onRefresh, onSchema, onTable }: Props) {
    const [search, setSearch] = useState('');
    const [collapsedSchema, setCollapsedSchema] = useState('');
    const [mobileOpen, setMobileOpen] = useState(false);
    const keyword = search.trim().toLowerCase();
    const matchingTables = tables.filter((table) => !keyword || `${selectedSchema}.${table.name}`.toLowerCase().includes(keyword));
    const visibleSchemas = schemas.filter((schema) => !keyword
        || `${schema.key} ${schema.label} ${schema.alias || ''} ${schema.database || ''}`.toLowerCase().includes(keyword)
        || (schema.key === selectedSchema && matchingTables.length > 0));

    return (
        <aside className={`flow-layer-tree flow-ontology-schema-sidebar flow-explorer-catalog${mobileOpen ? ' is-mobile-open' : ''}`} aria-label="Explorer catalog">
            <header>
                <div className="flow-catalog-title"><DatabaseOutlined /><strong>Catalog</strong></div>
                <button type="button" className="flow-catalog-mobile-toggle" aria-expanded={mobileOpen} aria-controls="flow-catalog-content" onClick={() => setMobileOpen(!mobileOpen)}>
                    Browse catalog <DownOutlined />
                </button>
                <button type="button" className="flow-catalog-refresh" aria-label="Refresh catalog" title="Refresh catalog" onClick={onRefresh}><ReloadOutlined /></button>
            </header>
            <div className="flow-catalog-content" id="flow-catalog-content">
                {onCreateTable && <Button className="flow-catalog-create-table" size="small" title={`Create a storage table in ${selectedSchema}`} onClick={onCreateTable}>New storage table</Button>}
                <Input className="flow-ontology-schema-search" aria-label="Search catalog" placeholder="Search catalog" prefix={<SearchOutlined />} value={search} onChange={(event) => setSearch(event.target.value)} allowClear />
                {!!keyword && <p className="flow-catalog-scope">Searches schema names and tables in {selectedSchema || 'the open schema'}.</p>}
                <ul className="flow-catalog-list" aria-label="Schemas">
                    {visibleSchemas.map((schema) => {
                        const selected = schema.key === selectedSchema;
                        const expanded = selected && (!!keyword || collapsedSchema !== schema.key);
                        return (
                            <li key={schema.key} style={{ '--schema-color': schemaColor(schema.key) } as CSSProperties}>
                                <button type="button" className={`flow-catalog-schema${selected ? ' is-selected' : ''}`} aria-expanded={expanded} onClick={() => {
                                    if (selected) setCollapsedSchema(expanded ? schema.key : '');
                                    else { setCollapsedSchema(''); onSchema(schema.key); }
                                }}>
                                    {expanded ? <DownOutlined /> : <RightOutlined />}
                                    <i className="flow-schema-color-dot" aria-hidden="true" />
                                    <strong>{schema.alias || schema.label}</strong>
                                    <code>{schema.key}</code>
                                    <span className="flow-catalog-count">{schema.tables ?? 0}</span>
                                </button>
                                {expanded && <ul className="flow-catalog-tables" aria-label={`Tables in ${schema.key}`}>
                                    {tablesLoading ? <li className="flow-catalog-empty" role="status">Loading tables…</li> : matchingTables.length ? matchingTables.map((table) => (
                                        <li key={table.name}>
                                            <button type="button" title={table.name} aria-current={table.name === selectedTable ? 'page' : undefined} onClick={() => { onTable(table); setMobileOpen(false); }}>
                                                <TemplateGlyph kind={inferSiteKind(table.dataType || '', table.name)} source={table.name} />
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
            </div>
        </aside>
    );
}
