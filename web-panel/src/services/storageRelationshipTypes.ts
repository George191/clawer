import client, { unwrap } from './api';

export type StorageRelationshipType = {
  id: string; name: string; description: string; revision: string; enabled: boolean;
  source: { schema: string; table: string; fields: string[] };
  target: { schema: string; table: string; fields: string[] };
  cardinality: 'one_to_one' | 'one_to_many' | 'many_to_one' | 'many_to_many';
  direction: 'incoming' | 'outgoing';
};
export type StorageRelationshipTypesPage = { items: StorageRelationshipType[]; canManage: boolean };
export type StorageRelationshipTypeCreate = {
  name: string; description: string; cardinality: StorageRelationshipType['cardinality'];
  target: { schema: string; table: string }; direction: StorageRelationshipType['direction'];
};
const path = (schema: string, table: string) => `/etl/${encodeURIComponent(schema)}/${encodeURIComponent(table)}/relationship-types`;

export const fetchStorageRelationshipTypes = (schema: string, table: string, signal: AbortSignal) =>
  client.get(path(schema, table), { signal }).then(result => unwrap<StorageRelationshipTypesPage>(result.data));

export const createStorageRelationshipType = (schema: string, table: string, payload: StorageRelationshipTypeCreate, signal: AbortSignal) =>
  client.post(path(schema, table), payload, { signal }).then(result => unwrap<StorageRelationshipType>(result.data));

export const saveStorageRelationshipType = (schema: string, table: string, id: string,
  payload: { name: string; description: string; expectedRevision: string }, signal: AbortSignal) =>
  client.patch(`${path(schema, table)}/${encodeURIComponent(id)}`, payload, { signal })
    .then(result => unwrap<StorageRelationshipType>(result.data));
