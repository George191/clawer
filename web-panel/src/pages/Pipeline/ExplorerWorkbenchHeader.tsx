import { useEffect, useRef, useState, type ReactNode } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { AppstoreOutlined, BarChartOutlined, DownOutlined, SearchOutlined, TableOutlined } from '@/components/ui/icons';
import { fetchExplorerRowByKey } from '../../services/api';
import { createSaved, listSaved, openSaved, removeSaved, savedPath, sharing, type Exploration, type ListKey, type SavedDetail, type SavedKind, type SavedMeta, type WorkbenchQuery } from '../../services/explorerSaved';
import { explorerRowTitle } from './ExplorerObjectOverview';
import { ExplorerRecordLink } from './ExplorerRecordLink';
import './explorer-workbench.css';
export type ResourceView = 'preview' | 'schema' | 'stream' | 'evidence' | 'api';
type RecordRow = Record<string, unknown>;
type Selection = {
    identity: RecordRow;
    record: RecordRow;
    title: string;
};
type Props = {
    schema: string;
    table: string;
    label: string;
    titleProperty?: string | null;
    primaryKey?: string[];
    color?: string;
    glyph?: ReactNode;
    fields: string[];
    names?: Record<string, string>;
    rows: RecordRow[];
    selected: Selection[];
    query: WorkbenchQuery;
    resource: ResourceView;
    admin: boolean;
    layoutControl: ReactNode;
    selectResource: (v: ResourceView) => void;
    apply: (q: WorkbenchQuery) => void;
    addFilter: (field: string) => void;
    toggleCatalog: () => void;
    catalogOpen: boolean;
    mode: 'explore' | 'results';
    setMode: (mode: 'explore' | 'results') => void;
    clearSelection: () => void;
    filterButton?: ReactNode;
    partitionsButton?: ReactNode;
};
function textValue(value: unknown) {
    return value == null ? '' : typeof value === 'object' ? JSON.stringify(value) : String(value);
}
function fail(e: unknown) {
    const data = (e as {
        response?: {
            data?: {
                detail?: unknown;
                errors?: unknown;
                message?: string;
            };
        };
    })?.response?.data;
    const detail = data?.errors ?? data?.detail ?? data?.message;
    return typeof detail === 'string' ? detail : e instanceof Error ? e.message : 'Request failed';
}
function download(format: 'csv' | 'json', rows: RecordRow[], fields: string[], name: string) {
    const quote = (value: unknown) => {
        let raw = textValue(value);
        if (typeof value === 'string' && /^[\s]*[=+@\-]/.test(raw))
            raw = "'" + raw;
        return '"' + raw.replace(/"/g, '""') + '"';
    };
    const output = format === 'json' ? JSON.stringify(rows, null, 2) : '\uFEFF' + [fields.map(quote).join(','), ...rows.map(r => fields.map(f => quote(r[f])).join(','))].join('\r\n');
    const href = URL.createObjectURL(new Blob([output], { type: format === 'csv' ? 'text/csv;charset=utf-8' : 'application/json' }));
    const a = document.createElement('a');
    a.href = href;
    a.download = name + '.' + format;
    a.click();
    window.setTimeout(() => URL.revokeObjectURL(href), 1000);
}
function RelatedList({ schema, table, keys, titleProperty, primaryKey }: {
    schema: string;
    table: string;
    keys: ListKey[];
    titleProperty?: string | null;
    primaryKey?: string[];
}) {
    const [offset, setOffset] = useState(0);
    const [labels, setLabels] = useState<Record<string, string>>({});
    useEffect(() => {
        const controller = new AbortController();
        setLabels({});
        const queue = keys.slice(offset, offset + 8);
        async function next() {
            while (queue.length && !controller.signal.aborted) {
                const key = queue.shift()!, id = JSON.stringify(key);
                try {
                    const item = await fetchExplorerRowByKey(schema, table, key, controller.signal);
                    if (!controller.signal.aborted)
                        setLabels(v => ({ ...v, [id]: explorerRowTitle(item.row, item.identity, titleProperty, primaryKey ?? item.primaryKey) }));
                }
                catch {
                    if (!controller.signal.aborted)
                        setLabels(v => ({ ...v, [id]: 'Object unavailable' }));
                }
            }
        }
        void Promise.all([next(), next(), next()]);
        return () => controller.abort();
    }, [schema, table, keys, offset, titleProperty, primaryKey]);
    return <><ul className="wb-list">{keys.slice(offset, offset + 8).map(key => <li key={JSON.stringify(key)}>
        <ExplorerRecordLink schema={schema} table={table} identity={key}>{labels[JSON.stringify(key)] ?? 'Loading object…'}</ExplorerRecordLink></li>)}</ul>
        {keys.length > 8 && <div><button disabled={!offset} onClick={() => setOffset(n => n - 8)}>Previous</button> {offset + 1}–{Math.min(offset + 8, keys.length)} / {keys.length} <button disabled={offset + 8 >= keys.length} onClick={() => setOffset(n => n + 8)}>Next</button></div>}</>;
}
function ExploreChart({ field, fields, names, rows, select, onFilter }: {
    field: string;
    fields: string[];
    names?: Record<string, string>;
    rows: RecordRow[];
    select: (s: string) => void;
    onFilter: (s: string) => void;
}) {
    const counts = new Map<string, number>();
    rows.forEach(row => { const text = textValue(row[field]); counts.set(text, (counts.get(text) ?? 0) + 1); });
    const groups = Array.from(counts).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).slice(0, 12);
    return <section className="wb-explore" aria-label="Explore results"><div><h2>Explore</h2>
        <label>Property <select value={field} onChange={e => select(e.target.value)}>{fields.map(f => <option key={f} value={f}>{names?.[f] || f}</option>)}</select></label></div>
        <h3>{names?.[field] || field}</h3><p>Value counts across {rows.length} rows on this results page</p>
        {groups.map(([name, count]) => <div className="wb-bar" key={name}><span title={name}>{name || 'No value'}</span>
            <i style={{ width: Math.max(2, count / groups[0][1] * 100) + '%' }}/><strong>{count}</strong></div>)}
        <button disabled={!field} onClick={() => onFilter(field)}>Filter by this property</button></section>;
}
export default function ExplorerWorkbenchHeader(p: Props) {
    const path = savedPath(p.schema, p.table);
    const [menu, show] = useState(''), [search, setSearch] = useState(''), [choice, choose] = useState('');
    const [chart, setChart] = useState(''), [name, setName] = useState(''), [error, setError] = useState('');
    const [busy, setBusy] = useState(false), [notice, setNotice] = useState('');
    const [saved, setSaved] = useState<SavedDetail>(), [shareLink, setShareLink] = useState('');
    const [page, setPage] = useState(0), [items, setItems] = useState<{
        items: SavedMeta[];
        hasMore: boolean;
    }>(), [generation, refresh] = useState(0);
    const [saveKind, setSaveKind] = useState<SavedKind>('exploration');
    const opening = useRef<AbortController>();
    const root = useRef<HTMLElement>(null);
    const location = useLocation();
    const navigate = useNavigate();
    const applyRef = useRef(p);
    applyRef.current = p;
    const link = () => new URL('/ontology/explorer?' + new URLSearchParams({ schema: p.schema, table: p.table }), window.location.origin);
    const kind = menu === 'lists' ? 'list' : 'exploration';
    const preservedSavedId = new URLSearchParams(location.search).get('saved');
    useEffect(() => () => {
        opening.current?.abort();
    }, []);
    function openMenu(m: string) { show(v => v === m ? '' : m); setError(''); setNotice(''); setPage(0); setShareLink(''); }
    function display(detail: SavedDetail) {
        setSaved(detail);
        if (detail.kind === 'list') {
            show('objects');
            return;
        }
        const view = detail.content;
        applyRef.current.selectResource('preview');
        applyRef.current.apply(view);
        setChart(view.chart ?? '');
        applyRef.current.setMode(view.view);
        show('');
    }
    async function openId(id: string) {
        opening.current?.abort();
        const c = new AbortController();
        opening.current = c;
        try {
            const detail = await openSaved(path, id, c.signal);
            if (!c.signal.aborted)
                display(detail);
        }
        catch (e) {
            if (!c.signal.aborted)
                setError(fail(e));
        }
    }
    useEffect(() => { if (preservedSavedId)
        void openId(preservedSavedId); }, [path, preservedSavedId]);
    useEffect(() => {
        if (menu !== 'lists' && menu !== 'explorations')
            return;
        const c = new AbortController();
        setItems(undefined);
        listSaved(path, kind, page * 25, c.signal).then(v => { if (!c.signal.aborted)
            setItems(v); }, e => { if (!c.signal.aborted)
            setError(fail(e)); });
        return () => c.abort();
    }, [path, menu, kind, page, generation]);
    useEffect(() => {
        if (!menu)
            return;
        const outside = (event: PointerEvent) => { if (!root.current?.contains(event.target as Node))
            show(''); };
        const key = (e: KeyboardEvent) => { if (e.key === 'Escape')
            show(''); };
        document.addEventListener('pointerdown', outside);
        document.addEventListener('keydown', key);
        return () => { document.removeEventListener('pointerdown', outside); document.removeEventListener('keydown', key); };
    }, [menu]);
    function beginSave(k: SavedKind) {
        setSaveKind(k);
        setName('');
        setError('');
        setNotice('');
        show('save');
    }
    function snapshot(): Exploration {
        return { ...p.query, chart: chart || null, view: p.mode };
    }
    async function save() {
        if (!name.trim() || busy)
            return;
        setBusy(true);
        setError('');
        try {
            const content = saveKind === 'list' ? { keys: p.selected.map(r => Object.fromEntries(Object.entries(r.identity).map(([k, v]) => [k, String(v)]))) } : snapshot();
            const item = await createSaved(path, saveKind, name.trim(), content);
            setSaved({ ...item, content: content as SavedDetail['content'], owned: true });
            show('');
            setNotice('Saved ' + item.name);
        }
        catch (e) {
            setError(fail(e));
        }
        finally {
            setBusy(false);
        }
    }
    async function share() {
        if (!saved || busy)
            return;
        setBusy(true);
        try {
            let item: SavedMeta = saved;
            if (!saved.shared) {
                item = await sharing(path, saved, true);
                setSaved({ ...saved, ...item });
            }
            const u = link();
            u.searchParams.set('saved', item.id);
            setShareLink(u.href);
        }
        catch (e) {
            setError(fail(e));
        }
        finally {
            setBusy(false);
        }
    }
    function selectMode(mode: 'results' | 'explore') { p.selectResource('preview'); p.setMode(mode); show(''); }
    const field = chart && p.fields.includes(chart) ? chart : p.fields[0] ?? '';
    const find = p.fields.filter(f => [f, p.names?.[f]].join(' ').toLowerCase().includes(search.toLowerCase())).slice(0, 30);
    const changeField = (f: string) => { choose(f); setSearch(p.names?.[f] || f); show('property'); };
    return <header className="wb-header" ref={root}>
        <div className="wb-line wb-top"><strong className="wb-current"><i style={{ background: p.color }}>{p.glyph ?? <TableOutlined />}</i>{saved?.name || p.label}</strong>
            <button onClick={() => { setSaved(undefined); if (preservedSavedId)
        navigate(link().pathname + link().search, { replace: true }); p.apply({ filters: [], sorts: [], search: '' }); p.clearSelection(); setChart(''); selectMode('results'); setNotice(''); }}>⊕ New exploration</button>
            <div className="wb-spacer"/><button onClick={() => openMenu('explorations')}>Explorations <DownOutlined /></button>
            <button onClick={() => openMenu('lists')}>Lists <DownOutlined /></button>
    </div>
        <div className="wb-line wb-query"><button className="wb-catalog-toggle" onClick={p.toggleCatalog} aria-label="Browse object types" aria-expanded={p.catalogOpen}><SearchOutlined /></button>
            <span className="wb-type"><i style={{ background: p.color }}>{p.glyph ?? <TableOutlined />}</i><strong>{p.label}</strong></span>
            <label className="wb-property-search"><SearchOutlined /><input aria-label="Search properties to add a chart or filter" placeholder="Search properties to add a chart or filter…" value={search} onFocus={() => show('search')} onChange={e => { setSearch(e.target.value); show('search'); }} onKeyDown={e => { if (e.key === 'Enter' && find[0])
        changeField(find[0]); }}/></label>
            <button onClick={() => openMenu('share')}>Share</button><button className="wb-primary" onClick={() => beginSave('exploration')}>Save</button></div>
        <div className="wb-line wb-tools">
            <div className="wb-left">{p.layoutControl}
                {!!p.selected.length && <button className="wb-selected" aria-label="Clear selected objects" onClick={p.clearSelection}>{p.selected.length} Selected ×</button>}
                {p.filterButton}{p.partitionsButton}</div>
            <div className="wb-views"><button aria-pressed={p.resource === 'preview' && p.mode === 'explore'} onClick={() => selectMode('explore')}><BarChartOutlined /> Explore</button>
            <button aria-pressed={p.resource === 'preview' && p.mode === 'results'} onClick={() => selectMode('results')}><AppstoreOutlined /> Results</button></div>
            <div className="wb-right"><button onClick={() => openMenu('asset')}>{p.resource === 'preview' ? 'Open in' : ({ schema: 'Schema', stream: 'Stream', evidence: 'Evidence', api: 'Published APIs' } as const)[p.resource]} <DownOutlined /></button>
                <button onClick={() => openMenu('export')}>Export <DownOutlined /></button></div></div>
        {error && <p className="wb-feedback" role="alert">{error}</p>}{notice && <p className="wb-feedback" role="status">{notice}</p>}
        {menu && <section className={'wb-popup wb-popup--' + menu} aria-label="Explorer tools">
            {menu === 'search' && <>{find.map(f => <button key={f} onClick={() => changeField(f)}>{p.names?.[f] || f}</button>)}{!find.length && <p>No matching properties</p>}</>}
            {menu === 'property' && <><strong>{p.names?.[choice] || choice}</strong>
                <button onClick={() => { setChart(choice); setSearch(''); selectMode('explore'); }}>Add chart</button>
                <button onClick={() => { setSearch(''); selectMode('results'); p.addFilter(choice); }}>Add filter</button></>}
            {menu === 'asset' && <>{(['preview', 'schema', 'stream', 'evidence', ...(p.admin ? ['api'] : [])] as ResourceView[]).map(view => <button key={view} onClick={() => { p.selectResource(view); p.setMode('results'); show(''); }}>
                    {{ preview: 'Data preview', schema: 'Properties & column mapping', stream: 'Stream', evidence: 'Evidence', api: 'Published APIs' }[view]}</button>)}</>}
            {menu === 'export' && <>{(['csv', 'json'] as const).map(format => <button disabled={!p.rows.length} key={format} onClick={() => {
                        download(format, p.selected.length ? p.selected.map(r => ({ ...r.record, ...r.identity })) : p.rows, p.fields, p.table);
                        show('');
                    }}>{format.toUpperCase()} · {p.selected.length ? 'selected objects' : 'current result page'}</button>)}</>}
            {(menu === 'explorations' || menu === 'lists') && <><strong>{menu === 'lists' ? 'Saved lists' : 'Saved explorations'}</strong>
                {menu === 'lists' && <button disabled={!p.selected.length || p.selected.some(r => !Object.keys(r.identity).length)} onClick={() => beginSave('list')}>Save selected objects as list</button>}
                {!items && !error && <p role="status">Loading…</p>}{items && !items.items.length && <p>No saved {menu} for this object type.</p>}
                {items?.items.map(item => <div className="wb-item" key={item.id}><button onClick={() => openId(item.id)}>{item.name}</button>
                    <button aria-label={'Delete ' + item.name} onClick={async () => { if (!window.confirm('Delete ' + item.name + '?'))
                    return; try {
                    await removeSaved(path, item);
                    if (saved?.id === item.id)
                        setSaved(undefined);
                    refresh(n => n + 1);
                }
                catch (e) {
                    setError(fail(e));
                } }}>×</button></div>)}
                {(page > 0 || items?.hasMore) && <div><button disabled={!page} onClick={() => setPage(v => v - 1)}>Previous</button><button disabled={!items?.hasMore} onClick={() => setPage(v => v + 1)}>Next</button></div>}</>}
            {menu === 'objects' && saved?.kind === 'list' && <><strong>{saved.name}</strong><RelatedList schema={p.schema} table={p.table} keys={saved.content.keys ?? []} titleProperty={p.titleProperty} primaryKey={p.primaryKey}/></>}
            {menu === 'save' && <form onSubmit={e => { e.preventDefault(); void save(); }}><label>{saveKind === 'list' ? 'List name' : 'Exploration name'}<input autoFocus required maxLength={100} value={name} onChange={e => setName(e.target.value)}/></label>
                <button className="wb-primary" disabled={busy || !name.trim()}>Save {saveKind}</button></form>}
            {menu === 'share' && <><strong>Share {saved?.name || 'exploration'}</strong>
                {!saved ? <button onClick={() => beginSave('exploration')}>Save exploration first</button> : <>
                    <p>People with the link must sign in and have access to this object type.</p>
                    <button disabled={busy || (!saved.owned && !saved.shared)} onClick={share}>{saved.shared ? 'Show share link' : 'Enable link sharing'}</button>
                    {saved.owned && saved.shared && <button onClick={async () => { try {
                    const item = await sharing(path, saved, false);
                    setSaved({ ...saved, ...item });
                    setShareLink('');
                }
                catch (e) {
                    setError(fail(e));
                } }}>Stop sharing</button>}
                    {shareLink && <label>Share link<input readOnly value={shareLink} onFocus={e => e.currentTarget.select()}/></label>}</>}</>}
        </section>}
        {p.resource === 'preview' && p.mode === 'explore' && <ExploreChart field={field} select={setChart} fields={p.fields} names={p.names} rows={p.rows} onFilter={f => { selectMode('results'); p.addFilter(f); }}/>}
    </header>;
}
