import ExplorerRecordMutation, { recordMutationActions } from './ExplorerRecordMutation';
import { PropertyLabel } from './PropertyTypeIcon';
import { ExplorerValueProvider } from './ExplorerValue';
import { useEffect, useRef, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { Button, message } from '@/components/ui';
import { ReloadOutlined } from '@/components/ui/icons';
import { deleteEtlRow, fetchExplorerRowByKey, updateEtlRow } from '../../services/api';
import { layoutPath, readLayouts, type Layouts } from '../../services/explorerLayouts';
import { publishedLayoutPath } from '../../services/explorerLayouts';
import { fetchPublishedOntologyType, type PublishedOntologyType } from '../../services/ontology';
import { fetchPublishedRecord, type ExplorerDetailSource, type PublishedRecordDetails } from '../../services/publishedExplorerDetails';
import { publishedColumnTypes, publishedPresentation } from '../../services/publishedExplorerPresentation';
import { publishedExplorerHref } from '../../services/publishedExplorerHandoff';
import { usePublishedObjectTools } from './PublishedObjectTools';
import PublishedExplorerRelationships from './PublishedExplorerRelationships';
import SemanticTypeGlyph from './SemanticTypeGlyph';
import type { ExplorerPresentationRead } from './ExplorerObjectOverview';
import { editorValue, rowChanges, rowNullControl, type RowDraft } from './explorerRowEditing';
import {
    ExplorerObjectOverview, ExplorerProperties, ExplorerRecordActions, ExplorerTypeIcon, explorerRowTitle, useExplorerPresentation,
} from './ExplorerSelection';
import ExplorerRowLinks, { useExplorerLinkAvailability } from './ExplorerRowLinks';
import useExplorerErrorToast from './useExplorerErrorToast';
import { getSessionState } from '../../services/session';
import './ontology-frame.css';
import './explorer-record.css';

type Identity = Record<string, string | number | boolean>;
type RecordResult = Awaited<ReturnType<typeof fetchExplorerRowByKey>>;
const live = (identity: string, signal?: AbortSignal) => {
    const session = getSessionState();
    return !signal?.aborted && session.authenticated && !session.locked && session.identity === identity;
};

function parseIdentity(value: string | null, published: boolean): Identity | undefined {
    if (!value || value.length > 16000) return;
    try {
        const key: unknown = JSON.parse(value);
        if (!key || typeof key !== 'object' || Array.isArray(key)) return;
        const fields = Object.entries(key);
        if (fields.length > 0 && fields.length <= (published ? 16 : 32) && fields.every(([name, v]) =>
            published ? (name === '$recordId' || /^[a-z][a-z0-9_-]{0,62}$/.test(name)) && typeof v === 'string' && v.length <= 4096
              : /^[a-z_][a-z0-9_]{0,62}$/.test(name) && (typeof v === 'string' || typeof v === 'boolean' ||
            (typeof v === 'number' && Number.isSafeInteger(v))))) return key as Identity;
    } catch { /* Malformed links render the invalid-link state. */ }
}

function requestError(error: unknown) {
    const r = (error as {response?:{status?:number;data?:{errors?:unknown;detail?:unknown;message?:string}}})?.response;
    const detail = r?.data?.errors ?? r?.data?.detail ?? r?.data?.message;
    if (typeof detail === 'string') return detail;
    if (detail && typeof detail === 'object' && 'message' in detail && typeof detail.message === 'string') return detail.message;
    if (error instanceof Error) return error.message;
    return 'Unable to complete the request.';
}

function RecordPage({ source, identity }: { source: ExplorerDetailSource; identity: Identity }) {
    const schema = source.kind === 'backed' ? source.schema : '';
    const table = source.kind === 'backed' ? source.table : '';
    const sourceKey = JSON.stringify(source);
    const navigate = useNavigate();
    const [result, setResult] = useState<RecordResult>();
    const [objectType, setObjectType] = useState<PublishedOntologyType>();
    const [publishedRecord, setPublishedRecord] = useState<PublishedRecordDetails>();
    const [layouts, setLayouts] = useState<Layouts>();
    const [layoutError, setLayoutError] = useState('');
    const [error, setError] = useState('');
    const [mutationError, setMutationError] = useState('');
    const [validationError, setValidationError] = useState('');
    const [revision, reload] = useState(0);
    const [mode, setMode] = useState<'overview'|'properties'|'links'|'history'|`group:${string}`>('overview');
    const [draft, setDraft] = useState<RowDraft>();
    const rememberedEditValues = useRef<RowDraft>({});
    const [confirmDelete, setConfirmDelete] = useState(false);
    const [busy, setBusy] = useState(false);
    const [stale, setStale] = useState(false);
    const tablePath = source.kind === 'published' ? publishedExplorerHref(source.definitionId) : '/ontology/explorer?' + new URLSearchParams({ schema, table });
    const [key] = useState(identity);
    const sessionIdentity = getSessionState().identity;
    const alive = useRef(true);
    useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
    useEffect(() => {
        const controller = new AbortController();
        setResult(undefined); setError(''); setStale(false); setConfirmDelete(false); setDraft(undefined);
        setMutationError(''); setValidationError('');
        setObjectType(undefined); setPublishedRecord(undefined);
        setLayouts(undefined); setLayoutError('');
        readLayouts(source.kind === 'published' ? publishedLayoutPath(source.definitionId) : layoutPath(schema,table),controller.signal)
            .then(value => { if (live(sessionIdentity, controller.signal)) setLayouts(value); })
            .catch(err => { if (live(sessionIdentity, controller.signal) && err?.code !== 'ERR_CANCELED') setLayoutError(requestError(err)); });
        if (source.kind === 'published') {
            Promise.all([fetchPublishedRecord(source.definitionId, key, controller.signal), fetchPublishedOntologyType(source.definitionId, controller.signal)])
                .then(([record, type]) => { if (live(sessionIdentity, controller.signal)) {
                    setObjectType(type); setPublishedRecord(record);
                    setResult({ row: record.properties, identity: record.identity!, primaryKey: type.definition.primaryKey, columnTypes: publishedColumnTypes(type) });
                } })
                .catch(err => { if (live(sessionIdentity, controller.signal) && err?.code !== 'ERR_CANCELED') setError(requestError(err)); });
        } else {
            fetchExplorerRowByKey(schema, table, key, controller.signal)
                .then(value => { if (live(sessionIdentity, controller.signal)) setResult(value); })
                .catch(err => { if (live(sessionIdentity, controller.signal) && err?.code !== 'ERR_CANCELED') setError(requestError(err)); });
        }
        return () => controller.abort();
    }, [sourceKey,schema,table,key,revision,sessionIdentity]);
    const physicalMetadata = useExplorerPresentation(schema, table, revision);
    const metadata: ExplorerPresentationRead | undefined = source.kind === 'published' ? objectType ? { presentation: publishedPresentation(objectType) } : undefined : physicalMetadata;
    useExplorerErrorToast(error, 'Unable to read object', () => reload(value => value + 1));
    useExplorerErrorToast(layoutError, 'Property layout unavailable');
    useExplorerErrorToast(metadata?.error ? 'Unable to read object presentation.' : undefined, 'Object presentation unavailable');
    useExplorerErrorToast(mutationError, 'Unable to update object');
    const presentation = metadata?.presentation;
    const typeName = presentation?.displayName || table;
    const title = result ? (metadata
        ? explorerRowTitle(result.row, result.identity, presentation?.titleProperty, presentation?.primaryKey ?? result.primaryKey)
        : 'Loading title…') : typeName;
    const row = result && { record: result.row, identity: result.identity, key: JSON.stringify(result.identity), title, columnTypes: result.columnTypes };
    const groups = presentation?.groups ?? [];
    const group = mode.startsWith('group:') ? mode.slice(6) : undefined;
    const currentGroup = group && groups.includes(group) ? group : undefined;
    const editFields = result ? Object.keys(result.columnTypes).filter(name => !Object.prototype.hasOwnProperty.call(result.identity,name)) : [];
    const layout=layouts?.personal.layout ?? layouts?.default.layout;
    const presentations=Object.fromEntries((layout?.columns ?? []).map(c=>[c.field,c.presentation??'value']));
    const links = useExplorerLinkAvailability(schema, table, result?.identity, revision);
    const native = objectType?.definition.source.mode === 'none';
    const hasLinks = source.kind === 'published' ? native : !!links.page;
    const currentMode = mode === 'links' && !hasLinks || mode === 'history' && !publishedRecord?.hasHistory ? 'overview' : mode;
    const objectTools = usePublishedObjectTools({ objectType, actions: publishedRecord?.actions || [], record: publishedRecord,
        returnTo: '/ontology?catalog=published', busy: !result || !!error,
        onChanged: kind => { if (kind === 'delete') navigate(tablePath, { replace:true }); else reload(value => value + 1); } });
    const locked=busy || !!draft || confirmDelete || objectTools.mutationOpen;
    async function mutate(remove: boolean) {
        if (source.kind !== 'backed' || !result?.rowVersion || busy || stale) return;
        setMutationError(''); setValidationError(''); setBusy(true);
        const identity = getSessionState().identity;
        let requesting = remove;
        try {
            if (remove) {
                await deleteEtlRow(schema,table,result.identity,result.rowVersion);
                if (!alive.current || !live(identity)) return;
                message.success('Row deleted.');
                navigate(tablePath,{replace:true});
            } else if (draft) {
                const values=rowChanges(draft,result.row,result.columnTypes);
                if (Object.keys(values).length) {
                    requesting = true;
                    await updateEtlRow(schema,table,result.identity,values,result.rowVersion);
                    if (!alive.current || !live(identity)) return;
                    message.success('Changes saved.');
                } else message.info('No changes to save.');
                setDraft(undefined);reload(n=>n+1);
            }
        } catch (err) {
            if (!alive.current || !live(identity) || (err as { code?: string })?.code === 'ERR_CANCELED') return;
            const response=(err as {response?:{status:number;data?:{errors?:{code?:string};detail?:{code?:string}}}})?.response;
            if ((response?.data?.errors?.code ?? response?.data?.detail?.code)==='row_version_conflict') setStale(true);
            const detail = requestError(err);
            if (requesting) setMutationError(detail); else setValidationError(detail);
        } finally { if (alive.current) setBusy(false); }
    }
    const showLink = async () => {
        try {
            if (!navigator.clipboard?.writeText) throw new Error('Clipboard unavailable');
            await navigator.clipboard.writeText(window.location.href);
            message.success('Object link copied.');
        } catch { message.error('Unable to copy the object link. Check clipboard permissions.'); }
    };
    const actions = source.kind === 'published' && publishedRecord ? objectTools.actionsForRecord(publishedRecord) : recordMutationActions({
        edit: result?.rowVersion && result.rowActions?.edit && editFields.length > 0 ? ()=>{
            setMode('properties');setMutationError('');setValidationError('');
            rememberedEditValues.current = {};
            setDraft(Object.fromEntries(editFields.map(name=>[name,editorValue(result.columnTypes[name],result.row[name])])));
        } : undefined,
        remove: result?.rowVersion && result.rowActions?.delete ? ()=>{
            setMutationError('');setValidationError('');setConfirmDelete(true);
        } : undefined,
    });
    const mutation = source.kind === 'published' ? objectTools.renderMutation(publishedRecord) : result && draft
        ? <ExplorerRecordMutation mode="edit" title={title} error={validationError}
            busy={busy} disabled={stale} fields={editFields.map(name => ({ key:name, label:name, type:result.columnTypes[name], value:draft[name],
                onChange:(value:string | null)=>setDraft(current=>current ? {...current,[name]:value} : current),
                nullControl:result.columnNullable?.[name] === true ? rowNullControl(name, draft[name], rememberedEditValues.current,
                    value=>setDraft(current=>current ? {...current,[name]:value} : current)) : undefined }))}
            onSubmit={()=>mutate(false)} onCancel={()=>{ if (!busy) { setDraft(undefined);setValidationError(''); } }} /> : null;
    return <article className="flow-console--ontology explorer-object-workspace explorer-full-record">
        <nav aria-label="Object location"><Link to="/ontology">Ontology</Link><span>/</span>
            <Link to={tablePath}>{source.kind === 'published' ? typeName : `${schema}.${table}`}</Link></nav>
        <header><div className="record-heading">
            <div className="record-type">{objectType ? <SemanticTypeGlyph definition={objectType.definition} /> : <ExplorerTypeIcon dataType={presentation?.dataType} table={table} />}<span>{typeName}</span></div>
            <h1>{title}</h1>
        </div><div className="record-operations">
            <Button onClick={showLink} disabled={!result}>Share link</Button>
            <Button icon={<ReloadOutlined />} disabled={busy || (!!draft && !stale) || objectTools.mutationOpen} onClick={()=>reload(n=>n+1)}>Refresh</Button>
            <ExplorerRecordActions items={actions} row={row || undefined} disabled={locked || stale} />
        </div></header>
        {!result && !error && <p role="status">Loading object…</p>}
        {result && row && <>
            <dl className="record-identity">{Object.entries(result.identity).map(([name,value])=>
                <div key={name}><dt><PropertyLabel type={result.columnTypes[name] || 'string'}>{name}</PropertyLabel></dt><dd>{String(value)}</dd></div>)}</dl>
            <div className="record-tabs" aria-label="Object views">{(['overview','properties'] as const).map(tab=>
                <button type="button" key={tab} aria-pressed={tab===currentMode} disabled={locked} onClick={()=>setMode(tab)}>
                    {{overview:'Overview',properties:'Properties'}[tab]}</button>)}
                {groups.map(name => <button type="button" key={name} disabled={locked} aria-pressed={currentMode === 'group:' + name}
                    onClick={() => setMode(`group:${name}`)}>{name}</button>)}
                {hasLinks && <button type="button" aria-pressed={currentMode==='links'} disabled={locked} onClick={()=>setMode('links')}>Links</button>}
                {publishedRecord?.hasHistory && <button type="button" aria-pressed={currentMode==='history'} disabled={locked} onClick={()=>setMode('history')}>History</button>}
            </div>
            <div className="record-content">{mutation ?? (currentMode==='links' ? source.kind === 'published'
                ? <PublishedExplorerRelationships definitionId={source.definitionId} recordId={publishedRecord!.id!} />
                : <ExplorerRowLinks schema={schema} table={table} primaryKey={result.identity} initialPage={links.page}/> :
                currentMode === 'history' && publishedRecord ? objectTools.renderHistory(publishedRecord) :
                !layouts && !layoutError ? <p role="status">Loading property layout…</p> :
                layoutError ? null :
                !metadata ? <p role="status">Loading object presentation…</p> :
                metadata.error ? null :
                currentMode==='properties' ? <ExplorerProperties presentation={presentation} presentations={presentations} row={row}/> :
                <ExplorerObjectOverview row={row} presentation={presentation} presentations={presentations} typeName={typeName} group={currentGroup}/>)}</div>
        </>}
        {objectTools.dialogs}
        {source.kind === 'backed' && result && confirmDelete && <ExplorerRecordMutation mode="delete" title={title}
            busy={busy} disabled={stale} onSubmit={()=>mutate(true)} onCancel={()=>{ if (!busy) setConfirmDelete(false); }} />}
    </article>;
}

export default function ExplorerRecordPage() {
    const location=useLocation();
    const params=new URLSearchParams(location.search);
    const schema=params.get('schema') ?? '', table=params.get('table') ?? '';
    const definitionId = params.get('objectType') || '';
    const published = params.has('objectType');
    const identity=parseIdentity(params.get('key'), published);
    const validSource = published ? /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(definitionId) && !params.has('schema') && !params.has('table')
        : [schema,table].every(v=>/^[a-z_][a-z0-9_]{0,62}$/.test(v));
    if (!identity || !validSource) {
        return <article className="flow-console--ontology explorer-full-record"><h1>Invalid object link</h1><Link to="/ontology/explorer">Return to Explorer</Link></article>;
    }
    const source: ExplorerDetailSource = published ? { kind: 'published', definitionId } : { kind: 'backed', schema, table };
    return <ExplorerValueProvider source={source}><RecordPage key={JSON.stringify([location.search, getSessionState().identity])} source={source} identity={identity}/></ExplorerValueProvider>;
}
