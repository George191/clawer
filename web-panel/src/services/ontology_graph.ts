import client from './api';
import type { OntologyRecord } from './ontology';

export type Page<T> = { items: T[]; hasMore: boolean };
export type LinkType = {
  id: string;
  key: string;
  name: string;
  sourceDefinitionId: string;
  targetDefinitionId: string;
  cardinality: string;
};
export type SetQuery = {
  filters: { field: string; op: 'eq' | 'contains' | 'gt' | 'lt'; value: unknown }[];
  groupBy?: string;
  sumField?: string;
};
export type SetResult = Page<OntologyRecord> & {
  count: number;
  sum: number | string | null;
  groups: { value: unknown; count: number; sum: number | string | null }[];
  groupsHasMore: boolean;
  query?: SetQuery;
};
export type NamedSet = { id: string; name: string };
export type Neighbor = { linkId: string; canUnlink: boolean; record: OntologyRecord & { id: string } };

export async function graphRequest<T>(
  method: 'get' | 'post' | 'put' | 'delete',
  url: string,
  options: { data?: unknown; params?: Record<string, unknown>; signal?: AbortSignal } = {},
) {
  const response = await client.request<T | { code: number; data: T }>({
    baseURL: '/api/ontology/v1',
    method,
    url,
    ...options,
  });
  const body = response.data;
  return (body && typeof body === 'object' && 'code' in body && 'data' in body ? body.data : body) as T;
}
