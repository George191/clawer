import type { SemanticDefinition, SemanticProperty } from '../../services/ontology';
import type { EtlObjectPreview } from '../../services/types';
import { explorerPropertyType } from '../../services/explorerProperties';

type PropertySettings = Pick<SemanticDefinition, 'properties' | 'primaryKey' | 'titleKey'>;

export function initialProperties(existing: boolean): PropertySettings {
  return existing ? { properties: [], primaryKey: [], titleKey: '' } : {
    properties: [
      { id: 'id', name: 'ID', type: 'string', required: true },
      { id: 'name', name: 'Name', type: 'string', required: false },
    ],
    primaryKey: ['id'], titleKey: 'name',
  };
}

export function sourceProperties(preview: EtlObjectPreview): PropertySettings {
  const properties: SemanticProperty[] = preview.properties.map(column => ({
    id: column.name, name: column.displayName || column.name, type: explorerPropertyType(column.type),
    sourceColumn: column.name, required: !column.nullable,
  }));
  const candidates = [preview.titleProperty, 'name', 'title', ...preview.primaryKey];
  const title = candidates.find(id => properties.some(p => p.id === id && p.type !== 'json'))
    ?? properties.find(p => p.type !== 'json')?.id;
  return { properties, primaryKey: [...preview.primaryKey], titleKey: title ?? '' };
}

type PropertyIssue = { index?: number; field?: keyof SemanticProperty; message: string };

export function propertyRemovalReason(definition: SemanticDefinition, property: SemanticProperty, preview?: EtlObjectPreview) {
  if (property.sourceColumn && preview?.primaryKey.includes(property.sourceColumn)) return 'The source primary key must stay mapped.';
  if (property.id && definition.primaryKey.includes(property.id)) return 'Assign a replacement primary key before removing this property.';
  if (property.id && definition.titleKey === property.id) return 'Choose another title property before removing this property.';
  if (definition.security.recordRules?.some(rule => rule.kind === 'property' && rule.field === property.id)) return 'Update record access rules before removing this property.';
  return '';
}

export function propertyIssue(definition: SemanticDefinition, preview?: EtlObjectPreview): PropertyIssue | undefined {
  const {properties, primaryKey, titleKey} = definition;
  if (!properties.length) return { message: 'Create a property for the primary key and select a title property.' };
  const ids = new Set<string>(), columns = new Set<string>();
  for (const [index, p] of properties.entries()) {
    const issue = (field: keyof SemanticProperty, reason: string) => ({
      index, field, message: `Property ${index + 1}${p.name || p.id ? ' (' + (p.name || p.id) + ')' : ''} · ${reason}`,
    });
    if (!p.name.trim()) return issue('name', 'Name is required.');
    if (p.name.trim().length > 120) return issue('name', 'Name must be at most 120 characters.');
    if (!/^[a-z][a-z0-9_-]{0,62}$/.test(p.id)) return issue('id', 'API name must start with a lowercase letter and use up to 63 lowercase letters, digits, underscores or hyphens.');
    if (ids.has(p.id)) return issue('id', `API name “${p.id}” is already in use.`);
    ids.add(p.id);
    if (definition.source.mode === 'existing') {
      const column = preview?.properties.find(c => c.name === p.sourceColumn);
      if (!column) return issue('sourceColumn', 'Choose a source column, or remove this property from the mapping.');
      if (columns.has(column.name)) return issue('sourceColumn', `Source column “${column.name}” is already mapped.`);
      columns.add(column.name);
      if (p.type !== explorerPropertyType(column.type)) return issue('type', `Base type must match source column “${column.name}” (${column.type}).`);
      if (p.required && column.nullable) return issue('required', `Source column “${column.name}” allows null values. Clear Required.`);
    }
    if (primaryKey.includes(p.id) && ['number', 'json'].includes(p.type)) return issue('type', 'Primary key must be a string, integer, boolean, date or datetime.');
    if (primaryKey.includes(p.id) && !p.required) return issue('required', 'A primary key property must be required.');
    if (p.id === titleKey && p.type === 'json') return issue('type', 'Choose a scalar base type or assign Title key to another property.');
  }
  if (!primaryKey.length) return { message: 'Select at least one primary key in Configuration.' };
  if (primaryKey.length > 16 || new Set(primaryKey).size !== primaryKey.length || primaryKey.some(key => !ids.has(key))) return { message: 'Primary key must reference 1 to 16 distinct properties.' };
  if (!ids.has(titleKey)) return { message: 'Select a scalar property and enable Title key in Configuration.' };
  return undefined;
}
