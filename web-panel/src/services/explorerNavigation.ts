type CatalogKind = 'backed' | 'published';

export function ontologyCatalogHref(search: string, kind: CatalogKind) {
  const source = new URLSearchParams(search), params = new URLSearchParams();
  if (kind === 'published') {
    params.set('catalog', 'published');
    const keyword = source.get('publishedQ')?.trim();
    if (keyword) params.set('publishedQ', keyword);
    const offset = Number(source.get('publishedOffset'));
    if (Number.isInteger(offset) && offset > 0 && offset <= 100_000) params.set('publishedOffset', String(offset));
    for (const key of ['publishedHidden', 'publishedDeleted']) if (source.get(key) === 'true') params.set(key, 'true');
  } else {
    const keyword = source.get('q');
    if (keyword?.trim()) params.set('q', keyword);
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
