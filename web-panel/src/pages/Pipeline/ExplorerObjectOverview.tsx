import { ExplorerValue } from './ExplorerValue';
import { lazy, Suspense, useEffect, useState } from 'react';
import { ProfileOutlined } from '@/components/ui/icons';
import { inferSiteKind, TemplateGlyph } from '../AICollect/WorkspaceDock';
import { fetchExplorerProperties, type ExplorerPropertyMapping } from '../../services/explorerProperties';
import { accentStyle } from '../../theme/tokens';

export type ExplorerOverviewPresentation = ExplorerPropertyMapping;
export type ExplorerPresentationRead = { presentation?: ExplorerOverviewPresentation; error?: true };
export type ExplorerPropertyPresentation = 'value' | 'timeSeries' | 'geoJson' | 'media';
type RowValues = Record<string, unknown>;
export type ExplorerPreviewRow = {
  record: RowValues;
  key: string;
  title: string;
  identity: RowValues;
  columnTypes: Record<string, string>;
  version?: string;
};

type Format = 'default' | 'number' | 'percent' | 'date' | 'dateTime';
type Field = {
  field: string | null;
  key: string;
  label: string;
  type: string;
  description?: string;
  format: Format;
  presentation: ExplorerPropertyPresentation;
  summary?: boolean;
  group?: string | null;
};
const ExplorerPropertyPreview = lazy(() => import('./ExplorerPropertyPreview'));
const fieldValue = (record: RowValues, identity: RowValues, field: string) =>
  Object.prototype.hasOwnProperty.call(identity, field) ? identity[field] : record[field];
export const propertyValue = (row: ExplorerPreviewRow, field: string) => fieldValue(row.record, row.identity, field);
const rawText = (value: unknown) => typeof value === 'object' ? JSON.stringify(value) : String(value);
const notEmpty = (value: unknown) => value !== undefined && value !== null && value !== '';

export function explorerRowTitle(record: RowValues, identity: RowValues, titleProperty?: string | null,
  primaryKey: readonly string[] = [], fallback = 'Untitled row') {
  if (titleProperty) {
    const value = fieldValue(record, identity, titleProperty);
    return notEmpty(value) && typeof value !== 'object' && String(value).trim() ? rawText(value) : 'Title unavailable';
  }
  const keys = Object.keys(identity).sort();
  const ordered = [...primaryKey.filter(key => Object.prototype.hasOwnProperty.call(identity, key)),
    ...keys.filter(key => !primaryKey.includes(key))];
  return ordered.length ? 'Primary key: ' + ordered.map(key => key + '=' + rawText(identity[key])).join(' · ') : fallback;
}

export function useExplorerPresentation(schema: string, table: string, revision: string | number = 0) {
  const key = JSON.stringify([schema, table]);
  const [read, setRead] = useState<ExplorerPresentationRead & { key: string }>();
  useEffect(() => {
    if (!schema || !table) return;
    const controller = new AbortController();
    fetchExplorerProperties(schema, table, controller.signal).then(result => {
      if (!controller.signal.aborted) setRead({ key, presentation: result });
    }, () => {
      if (!controller.signal.aborted) setRead({ key, error: true });
    });
    return () => controller.abort();
  }, [key, schema, table, revision]);
  return read?.key === key ? read : undefined;
}

export function ExplorerTypeIcon({ dataType, table, compact }: { dataType?: string; table: string; compact?: boolean }) {
  const className = `flow-explorer-selection__icon${compact ? ' is-compact' : ''}`;
  return <span aria-hidden="true" className={className} style={accentStyle(dataType || '')}>
    <TemplateGlyph kind={inferSiteKind(dataType || '', table)} source={table} />
  </span>;
}

export function explorerFields(row: ExplorerPreviewRow, columns: readonly string[], presentation?: ExplorerOverviewPresentation,
  presentations?: Record<string, ExplorerPropertyPresentation>): Field[] {
  if (!presentation) return columns.map(field => ({
    field, key: field, label: field, type: row.columnTypes[field] || 'Unknown', format: 'default',
    presentation: presentations?.[field] || 'value',
  }));
  return presentation.properties.filter(property => property.visibility !== 'hidden' &&
    (property.sourceColumn == null || columns.includes(property.sourceColumn))).map(property => ({
    field: property.sourceColumn, key: property.id, label: property.name, description: property.description,
    type: property.type || property.sqlType || 'Unknown', format: property.formatter,
    presentation: property.presentation !== 'value' ? property.presentation : presentations?.[property.sourceColumn ?? ''] || 'value',
    summary: property.summary, group: property.group,
  }));
}

function formattedText(value: unknown, format: Format) {
  if (format === 'number' && (typeof value === 'number' || typeof value === 'string' && /^-?\d+$/.test(value))) {
    return new Intl.NumberFormat(undefined, { maximumFractionDigits: 12 }).format(typeof value === 'string' ? BigInt(value) : value);
  }
  if (format === 'percent' && (typeof value === 'number' || typeof value === 'string' && /^-?\d+(?:\.\d+)?$/.test(value))) {
    const number = Number(value);
    if (Number.isFinite(number) && Math.abs(number) <= Number.MAX_SAFE_INTEGER) return new Intl.NumberFormat(undefined, { style: 'percent', maximumFractionDigits: 3 }).format(number);
  }
  if ((format === 'date' || format === 'dateTime') && typeof value === 'string' && /^\d{4}-\d{2}-\d{2}(?:T|$)/.test(value)) {
    const instant = new Date(value);
    if (Number.isFinite(instant.getTime())) return new Intl.DateTimeFormat(undefined, {
      dateStyle: 'medium', timeStyle: format === 'dateTime' ? 'short' : undefined, timeZone: 'UTC',
    }).format(instant);
  }
  return rawText(value);
}

export function ExplorerPropertyValue({ value, format = 'default', identity, field }: { value: unknown; format?: Format; identity?: RowValues; field?: string | null }) {
  if (value === undefined) return <span className="flow-explorer-selection__empty-value">Missing</span>;
  if (value === null) return <span className="flow-explorer-selection__empty-value">No value</span>;
  if (value === '') return <span className="flow-explorer-selection__empty-value">Empty string</span>;
  return <ExplorerValue value={value} text={formattedText(value, format)} identity={identity} field={field} />;
}

export const explorerFieldValue = (row: ExplorerPreviewRow, field: Field) =>
  field.field === null ? undefined : propertyValue(row, field.field);

export function ExplorerFieldValue({ row, field }: { row: ExplorerPreviewRow; field: Field }) {
  const value = explorerFieldValue(row, field);
  return field.presentation !== 'value' && notEmpty(value)
    ? <Suspense fallback={<span role="status">Loading property preview…</span>}>
      <ExplorerPropertyPreview kind={field.presentation} value={value} label={field.label} />
    </Suspense> : <ExplorerPropertyValue value={value} format={field.format} identity={row.identity} field={field.field} />;
}

export function ExplorerObjectOverview({ row, columns = Object.keys(row.record), presentation, presentations, group, typeName }: {
  row: ExplorerPreviewRow; columns?: readonly string[]; presentation?: ExplorerOverviewPresentation;
  presentations?: Record<string, ExplorerPropertyPresentation>; group?: string; typeName: string;
}) {
  const fields = explorerFields(row, columns, presentation, presentations);
  const summary = group ? [] : fields.filter(field => field.summary).slice(0, 4);
  const details = group ? fields.filter(field => field.group === group) : fields.filter(field => !field.group);
  const heading = group ?? `Key ${typeName} Information`;
  return <div className="flow-explorer-overview">
    {!!summary.length && <dl className="flow-explorer-overview__summary" aria-label="Summary properties">
      {summary.map(field => <div key={field.key}><dt>{field.label}</dt><dd>
        <ExplorerPropertyValue value={explorerFieldValue(row, field)} format={field.format} identity={row.identity} field={field.field} />
      </dd></div>)}
    </dl>}
    {(details.length > 0 || group !== undefined) && <section className="flow-explorer-overview__information" aria-label={heading}>
      <h5><ProfileOutlined />{heading}</h5>
      {details.length > 0 ? <dl>{details.map(field => <div key={field.key}>
        <dt title={field.description || undefined}>{field.label}</dt><dd><ExplorerFieldValue row={row} field={field} /></dd>
      </div>)}</dl> : <p>No visible properties in this group.</p>}
    </section>}
  </div>;
}
