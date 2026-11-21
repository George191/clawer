import { graphRequest } from './ontology_graph';
import type { SetQuery, SetResult } from './ontology_graph';
import type { OntologyRecord, OntologyRecordPage } from './ontology';
import type { PublishedExplorerQuery, PublishedIdentity } from './publishedExplorerHandoff';

export type PublishedExplorerRecord = OntologyRecord & { identity: PublishedIdentity | null; hasHistory: boolean };
export type PublishedExplorerResult = Omit<SetResult, 'items'> & { items: PublishedExplorerRecord[]; actions?: OntologyRecordPage['actions'] };
export function queryPublishedExplorer(definitionId: string, query: PublishedExplorerQuery, limit: number, offset: number, signal: AbortSignal, groupOffset = 0) {
  return graphRequest<PublishedExplorerResult>('post', '/object-types/' + encodeURIComponent(definitionId) + '/explorer/query', { data: query, params: { limit, offset, groupOffset }, signal });
}
export function loadPublishedSavedSet(definitionId: string, setId: string, signal: AbortSignal) {
  return graphRequest<SetResult & { query: SetQuery; name: string }>('get', '/object-types/' + encodeURIComponent(definitionId) + '/sets/' + encodeURIComponent(setId), { params: { limit: 1 }, signal });
}
