import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { ApiOutlined, AppstoreOutlined, ArrowLeftOutlined, BarChartOutlined, CheckOutlined, CloseOutlined, CopyOutlined, DeleteOutlined, DownOutlined, DownloadCircleFilled, FileSearchOutlined, LinkOutlined, LoadingOutlined, ProfileOutlined, SearchOutlined, ShareAltOutlined, SyncOutlined, TableOutlined } from '@/components/ui/icons';
import { Button, Dropdown, Modal, message } from '@/components/ui';
import type { SheetData } from 'write-excel-file/browser';
import { projectHref } from '../../services/projectDomains';
import { createSaved, listSaved, openSaved, publishedSavedPath, removeSaved, savedPath, sharing, type Exploration, type SavedDetail, type SavedKind, type SavedMeta, type WorkbenchQuery } from '../../services/explorerSaved';
import './explorer-workbench.css';
export type ResourceView = 'preview' | 'schema' | 'stream' | 'evidence' | 'api';
export type ExplorerToolAction = { key: string; label: string; group: 'analyze' | 'inspect'; icon?: ReactNode; run: () => void };
type RecordRow = Record<string, unknown>;
type Selection = {
    identity: RecordRow;
    record: RecordRow;
    title: string;
};
type ExplorerSource = { kind: 'backed'; schema: string; table: string } | { kind: 'published'; definitionId: string };
type Props<Q extends object = WorkbenchQuery> = {
    source: ExplorerSource;
    label: string;
    available: boolean;
    glyph?: ReactNode;
    fields: string[];
    names?: Record<string, string>;
    rows: RecordRow[];
    selected: Selection[];
    query: Q;
    resource: ResourceView;
    availableResources?: ResourceView[];
    additionalTools?: ExplorerToolAction[];
    admin: boolean;
    layoutControl: ReactNode;
    selectResource: (v: ResourceView) => void;
    saved?: SavedDetail<Q>;
    savedLoading: boolean;
    savedError: string;
    openSavedItem: (id: string) => void;
    closeSavedItem: () => void;
    saveCompleted: (detail: SavedDetail<Q>) => void;
    updateSavedItem: (detail: SavedDetail<Q>) => void;
    addFilter: (field: string) => void;
    backToTypes: () => void;
    toggleCatalog: () => void;
    closeCatalog: () => void;
    catalogOpen: boolean;
    mode: 'explore' | 'results';
    setMode: (mode: 'explore' | 'results') => void;
    clearSelection: () => void;
    filterButton?: ReactNode;
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
async function downloadExcel(rows: RecordRow[], fields: string[], name: string) {
    const { default: writeExcelFile } = await import('write-excel-file/browser');
    const data: SheetData = [fields.map(value => ({ value, type: String, fontWeight: 'bold' }))];
    for (const [index, row] of rows.entries()) data.push(fields.map(field => {
        const value = row[field];
        if (value == null) return null;
        if (typeof value === 'number' && Math.abs(value) < 1e15) return { value, type: Number };
        if (typeof value === 'boolean') return { value, type: Boolean };
        const text = textValue(value);
        if (text.length > 32767) throw new Error(`Column "${field}", row ${index + 2} exceeds Excel's 32,767 character limit.`);
        return { value: text, type: String, format: '@' };
    }));
    await writeExcelFile(data, { sheet: 'Results', stickyRowsCount: 1 }).toFile(name + '.xlsx');
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
export default function ExplorerWorkbenchHeader<Q extends object = WorkbenchQuery>(p: Props<Q>) {
    const sourceKey = JSON.stringify(p.source);
    const path = useMemo(() => p.source.kind === 'published' ? publishedSavedPath(p.source.definitionId) : savedPath(p.source.schema, p.source.table), [sourceKey]);
    const sourceLabel = p.source.kind === 'published' ? p.label : p.source.schema + '.' + p.source.table;
    const saved = p.saved;
    const [menu, show] = useState(''), [search, setSearch] = useState(''), [choice, choose] = useState('');
    const [chart, setChart] = useState(''), [name, setName] = useState(''), [error, setError] = useState('');
    const [busy, setBusy] = useState(false), [dialog, setDialog] = useState<'save' | 'share' | ''>('');
    const [appSearch, setAppSearch] = useState(''), [appGroup, setAppGroup] = useState('all');
    const [exporting, setExporting] = useState(false);
    const openInTrigger = useRef<HTMLButtonElement>(null);
    const [deleteTarget, setDeleteTarget] = useState<SavedMeta>();
    const [shareTarget, setShareTarget] = useState<SavedDetail<Q>>();
    const [librarySearch, setLibrarySearch] = useState(''), [access, setAccess] = useState<'all' | 'private' | 'shared'>('all');
    const [page, setPage] = useState(0), [items, setItems] = useState<{ items: SavedMeta[]; hasMore: boolean }>();
    const [generation, refresh] = useState(0), [saveKind, setSaveKind] = useState<SavedKind>('exploration');
    const opening = useRef<AbortController>();
    const root = useRef<HTMLElement>(null), propertySearch = useRef<HTMLLabelElement>(null), popup = useRef<HTMLElement>(null);
    const kind = menu === 'lists' ? 'list' : 'exploration';
    const library = menu === 'lists' || menu === 'explorations';
    const notify = (content: string, description?: string) => message.success({ content, description, className: 'explorer-toast' });
    const link = (id: string) => new URL(projectHref('/ontology/explorer?' + new URLSearchParams(p.source.kind === 'published' ? { objectType: p.source.definitionId, saved: id } : { schema: p.source.schema, table: p.source.table, saved: id }))).href;
    useEffect(() => () => opening.current?.abort(), []);
    useEffect(() => { setChart(saved?.kind === 'exploration' ? saved.content.chart ?? '' : ''); }, [saved?.id]);
    function openMenu(m: string) { show(v => v === m ? '' : m); setError(''); setPage(0); setLibrarySearch(''); setAccess('all'); }
    useEffect(() => {
        if (!library) return;
        const c = new AbortController();
        setItems(undefined); setError('');
        listSaved(path, kind, page * 25, c.signal, librarySearch, access).then(v => {
            if (!c.signal.aborted) setItems(v);
        }, e => { if (!c.signal.aborted) setError(fail(e)); });
        return () => c.abort();
    }, [path, library, kind, page, generation, librarySearch, access]);
    useEffect(() => {
        if (!menu || library || menu === 'asset') return;
        const outside = (event: PointerEvent) => {
            const target = event.target as Node;
            const trigger = target instanceof Element ? target.closest('[data-wb-menu]') : null;
            const within = popup.current?.contains(target)
                || ((menu === 'search' || menu === 'property') && propertySearch.current?.contains(target))
                || (trigger && root.current?.contains(trigger) && trigger.getAttribute('data-wb-menu') === menu);
            if (!within) show('');
        };
        const key = (e: KeyboardEvent) => { if (e.key === 'Escape') show(''); };
        document.addEventListener('pointerdown', outside, true); document.addEventListener('keydown', key);
        return () => { document.removeEventListener('pointerdown', outside, true); document.removeEventListener('keydown', key); };
    }, [menu, library]);
    function beginSave(k: SavedKind) {
        if (p.savedLoading || p.savedError) return;
        setSaveKind(k); setName(''); setError(''); show(''); setDialog('save');
    }
    function snapshot(): Exploration<Q> { return { ...p.query, chart: chart || null, view: p.mode }; }
    const listKeys = p.selected.length
        ? p.selected.map(r => Object.fromEntries(Object.entries(r.identity).map(([k, v]) => [k, String(v)])))
        : saved?.kind === 'list' ? saved.content.keys ?? [] : [];
    async function save() {
        if (p.savedLoading || p.savedError || !name.trim() || busy || (saveKind === 'list' && !listKeys.length) || (saveKind === 'exploration' && saved?.kind === 'list')) return;
        setBusy(true); setError('');
        try {
            const content = saveKind === 'list' ? { keys: listKeys } : snapshot();
            const item = await createSaved<Q>(path, saveKind, name.trim(), content);
            setDialog(''); show(''); refresh(n => n + 1);
            p.saveCompleted({ ...item, content: content as SavedDetail<Q>['content'], owned: true });
            notify(`${saveKind === 'list' ? 'List' : 'Exploration'} saved`, item.name);
        } catch (e) {
            const detail = fail(e); setError(detail);
            message.error({ content: `Unable to save ${saveKind}`, description: detail, className: 'explorer-toast' });
        } finally { setBusy(false); }
    }
    async function openShare(item?: SavedMeta) {
        show(''); setError(''); setDialog('share'); setShareTarget(undefined);
        if (!item) return;
        if (item.id === saved?.id) { setShareTarget(saved); return; }
        opening.current?.abort();
        const c = new AbortController(); opening.current = c; setBusy(true);
        try { const detail = await openSaved<Q>(path, item.id, c.signal); if (!c.signal.aborted) setShareTarget(detail); }
        catch (e) { if (!c.signal.aborted) setError(fail(e)); }
        finally { if (!c.signal.aborted) setBusy(false); }
    }
    async function setSharing(enabled: boolean) {
        if (!shareTarget || !shareTarget.owned || busy) return;
        setBusy(true); setError('');
        try {
            const item = await sharing(path, shareTarget, enabled), detail = { ...shareTarget, ...item };
            setShareTarget(detail); if (saved?.id === item.id) p.updateSavedItem(detail); refresh(n => n + 1);
            notify(enabled ? 'Link sharing enabled' : 'Link sharing disabled', detail.name);
        } catch (e) {
            const detail = fail(e); setError(detail);
            message.error({ content: 'Unable to update link sharing', description: detail, className: 'explorer-toast' });
        } finally { setBusy(false); }
    }
    async function deleteSaved() {
        if (!deleteTarget || busy) return;
        setBusy(true); setError('');
        try {
            await removeSaved(path, deleteTarget);
            if (saved?.id === deleteTarget.id) p.closeSavedItem();
            setDeleteTarget(undefined); setDialog(''); show(''); refresh(n => n + 1);
            notify(`${deleteTarget.kind === 'list' ? 'List' : 'Exploration'} deleted`, deleteTarget.name);
        } catch (e) {
            const detail = fail(e); setError(detail);
            message.error({ content: `Unable to delete ${deleteTarget.kind}`, description: detail, className: 'explorer-toast' });
        } finally { setBusy(false); }
    }
    function selectMode(mode: 'results' | 'explore') { p.selectResource('preview'); p.setMode(mode); show(''); }
    const field = chart && p.fields.includes(chart) ? chart : p.fields[0] ?? '';
    const find = p.fields.filter(f => [f, p.names?.[f]].join(' ').toLowerCase().includes(search.toLowerCase())).slice(0, 30);
    const changeField = (f: string) => { choose(f); setSearch(p.names?.[f] || f); show('property'); };
    const closeOpenIn = () => { show(''); openInTrigger.current?.focus(); };
    const openResource = (view: ResourceView) => { p.selectResource(view); if (p.source.kind === 'backed' || view === 'preview') p.setMode('results'); closeOpenIn(); };
    const exportDescription = p.selected.length ? `Download ${p.selected.length} selected object${p.selected.length === 1 ? '' : 's'}.` : `Download ${p.rows.length} object${p.rows.length === 1 ? '' : 's'} on this results page.`;
    const resources = p.availableResources ?? (p.source.kind === 'backed' ? ['preview', 'stream', 'evidence', 'schema', ...(p.admin ? ['api'] : [])] : ['preview']);
    const sourceTool = p.source.kind === 'published';
    const apps = [
        ...(resources.includes('preview') ? [{ key: 'preview', group: 'analyze', label: 'Data preview', icon: <TableOutlined />, run: () => openResource('preview') }] : []),
        ...(resources.includes('stream') ? [{ key: 'stream', group: sourceTool ? 'inspect' : 'analyze', label: sourceTool ? 'Source stream' : 'Stream', icon: <SyncOutlined />, run: () => openResource('stream') }] : []),
        ...(resources.includes('evidence') ? [{ key: 'evidence', group: sourceTool ? 'inspect' : 'analyze', label: sourceTool ? 'Source evidence' : 'Evidence', icon: <FileSearchOutlined />, run: () => openResource('evidence') }] : []),
        ...(resources.includes('schema') ? [{ key: 'schema', group: 'inspect', label: sourceTool ? 'Source column mapping' : 'Properties & column mapping', icon: <ProfileOutlined />, run: () => openResource('schema') }] : []),
        ...(resources.includes('api') && p.admin ? [{ key: 'api', group: 'inspect', label: sourceTool ? 'Source APIs' : 'Published APIs', icon: <ApiOutlined />, run: () => openResource('api') }] : []),
        ...(p.additionalTools ?? []).map(tool => ({ ...tool, run: () => { closeOpenIn(); tool.run(); } })),
        { key: 'excel', group: 'export', label: 'Export as Excel', description: exportDescription, icon: exporting ? <LoadingOutlined spin /> : <DownloadCircleFilled />, disabled: exporting || !p.rows.length, run: async () => {
            if (exporting) return;
            setExporting(true);
            try {
                await downloadExcel(p.selected.length ? p.selected.map(r => p.source.kind === 'published' ? r.record : ({ ...r.record, ...r.identity })) : p.rows, p.fields, p.source.kind === 'published' ? p.label : p.source.table);
                closeOpenIn();
            } catch (e) { message.error({ content: 'Excel export failed', description: fail(e), className: 'explorer-toast' }); }
            finally { setExporting(false); }
        } },
    ];
    const matchingApps = apps.filter(app => (appGroup === 'all' || app.group === appGroup) && app.label.toLowerCase().includes(appSearch.toLowerCase().trim()));
    const appMenu = <section className="wb-open-in" aria-label="Open in tools" onBlurCapture={event => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null) && event.relatedTarget !== openInTrigger.current) show(current => current === 'asset' ? '' : current);
    }} onKeyDown={event => {
        const target = event.target as HTMLElement, group = target.closest('[role="menu"]');
        if (!group) return;
        const buttons = Array.from(group.querySelectorAll<HTMLButtonElement>('button:not(:disabled)')), index = buttons.indexOf(target as HTMLButtonElement);
        if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
            event.preventDefault();
            buttons[event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1 : (index + (event.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length]?.focus();
        }
        if (event.key === 'ArrowRight' && group.classList.contains('wb-open-in-categories')) { event.preventDefault(); event.currentTarget.querySelector<HTMLButtonElement>('.wb-open-in-actions button:not(:disabled)')?.focus(); }
        if (event.key === 'ArrowLeft' && group.classList.contains('wb-open-in-actions')) { event.preventDefault(); event.currentTarget.querySelector<HTMLButtonElement>('[aria-checked="true"]')?.focus(); }
    }}>
        <label className="wb-open-in-search"><SearchOutlined /><input autoFocus aria-label="Search Open in tools" placeholder="Search for apps…" value={appSearch} onChange={event => { setAppSearch(event.target.value); setAppGroup('all'); }} onKeyDown={event => {
            if (event.key === 'ArrowDown') { event.preventDefault(); event.currentTarget.closest('section')?.querySelector<HTMLButtonElement>('.wb-open-in-actions button:not(:disabled)')?.focus(); }
        }}/></label>
        <div className="wb-open-in-body">
            <nav className="wb-open-in-categories" role="menu" aria-label="Tool categories">{[['all', 'All'], ...(apps.some(app => app.group === 'analyze') ? [['analyze', 'Analyze objects']] : []), ...(apps.some(app => app.group === 'inspect') ? [['inspect', 'Inspect']] : []), ['export', 'Export objects']].map(([key, label]) => <button type="button" role="menuitemradio" aria-checked={appGroup === key} key={key} onClick={() => setAppGroup(key)}>{label}</button>)}</nav>
            <div className="wb-open-in-actions" role="menu" aria-label="Available tools">{matchingApps.map(app => <button type="button" role="menuitem" key={app.key} disabled={'disabled' in app && app.disabled} onClick={app.run}>{app.icon}<span>{app.label}{'description' in app && <small>{app.description}</small>}</span></button>)}{!matchingApps.length && <p role="status">No matching apps</p>}</div>
        </div>
    </section>;
    const libraryContent = <section className="wb-library" aria-label={`Saved ${menu}`}>
        {error && <p role="alert" className="wb-library-error">{error}</p>}
        <div className="wb-library-heading"><div><h3>{menu === 'lists' ? 'Lists' : 'Explorations'}</h3><p>{menu === 'lists' ? 'Saved object selections' : 'Saved searches and views'}</p></div><button aria-label="Close saved items" onClick={() => show('')}><CloseOutlined /></button></div>
        <div className="wb-library-controls"><label><SearchOutlined /><input aria-label={`Search ${menu}`} placeholder={`Search ${menu}…`} maxLength={100} value={librarySearch} onChange={e => { setLibrarySearch(e.target.value); setPage(0); }}/></label><select aria-label="Saved item sharing" value={access} onChange={e => { setAccess(e.target.value as typeof access); setPage(0); }}><option value="all">All</option><option value="private">Private</option><option value="shared">Shared</option></select></div>
        <div className="wb-library-items">
            {!items && !error && <p role="status" className="wb-library-empty">Loading saved items…</p>}
            {items && !items.items.length && <div className="wb-library-empty"><SearchOutlined /><strong>{librarySearch || access !== 'all' ? 'No matching saved items' : `No saved ${menu}`}</strong><p>{librarySearch || access !== 'all' ? 'Try another search or sharing filter.' : 'Save your work to return to it later.'}</p></div>}
            {items?.items.map(item => <div className={'wb-library-item' + (saved?.id === item.id ? ' is-current' : '')} key={item.id}>
                <button className="wb-library-open" aria-label={`Open ${item.kind} ${item.name}`} aria-current={saved?.id === item.id ? 'page' : undefined} onClick={() => { show(''); p.openSavedItem(item.id); }}><span className="wb-library-icon">{item.kind === 'list' ? <TableOutlined /> : <BarChartOutlined />}</span><span><strong>{item.name}</strong><small>{item.shared ? 'Shared link enabled' : 'Private'} · {sourceLabel}</small></span>{saved?.id === item.id && <CheckOutlined />}</button>
                <div className="wb-library-actions"><button aria-label={`Share ${item.kind} ${item.name}`} title="Share" onClick={() => void openShare(item)}><ShareAltOutlined /></button><button aria-label={`Delete ${item.kind} ${item.name}`} title="Delete" onClick={() => { show(''); setError(''); setDeleteTarget(item); }}><DeleteOutlined /></button></div>
            </div>)}
        </div>
        <footer className="wb-library-footer"><span>Created by you</span><div><button disabled={!page} onClick={() => setPage(v => v - 1)}>Previous</button><span>{page + 1}</span><button disabled={!items?.hasMore} onClick={() => setPage(v => v + 1)}>Next</button></div></footer>
    </section>;
    return <header className="wb-header" ref={root}>
        <div className="wb-line wb-query">
            <button type="button" className="wb-back" onClick={p.backToTypes}><ArrowLeftOutlined /> Back to object types</button>
            <span className="wb-type">{p.available && <button type="button" className="wb-catalog-toggle wb-type-icon" onClick={p.toggleCatalog} aria-label="Browse object types" title="Browse object types" aria-expanded={p.catalogOpen}><i>{p.glyph ?? <TableOutlined />}</i></button>}<strong title={sourceLabel}>{p.label}</strong></span>
            {p.available && <><label className="wb-property-search" ref={propertySearch} onClick={() => show('search')}><SearchOutlined /><input aria-label="Search properties to add a chart or filter" placeholder="Search properties to add a chart or filter…" value={search} onFocus={() => show('search')} onChange={e => { setSearch(e.target.value); show('search'); }} onKeyDown={e => { if (e.key === 'Enter' && find[0]) changeField(find[0]); }}/></label>
            {(['explorations', 'lists'] as const).map(entry => <Dropdown key={entry} trigger={['click']} placement="bottomRight" open={menu === entry}
                onOpenChange={next => { if (next) openMenu(entry); else show(current => current === entry ? '' : current); }}
                overlayClassName="flow-menu-popup wb-library-popup" popupRender={() => libraryContent}>
                <button type="button" data-wb-menu={entry}>{entry === 'lists' ? 'Lists' : 'Explorations'} <DownOutlined /></button>
            </Dropdown>)}
            <button onClick={() => void openShare(saved)} disabled={p.savedLoading || !!p.savedError}>Share</button><button className="wb-primary" disabled={p.savedLoading || !!p.savedError} onClick={() => beginSave(saved?.kind === 'list' ? 'list' : 'exploration')}>Save</button></>}
        </div>
        {p.available && <><div className="wb-line wb-tools">
            <div className="wb-left">{p.layoutControl}{p.filterButton}
                {saved && <div className="wb-active-saved" aria-label="Current saved item"><span className="wb-saved-kind">{saved.kind === 'list' ? <TableOutlined /> : <BarChartOutlined />}{saved.kind === 'list' ? 'List' : 'Exploration'}</span><strong title={saved.name}>{saved.name}</strong><button type="button" aria-label="Return to all rows" title="Return to all rows" onClick={p.closeSavedItem}><CloseOutlined /></button></div>}
                {!!p.selected.length && <div className="wb-selection-tools" role="toolbar" aria-label="Selected object tools">
                    <span className="wb-selection-count" role="status" aria-live="polite" aria-atomic="true"><strong>{p.selected.length}</strong> selected</span>
                    <button type="button" disabled={p.selected.some(row => !Object.keys(row.identity).length)} onClick={() => beginSave('list')}><TableOutlined /> Save list</button>
                    <button type="button" className="wb-selection-clear" aria-label="Clear selected objects" title="Clear selection" onClick={p.clearSelection}><CloseOutlined /></button>
                </div>}
            </div>
            <div className="wb-views"><button aria-pressed={p.resource === 'preview' && p.mode === 'explore'} onClick={() => selectMode('explore')}><BarChartOutlined /> Explore</button><button aria-pressed={p.resource === 'preview' && p.mode === 'results'} onClick={() => selectMode('results')}><AppstoreOutlined /> Results</button></div>
            <div className="wb-right"><Dropdown trigger={['click']} placement="bottomRight" open={menu === 'asset'} onOpenChange={next => { if (next) { openMenu('asset'); setAppSearch(''); setAppGroup('all'); } else show(current => current === 'asset' ? '' : current); }} overlayClassName="flow-menu-popup wb-open-in-popup" popupRender={() => appMenu}>
                <button type="button" className="wb-open-in-trigger" ref={openInTrigger}>Open in <DownOutlined /></button>
            </Dropdown>
            </div>
        </div>
        {menu && !library && menu !== 'asset' && <section className={'wb-popup wb-popup--' + menu} ref={popup} aria-label="Explorer tools">
            {error && <p role="alert" className="wb-library-error">{error}</p>}
            {menu === 'search' && <>{find.map(f => <button key={f} onClick={() => changeField(f)}>{p.names?.[f] || f}</button>)}{!find.length && <p>No matching properties</p>}</>}
            {menu === 'property' && <><strong>{p.names?.[choice] || choice}</strong><button onClick={() => { setChart(choice); setSearch(''); selectMode('explore'); }}>Add chart</button><button onClick={() => { setSearch(''); selectMode('results'); p.addFilter(choice); }}>Add filter</button></>}

        </section>}
        {p.resource === 'preview' && p.mode === 'explore' && !p.savedLoading && !p.savedError && <ExploreChart field={field} select={setChart} fields={p.fields} names={p.names} rows={p.rows} onFilter={f => { selectMode('results'); p.addFilter(f); }}/>}</>}
        <Modal className="flow-surface-modal wb-saved-dialog" title="Save your work" open={dialog === 'save'} width={460} onCancel={() => { if (!busy) setDialog(''); }} keyboard={!busy} maskClosable={!busy} footer={<><Button disabled={busy} onClick={() => setDialog('')}>Cancel</Button><Button type="primary" loading={busy} disabled={!name.trim() || (saveKind === 'list' && !listKeys.length) || (saveKind === 'exploration' && saved?.kind === 'list')} onClick={() => void save()}>Save {saveKind}</Button></>}>
            <div className="wb-save-kind"><button type="button" aria-pressed={saveKind === 'exploration'} disabled={saved?.kind === 'list'} onClick={() => setSaveKind('exploration')}><BarChartOutlined /><strong>Exploration</strong><span>Search, filters, sorting and chart</span></button><button type="button" aria-pressed={saveKind === 'list'} disabled={!listKeys.length} onClick={() => setSaveKind('list')}><TableOutlined /><strong>List</strong><span>{listKeys.length} selected objects</span></button></div>
            {saved?.kind === 'list' && <p className="wb-dialog-help">A list keeps a fixed object selection. Return to all rows to create an exploration.</p>}
            <form onSubmit={e => { e.preventDefault(); void save(); }}><label className="wb-dialog-field">{saveKind === 'list' ? 'List name' : 'Exploration name'}<input autoFocus required maxLength={100} value={name} placeholder={saveKind === 'list' ? 'Name this object selection' : 'Name this exploration'} onChange={e => setName(e.target.value)}/></label></form>
            {error && <p role="alert" className="wb-dialog-error">{error}</p>}
        </Modal>
        <Modal className="flow-surface-modal wb-saved-dialog" title={shareTarget ? `Share ${shareTarget.kind}` : 'Share'} open={dialog === 'share'} width={480} onCancel={() => { if (!busy) setDialog(''); }} keyboard={!busy} maskClosable={!busy} footer={<Button disabled={busy} onClick={() => setDialog('')}>Done</Button>}>
            {busy && !shareTarget ? <p role="status">Loading saved item…</p> : shareTarget ? <>
                <div className="wb-share-resource">{shareTarget.kind === 'list' ? <TableOutlined /> : <BarChartOutlined />}<div><strong>{shareTarget.name}</strong><span>{shareTarget.kind === 'list' ? `${shareTarget.content.keys?.length ?? 0} saved object references` : 'Saved search, filters, sorting and chart'}</span></div></div>
                <p className="wb-dialog-help">{shareTarget.kind === 'list' ? 'This link opens the saved object selection.' : 'This link opens the saved exploration. Save current changes as a new exploration to share them.'}</p>
                <p className="wb-dialog-help">People with this link must sign in and have access to the underlying {p.source.kind === 'published' ? 'object type' : 'table'}. Sharing does not grant data access.</p>
                <div className="wb-share-access"><LinkOutlined /><div><strong>{shareTarget.shared ? 'Link sharing enabled' : 'Private'}</strong><span>{shareTarget.shared ? 'People with data access can open this saved item.' : 'Only you can open this saved item.'}</span></div>{shareTarget.owned && <Button size="small" loading={busy} onClick={() => void setSharing(!shareTarget.shared)}>{shareTarget.shared ? 'Disable link' : 'Enable link'}</Button>}</div>
                {shareTarget.shared && <label className="wb-dialog-field">Share link<div className="wb-share-link"><input readOnly value={link(shareTarget.id)} onFocus={e => e.currentTarget.select()}/><Button icon={<CopyOutlined />} aria-label="Copy share link" onClick={async () => { try { await navigator.clipboard.writeText(link(shareTarget.id)); notify('Share link copied'); } catch { const detail = 'Select and copy the link above.'; setError('Unable to copy. ' + detail); message.error({ content: 'Unable to copy share link', description: detail, className: 'explorer-toast' }); } }}>Copy</Button></div></label>}
            </> : !error && <div className="wb-share-empty"><ShareAltOutlined /><strong>Save this result before sharing</strong><p>Choose a saved exploration or list, or save the current result.</p><Button type="primary" onClick={() => beginSave('exploration')}>Save exploration</Button></div>}
            {error && <p role="alert" className="wb-dialog-error">{error}</p>}
        </Modal>
        <Modal className="flow-surface-modal wb-saved-dialog wb-delete-dialog" title={`Delete ${deleteTarget?.kind ?? 'saved item'}?`} open={!!deleteTarget} width={440} onCancel={() => { if (!busy) setDeleteTarget(undefined); }} keyboard={!busy} maskClosable={!busy} footer={<><Button disabled={busy} onClick={() => setDeleteTarget(undefined)}>Cancel</Button><Button danger type="primary" loading={busy} onClick={() => void deleteSaved()}>Delete {deleteTarget?.kind}</Button></>}>
            <div className="wb-delete-resource"><DeleteOutlined /><strong>{deleteTarget?.name}</strong></div><p className="wb-dialog-help">This removes the saved {deleteTarget?.kind} and disables its share link. The underlying objects are unchanged.</p>{error && <p role="alert" className="wb-dialog-error">{error}</p>}
        </Modal>
    </header>;
}
