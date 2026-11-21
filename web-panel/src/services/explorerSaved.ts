import client, { unwrap, type EtlDataQuery } from './api';
export type SavedKind = 'exploration' | 'list';
export type WorkbenchQuery = Pick<EtlDataQuery, 'filters' | 'sorts' | 'search' | 'partition'>;
export type Exploration<Q extends object = WorkbenchQuery> = Q & {
    chart: string | null;
    view: 'explore' | 'results';
};
export type SavedMeta = {
    id: string;
    kind: SavedKind;
    name: string;
    shared: boolean;
    version: string;
};
export type ListKey = Record<string, string>;
export type SavedDetail<Q extends object = WorkbenchQuery> = SavedMeta & {
    owned: boolean;
    content: Exploration<Q> & {
        keys?: ListKey[];
    };
};
export type SavedEndpoint = { path: string; baseURL?: string };
export function savedPath(schema: string, table: string): SavedEndpoint {
    return { path: '/etl/' + encodeURIComponent(schema) + '/' + encodeURIComponent(table) + '/saved-items' };
}
export function publishedSavedPath(definitionId: string): SavedEndpoint {
    return { path: '/object-types/' + encodeURIComponent(definitionId) + '/explorer/saved-items', baseURL: '/api/ontology/v1' };
}
export async function listSaved(endpoint: SavedEndpoint, kind: SavedKind, offset: number, signal: AbortSignal, search = '', sharing: 'all' | 'private' | 'shared' = 'all') {
    return unwrap<{
        items: SavedMeta[];
        hasMore: boolean;
    }>((await client.get(endpoint.path, { baseURL: endpoint.baseURL, params: { kind, offset, search, sharing }, signal })).data);
}
export async function openSaved<Q extends object = WorkbenchQuery>(endpoint: SavedEndpoint, id: string, signal?: AbortSignal) {
    return unwrap<SavedDetail<Q>>((await client.get(endpoint.path + '/' + encodeURIComponent(id), { baseURL: endpoint.baseURL, signal })).data);
}
export async function createSaved<Q extends object = WorkbenchQuery>(endpoint: SavedEndpoint, kind: SavedKind, name: string, content: Exploration<Q> | {
    keys: ListKey[];
}) {
    return unwrap<SavedMeta>((await client.post(endpoint.path, { kind, name, content }, { baseURL: endpoint.baseURL })).data);
}
export async function sharing(endpoint: SavedEndpoint, item: SavedMeta, shared: boolean) {
    return unwrap<SavedMeta>((await client.put(endpoint.path + '/' + item.id + '/sharing', { version: item.version, shared }, { baseURL: endpoint.baseURL })).data);
}
export async function removeSaved(endpoint: SavedEndpoint, item: SavedMeta) {
    await client.delete(endpoint.path + '/' + item.id, { baseURL: endpoint.baseURL, params: { version: item.version } });
}
