import PropertyTypeIcon from './PropertyTypeIcon';
import { useEffect, useRef, useState } from 'react';
import { Button, Checkbox, Input, Select } from '@/components/ui';
import { CloseOutlined, DeleteOutlined, PlusOutlined, SearchOutlined, TableOutlined } from '@/components/ui/icons';
import { queryExplorerPreview } from '../../services/api';
import { getSessionState } from '../../services/session';
import type { SemanticDefinition, SemanticProperty } from '../../services/ontology';
import type { EtlObjectPreview, ExplorerPreviewResult } from '../../services/types';
import { propertyIssue } from './semanticWizardProperties';
import './semantic-properties-workbench.css';

const semanticPropertyTypes = ['string', 'integer', 'number', 'boolean', 'date', 'datetime', 'json'];
const label = (property: SemanticProperty) => property.name || property.id || 'Untitled property';
const display = (value: unknown) => value == null ? '—' : typeof value === 'object' ? JSON.stringify(value) : String(value);
const selectStyle = { className: 'flow-ontology__select', popupClassName: 'flow-ontology__select-popup' };

type Props = {
  definition: SemanticDefinition;
  preview?: EtlObjectPreview;
  busy: boolean;
  showIssues: boolean;
  onAdd: () => void;
  onAutoMap: () => void;
  onChange: (position: number, value: Partial<SemanticProperty>) => void;
  onRemove: (position: number) => void;
  onPrimary: (key: string, enabled: boolean) => void;
  onTitle: (key: string) => void;
};
export default function SemanticPropertiesWorkbench({ definition, preview, busy, showIssues, onAdd, onAutoMap, onChange, onRemove, onPrimary, onTitle }: Props) {
  const { properties, primaryKey, titleKey, source } = definition;
  const sourceBacked = source.mode === 'existing';
  const [mode, setMode] = useState<'properties' | 'mapping'>('properties');
  const [search, setSearch] = useState('');
  const [selected, select] = useState<number | undefined>(properties.length ? 0 : undefined);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [onlyMapped, setOnlyMapped] = useState(false);
  const [sample, setSample] = useState<ExplorerPreviewResult>();
  const [sampleError, setSampleError] = useState(false);
  const [retry, setRetry] = useState(0);
  const schema = source.schema || '';
  const table = source.table || '';
  const sourceColumns = preview?.properties || [];
  const sourceNames = new Set(sourceColumns.map(column => column.name));
  const mapped = new Set(properties.map(property => property.sourceColumn).filter(column => !!column && sourceNames.has(column)));
  const searchTerm = search.trim().toLocaleLowerCase();
  const property = selected === undefined ? undefined : properties[selected];
  const picked = selected ?? 0;
  const activeName = property ? label(property) : '';
  const validId = !!property && /^[a-z][a-z0-9_-]{0,62}$/.test(property.id) && properties.filter(item => item.id === property.id).length === 1;
  const primary = !!property && primaryKey.includes(property.id);
  const isTitle = !!property && !!titleKey && titleKey === property.id;
  const column = sourceColumns.find(c => c.name === property?.sourceColumn);
  const physicalKey = !!column && !!preview?.primaryKey.includes(column.name);
  const usesPhysicalKey = !!preview?.primaryKey.length && preview.primaryKey.length === primaryKey.length
    && preview.primaryKey.every(name => properties.some(p => p.sourceColumn === name && primaryKey.includes(p.id)));
  const removalHint = physicalKey ? 'The source primary key must stay mapped.'
    : primary ? 'Assign a replacement primary key before removing this property.'
    : isTitle ? 'Choose another title property before removing this property.' : '';
  const issue = showIssues ? propertyIssue(definition, preview) : undefined;
  const issueIndex = issue?.index, issueField = issue?.field;
  const inspector = useRef<HTMLElement>(null);
  useEffect(() => {
    if (issueIndex === undefined) return;
    setMode('properties');
    setSearch('');
    select(issueIndex);
  }, [issueIndex, issueField]);
  useEffect(() => {
    if (issueIndex === undefined || selected !== issueIndex || !issueField) return;
    inspector.current?.querySelector<HTMLElement>('input[aria-invalid="true"], [role="combobox"][aria-invalid="true"]')?.focus();
  }, [issueIndex, issueField, selected]);

  const eligibleTitle = !!property && validId && property.type !== 'json';
  const eligiblePrimary = !!property && validId && !['number', 'json'].includes(property.type);

  useEffect(() => {
    if (!previewOpen || !schema || !table) return;
    const request = new AbortController();
    const identity = getSessionState().identity;
    const current = () => !request.signal.aborted && getSessionState().identity === identity && !getSessionState().locked;
    setSample(undefined);
    setSampleError(false);
    queryExplorerPreview(schema, table, { limit: 5, offset: 0, filters: [], sorts: [] }, request.signal).then(
      result => { if (current()) setSample(result); },
      () => { if (current()) setSampleError(true); },
    );
    return () => request.abort();
  }, [previewOpen, retry, schema, table]);

  const add = () => {
    setMode('properties');
    setSearch('');
    select(properties.length);
    onAdd();
  };
  const mapAll = () => {
    onAutoMap();
    setSearch('');
    select(sourceColumns.length ? 0 : undefined);
  };
  const switchMode = (next: 'properties' | 'mapping') => {
    setMode(next);
    setSearch('');
  };
  const openProperty = (index: number) => {
    setMode('properties');
    setSearch('');
    select(index);
  };
  const markers = (item: SemanticProperty) => item.id && <>
    {primaryKey.includes(item.id) && <span className="flow-foundry-property-tag is-key">Primary key</span>}
    {titleKey === item.id && <span className="flow-foundry-property-tag is-title">Title</span>}
  </>;
  const propertyRows = properties.map((item, index) => ({ item, index })).filter(({ item }) =>
    `${label(item)} ${item.id} ${item.sourceColumn || ''} ${item.type}`.toLocaleLowerCase().includes(searchTerm));
  const columnRows = sourceColumns.filter(column => column.name.toLocaleLowerCase().includes(searchTerm));
  const sampleColumns = sourceColumns.filter(column => (!sample || sample.columns.includes(column.name)) && (!onlyMapped || mapped.has(column.name)));
  const sourceSample = () => {
    if (previewOpen) {
      setSample(undefined);
      setSampleError(false);
    }
    setPreviewOpen(value => !value);
  };
  return <section className={`flow-foundry-workbench flow-foundry-properties flow-semantic-properties${previewOpen ? ' has-source-preview' : ''}`} aria-label="Property workbench">
    <div className={`flow-foundry-property-workspace${property ? ' has-inspector' : ''}`}>
      <div className="flow-foundry-property-main">
        <header className="flow-foundry-property-header">
          <div><h3 tabIndex={-1}>Properties</h3><span>{sourceBacked ? `${mapped.size} of ${sourceColumns.length} columns mapped` : `${properties.length} ${properties.length === 1 ? 'property' : 'properties'}`}</span></div>
          {sourceBacked && <nav aria-label="Property views">
            <button type="button" aria-pressed={mode === 'properties'} onClick={() => switchMode('properties')}>Properties ({properties.length})</button>
            <button type="button" aria-pressed={mode === 'mapping'} onClick={() => switchMode('mapping')}>Column mapping</button>
          </nav>}
          <div className="flow-foundry-property-tools">
            <Input aria-label={mode === 'properties' ? 'Search properties' : 'Search source columns'} value={search} onChange={event => setSearch(event.target.value)} prefix={<SearchOutlined />} placeholder="Search" allowClear />
            <Button size="small" icon={<PlusOutlined />} disabled={properties.length >= 200} onClick={add}>Create property</Button>
          </div>
        </header>
        <section className="flow-foundry-property-list" aria-label={mode === 'properties' ? 'Properties list' : 'Source column mapping list'}>
          <div className="flow-foundry-property-table-wrap">
          {mode === 'properties' ? <table aria-label="Properties">
            <thead><tr><th>Property name</th><th>Base type</th><th>Required</th>{sourceBacked && <th>Column</th>}</tr></thead>
            <tbody>{propertyRows.map(({ item, index }) => <tr key={index} className={selected === index ? 'is-selected' : undefined} onClick={() => select(index)}>
              <td><button type="button" title={label(item)} aria-pressed={selected === index} className="flow-foundry-property-select"><PropertyTypeIcon type={item.type} /><span className="flow-property-name">{label(item)}</span>{markers(item)}</button></td>
              <td>{item.type}</td><td>{item.required ? 'Required' : 'Optional'}</td>
              {sourceBacked && <td><code>{item.sourceColumn || 'Unmapped'}</code></td>}
            </tr>)}{!propertyRows.length && <tr><td colSpan={sourceBacked ? 4 : 3} className="flow-foundry-property-empty">{properties.length ? 'No matching properties.' : 'Create a property to get started.'}</td></tr>}</tbody>
          </table> : <table aria-label="Column mapping">
            <thead><tr><th>Source column</th><th>Source type</th><th>Mapped property</th></tr></thead>
            <tbody>{columnRows.map(column => {
              const index = properties.findIndex(item => item.sourceColumn === column.name);
              const candidates = properties.flatMap((item, position) => item.sourceColumn ? [] : [{ value: position, label: label(item) }]);
              return <tr key={column.name} className={selected !== undefined && selected === index ? 'is-selected' : undefined}>
                <td><code>{column.name}</code></td><td>{column.type}</td><td>{index >= 0
                  ? <button className="flow-foundry-property-link" type="button" onClick={() => openProperty(index)}>{label(properties[index])}</button>
                  : candidates.length ? <Select {...selectStyle} aria-label={`Map ${column.name} to a property`} options={candidates} placeholder="Map to a property" onChange={(position: number) => { onChange(position, { sourceColumn: column.name }); select(position); }} />
                  : <span className="flow-foundry-property-muted">Unmapped</span>}</td>
              </tr>;
            })}{!columnRows.length && <tr><td colSpan={3} className="flow-foundry-property-empty">No matching source columns.</td></tr>}</tbody>
          </table>}
          </div>
        </section>
      </div>
      {property ? <aside ref={inspector} className="flow-foundry-property-inspector" aria-label="Property inspector">
        <header><PropertyTypeIcon type={property.type} /><strong title={activeName}>{activeName}</strong>{markers(property)}
          <button type="button" title={removalHint || "Remove property"} aria-label="Remove property" disabled={!!removalHint} onClick={() => { onRemove(picked); select(properties.length > 1 ? Math.min(picked, properties.length - 2) : undefined); }}><DeleteOutlined /></button>
          <button type="button" aria-label="Close property inspector" onClick={() => select(undefined)}><CloseOutlined /></button></header>
        <div className="flow-foundry-property-fields">
          <h4>General</h4>
          <label className="flow-create-field"><span>Name</span><Input aria-invalid={showIssues && selected === issueIndex && issueField === "name"} maxLength={120} aria-label="Property name" value={property.name} onChange={event => onChange(picked, { name: event.target.value })} /></label>
          <label className="flow-create-field"><span>API name</span><Input aria-invalid={showIssues && selected === issueIndex && issueField === "id"} maxLength={63} aria-label="Property ID" value={property.id} onChange={event => onChange(picked, { id: event.target.value })} /></label>
          <label className="flow-create-field"><span>Base type</span><Select {...selectStyle} aria-invalid={showIssues && selected === issueIndex && issueField === "type"} aria-label="Property type" value={property.type} options={semanticPropertyTypes.map(value => ({ value, label: value[0].toUpperCase() + value.slice(1), disabled: (primary && ['number', 'json'].includes(value)) || (isTitle && value === 'json') }))} onChange={(type: string) => onChange(picked, { type })} /></label>
          {sourceBacked && <label className="flow-create-field"><span>Source column</span><Select {...selectStyle} aria-invalid={showIssues && selected === issueIndex && issueField === "sourceColumn"} aria-label="Source column" disabled={physicalKey} allowClear showSearch value={property.sourceColumn || undefined} placeholder="Select a column" options={sourceColumns.filter(column => column.name === property.sourceColumn || !mapped.has(column.name)).map(column => ({ value: column.name, label: column.name }))} onChange={(sourceColumn?: string) => onChange(picked, { sourceColumn })} /></label>}
        </div>
        <div className="flow-foundry-property-fields flow-property-configuration">
          <h4>Configuration</h4>
          <Checkbox aria-invalid={selected === issueIndex && issueField === 'required'} checked={property.required} disabled={(primary && property.required) || (column?.nullable && !property.required)} onChange={event => onChange(picked, { required: event.target.checked })}>Required</Checkbox>
          <Checkbox checked={primary} disabled={usesPhysicalKey || (primary ? primaryKey.length === 1 : !eligiblePrimary || !!column?.nullable || primaryKey.length >= 16)} onChange={event => onPrimary(property.id, event.target.checked)}>Primary key</Checkbox>
          <Checkbox checked={isTitle} disabled={isTitle || !eligibleTitle} onChange={event => onTitle(event.target.checked ? property.id : '')}>Title key</Checkbox>
          {removalHint && <small>{removalHint}</small>}
          {titleKey && !isTitle && <small>Current title: {properties.find(item => item.id === titleKey)?.name || titleKey}</small>}
        </div>
      </aside> : <div className="flow-property-inspector-idle">Select a property to edit its settings.</div>}
    </div>
    {sourceBacked && <section className="flow-foundry-property-preview" aria-label="Source preview">
      <div className="flow-foundry-property-preview-tools">
        <strong title={table}><TableOutlined />{table || 'Source table'}</strong>
        {previewOpen && <Checkbox checked={onlyMapped} onChange={event => setOnlyMapped(event.target.checked)}>Show mapped columns only</Checkbox>}
        {!properties.length && <Button size="small" disabled={!sourceColumns.length} onClick={mapAll}>Map source columns</Button>}
        <Button size="small" aria-expanded={previewOpen} disabled={busy || !preview} onClick={sourceSample}>{previewOpen ? 'Hide preview' : 'Preview table'}</Button>
      </div>
      {previewOpen && <div className="flow-foundry-property-table-wrap">
        {sampleError ? <div className="flow-foundry-property-empty" role="alert">The source sample could not be loaded. <Button size="small" onClick={() => setRetry(value => value + 1)}>Retry</Button></div> : !sample ? <div className="flow-foundry-property-empty" role="status">Loading up to 5 source rows…</div> : sampleColumns.length ? <table aria-label="Source table sample">
          <thead><tr><th>#</th>{sampleColumns.map(column => {
            const index = properties.findIndex(item => item.sourceColumn === column.name);
            return <th key={column.name} className={selected !== undefined && selected === index ? 'is-selected' : undefined}><code>{column.name}</code><small>{column.type}</small>{index >= 0 && <button type="button" className="flow-foundry-property-link" onClick={() => openProperty(index)}>{label(properties[index])}</button>}</th>;
          })}</tr></thead>
          <tbody>{sample.rows.map((row, index) => <tr key={index}><th scope="row">{index + 1}</th>{sampleColumns.map(column => { const content = display(row[column.name]); return <td key={column.name} title={content.slice(0, 300)}>{content.slice(0, 120)}</td>; })}</tr>)}{!sample.rows.length && <tr><td colSpan={sampleColumns.length + 1} className="flow-foundry-property-empty">No source rows available.</td></tr>}</tbody>
        </table> : <p className="flow-foundry-property-empty">No columns match the preview settings.</p>}
      </div>}
    </section>}
  </section>;
}
