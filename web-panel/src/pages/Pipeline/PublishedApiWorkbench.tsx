import { useEffect, useRef, useState } from 'react';
import { Button, Input, Select, message } from '@/components/ui';
import { ApiOutlined, CopyOutlined, DeleteOutlined, PlusOutlined, ReloadOutlined, SearchOutlined } from '@/components/ui/icons';
import type { EtlPublishedApi } from '../../services/api';
import { apiUrl } from '../../services/endpoints';
import { publicationDateInput, publicationDateValue, publicationPredicate, publicationRequest, publicationState } from './publishedApiPresentation';
import useExplorerErrorToast from './useExplorerErrorToast';
import './published-api-workbench.css';

export interface PublishedApiWorkbenchProps {
  items: EtlPublishedApi[]; title?: string; loading?: boolean; busy?: boolean; dirty?: boolean; error?: string;
  onReload?: () => void; onAdd?: () => void; onUpdate?: (id: string, changes: Partial<Omit<EtlPublishedApi, 'id'>>) => void;
  onSave?: () => void; onDiscard?: () => void; onDelete?: (api: EtlPublishedApi) => void; dataTypes?: string[]; dataSources?: string[];
}
const dates = (value: string) => Number.isFinite(Date.parse(value)) ? new Date(value).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }) : 'Not set';
const parameters = [
  ['limit', 'integer', '50', 'Rows per page, from 1 to 500.'],
  ['offset', 'integer', '0', 'Use nextOffset from the previous response; maximum 1,000,000.'],
  ['search', 'string', '—', 'Narrow the published range using a text search.'],
  ['filters', 'JSON array', '[]', 'URL-encoded filter rows. These further narrow the published range.'],
];
export default function PublishedApiWorkbench({ items, title = 'Published APIs', loading, busy, dirty, error, onReload, onAdd, onUpdate, onSave, onDiscard, onDelete, dataTypes = [], dataSources = [] }: PublishedApiWorkbenchProps) {
  useExplorerErrorToast(error, 'Unable to load published APIs', onReload ? () => { if (!loading && !busy && !dirty) onReload(); } : undefined);
  const [selected, select] = useState(''), [search, setSearch] = useState('');
  const [tab, setTab] = useState<'overview' | 'request' | 'range'>('overview');
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
  const visible = items.filter(item => (item.id + ' ' + item.url + ' ' + publicationState(item, now).label).toLowerCase().includes(search.trim().toLowerCase()));
  const copy = async (value: string, content: string) => {
    try { await navigator.clipboard.writeText(value); message.success({ content, className: 'explorer-toast' }); }
    catch { message.error({ content: 'Unable to copy', description: 'Select and copy the text in the panel.', className: 'explorer-toast' }); }
  };
  const endpoint = item ? apiUrl(item.url) : '', editable = !!onUpdate, disabled = !!busy || !!loading;
  const invalid = items.some(api => !api.validFrom || !api.validUntil || !Number.isFinite(Date.parse(api.validFrom)) || !Number.isFinite(Date.parse(api.validUntil)) || Date.parse(api.validUntil) <= Date.parse(api.validFrom));
  const state = item && publicationState(item, now), predicate = item && publicationPredicate(item);
  return <section className="published-api-workbench" aria-label={title} aria-busy={loading}>
    <header className="api-workbench-toolbar"><div><ApiOutlined /><strong>{title}</strong><span>{items.length} {items.length === 1 ? 'publication' : 'publications'}</span></div><div>
      {dirty && <span className="api-unsaved">Unsaved changes</span>}
      {dirty && onDiscard && <Button size="small" disabled={disabled} onClick={onDiscard}>Discard changes</Button>}
      {onReload && <Button size="small" icon={<ReloadOutlined />} disabled={disabled || dirty} onClick={onReload}>Refresh</Button>}
      {onSave && <Button size="small" type="primary" loading={busy} disabled={loading || !dirty || invalid} onClick={onSave}>Save changes</Button>}
      {onAdd && <Button size="small" icon={<PlusOutlined />} disabled={disabled || dirty} onClick={onAdd}>Publish API</Button>}
    </div></header>
    {dirty && invalid && <p className="api-workbench-error" role="alert">Each publication needs a valid start date and a later expiry date.</p>}
    {error && !items.length ? null
      : loading && !items.length ? <div className="api-workbench-empty" role="status"><ReloadOutlined spin /><strong>Loading published APIs…</strong></div> : !items.length ? <div className="api-workbench-empty" role="status">
      <ApiOutlined /><strong>No published APIs</strong><p>{onAdd ? 'Publish the current data range to provide a read-only endpoint.' : 'This source has no published read endpoints.'}</p>
      {onAdd && <Button type="primary" size="small" icon={<PlusOutlined />} disabled={disabled} onClick={onAdd}>Publish API</Button>}
    </div> : <div className="api-workbench-layout">
      <aside className="api-workbench-catalog" aria-label="Published API catalog"><label className="api-catalog-search"><SearchOutlined /><input aria-label="Find published API" placeholder="Find an API…" value={search} onChange={event => setSearch(event.target.value)} /></label>
        <nav aria-label="Select published API">{visible.map(api => { const status = publicationState(api, now); return <button type="button" key={api.id} aria-label={`Select API ${api.id}`} aria-current={api.id === selected ? 'true' : undefined} onClick={() => select(api.id)}>
          <span className="api-method">GET</span><span><strong>Read published rows</strong><code title={api.id}>{api.id.slice(0, 8)}</code><small className={'api-status is-' + status.tone}>{status.label}</small></span>
        </button>; })}{!visible.length && <p className="api-no-match" role="status">No matching APIs</p>}</nav>
      </aside>
      {item && <div className="api-workbench-detail"><header className="api-document-heading"><div><span className="api-eyebrow">READ API</span><h3>Read published rows</h3><span className={'api-status is-' + state!.tone}>{state!.label}</span></div><code className="api-publication-id">{item.id}</code></header>
        <nav className="api-document-tabs" aria-label="API documentation"><button type="button" aria-pressed={tab === 'overview'} onClick={() => setTab('overview')}>Overview</button><button type="button" aria-pressed={tab === 'request'} onClick={() => setTab('request')}>Request</button><button type="button" aria-pressed={tab === 'range'} onClick={() => setTab('range')}>Data range</button></nav>
        <div className="api-document-body">
          <div className="api-endpoint"><span className="api-method">GET</span><code>{endpoint}</code><button type="button" aria-label="Copy API endpoint" title="Copy endpoint" onClick={() => void copy(endpoint, 'API endpoint copied')}><CopyOutlined /></button></div>
          {tab === 'overview' && <>
            <section className="api-document-section"><h4>Access</h4><div className="api-access-summary"><div><span>Authentication</span><strong>X-API-Key</strong></div><div><span>API key</span><strong>{item.keyStatus === 'active' ? 'Issued' : item.keyStatus === 'unissued' ? 'Not issued' : 'Revoked'}</strong></div><div><span>Permission</span><strong>Read published range</strong></div></div>
              <a className="api-key-link" href={'/account/settings?tab=security&publication=' + encodeURIComponent(item.id)} onClick={event => { if (dirty) { event.preventDefault(); message.warning({ content: 'Save API changes before managing the key', className: 'explorer-toast' }); } }}>Manage API key</a>
            </section>
            <section className="api-document-section"><h4>Validity <small>Local time</small></h4><div className="api-validity-fields">
              <label>Valid from{editable ? <Input size="small" type="datetime-local" aria-label="API valid from" disabled={disabled} value={publicationDateInput(item.validFrom)} onChange={event => onUpdate?.(item.id, { validFrom: publicationDateValue(event.target.value) })} /> : <strong>{dates(item.validFrom)}</strong>}</label>
              <label>Expires at{editable ? <Input size="small" type="datetime-local" aria-label="API expires at" disabled={disabled} value={publicationDateInput(item.validUntil)} onChange={event => onUpdate?.(item.id, { validUntil: publicationDateValue(event.target.value) })} /> : <strong>{dates(item.validUntil)}</strong>}</label>
            </div></section>
            <section className="api-document-section"><h4>Published range</h4><p>{predicate || 'No active property filters'}{item.search ? ' · Search: ' + item.search : ''}</p><button className="api-text-button" type="button" onClick={() => setTab('range')}>View data range</button></section>
          </>}
          {tab === 'request' && <>
            <section className="api-document-section"><h4>Request example</h4><p>Set <code>API_KEY</code> to an issued publication key. Credentials are sent in the header.</p><div className="api-code-example"><header><div>{(['curl', 'http'] as const).map(value => <button type="button" key={value} aria-pressed={value === language} onClick={() => setLanguage(value)}>{value === 'curl' ? 'cURL' : 'HTTP'}</button>)}</div><button type="button" aria-label="Copy request example" onClick={() => void copy(publicationRequest(endpoint, language), 'Request example copied')}><CopyOutlined /> Copy</button></header><pre><code>{publicationRequest(endpoint, language)}</code></pre></div></section>
            <section className="api-document-section"><h4>Query parameters</h4><table className="api-parameter-table"><thead><tr><th>Parameter</th><th>Type</th><th>Default</th><th>Description</th></tr></thead><tbody>{parameters.map(([name, type, value, description]) => <tr key={name}><th><code>{name}</code></th><td>{type}</td><td><code>{value}</code></td><td>{description}</td></tr>)}</tbody></table></section>
            <section className="api-document-section"><h4>Pagination</h4><p>The JSON response includes <code>nextOffset</code>. Use it as the next request’s <code>offset</code> until it is null. Search and filter parameters narrow the published range.</p></section>
          </>}
          {tab === 'range' && <>
            <section className="api-document-section"><h4>Property conditions</h4><p className="api-range-predicate">{predicate || 'No active property filters'}</p><p>AND, OR and parentheses retain the saved grouping. Disabled conditions do not limit this range.</p></section>
            <section className="api-document-section"><h4>Scope</h4><dl className="api-scope-facts"><div><dt>Search</dt><dd>{item.search || 'Any value'}</dd></div><div><dt>Partition</dt><dd>{item.partition || 'All partitions'}</dd></div><div><dt>Ordering</dt><dd>{item.sorts?.length ? item.sorts.map(sort => `${sort.field} ${sort.direction.toUpperCase()}`).join(', ') : 'Not specified'}</dd></div></dl>
              <div className="api-validity-fields"><label>Data types{editable ? <Select mode="tags" size="small" popupClassName="flow-select-popup" aria-label="Published data types" disabled={disabled} value={item.filterType} onChange={values => onUpdate?.(item.id, { filterType: values })} options={dataTypes.map(value => ({ value, label: value }))} placeholder="All data types" allowClear /> : <strong>{item.filterType.join(', ') || 'All data types'}</strong>}</label>
                <label>Sources{editable ? <Select mode="tags" size="small" popupClassName="flow-select-popup" aria-label="Published sources" disabled={disabled} value={item.filterSource} onChange={values => onUpdate?.(item.id, { filterSource: values })} options={dataSources.map(value => ({ value, label: value }))} placeholder="All sources" allowClear /> : <strong>{item.filterSource.join(', ') || 'All sources'}</strong>}</label></div>
            </section>
            <details className="api-published-definition"><summary>Published definition</summary><pre>{JSON.stringify({ filters: item.filters || [], sorts: item.sorts || [], search: item.search || '', partition: item.partition ?? null, filterType: item.filterType, filterSource: item.filterSource }, null, 2)}</pre></details>
            <p className="api-range-note">This publication keeps its saved range. Changing the Explorer search, filters or selection does not change it.</p>
          </>}
        </div>
        {onDelete && <footer className="api-document-footer"><span>Deleting this publication also invalidates its key.</span><button type="button" disabled={disabled || dirty} onClick={() => onDelete(item)}><DeleteOutlined /> Delete publication</button></footer>}
      </div>}
    </div>}
  </section>;
}
