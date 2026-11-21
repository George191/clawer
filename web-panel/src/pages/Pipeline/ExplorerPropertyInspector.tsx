import PropertyTypeIcon from './PropertyTypeIcon';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { DeleteOutlined, PlusOutlined } from '@/components/ui/icons';
import type { EtlTableColumn } from '../../services/types';
import type { ExplorerMappedProperty as Property, ExplorerPropertyMapping as Mapping } from '../../services/explorerProperties';
import { canonColumn } from './ColumnLineageReferences';
import ExplorerColumnPlannerStatistic from './ExplorerColumnPlannerStatistic';
import { columnPresentations } from './ExplorerResultLayout';
import { INDEX_PREVIEW_SCHEMA, INDEX_PREVIEW_TABLE } from './SandboxIndexPreview';

const tabs = ['General','Display','Interaction','Details','Advanced'] as const;
export const businessTypes = ['string','integer','number','boolean','date','datetime','json'] as const;
export const readable = (value: string) => ({dateTime:'Date and time',timeSeries:'Time series',geoJson:'Geographic shape',default:'No formatting'}[value] || value.charAt(0).toUpperCase()+value.slice(1));
export function propertyFormatters(type: Property['type']): Property['formatter'][] {
    if (type==='integer' || type==='number') return ['default','number','percent'];
    if (type==='date' || type==='datetime') return ['default','date','dateTime'];
    return ['default'];
}
export function PropertyBadges({property, title}: {property: Property; title: string|null}) {
    return <>{property.primaryKeyPosition != null && <span className="flow-foundry-property-tag is-key">Primary key</span>}
        {property.sourceColumn && property.sourceColumn === title && <span className="flow-foundry-property-tag is-title">Title</span>}</>;
}

type Props = {
    mapping: Mapping; property: Property; columns: EtlTableColumn[]; editable: boolean; isNew: boolean; identity: string;
    onPatch: (patch: Partial<Property>) => void; onMap: (column: string|null) => void;
    onTitle: (enabled: boolean) => void; onRemove: () => void; onGroup: (group: string|null) => void;
};
export default function ExplorerPropertyInspector({mapping,property:p,columns,editable,isNew,identity,onPatch,onMap,onTitle,onRemove,onGroup}: Props) {
    const [tab, setTab] = useState<typeof tabs[number]>('General');
    const [removing, setRemoving] = useState(false);
    const [group, setGroup] = useState('');
    const col = columns.find(c => c.name === p.sourceColumn);
    const bound = !!mapping.sourceDefinitionId;
    const locked = !editable || bound;
    const title = !!p.sourceColumn && mapping.titleProperty === p.sourceColumn;
    const canReference = col && canonColumn(col.name) && canonColumn(mapping.table) &&
        /^ts_[a-z0-9_]{1,60}$/.test(mapping.schema) && mapping.schema !== 'ts_meta' && !mapping.schema.endsWith('_hist') &&
        (mapping.schema !== INDEX_PREVIEW_SCHEMA || mapping.table !== INDEX_PREVIEW_TABLE);
    const field = (text: string, value: string, values: readonly string[], disabled: boolean, change: (v: string) => void) =>
        <label>{text}<select aria-label={text} value={value} disabled={disabled} onChange={e => change(e.target.value)}>
            {values.map(v => <option key={v} value={v}>{readable(v)}</option>)}
        </select></label>;
    return <aside className="flow-foundry-property-inspector physical-property-inspector" aria-label="Property editor">
        <header><PropertyTypeIcon type={p.type} /><strong>{p.name || p.id}</strong>
            <PropertyBadges property={p} title={mapping.titleProperty}/>
            {editable && !bound && <button type="button" aria-label={`Delete property ${p.name}`} disabled={title}
                title={title ? 'Clear the title designation first' : 'Delete property'} onClick={() => setRemoving(true)}><DeleteOutlined /></button>}
        </header>
        <nav aria-label="Property settings">{tabs.map(t => <button type="button" key={t} aria-pressed={tab === t} onClick={() => setTab(t)}>{t}</button>)}</nav>
        <div className="physical-property-fields">
            {removing && <div role="group" aria-label="Confirm property removal"><p>Remove {p.name} from the object mapping?</p>
                <button type="button" onClick={() => {setRemoving(false);onRemove();}}>Remove property</button> <button type="button" onClick={() => setRemoving(false)}>Cancel</button></div>}
            {tab === 'General' && <>
                <h4>Overview</h4>
                <label>Name<input value={p.name} maxLength={120} disabled={locked} onChange={e => onPatch({name:e.target.value})}/></label>
                <label>Description<textarea rows={3} value={p.description} maxLength={4000} disabled={!editable} onChange={e => onPatch({description:e.target.value})}/></label>
                {field('Base type',p.type,businessTypes,locked || !!col,v => onPatch({type:v as Property['type']}))}
                {field('Status',p.status,['active','experimental','deprecated'],!editable,v => onPatch({status:v as Property['status']}))}
                <h4>Configuration</h4>
                <label className="physical-property-toggle">Title key<input type="checkbox" checked={title} disabled={locked || !p.sourceColumn || p.type==='json' || p.visibility==='hidden'} onChange={e => onTitle(e.target.checked)}/></label>
                {mapping.titleProperty && <small>Current title · {mapping.properties.find(p => p.sourceColumn === mapping.titleProperty)?.name || mapping.titleProperty}</small>}
                <label className="physical-property-toggle">Primary key<input type="checkbox" disabled checked={p.primaryKeyPosition != null}/></label>
                {p.primaryKeyPosition != null && <small>PostgreSQL key position {p.primaryKeyPosition}</small>}
            </>}
            {tab === 'Display' && <>
                {field('Visibility',p.visibility,title ? ['normal','prominent'] : ['normal','prominent','hidden'],!editable,v => onPatch({visibility:v as Property['visibility'], ...(v==='hidden' ? {summary:false} : {})}))}
                {field('Base formatter',p.formatter,propertyFormatters(p.type),!editable,v => onPatch({formatter:v as Property['formatter']}))}
                <label className="physical-property-toggle">Include in summary<input type="checkbox" checked={p.summary} disabled={!editable || !col || p.visibility==='hidden'} onChange={e => onPatch({summary:e.target.checked})}/></label>
            </>}
            {tab === 'Interaction' && <>
                <h4>Property preview</h4>
                {field('Presentation',p.presentation,columnPresentations(p.sqlType || ''),!editable,v => onPatch({presentation:v as Property['presentation']}))}
            </>}
            {tab === 'Details' && <>
                <label>Property group<select aria-label="Property group" value={p.group || ''} disabled={!editable} onChange={e => onGroup(e.target.value || null)}>
                    <option value="">Ungrouped</option>{mapping.groups.map(g => <option key={g}>{g}</option>)}</select></label>
                {editable && <label>New group<span className="physical-property-group"><input aria-label="New group" value={group} maxLength={120} onChange={e => setGroup(e.target.value)}/>
                    <button type="button" aria-label="Add property group" disabled={!group.trim()} onClick={() => {onGroup(group.trim());setGroup('');}}><PlusOutlined/>Add</button></span></label>}
                <dl><dt>Physical column</dt><dd>{col?.name || 'Unmapped'}</dd><dt>PostgreSQL type</dt><dd>{col?.type || '—'}</dd>
                    {col && <><dt>Nullable</dt><dd>{col.nullable ? 'Yes' : 'No'}</dd>{col.comment && <><dt>Column comment</dt><dd>{col.comment}</dd></>}</>}</dl>
                {canReference && col && <Link to={'/ontology/lineage?' + new URLSearchParams({schema:mapping.schema,table:mapping.table,tableLineage:'1',column:col.name})}>Open column references</Link>}
                {canReference && col && editable && <ExplorerColumnPlannerStatistic schema={mapping.schema} table={mapping.table} column={col.name} identity={identity}/>}
            </>}
            {tab === 'Advanced' && <>
                <label>Property ID<input value={p.id} disabled={locked || !isNew} maxLength={63} onChange={e => onPatch({id:e.target.value})}/></label>
                <label>Source column<select aria-label="Source column" value={p.sourceColumn || ''} disabled={locked || title} onChange={e => onMap(e.target.value || null)}>
                    <option value="">Unmapped</option>{columns.filter(c => p.sourceColumn===c.name || !mapping.properties.some(other => other.sourceColumn === c.name))
                        .map(c => <option key={c.name}>{c.name}</option>)}</select></label>
                {title && <small>Clear the title key before changing its source column.</small>}
                <dl><dt>Definition</dt><dd>{bound ? 'Published ontology type' : 'Physical Explorer mapping'}</dd></dl>
            </>}
        </div>
    </aside>;
}
