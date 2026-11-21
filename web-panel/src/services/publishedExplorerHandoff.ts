import type { OntologyRecord } from './ontology';
import type { SetQuery } from './ontology_graph';

export type PublishedIdentity = Record<string, string>;
export type PublishedExplorerQuery = SetQuery & {
  keys?: PublishedIdentity[];
  sorts?: { field: string; direction: 'asc' | 'desc' }[];
};
export type PublishedExploration = { query: PublishedExplorerQuery };
export type PublishedExplorerEntry = {
  definitionId: string;
  query: PublishedExplorerQuery;
  savedSet: string;
  saved: string;
  returnTo: string;
};
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const property = /^[a-z][a-z0-9_-]{0,62}$/;
const object = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value);

export function validatePublishedExplorerQuery(value: unknown): PublishedExplorerQuery {
  if (!object(value) || Object.keys(value).some(key => !['filters', 'groupBy', 'sumField', 'keys', 'sorts'].includes(key))) throw new Error('Invalid published object set.');
  if (!Array.isArray(value.filters) || value.filters.length > 20 || value.filters.some(item => !object(item)
    || Object.keys(item).some(key => !['field', 'op', 'value'].includes(key)) || typeof item.field !== 'string' || !property.test(item.field)
    || !['eq', 'contains', 'gt', 'lt'].includes(String(item.op)) || !Object.prototype.hasOwnProperty.call(item, 'value'))) throw new Error('Invalid published object filters.');
  for (const field of ['groupBy', 'sumField']) if (value[field] != null && (typeof value[field] !== 'string' || !property.test(value[field]))) throw new Error('Invalid published aggregation property.');
  if (value.sorts !== undefined && (!Array.isArray(value.sorts) || value.sorts.length > 20 || value.sorts.some(item => !object(item)
    || Object.keys(item).some(key => !['field', 'direction'].includes(key)) || typeof item.field !== 'string' || !property.test(item.field) || !['asc', 'desc'].includes(String(item.direction))))) throw new Error('Invalid published object sorting.');
  if (value.keys !== undefined && (!Array.isArray(value.keys) || value.keys.length > 100 || value.keys.some(key => !object(key)
    || !Object.keys(key).length || Object.entries(key).some(([field, item]) => (field !== '$recordId' && !property.test(field)) || typeof item !== 'string' || item.length > 4096)))) throw new Error('Invalid published object selection.');
  return value as PublishedExplorerQuery;
}

export function parsePublishedExplorerEntry(search: string, hash = ''): PublishedExplorerEntry {
  const params = new URLSearchParams(search);
  const fragment = new URLSearchParams(hash.replace(/^#/, ''));
  const definitionId = params.get('objectType') || '';
  if (!uuid.test(definitionId)) throw new Error('Select a valid published object type.');
  if (params.has('schema') || params.has('table')) throw new Error('Published objects and physical tables must be opened separately.');
  const savedSet = params.get('savedSet') || '', saved = params.get('saved') || '';
  if ((savedSet && !uuid.test(savedSet)) || (saved && !uuid.test(saved))) throw new Error('Invalid saved object set link.');
  if (params.has('objectSet')) throw new Error('Object set scope must be carried in the URL fragment.');
  if (savedSet && fragment.has('objectSet')) throw new Error('The link contains two object set scopes.');
  const serialized = fragment.get('objectSet');
  if (serialized && serialized.length > 131072) throw new Error('The object set link is too large.');
  let query: PublishedExplorerQuery = { filters: [] };
  if (serialized !== null) {
    try { query = validatePublishedExplorerQuery(JSON.parse(serialized)); }
    catch (error) { throw new Error(error instanceof SyntaxError ? 'The object set link is not valid JSON.' : (error as Error).message); }
  }
  const requestedReturn = params.get('returnTo');
  const returnTo = requestedReturn === '/ontology' || requestedReturn?.startsWith('/ontology?') ? requestedReturn : '/ontology?catalog=published&objectType=' + definitionId;
  return { definitionId, query, savedSet, saved, returnTo };
}

export function publishedExplorerHref(definitionId: string, options: { query?: PublishedExplorerQuery; savedSet?: string; saved?: string; returnTo?: string } = {}) {
  const params = new URLSearchParams({ objectType: definitionId });
  const fragment = new URLSearchParams();
  if (options.query) fragment.set('objectSet', JSON.stringify(options.query));
  if (options.savedSet) params.set('savedSet', options.savedSet);
  if (options.saved) params.set('saved', options.saved);
  if (options.returnTo) params.set('returnTo', options.returnTo);
  parsePublishedExplorerEntry(params.toString(), fragment.toString());
  return '/ontology/explorer?' + params + (fragment.size ? '#' + fragment : '');
}

export function publishedRecordIdentity(record: OntologyRecord & { identity?: PublishedIdentity | null }): PublishedIdentity | null {
  return record.identity ?? null;
}
