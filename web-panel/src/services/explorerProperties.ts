import client, { unwrap } from './api';
import type { Presentation } from './explorerLayouts';

export type ExplorerPropertyType = 'string' | 'integer' | 'number' | 'boolean' | 'date' | 'datetime' | 'json';

export function explorerPropertyType(sqlType: string): ExplorerPropertyType {
  const type = sqlType.toLowerCase();
  if (type.endsWith('[]') || /^jsonb?$/.test(type)) return 'json';
  if (/^(smallint|integer|bigint|int[248]?|smallserial|serial[248]?|bigserial)$/.test(type)) return 'integer';
  if (/^(real|double precision|float[48]?|numeric|decimal)(\(.*\))?$/.test(type)) return 'number';
  if (type === 'boolean' || type === 'bool') return 'boolean';
  if (type === 'date') return 'date';
  if (type.startsWith('timestamp')) return 'datetime';
  return 'string';
}

export interface ExplorerMappedProperty {
  id: string;
  name: string;
  description: string;
  type: ExplorerPropertyType;
  sourceColumn: string | null;
  sqlType: string | null;
  primaryKeyPosition: number | null;
  visibility: 'normal' | 'prominent' | 'hidden';
  summary: boolean;
  group: string | null;
  status: 'active' | 'experimental' | 'deprecated';
  formatter: 'default' | 'number' | 'percent' | 'date' | 'dateTime';
  presentation: Presentation;
}

export interface ExplorerPropertyMapping {
  schema: string;
  table: string;
  dataType: string;
  version: string;
  sourceDefinitionId: string | null;
  titleProperty: string | null;
  displayName: string;
  description: string;
  icon: string;
  color: string;
  iconColor?: string;
  primaryKey: string[];
  groups: string[];
  properties: ExplorerMappedProperty[];
}

const pathFor = (schema: string, table: string) =>
  '/etl/' + encodeURIComponent(schema) + '/' + encodeURIComponent(table) + '/property-mapping';

export async function fetchExplorerProperties(schema: string, table: string, signal?: AbortSignal, definitionId?: string, connectionId?: string) {
  return unwrap<ExplorerPropertyMapping>((await client.get(pathFor(schema, table), { signal, params: { definitionId, connectionId } })).data);
}

export async function saveExplorerProperties(schema: string, table: string, mapping: ExplorerPropertyMapping) {
  const { version, sourceDefinitionId, titleProperty, displayName, description, icon, color, groups } = mapping;
  const properties = mapping.properties.map(({ sqlType, primaryKeyPosition, ...property }) => property);
  const body = { version, sourceDefinitionId, titleProperty, displayName, description, icon, color, groups, properties };
  return unwrap<ExplorerPropertyMapping>((await client.put(pathFor(schema, table), body)).data);
}
