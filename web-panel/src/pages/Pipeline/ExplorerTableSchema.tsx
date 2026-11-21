import PropertyTypeIcon from './PropertyTypeIcon';
import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { Button, message } from '@/components/ui';
import { FilterOutlined, PlusOutlined, SearchOutlined } from '@/components/ui/icons';
import { fetchEtlTableColumns } from '../../services/api';
import { explorerPropertyType, fetchExplorerProperties, saveExplorerProperties, type ExplorerMappedProperty as Property, type ExplorerPropertyMapping as Mapping } from '../../services/explorerProperties';
import { ontologyError } from '../../services/ontology';
import { publishedExplorerHref } from '../../services/publishedExplorerHandoff';
import type { EtlTableColumn } from '../../services/types';
import { getSessionState } from '../../services/session';
import ExplorerMappingPreview from './ExplorerMappingPreview';
import ExplorerPropertyInspector, { PropertyBadges, propertyFormatters, readable } from './ExplorerPropertyInspector';
import { useSandboxIdentity } from './SandboxMarkings';
import './semantic-properties-workbench.css';
import './physical-property-workbench.css';

type Props = { schema: string; table: string; canManage?: boolean; onSaved?: () => void; onDirtyChange?: (dirty: boolean) => void };
function nextId(name: string, properties: Property[]) {
    let base = name.toLowerCase().replace(/[^a-z0-9_-]+/g,'_');
    if (!/^[a-z]/.test(base)) base = 'property_' + base;
    base = base.slice(0,63);
    let id = base, counter = 1;
    while (properties.some(p => p.id === id)) {
        const suffix = '_' + ++counter; id = base.slice(0,63-suffix.length) + suffix;
    }
    return id;
}
const sourceFields = (column?: EtlTableColumn) => ({
    sourceColumn:column?.name || null, sqlType:column?.type || null, primaryKeyPosition:column?.primaryPosition ?? null,
    ...(column ? {type:explorerPropertyType(column.type)} : {}),
});
function makeProperty(properties: Property[], column?: EtlTableColumn): Property {
    const id = nextId(column?.name || 'property',properties);
    return {id, name:column?.name || 'New property', description:column?.comment || '', type:'string',
        visibility:'normal', summary:false, group:null, status:'experimental', formatter:'default', presentation:'value', ...sourceFields(column)};
}
function Workbench({schema,table,canManage,identity,onSaved,onDirtyChange}: Props & {identity:string}) {
    const [saved,setSaved] = useState<Mapping>();
    const [draft,setDraft] = useState<Mapping>();
    const [columns,setColumns] = useState<EtlTableColumn[]>([]);
    const [newProperties,setNewProperties] = useState<Set<number>>(new Set());
    const [selected,setSelected] = useState<number>(0);
    const [mode,setMode] = useState<'properties'|'mapping'>('properties');
    const [searching,setSearching] = useState(false);
    const [term,setTerm] = useState('');
    const [filterOpen,setFilterOpen] = useState(false);
    const [filter,setFilter] = useState('');
    const [busy,setBusy] = useState(false);
    const [error,setError] = useState('');
    const [conflict,setConflict] = useState(false);
    const [reload,setReload] = useState(0);
    const alive = useRef(false), saving = useRef(false);
    useEffect(() => {
        alive.current = true;
        const read = new AbortController();
        setError(''); setSaved(undefined); setDraft(undefined); setConflict(false);
        Promise.all([fetchExplorerProperties(schema,table,read.signal),fetchEtlTableColumns(schema,table,read.signal)])
            .then(([mapping,cols]) => { if (!read.signal.aborted && getSessionState().identity===identity) {
                setSaved(mapping); setDraft(mapping); setColumns([...cols].sort((a,b) => a.ordinalPosition-b.ordinalPosition)); setSelected(0);setNewProperties(new Set());
            }}).catch(e => { if (!read.signal.aborted) setError(ontologyError(e,'Unable to load property metadata.')); });
        return () => {alive.current=false;read.abort();};
    }, [schema,table,identity,reload]);
    const dirty = draft !== saved && JSON.stringify(draft) !== JSON.stringify(saved);
    useEffect(() => {
        onDirtyChange?.(dirty);
        return () => onDirtyChange?.(false);
    }, [dirty, onDirtyChange]);
    if (!draft || !saved) return <section aria-label="Properties">{error ? <p role="alert">{error} <Button onClick={() => setReload(v=>v+1)}>Retry metadata</Button></p> : <p role="status">Loading properties…</p>}</section>;
    const editable = !!canManage && !busy, bound = !!draft.sourceDefinitionId, structural = editable && !bound;
    const p = draft.properties[selected];
    const change = (next:Mapping) => {if (!editable) return;setDraft(next);if (!conflict) setError('');};
    const patch = (i:number,delta:Partial<Property>) => change({...draft,properties:draft.properties.map((v,k)=>k===i?{...v,...delta}:v)});
    const choose = setSelected;
    const reveal = (id:string) => {setMode('properties');setTerm('');setFilter('');choose(draft.properties.findIndex(v=>v.id===id));};
    const create = (column?:EtlTableColumn) => {
        if (!structural) return;
        change({...draft,properties:[...draft.properties,makeProperty(draft.properties,column)]});
        setNewProperties(new Set([...newProperties,draft.properties.length]));
        setTerm('');setFilter('');choose(draft.properties.length);setMode('properties');
    };
    const mapped = new Map(draft.properties.filter(v=>v.sourceColumn).map((v)=>[v.sourceColumn,v]));
    const autoMap = () => {
        if (!structural) return;
        const items = [...draft.properties];
        for (const col of columns) {
            if (mapped.has(col.name)) continue;
            const i=items.findIndex(v=>!v.sourceColumn && v.id===col.name && v.type===explorerPropertyType(col.type));
            if (i>=0) items[i]={...items[i],...sourceFields(col)}; else items.push(makeProperty(items,col));
        }
        change({...draft,properties:items});setFilter('');setTerm('');
    };
    const mapColumn = (col:EtlTableColumn,id:string) => {
        if (!structural || draft.titleProperty===col.name) return;
        if (id===':create') {create(col);return;}
        change({...draft,properties:draft.properties.map(v=>v.id===id?{...v,...sourceFields(col),formatter:'default',presentation:'value'}:
            v.sourceColumn===col.name?{...v,...sourceFields(),summary:false,presentation:'value'}:v)});
        if (id) choose(draft.properties.findIndex(v=>v.id===id));
    };
    const save = async () => {
        if (!editable || !dirty || saving.current || conflict) return;
        if (draft.properties.some(item=>!item.name.trim() || (!saved.properties.some(existing=>existing.id===item.id) && !/^[a-z][a-z0-9_-]{0,62}$/.test(item.id))) ||
            new Set(draft.properties.map(item=>item.id)).size!==draft.properties.length) {
            message.error({content:'Unable to save properties',description:'Each property needs a name and a unique ID starting with a lowercase letter.',className:'explorer-toast'});return;
        }
        saving.current=true;setBusy(true);setError('');
        try {
            const result=await saveExplorerProperties(schema,table,draft);
            if (alive.current && getSessionState().identity===identity) {setSaved(result);setDraft(result);setNewProperties(new Set());message.success({content:'Property mapping saved',className:'explorer-toast'});onSaved?.();}
        } catch (e) {
            if (alive.current && getSessionState().identity===identity) {
                message.error({content:'Unable to save properties',description:ontologyError(e,'Unable to save the property mapping.'),className:'explorer-toast'});
                setConflict((e as {response?:{status?:number}}).response?.status===409);
            }
        } finally {saving.current=false;if (alive.current) setBusy(false);}
    };
    const discard = () => {
        if (busy) return;
        if (conflict) {setReload(v=>v+1);return;}
        setDraft(saved);setSelected(0);setNewProperties(new Set());setError('');
    };
    const termLower=term.trim().toLowerCase();
    const matches=(item:Property|undefined) => !filter || (filter==='unmapped' ? !item?.sourceColumn : filter==='mapped' ? !!item?.sourceColumn : item?.status===filter || item?.visibility===filter);
    const items=draft.properties.map((item,index)=>({item,index})).filter(({item})=>matches(item)&&[item.id,item.name,item.sourceColumn,item.type].some(v=>v?.toLowerCase().includes(termLower)));
    const cols=columns.filter(c=>matches(mapped.get(c.name)) && [c.name,c.type,mapped.get(c.name)?.name].some(v=>v?.toLowerCase().includes(termLower)));
    const switchMode=(value:typeof mode)=>{setMode(value);setTerm('');setFilter('');};
    return <section className="flow-foundry-properties physical-property-workbench" aria-label="Property workbench">
        <div className={'flow-foundry-property-workspace'+(p || dirty?' has-inspector':'')}>
            <div className="flow-foundry-property-main">
                <header className="flow-foundry-property-header">
                    <nav aria-label="Property views"><button type="button" aria-pressed={mode==='properties'} onClick={()=>switchMode('properties')}>Properties ({draft.properties.length})</button>
                        <button type="button" aria-pressed={mode==='mapping'} onClick={()=>switchMode('mapping')}>Column mapping</button></nav>
                </header>
                <section className="flow-foundry-property-list"><div className="flow-foundry-property-tools">
                    <strong>{mode==='properties'?'Properties':'Source columns'} <span>{mode==='properties'?draft.properties.length:columns.length}</span></strong>
                    {bound ? <Link to={publishedExplorerHref(draft.sourceDefinitionId!)}>Open published type</Link>
                        : canManage && <Button size="small" icon={<PlusOutlined/>} disabled={!structural} onClick={()=>create()}>Create property</Button>}
                    <Button size="small" aria-label="Search properties and columns" aria-expanded={searching} icon={<SearchOutlined/>}
                        onClick={()=>{setSearching(!searching);if(searching)setTerm('');}}/>
                    <Button size="small" aria-label="Filter properties and columns" aria-expanded={filterOpen} icon={<FilterOutlined/>}
                        onClick={()=>{setFilterOpen(!filterOpen);if(filterOpen)setFilter('');}}/>
                {(searching||filterOpen) && <div className="physical-property-search">{searching && <input aria-label="Search properties or source columns" autoFocus placeholder="Search properties or source columns" value={term} onChange={e=>setTerm(e.target.value)}/>}
                    {filterOpen && <select aria-label="Filter properties" value={filter} onChange={e=>setFilter(e.target.value)}><option value="">All properties</option>
                        {['mapped','unmapped','active','experimental','deprecated','normal','prominent','hidden'].map(v=><option key={v} value={v}>{readable(v)}</option>)}</select>}</div>}
                </div>
                <div className="flow-foundry-property-table-wrap"><table aria-label={mode==='properties'?'Registered properties':'Physical column mappings'}>
                    {mode==='properties'?<><thead><tr>{['Property name','Status','Visibility','Base formatter','Column'].map(s=><th key={s} scope="col">{s}</th>)}</tr></thead><tbody>
                        {items.map(({item,index})=><tr key={index} className={index===selected?'is-selected':''} onClick={()=>choose(index)}>
                            <td><button type="button" className="flow-foundry-property-select" aria-label={'Inspect '+item.name} aria-pressed={index===selected} onClick={()=>choose(index)}>
                                <span className="physical-selection" aria-hidden="true"/><PropertyTypeIcon type={item.type} /><span className="physical-property-name" title={item.name || item.id}>{item.name || item.id}</span>
                                <PropertyBadges property={item} title={draft.titleProperty}/></button></td>
                            <td><span className={'physical-chip status-'+item.status}>{readable(item.status)}</span></td>
                            <td>{editable ? <select className="physical-chip visibility" aria-label={'Visibility for '+item.name} value={item.visibility}
                                onChange={e=>patch(index,{visibility:e.target.value as Property['visibility'], ...(e.target.value==='hidden'?{summary:false}:{})})}>
                                {(draft.titleProperty && item.sourceColumn===draft.titleProperty?['normal','prominent']:['normal','prominent','hidden']).map(v=><option key={v} value={v}>{readable(v)}</option>)}
                            </select> : <span className="physical-chip visibility">{readable(item.visibility)}</span>}</td>
                            <td>{editable && propertyFormatters(item.type).length>1 ? <select className="physical-chip formatter" aria-label={'Formatter for '+item.name} value={item.formatter}
                                onChange={e=>patch(index,{formatter:e.target.value as Property['formatter']})}>
                                {propertyFormatters(item.type).map(v=><option key={v} value={v}>{readable(v)}</option>)}
                            </select> : <span className="physical-chip formatter">{readable(item.formatter)}</span>}</td>
                            <td><code>{item.sourceColumn || '—'}</code></td></tr>)}
                        {!items.length && <tr><td colSpan={5}>{draft.properties.length?'No properties match the current filters.':'No properties registered.'}</td></tr>}</tbody></>
                    : <><thead><tr>{['Source column','PostgreSQL type','Mapped property','Constraints'].map(s=><th key={s} scope="col">{s}</th>)}</tr></thead><tbody>
                        {cols.map(c=>{const target=mapped.get(c.name);return <tr key={c.name} className={p && p.sourceColumn===c.name?'is-selected':''}>
                            <td><code>{c.name}</code></td><td>{c.type}</td><td><select aria-label={'Mapping for '+c.name} disabled={!structural || draft.titleProperty===c.name} value={target?.id || ''} onChange={e=>mapColumn(c,e.target.value)}>
                                <option value="">Unmapped</option>{draft.properties.filter(v=>!v.sourceColumn||v.sourceColumn===c.name).map(v=><option value={v.id} key={v.id}>{v.name}</option>)}
                                {!target && <option value=":create">Create mapped property</option>}</select>
                                {target && <button type="button" className="flow-foundry-property-link" onClick={()=>reveal(target.id)}>Inspect property</button>}</td>
                            <td>{[c.primary?'Primary key':'',c.nullable?'Nullable':'NOT NULL'].filter(Boolean).join(' · ')}</td></tr>;})}
                        {!cols.length && <tr><td colSpan={4}>No columns match the current filters.</td></tr>}</tbody></>}
                </table></div></section>
            </div>
            {(p || dirty) && <ExplorerPropertyInspector key={selected} mapping={draft} property={p} columns={columns} editable={editable}
                dirty={dirty} busy={busy} conflict={conflict} onSave={()=>void save()} onDiscard={discard}
                isNew={newProperties.has(selected)} identity={identity} onPatch={delta=>patch(selected,delta)}
                onMap={name=>patch(selected,{...sourceFields(columns.find(c=>c.name===name)),formatter:'default',presentation:'value',...(!name ? {summary:false} : {})})}
                onTitle={enabled=>change({...draft,titleProperty:enabled?p.sourceColumn:null})}
                onGroup={group=>change({...draft,groups:group&&!draft.groups.includes(group)?[...draft.groups,group]:draft.groups,properties:draft.properties.map((v,i)=>i===selected?{...v,group}:v)})}
                onRemove={()=>{setNewProperties(new Set([...newProperties].filter(i=>i!==selected).map(i=>i>selected?i-1:i)));change({...draft,properties:draft.properties.filter((_,i)=>i!==selected)});choose(Math.max(0,selected-1));}}/>}
        </div>
        <ExplorerMappingPreview key={draft.sourceDefinitionId || 'physical'} schema={schema} table={table} columns={columns} mapping={draft} selected={p} onSelect={reveal} onAutomap={structural?autoMap:undefined}/>
    </section>;
}
export default function ExplorerTableSchema(props: Props) {
    const identity=useSandboxIdentity();
    return identity ? <Workbench key={JSON.stringify([identity,props.schema,props.table])} {...props} identity={identity}/> : null;
}
