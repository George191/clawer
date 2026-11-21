import client, { unwrap } from './api';

export type ExplorerSort = { field: string; direction: 'asc' | 'desc' };
export type Presentation = 'value' | 'timeSeries' | 'geoJson' | 'media';
export type ResultColumn = { field: string; visible: boolean; width: number; presentation: Presentation };
export type ResultLayout = { columns: ResultColumn[]; wrap: boolean; sorts: ExplorerSort[] };
export type LayoutSlot = { version: string | null; layout: ResultLayout | null };
export type LayoutScope = 'personal' | 'default';
export type Layouts = { personal: LayoutSlot; default: LayoutSlot; canManageDefault?: boolean };
export type LayoutEndpoint = { path: string; baseURL?: string };

export function layoutPath(schema: string, table: string) {
  return '/etl/' + encodeURIComponent(schema) + '/' + encodeURIComponent(table) + '/result-layout';
}

export function publishedLayoutPath(definitionId: string): LayoutEndpoint {
  return { path: '/object-types/' + encodeURIComponent(definitionId) + '/explorer/result-layout', baseURL: '/api/ontology/v1' };
}

export async function readLayouts(target: LayoutEndpoint | string, signal: AbortSignal) {
  const endpoint = typeof target === 'string' ? { path: target } : target;
  return unwrap<Layouts>((await client.get(endpoint.path, { baseURL: endpoint.baseURL, signal })).data);
}

export async function writeLayout(target: LayoutEndpoint | string, scope: LayoutScope, slot: LayoutSlot) {
  const endpoint = typeof target === 'string' ? { path: target } : target;
  return unwrap<LayoutSlot>((await client.put(endpoint.path + '/' + scope, slot, { baseURL: endpoint.baseURL })).data);
}
