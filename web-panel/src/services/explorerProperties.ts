import client, { unwrap } from './api';
import type { Presentation } from './explorerLayouts';

export type ExplorerPropertyType = 'string' | 'integer' | 'number' | 'boolean' | 'date' | 'datetime' | 'json';
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
  version: string;
  sourceDefinitionId: string | null;
  titleProperty: string | null;
  displayName: string;
  description: string;
  icon: string;
  color: string;
  primaryKey: string[];
  groups: string[];
  properties: ExplorerMappedProperty[];
}

const pathFor = (schema: string, table: string) =>
  '/etl/' + encodeURIComponent(schema) + '/' + encodeURIComponent(table) + '/property-mapping';

export async function fetchExplorerProperties(schema: string, table: string, signal?: AbortSignal, definitionId?: string) {
  return unwrap<ExplorerPropertyMapping>((await client.get(pathFor(schema, table), { signal, params: { definitionId } })).data);
}

export async function saveExplorerProperties(schema: string, table: string, mapping: ExplorerPropertyMapping) {
  const { version, sourceDefinitionId, titleProperty, displayName, description, icon, color, groups } = mapping;
  const properties = mapping.properties.map(({ sqlType, primaryKeyPosition, ...property }) => property);
  const body = { version, sourceDefinitionId, titleProperty, displayName, description, icon, color, groups, properties };
  return unwrap<ExplorerPropertyMapping>((await client.put(pathFor(schema, table), body)).data);
}
