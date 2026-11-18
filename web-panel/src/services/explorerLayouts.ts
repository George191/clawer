import client, { unwrap } from './api';

export type ExplorerSort = { field: string; direction: 'asc' | 'desc' };
export type Presentation = 'value' | 'timeSeries' | 'geoJson' | 'media';
export type ResultColumn = { field: string; visible: boolean; width: number; presentation: Presentation };
export type ResultLayout = { columns: ResultColumn[]; wrap: boolean; sorts: ExplorerSort[] };
export type LayoutSlot = { version: string | null; layout: ResultLayout | null };
export type Layouts = { personal: LayoutSlot; default: LayoutSlot };

export function layoutPath(schema: string, table: string) {
  return '/etl/' + encodeURIComponent(schema) + '/' + encodeURIComponent(table) + '/result-layout';
}

export async function readLayouts(path: string, signal: AbortSignal) {
  return unwrap<Layouts>((await client.get(path, { signal })).data);
}

export async function writeLayout(path: string, scope: keyof Layouts, slot: LayoutSlot) {
  return unwrap<LayoutSlot>((await client.put(path + '/' + scope, slot)).data);
}
