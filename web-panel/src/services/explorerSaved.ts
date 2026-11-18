import client, { unwrap, type EtlDataQuery } from './api';
export type SavedKind = 'exploration' | 'list';
export type WorkbenchQuery = Pick<EtlDataQuery, 'filters' | 'sorts' | 'search' | 'partition'>;
export type Exploration = WorkbenchQuery & {
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
export type SavedDetail = SavedMeta & {
    owned: boolean;
    content: Exploration & {
        keys?: ListKey[];
    };
};
export function savedPath(schema: string, table: string) {
    return '/etl/' + encodeURIComponent(schema) + '/' + encodeURIComponent(table) + '/saved-items';
}
export async function listSaved(path: string, kind: SavedKind, offset: number, signal: AbortSignal) {
    return unwrap<{
        items: SavedMeta[];
        hasMore: boolean;
    }>((await client.get(path, { params: { kind, offset }, signal })).data);
}
export async function openSaved(path: string, id: string, signal?: AbortSignal) {
    return unwrap<SavedDetail>((await client.get(path + '/' + encodeURIComponent(id), { signal })).data);
}
export async function createSaved(path: string, kind: SavedKind, name: string, content: Exploration | {
    keys: ListKey[];
}) {
    return unwrap<SavedMeta>((await client.post(path, { kind, name, content })).data);
}
export async function sharing(path: string, item: SavedMeta, shared: boolean) {
    return unwrap<SavedMeta>((await client.put(path + '/' + item.id + '/sharing', { version: item.version, shared })).data);
}
export async function removeSaved(path: string, item: SavedMeta) {
    await client.delete(path + '/' + item.id, { params: { version: item.version } });
}
