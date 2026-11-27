import { LoadingIndicator } from '@/components/ui/loading';
import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Button } from '@/components/ui';
import { TableOutlined } from '@/components/ui/icons';
import { fetchRowPreviewPolicy, queryExplorerPreview } from '../../services/api';
import type { ExplorerMappedProperty, ExplorerPropertyMapping } from '../../services/explorerProperties';
import { listOntologyRecords } from '../../services/ontology';
import { PropertyBadges } from './ExplorerPropertyInspector';
import PropertyTypeIcon from './PropertyTypeIcon';
import type { EtlTableColumn } from '../../services/types';
import useExplorerErrorToast from './useExplorerErrorToast';

type Props = {
    schema: string; table: string; columns: EtlTableColumn[]; mapping: ExplorerPropertyMapping;
    selected?: ExplorerMappedProperty; onSelect: (id: string) => void; onAutomap?: () => void;
};

const cell = (value: unknown) => value == null ? '—' : typeof value === 'object' ? JSON.stringify(value) : String(value);

export default function ExplorerMappingPreview({schema, table, columns, mapping, selected, onSelect, onAutomap}: Props) {
    const [showMapped, setShowMapped] = useState(true);
    const [rows, setRows] = useState<Record<string,unknown>[]>();
    const [error, setError] = useState('');
    const [restricted, setRestricted] = useState(false);
    const [retry, setRetry] = useState(0);
    const semanticId = mapping.sourceDefinitionId;
    useEffect(() => {
        const request = new AbortController();
        setRows(undefined); setError(''); setRestricted(false);
        const read = async () => {
            const policy = await fetchRowPreviewPolicy(schema, table, request.signal);
            if (request.signal.aborted) return;
            if (policy.mode !== 'classic') { setRestricted(true); return; }
            if (semanticId) {
                const result = await listOntologyRecords(semanticId, 5, 0);
                return result.items.map(item => item.properties);
            }
            const result = await queryExplorerPreview(schema, table, {limit: 5, offset: 0, filters: [], sorts: [], withIdentity: true}, request.signal);
            return result.rows;
        };
        read().then(result => { if (!request.signal.aborted && result) setRows(result); })
            .catch(() => { if (!request.signal.aborted) setError('The authorized data preview could not be loaded.'); });
        return () => request.abort();
    }, [schema, table, semanticId, retry]);
    useExplorerErrorToast(error, 'Unable to load data preview', () => setRetry(v => v + 1));
    const mapped = new Map(mapping.properties.filter(p => p.sourceColumn).map(p => [p.sourceColumn, p]));
    // Semantic objects expose only the fields returned by their authorized records endpoint.
    const allowed = new Set(rows?.flatMap(row => Object.keys(row)));
    const fields = columns.filter(c => (!semanticId || allowed.has(mapped.get(c.name)?.id || '')) && (showMapped || !mapped.has(c.name))).map(c => ({
        key: semanticId ? mapped.get(c.name)!.id : c.name, name: c.name, type: c.type, primary:c.primary, property: mapped.get(c.name),
    }));
    return <section className="flow-foundry-property-preview physical-property-preview" aria-label="Data and column mappings">
        <div className="flow-foundry-property-preview-tools">
            <strong title={`${schema}.${table}`}><TableOutlined /> {table}</strong>
            <label><input type="checkbox" checked={showMapped} onChange={e => setShowMapped(e.target.checked)}/> Show mapped columns</label>
            {onAutomap && <Button size="small" disabled={columns.every(c => mapped.has(c.name))} onClick={onAutomap}>Automap all</Button>}
        </div>
        {error ? null
            : restricted ? <p>This table requires an indexed preview. <Link to={'/ontology/explorer?' + new URLSearchParams({schema,table})}>Open indexed preview</Link></p>
            : !rows ? <p role="status"><LoadingIndicator size={20}>Loading up to five records…</LoadingIndicator></p> : !rows.length ? <p>No records in this sample.</p> : !fields.length ? <p>{showMapped ? 'No preview columns available.' : 'No unmapped source columns.'}</p>
            : <div className="flow-foundry-property-table-wrap"><table aria-label="Data preview">
                <thead><tr><th scope="col">#</th>{fields.map(f => <th key={f.key} scope="col" className={f.property?.id === selected?.id && selected ? 'is-selected' : ''}>
                    <div className="physical-preview-column-heading"><PropertyTypeIcon type={f.type}/>
                        {f.property ? <button type="button" className="physical-preview-column-name" title={f.property.name === f.name ? f.name : `${f.name} (${f.property.name})`} onClick={() => onSelect(f.property!.id)}><code>{f.name}</code>
                            {f.property.name !== f.name && <span>({f.property.name})</span>}</button> : <code>{f.name}</code>}
                        {f.property ? <PropertyBadges property={f.property} title={mapping.titleProperty}/>
                            : f.primary && <span className="flow-foundry-property-tag is-key">Primary key</span>}
                    </div>
                </th>)}</tr></thead><tbody>{rows.map((r, i) => <tr key={i}><th scope="row">{i+1}</th>
                    {fields.map(f => { const value = cell(r[f.key]); return <td key={f.key} title={value.slice(0,300)}>{value.slice(0,160)}</td>; })}
                </tr>)}</tbody>
            </table></div>}
    </section>;
}
