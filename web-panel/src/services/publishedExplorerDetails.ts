import { apiUrl } from './endpoints';
import { graphRequest, type LinkType, type Neighbor, type Page } from './ontology_graph';
import type { OntologyRecord, OntologyRecordPage } from './ontology';
import type { ExplorerStorage } from './api';
import type { PublishedExplorerRecord } from './publishedExplorer';

export type ExplorerDetailSource = { kind: 'backed'; schema: string; table: string } | { kind: 'published'; definitionId: string };
const path = (definitionId: string) => '/object-types/' + encodeURIComponent(definitionId) + '/explorer';

export const fetchPublishedStorage = (definitionId: string, signal: AbortSignal) =>
  graphRequest<ExplorerStorage>('get', path(definitionId) + '/object-storage', { signal });
export type PublishedRecordDetails = PublishedExplorerRecord & { actions: NonNullable<OntologyRecordPage['actions']> };
export const fetchPublishedRecord = (definitionId: string, key: Record<string, unknown>, signal: AbortSignal) =>
  graphRequest<PublishedRecordDetails>('post', path(definitionId) + '/record', { data: { key }, signal });
export const fetchPublishedHtml = (definitionId: string, key: Record<string, unknown>, field: string, signal?: AbortSignal) =>
  graphRequest<{ html: string }>('post', path(definitionId) + '/record-html', { data: { key, field }, signal });
export async function fetchPublishedFile(definitionId: string, key: Record<string, unknown>, field: string, download: boolean, signal?: AbortSignal, resourcePath: (string | number)[] = []) {
  const result = await graphRequest<{ url: string }>('post', path(definitionId) + '/record-file', { data: { key, field, download, path: resourcePath }, signal });
  return { url: apiUrl(result.url) };
}

export type PublishedRelationship = LinkType & { direction: 'incoming' | 'outgoing'; targetDefinitionId: string };
export type PublishedRelationships = { relations: PublishedRelationship[]; items: OntologyRecord[]; selected?: string; hasMore: boolean; offset: number };
export async function fetchPublishedRelationships(definitionId: string, recordId: string, relation: string | undefined, offset: number, signal: AbortSignal): Promise<PublishedRelationships> {
  const types: LinkType[] = [];
  let more = true;
  while (more) {
    const page = await graphRequest<Page<LinkType>>('get', '/object-types/' + encodeURIComponent(definitionId) + '/link-types', { params: { limit: 100, offset: types.length }, signal });
    types.push(...page.items); more = page.hasMore;
  }
  const relations = types.flatMap(item => [
    ...(item.sourceDefinitionId === definitionId ? [{ ...item, direction: 'outgoing' as const, targetDefinitionId: item.targetDefinitionId }] : []),
    ...(item.targetDefinitionId === definitionId ? [{ ...item, direction: 'incoming' as const, targetDefinitionId: item.sourceDefinitionId }] : []),
  ]);
  const selected = relations.find(item => item.id + ':' + item.direction === relation) ?? (relation ? undefined : relations[0]);
  if (!selected) return { relations, items: [], hasMore: false, offset };
  const page = await graphRequest<Page<Neighbor>>('get', '/link-types/' + selected.id + '/records/' + recordId + '/neighbors', { params: { direction: selected.direction, limit: 10, offset }, signal });
  return { relations, items: page.items.map(item => item.record), selected: selected.id + ':' + selected.direction, hasMore: page.hasMore, offset };
}
