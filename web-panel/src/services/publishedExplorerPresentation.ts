import type { PublishedOntologyType } from './ontology';
import type { ExplorerPropertyMapping, ExplorerPropertyType } from './explorerProperties';

const sqlTypes: Record<string, string> = { string: 'text', integer: 'bigint', number: 'numeric', boolean: 'boolean', date: 'date', datetime: 'timestamp', json: 'jsonb' };

export function publishedColumnTypes(objectType: PublishedOntologyType) {
  return Object.fromEntries(objectType.definition.properties.map(property => [property.id, sqlTypes[property.type]]));
}

export function publishedPresentation(objectType: PublishedOntologyType): ExplorerPropertyMapping {
  const definition = objectType.definition;
  return {
    schema: '', table: definition.id, dataType: definition.id, version: String(objectType.revision), sourceDefinitionId: objectType.id,
    titleProperty: definition.titleKey, displayName: definition.name, description: definition.description,
    icon: definition.icon, color: definition.color, iconColor: definition.iconColor,
    primaryKey: definition.source.mode === 'none' ? ['$recordId'] : definition.primaryKey, groups: [],
    properties: definition.properties.map(property => ({
      id: property.id, name: property.name, description: '', type: property.type as ExplorerPropertyType,
      sourceColumn: property.id, sqlType: sqlTypes[property.type], primaryKeyPosition: definition.primaryKey.includes(property.id) ? definition.primaryKey.indexOf(property.id) : null,
      visibility: 'normal', summary: false, group: null, status: 'active', formatter: 'default', presentation: 'value',
    })),
  };
}
