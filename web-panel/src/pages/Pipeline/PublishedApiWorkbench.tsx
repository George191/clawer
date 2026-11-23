import { useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { Button, Input, NativeSelect, message } from '@/components/ui';
import { ApiOutlined, CopyOutlined, DeleteOutlined, PlusOutlined, ReloadOutlined, SearchOutlined } from '@/components/ui/icons';
import type { EtlPublishedApi } from '../../services/api';
import type { PublishedExplorerQuery } from '../../services/publishedExplorerHandoff';
import { apiUrl } from '../../services/endpoints';
import { publicationDateInput, publicationDateValue, publicationOrdering, publicationPredicate, publicationRequest, publicationState } from './publishedApiPresentation';
import PublishedApiRangeFilters from './PublishedApiRangeFilters';
import ApiCodeSnippet from './ApiCodeSnippet';
import useExplorerErrorToast from './useExplorerErrorToast';
import { generatePublicationSdk, sdkFilename, sdkLanguages, sdkRequirements, sdkUsage, type SdkLanguage } from '../../services/publicationSdk';
import './published-api-workbench.css';

export type PublishedReadApi = EtlPublishedApi & { name?: string; revision?: number; modelChanged?: boolean; fields?: string[]; query?: PublishedExplorerQuery };
export interface PublishedApiWorkbenchProps {
  items: PublishedReadApi[]; title?: string; loading?: boolean; busy?: boolean; dirty?: boolean; error?: string;
  scopeKind?: 'storage' | 'ontology'; userTokenSdk?: ReactNode;
  onManageKey?: (api: PublishedReadApi) => void; onEdit?: (api: PublishedReadApi) => void;
  onReload?: () => void; onAdd?: () => void; onUpdate?: (id: string, changes: Partial<Omit<EtlPublishedApi, 'id'>>) => void;
  onSave?: () => void; onDiscard?: () => void; onDelete?: (api: EtlPublishedApi) => void; storageAsset?: {schema: string; table: string};
}
const dates = (value: string) => Number.isFinite(Date.parse(value)) ? new Date(value).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }) : 'Not set';
const parameters = [
  ['limit', 'integer', '50', 'Rows per page, from 1 to 500.'],
  ['offset', 'integer', '0', 'Use nextOffset from the previous response; maximum 1,000,000.'],
  ['search', 'string', '—', 'Narrow the published range using a text search.'],
  ['filters', 'JSON array', '[]', 'URL-encoded filter rows. These further narrow the published range.'],
];
export default function PublishedApiWorkbench({ items, title = 'Published APIs', loading, busy, dirty, error, onReload, onAdd, onUpdate, onSave, onDiscard, onDelete, storageAsset, scopeKind = 'storage', userTokenSdk, onManageKey, onEdit }: PublishedApiWorkbenchProps) {
  useExplorerErrorToast(error, 'Unable to load published APIs', onReload ? () => { if (!loading && !busy && !dirty) onReload(); } : undefined);
  const [selected, select] = useState(''), [search, setSearch] = useState('');
  const [tab, setTab] = useState<'overview' | 'request' | 'range' | 'sdk'>('overview');
  const [selectedSdk, setSelectedSdk] = useState<SdkLanguage | 'user-token'>('typescript');
  const [sdkDownload, setSdkDownload] = useState<{url: string; source: string; endpoint: string; language: SdkLanguage}>();
  const [language, setLanguage] = useState<'curl' | 'http'>('curl');
  const [now, setNow] = useState(Date.now());
  const previous = useRef<string[]>([]);
  useEffect(() => {
    const added = items.find(item => !previous.current.includes(item.id));
    if (!items.some(item => item.id === selected) || (previous.current.length && added)) select(added?.id || items[0]?.id || '');
    previous.current = items.map(item => item.id);
  }, [items, selected]);
  useEffect(() => { const timer = window.setInterval(() => setNow(Date.now()), 60000); return () => window.clearInterval(timer); }, []);
  const item = items.find(item => item.id === selected);
  const sdkChoice = item ? selectedSdk : 'user-token';
  const sdkLanguage = sdkChoice === 'user-token' ? null : sdkChoice;
  const visible = items.filter(item => (item.id + ' ' + (item.name || '') + ' ' + item.url + ' ' + publicationState(item, now).label).toLowerCase().includes(search.trim().toLowerCase()));
  const copy = async (value: string, content: string) => {
    try { await navigator.clipboard.writeText(value); message.success({ content, className: 'explorer-toast' }); }
    catch { message.error({ content: 'Unable to copy', description: 'Select and copy the text in the panel.', className: 'explorer-toast' }); }
  };
  const endpoint = item ? apiUrl(item.url) : '', editable = !!onUpdate, disabled = !!busy || !!loading;
  const maxLimit = scopeKind === 'ontology' ? 100 : 500, maxOffset = scopeKind === 'ontology' ? 100000 : 1000000;
  useEffect(() => {
    setSdkDownload(undefined);
    if (!endpoint || !sdkLanguage) return;
    const source = generatePublicationSdk(endpoint, sdkLanguage, maxLimit, maxOffset);
    const url = URL.createObjectURL(new Blob([source], {type: 'text/plain;charset=utf-8'}));
    setSdkDownload({url, source, endpoint, language: sdkLanguage});
    return () => URL.revokeObjectURL(url);
  }, [endpoint, sdkLanguage, maxLimit, maxOffset]);
  const invalid = items.some(api => !api.validFrom || !api.validUntil || !Number.isFinite(Date.parse(api.validFrom)) || !Number.isFinite(Date.parse(api.validUntil)) || Date.parse(api.validUntil) <= Date.parse(api.validFrom));
  const statusFor = (api: PublishedReadApi) => api.modelChanged ? {label: 'Model changed', tone: 'warning'} : publicationState(api, now);
  const sdkReady = sdkDownload?.endpoint === endpoint && sdkDownload.language === sdkLanguage;
  const state = item && statusFor(item), predicate = item && (scopeKind === 'ontology' ? 'Saved object query and selected properties' : publicationPredicate(item));
  const savedQuery = item && tab === 'range' ? JSON.stringify(scopeKind === 'ontology' ? item.query : {
    filters: item.filters || [], sorts: item.sorts || [], search: item.search || '', partition: item.partition ?? null,
    filterType: item.filterType, filterSource: item.filterSource,
  }, null, 2) : undefined;
  const documentTabs = <nav className="api-document-tabs" aria-label="API documentation">
    <button type="button" aria-pressed={tab === 'overview' || (!item && tab !== 'sdk')} onClick={() => setTab('overview')}>Overview</button>
    {item && <><button type="button" aria-pressed={tab === 'request'} onClick={() => setTab('request')}>Request</button>
      <button type="button" aria-pressed={tab === 'range'} onClick={() => setTab('range')}>Data range</button></>}
    <button type="button" aria-pressed={tab === 'sdk'} onClick={() => setTab('sdk')}>SDK</button>
  </nav>;
  const sdkPanel = <section className="api-document-section">
    <h4>Download SDK</h4>
    {sdkLanguage && <p>Download the source file for this read-only API and add it to your project. Supply an issued X-API-Key at runtime.</p>}
    {item && <div className="api-sdk-controls">
      <label>Language<NativeSelect aria-label="SDK language" value={sdkChoice} onChange={event => setSelectedSdk(event.target.value as typeof selectedSdk)}>
        {Object.entries(sdkLanguages).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        {userTokenSdk && <option value="user-token">User-token SDK (TypeScript)</option>}
      </NativeSelect></label>
      {sdkLanguage && sdkReady && sdkDownload && <a className="ui-btn ui-btn-primary" href={sdkDownload.url} download={sdkFilename(sdkLanguage)}>Download {sdkLanguages[sdkLanguage]} SDK</a>}
    </div>}
    {sdkLanguage ? <>
      <p>{sdkRequirements[sdkLanguage]}</p><h4>Usage example</h4><ApiCodeSnippet source={sdkUsage[sdkLanguage]} language={sdkLanguage} /><p>Continue with nextOffset until it is null. For private certificate authorities, configure the runtime trust store before connecting; TLS verification stays enabled.</p>
      {sdkReady && sdkDownload && <div className="api-code-example"><header><strong>{sdkFilename(sdkLanguage)}</strong><button type="button" onClick={() => void copy(sdkDownload.source, 'SDK source copied')}><CopyOutlined /> Copy</button></header><ApiCodeSnippet source={sdkDownload.source} language={sdkLanguage} /></div>}
    </> : userTokenSdk}
  </section>;
  return <section className="published-api-workbench" aria-label={title} aria-busy={loading}>
    <header className="api-workbench-toolbar"><div><ApiOutlined /><strong>{title}</strong><span>{items.length} {items.length === 1 ? 'publication' : 'publications'}</span></div><div>
      {dirty && <span className="api-unsaved">Unsaved changes</span>}
      {dirty && onDiscard && <Button size="small" disabled={disabled} onClick={onDiscard}>Discard changes</Button>}
      {onReload && <Button size="small" icon={<ReloadOutlined />} disabled={disabled || dirty} onClick={onReload}>Refresh</Button>}
      {onSave && <Button size="small" type="primary" loading={busy} disabled={loading || !dirty || invalid} onClick={onSave}>Save changes</Button>}
      {onAdd && <Button size="small" icon={<PlusOutlined />} disabled={disabled || dirty} onClick={onAdd}>Publish API</Button>}
    </div></header>
    {dirty && invalid && <p className="api-workbench-error" role="alert">Each publication needs a valid start date and a later expiry date.</p>}
    {!items.length ? (loading ? <div className="api-workbench-empty" role="status"><ReloadOutlined spin /><strong>Loading published APIs…</strong></div> : <>
      {userTokenSdk && documentTabs}
      {userTokenSdk && tab === 'sdk' ? <div className="api-document-body">{sdkPanel}</div> : !error && <div className="api-workbench-empty">
        <ApiOutlined /><strong>No published APIs</strong><p>{onAdd ? 'Publish the current data range to provide a read-only endpoint.' : 'This source has no published read endpoints.'}</p>
      </div>}
    </>) : <div className="api-workbench-layout">
      <aside className="api-workbench-catalog" aria-label="Published API catalog"><label className="api-catalog-search"><SearchOutlined /><input aria-label="Find published API" placeholder="Find an API…" value={search} onChange={event => setSearch(event.target.value)} /></label>
        <nav aria-label="Select published API">{visible.map(api => { const status = statusFor(api); return <button type="button" key={api.id} aria-label={`Select API ${api.id}`} aria-current={api.id === selected ? 'true' : undefined} onClick={() => select(api.id)}>
          <span className="api-method">GET</span><span><strong>{api.name || 'Read published rows'}</strong><code title={api.id}>{api.id.slice(0, 8)}</code><small className={'api-status is-' + status.tone}>{status.label}</small></span>
        </button>; })}{!visible.length && <p className="api-no-match" role="status">No matching APIs</p>}</nav>
      </aside>
      {item && <div className="api-workbench-detail"><header className="api-document-heading"><div><span className="api-eyebrow">READ API</span><h3>{item.name || 'Read published rows'}</h3><span className={'api-status is-' + state!.tone}>{state!.label}</span></div><code className="api-publication-id">{item.id}</code></header>
        {documentTabs}
        <div className="api-document-body">
          <div className="api-endpoint"><span className="api-method">GET</span><code>{endpoint}</code><button type="button" aria-label="Copy API endpoint" title="Copy endpoint" onClick={() => void copy(endpoint, 'API endpoint copied')}><CopyOutlined /></button></div>
          {tab === 'overview' && <>
            <section className="api-document-section"><h4>Access</h4><div className="api-access-summary"><div><span>Authentication</span><strong>X-API-Key</strong></div><div><span>API key</span><strong>{item.keyStatus === 'active' ? 'Issued' : item.keyStatus === 'unissued' ? 'Not issued' : 'Revoked'}</strong></div><div><span>Permission</span><strong>Read published range</strong></div></div>
              {onManageKey ? <button type="button" className="api-key-link" disabled={disabled || dirty} onClick={() => onManageKey(item)}>Manage API key</button> : <a className="api-key-link" href={'/account/settings?tab=security&publication=' + encodeURIComponent(item.id)} onClick={event => { if (dirty) { event.preventDefault(); message.warning({ content: 'Save API changes before managing the key', className: 'explorer-toast' }); } }}>Manage API key</a>}
              {onEdit && <Button size="small" disabled={disabled || dirty} onClick={() => onEdit(item)}>Edit publication</Button>}
            </section>
            <section className="api-document-section"><h4>Validity <small>Local time</small></h4><div className="api-validity-fields">
              <label>Valid from{editable ? <Input size="small" type="datetime-local" aria-label="API valid from" disabled={disabled} value={publicationDateInput(item.validFrom)} onChange={event => onUpdate?.(item.id, { validFrom: publicationDateValue(event.target.value) })} /> : <strong>{dates(item.validFrom)}</strong>}</label>
              <label>Expires at{editable ? <Input size="small" type="datetime-local" aria-label="API expires at" disabled={disabled} value={publicationDateInput(item.validUntil)} onChange={event => onUpdate?.(item.id, { validUntil: publicationDateValue(event.target.value) })} /> : <strong>{dates(item.validUntil)}</strong>}</label>
            </div></section>
            {item.revision != null && <p className="api-range-note">Published model revision {item.revision}{item.modelChanged && ' · Model changed. Review and save the publication before using its key.'}</p>}
            <section className="api-document-section"><h4>Published range</h4><p>{predicate || 'No active property filters'}{item.search ? ' · Search: ' + item.search : ''}</p><button className="api-text-button" type="button" onClick={() => setTab('range')}>View data range</button></section>
          </>}
          {tab === 'request' && <>
            <section className="api-document-section"><h4>Request example</h4><p>Set <code>API_KEY</code> to an issued publication key. Credentials are sent in the header.</p><div className="api-code-example"><header><div>{(['curl', 'http'] as const).map(value => <button type="button" key={value} aria-pressed={value === language} onClick={() => setLanguage(value)}>{value === 'curl' ? 'cURL' : 'HTTP'}</button>)}</div><button type="button" aria-label="Copy request example" onClick={() => void copy(publicationRequest(endpoint, language), 'Request example copied')}><CopyOutlined /> Copy</button></header><pre><code>{publicationRequest(endpoint, language)}</code></pre></div></section>
            <section className="api-document-section"><h4>Query parameters</h4><table className="api-parameter-table"><thead><tr><th>Parameter</th><th>Type</th><th>Default</th><th>Description</th></tr></thead><tbody>{parameters.filter(row => scopeKind === 'storage' || ['limit', 'offset'].includes(row[0])).map(([name, type, value, description]) => <tr key={name}><th><code>{name}</code></th><td>{type}</td><td><code>{value}</code></td><td>{name === 'limit' ? `Rows per page, from 1 to ${maxLimit}.` : name === 'offset' ? `Use nextOffset from the previous response; maximum ${maxOffset.toLocaleString('en-US')}.` : description}</td></tr>)}</tbody></table></section>
            <section className="api-document-section"><h4>Response</h4><pre>{JSON.stringify(scopeKind === 'ontology' ? {items: [Object.fromEntries((item.fields || []).map(field => [field, null]))], hasMore: false, nextOffset: null} : {rows: [], total: 0, nextOffset: null}, null, 2)}</pre><p>Example structure only. Field values depend on the published data. HTTP errors must be handled; they are not empty successful pages.</p></section>
            <section className="api-document-section"><h4>Pagination</h4><p>The JSON response includes <code>nextOffset</code>. Use it as the next request’s <code>offset</code> until it is null. {scopeKind === 'storage' ? 'Search and filter parameters narrow the published range.' : 'The query and returned properties are fixed by the publisher.'}</p></section>
          </>}
          {tab === 'sdk' && sdkPanel}
          {tab === 'range' && scopeKind === 'ontology' && <>
            <section className="api-document-section"><h4>Published properties</h4><p>{item.fields?.join(', ')}</p></section>
            <section className="api-document-section"><h4>Saved object range</h4><p>The saved query is fixed for this publication. Current row and property access policies still apply. Model changes require a manager to review and save this publication again.</p>
              <dl className="api-scope-facts"><div><dt>Ordering</dt><dd>{publicationOrdering(item.query?.sorts)}</dd></div></dl>
            </section>
          </>}
          {tab === 'range' && scopeKind === 'storage' && <>
            <section className="api-document-section"><h4>Property conditions</h4><p className="api-range-predicate">{predicate || 'No active property filters'}</p><p>AND, OR and parentheses retain the saved grouping. Disabled conditions do not limit this range.</p></section>
            <section className="api-document-section"><h4>Scope</h4><dl className="api-scope-facts"><div><dt>Search</dt><dd>{item.search || 'Any value'}</dd></div><div><dt>Partition</dt><dd>{item.partition || 'All partitions'}</dd></div><div><dt>Ordering</dt><dd>{publicationOrdering(item.sorts)}</dd></div>
              {!(onUpdate && storageAsset) && <><div><dt>Data types</dt><dd>{item.filterType.join(', ') || 'All data types'}</dd></div><div><dt>Sources</dt><dd>{item.filterSource.join(', ') || 'All sources'}</dd></div></>}
            </dl>
              {onUpdate && storageAsset && <PublishedApiRangeFilters key={JSON.stringify([storageAsset.schema, storageAsset.table, item.partition])}
                {...storageAsset} item={item} disabled={disabled} onUpdate={changes => onUpdate(item.id, changes)} />}
            </section>
          </>}
          {savedQuery && <details className="api-published-definition"><summary>Saved query (JSON)</summary>
            <p>Ordering is saved with the publication. The JSON uses lowercase <code>asc</code> and <code>desc</code>.</p>
            <div className="api-code-example"><header><strong>JSON</strong><button type="button" aria-label="Copy saved query" onClick={() => void copy(savedQuery, 'Saved query copied')}><CopyOutlined /> Copy</button></header>
              <ApiCodeSnippet source={savedQuery} language="json" />
            </div>
          </details>}
          {tab === 'range' && scopeKind === 'storage' && <p className="api-range-note">This publication keeps its saved range. Changing the Explorer search, filters or selection does not change it.</p>}
        </div>
        {onDelete && <footer className="api-document-footer"><span>Deleting this publication also invalidates its key.</span><button type="button" disabled={disabled || dirty} onClick={() => onDelete(item)}><DeleteOutlined /> Delete publication</button></footer>}
      </div>}
    </div>}
  </section>;
}
