type CatalogKind = 'backed' | 'published';
const connectionId = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function publishedSourceFilter(value: string | null) {
  if (value === 'warehouse' || value === 'object-managed') return value;
  return value && connectionId.test(value) ? value.toLowerCase() : undefined;
}

function searchWithoutConnection(search: string) {
  const params = new URLSearchParams(search);
  for (const key of ['sourceId', 'batchSync', 'tableExport']) params.delete(key);
  return params;
}

export function dataConnectionHref(id?: string, search = '') {
  const params = searchWithoutConnection(search);
  if (id) params.set('sourceId', id);
  return '/ontology/connections' + (params.size ? '?' + params : '');
}

export function closeDataConnectionsHref(search: string) {
  const params = searchWithoutConnection(search);
  return '/ontology' + (params.size ? '?' + params : '');
}

export function ontologyCatalogHref(search: string, kind: CatalogKind) {
  const source = new URLSearchParams(search), params = new URLSearchParams();
  if (kind === 'published') {
    params.set('catalog', 'published');
    const filter = publishedSourceFilter(source.get('publishedSource'));
    if (filter) params.set('publishedSource', filter);
    const keyword = source.get('publishedQ')?.trim();
    if (keyword) params.set('publishedQ', keyword);
    const offset = Number(source.get('publishedOffset'));
    if (Number.isInteger(offset) && offset > 0 && offset <= 100_000) params.set('publishedOffset', String(offset));
    for (const key of ['publishedHidden', 'publishedDeleted']) if (source.get(key) === 'true') params.set(key, 'true');
  } else {
    const keyword = source.get('q');
    if (keyword?.trim()) params.set('q', keyword);
    const offset = Number(source.get('backedOffset'));
    if (Number.isInteger(offset) && offset > 0 && offset <= 1_000_000) params.set('backedOffset', String(offset));
    const group = source.get('group');
    if (group && ['favorites', 'ingestion', 'processing', 'serving', 'other'].includes(group)) params.set('group', group);
    const schema = source.get('source') || (!source.get('table') ? source.get('schema') : '');
    if (schema) params.set('source', schema);
  }
  if (source.get('view') === 'list') params.set('view', 'list');
  return '/ontology' + (params.size ? '?' + params : '');
}

export function catalogReturnHref(value: string | null | undefined, kind: CatalogKind) {
  return ontologyCatalogHref(value === '/ontology' || value?.startsWith('/ontology?') ? value.split('?')[1] || '' : '', kind);
}

export function compactExplorerSearch(search: string, kind: CatalogKind) {
  const params = new URLSearchParams(search), target = catalogReturnHref(params.get('returnTo'), kind);
  if (target === ontologyCatalogHref('', kind)) params.delete('returnTo');
  else params.set('returnTo', target);
  if (params.get('view') === 'preview') params.delete('view');
  return params.size ? '?' + params : '';
}

export function backedExplorerHref(schema: string, table: string, returnTo?: string) {
  const params = new URLSearchParams({ schema, table });
  if (returnTo) params.set('returnTo', returnTo);
  return '/ontology/explorer' + compactExplorerSearch(params.toString(), 'backed');
}
