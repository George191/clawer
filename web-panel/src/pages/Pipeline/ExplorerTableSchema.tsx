import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { canonColumn } from './ColumnLineageReferences';
import { INDEX_PREVIEW_SCHEMA, INDEX_PREVIEW_TABLE } from './SandboxIndexPreview';
import { Button, Input } from '@/components/ui';
import { SearchOutlined } from '@/components/ui/icons';
import { fetchEtlTableColumns } from '../../services/api';
import type { EtlTableColumn } from '../../services/types';

type Props = { schema: string; table: string };

export default function ExplorerTableSchema({ schema, table }: Props) {
    const [columns, setColumns] = useState<EtlTableColumn[]>();
    const [search, setSearch] = useState('');
    const [error, setError] = useState('');
    const [refresh, setRefresh] = useState(0);
    useEffect(() => {
        const controller = new AbortController();
        setColumns(undefined); setError('');
        fetchEtlTableColumns(schema, table, controller.signal)
            .then((result) => {
                if (!controller.signal.aborted) setColumns([...result].sort((a, b) => a.ordinalPosition - b.ordinalPosition));
            })
            .catch((failure) => {
                if (controller.signal.aborted) return;
                const detail = failure?.response?.data?.detail;
                setError(typeof detail === 'string' ? detail : failure?.message || 'Could not load column metadata');
            });
        return () => controller.abort();
    }, [schema, table, refresh]);

    const referenceEligible = /^ts_[a-z0-9_]{1,60}$/.test(schema) && schema !== 'ts_meta' && !schema.endsWith('_hist') &&
        (schema !== INDEX_PREVIEW_SCHEMA || table !== INDEX_PREVIEW_TABLE) && canonColumn(table);
    const term = search.trim().toLowerCase();
    const matches = columns?.filter((column) => !term || [column.name, column.type, column.comment, column.primary ? 'primary key pk' : '', !column.nullable ? 'not null' : 'nullable']
        .some((text) => text?.toLowerCase().includes(term)));
    return (
        <section className="flow-schema-details flow-explorer-column-sheet" aria-label="Table schema">
            <header>
                <div><h3>Columns</h3><p>Column definitions from the PostgreSQL catalog.</p></div>
                <div className="flow-schema-column-tools">
                    <Input size="small" aria-label="Search columns" placeholder="Search columns" prefix={<SearchOutlined />} value={search} allowClear onChange={(event) => setSearch(event.target.value)} />
                    {columns && <span>{term ? `${matches?.length} of ${columns.length}` : columns.length} columns</span>}
                </div>
            </header>
            {error ? <div className="flow-schema-state" role="alert"><strong>Column metadata unavailable</strong><p>{error}</p><Button size="small" onClick={() => setRefresh((count) => count + 1)}>Retry metadata</Button></div>
                : !columns ? <div className="flow-schema-state" role="status">Loading column metadata…</div>
                : !columns.length ? <div className="flow-schema-state" role="status">No columns returned for this table.</div>
                : !matches?.length ? <div className="flow-schema-state" role="status">No columns match your search.</div>
                : <>
                    <p className="flow-schema-scroll-hint">Scroll sideways to see all columns.</p>
                    <div className="flow-schema-column-grid" role="region" aria-label="Column definitions" tabIndex={0}><table>
                    <thead><tr><th scope="col">Name</th><th scope="col">PostgreSQL type</th><th scope="col">Constraints</th><th scope="col">Description</th></tr></thead>
                    <tbody>{matches.map((column) => <tr key={column.name}>
                        <td><code title={column.name}>{column.name}</code>{referenceEligible && canonColumn(column.name) && <>
                            {' '}<Link className="flow-evidence-lineage-link" aria-label={`Open column references: ${column.name}`}
                                to={`/ontology/lineage?${new URLSearchParams({ schema, table, tableLineage: '1', column: column.name })}`}>Column references</Link>
                        </>}</td>
                        <td><code>{column.type}</code></td>
                        <td><div className="flow-schema-constraints">
                            {column.primary && <span className="flow-schema-key" title={`Primary key${column.primaryPosition ? `, position ${column.primaryPosition}` : ''}`} aria-label="Primary key">PK</span>}
                            {!column.nullable && <span>NOT NULL</span>}
                            {!column.primary && column.nullable && <span className="flow-schema-no-value">—</span>}
                        </div></td>
                        <td className={column.comment ? '' : 'flow-schema-no-value'}>{column.comment || '—'}</td>
                    </tr>)}</tbody>
                </table></div></>}
        </section>
    );
}
