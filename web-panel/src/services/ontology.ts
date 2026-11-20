import client from './api';

export type SemanticSource = { mode: 'existing' | 'none'; schema?: string; table?: string; upstreamConnectionId?: string | null };
export type SemanticProperty = { id: string; name: string; type: string; sourceColumn?: string; required: boolean };
export type Principal = { kind: 'user' | 'group' | 'organization'; id: string };
export type SemanticAction = { kind: 'create' | 'modify' | 'delete'; submitters: Principal[]; fields: string[] };
export type FunctionOperand = {parameter: string} | {value: unknown};
export type FunctionAssignment = {field: string; operation: 'set'|'add'|'multiply'|'concat'; operands: FunctionOperand[]};
export type BusinessFunction = {key: string; name: string; assignments: FunctionAssignment[]};
export type RecordRule =
  | { kind: 'property'; readers: Principal[]; field: string; match: 'equals' | 'currentUser'; value?: string | boolean | number | null }
  | { kind: 'related'; readers: Principal[]; linkType: string; direction: 'incoming' | 'outgoing'; relatedRecordId: string };
export type SemanticSecurity = { mode: 'inherit' | 'explicit'; objectReaders: Principal[]; fieldReaders: Record<string, Principal[]>; recordRules?: RecordRule[]; markings?: string[] };
export type SemanticDefinition = {
  id: string; name: string; pluralName: string; description: string; icon: string; color: string; iconColor?: string; groups: string[];
  projectId: string; source: SemanticSource; properties: SemanticProperty[]; primaryKey: string[]; titleKey: string;
  actions: SemanticAction[]; security: SemanticSecurity; functions?: BusinessFunction[];
};
export type OntologyDraft = { id: string; revision: number; definition: SemanticDefinition };
export type OntologyIssue = { path: string; message: string };
export type OntologyValidation = { errors: OntologyIssue[]; warnings: OntologyIssue[] };
export type OntologyProject = { id: string; name: string; teamName: string; tenantId: string; ownerId: string | null; canName: boolean };
export type OntologyPrincipal = Principal & { name: string; email?: string | null };
export type PublishedOntologyType = { id: string; revision: number; definition: SemanticDefinition };
const config = { baseURL: '/api/ontology/v1' };
export function newOntologyRequestId() {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 15) | 64;
  bytes[8] = (bytes[8] & 63) | 128;
  const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('');
  return [hex.slice(0,8), hex.slice(8,12), hex.slice(12,16), hex.slice(16,20), hex.slice(20)].join('-');
}
const data = <T,>(body: T | { code: number; data: T }): T => body && typeof body === 'object' && 'code' in body && 'data' in body ? body.data : body as T;
const draftPath = (id: string) => '/drafts/' + encodeURIComponent(id);

export function ontologyError(error: unknown, fallback: string): string {
  const body = (error as { response?: { data?: { errors?: unknown; detail?: unknown } } })?.response?.data;
  const detail = body?.errors ?? body?.detail;
  if (typeof detail === 'string' && detail) return detail;
  const issues = detail && typeof detail === 'object' && 'errors' in detail ? detail.errors : detail;
  if (Array.isArray(issues)) {
    const messages = issues.flatMap(item => {
      if (typeof item === 'string') return [item];
      if (!item || typeof item !== 'object') return [];
      if (typeof item.message === 'string') return [typeof item.path === 'string' && item.path ? `${item.path} · ${item.message}` : item.message];
      if (typeof item.msg === 'string') return [item.msg];
      return Object.entries(item).flatMap(([field, message]) => typeof message === 'string' ? [`${field} · ${message}`] : []);
    }).filter(Boolean);
    if (messages.length) return messages.join('\n');
  }
  return !body && error instanceof Error && error.message ? error.message : fallback;
}

export const listOntologyProjects = () => client.get<{items: OntologyProject[]}>('/projects', config).then(r => data(r.data).items);
export const listOntologyTypeGroups = (q: string, signal: AbortSignal) =>
  client.get<{items: string[]}>('/type-groups', { ...config, params: { q }, signal }).then(r => data(r.data).items);
export const createOntologyProject = (teamId: string, name: string) =>
  client.post<OntologyProject>('/projects', { teamId, name }, config).then(r => data(r.data));
export const listOntologyPrincipals = (projectId: string, search = '', signal?: AbortSignal, userIds: string[] = []) => {
  const params = new URLSearchParams({ search });
  userIds.forEach(id => params.append('user_id', id));
  return client.get<{items: OntologyPrincipal[]}>('/projects/' + encodeURIComponent(projectId) + '/principals', { ...config, params, signal }).then(r => data(r.data).items);
};
export const listOntologyDrafts = () => client.get<{items: OntologyDraft[]}>('/drafts', config).then(r => data(r.data).items);
export const fetchOntologyDraft = (id: string) => client.get<OntologyDraft>(draftPath(id), config).then(r => data(r.data));
export const createOntologyDraft = (definition: SemanticDefinition) => client.post<OntologyDraft>('/drafts', definition, config).then(r => data(r.data));
export const updateOntologyDraft = (id: string, revision: number, definition: SemanticDefinition) => client.put<OntologyDraft>(draftPath(id), { revision, definition }, config).then(r => data(r.data));
export const validateOntologyDraft = (id: string, revision: number) => client.post<OntologyValidation>(draftPath(id) + '/validate', { revision }, config).then(r => data(r.data));
export const publishOntologyDraft = (id: string, revision: number) => client.post<{ id: string; revision: number; objectType: SemanticDefinition }>(draftPath(id) + '/publish', { revision }, config).then(r => data(r.data));
export const testOntologyPolicy = (definition: SemanticDefinition, userId: string) => client.post<{ visible: boolean; visibleFields: string[] }>('/security/preview', { projectId: definition.projectId, userId, sourceMode: definition.source.mode, properties: definition.properties.map(item => item.id), security: definition.security }, config).then(r => data(r.data));
export type PublishedCatalogItem = PublishedOntologyType & { hidden: boolean; deleted: boolean };
export type PublishedOntologyPage = { items: PublishedCatalogItem[]; hasMore: boolean; total: number };
export const listPublishedOntologyTypes = (limit: number, offset: number, q: string, projectId?: string, signal?: AbortSignal, includeHidden = false, includeDeleted = false) =>
  client.get<PublishedOntologyPage>('/object-types', { ...config, params: { limit, offset, q, projectId, includeHidden, includeDeleted }, signal }).then(r => data(r.data));
export const changePublishedCatalogState = (id: string, revision: number, operation: 'hide' | 'unhide' | 'delete' | 'restore') =>
  client.patch('/object-types/' + encodeURIComponent(id) + '/catalog-state', { revision, operation }, config);
export const fetchPublishedOntologyType = (id: string, signal?: AbortSignal) =>
  client.get<PublishedOntologyType>('/object-types/' + encodeURIComponent(id), { ...config, signal }).then(r => data(r.data));


export type OntologyRecord = { id: string | null; revision: number | null; properties: Record<string, unknown>; title: unknown };
export type OntologyRecordPage = { items: OntologyRecord[]; hasMore: boolean; actions?: Array<{ kind: SemanticAction['kind']; fields: string[] }> };
type ActionInput = { values: Record<string, unknown> } | { recordId: string; expectedRevision: number; values?: Record<string, unknown> };
export const listOntologyRecords = (id: string, limit: number, offset: number, deleted = false, signal?: AbortSignal) =>
  client.get<OntologyRecordPage>('/object-types/' + encodeURIComponent(id) + '/records', { ...config, signal, params: { limit, offset, deleted } }).then(r => data(r.data));
export type OntologyRecordHistoryPage = { items: Array<{ kind: SemanticAction['kind']; revision: number; createdAt: string;
  changes?: Record<string, { before?: unknown; after?: unknown }> | null; canUndo: boolean }>; hasMore: boolean };
export const listOntologyRecordHistory = (id: string, recordId: string, limit: number, offset: number, signal: AbortSignal) =>
  client.get<OntologyRecordHistoryPage>('/object-types/' + encodeURIComponent(id) + '/records/' + encodeURIComponent(recordId) + '/history', { ...config, params: { limit, offset }, signal }).then(r => data(r.data));
export const submitOntologyAction = (id: string, kind: SemanticAction['kind'], body: ActionInput) =>
  client.post<{ id: string; revision: number; deleted: boolean }>('/object-types/' + encodeURIComponent(id) + '/actions/' + kind, body, config).then(r => data(r.data));

export type TransactionStep =
  | { kind: 'create'; definitionId: string; values: Record<string, unknown>; alias: string }
  | { kind: 'modify' | 'delete'; definitionId: string; recordId: string; expectedRevision: number; values: Record<string, unknown> }
  | { kind: 'link' | 'unlink'; linkTypeId: string; source: string; target: string };
export type TransactionReceipt = {id: string; name: string; stepCount: number; createdAt: string; undoneAt: string | null};
const txPath = (project: string) => '/projects/' + encodeURIComponent(project) + '/transactions';
export const listTransactions = (project: string, offset: number) =>
  client.get<{items: TransactionReceipt[]; hasMore: boolean}>(txPath(project), {...config, params: {offset}}).then(r => data(r.data));
export const applyTransaction = (project: string, name: string, requestId: string, steps: TransactionStep[]) =>
  client.post<TransactionReceipt>(txPath(project), {name, requestId, steps}, config).then(r => data(r.data));
export const undoTransaction = (project: string, id: string) =>
  client.post<TransactionReceipt>(txPath(project) + '/' + encodeURIComponent(id) + '/undo', {}, config).then(r => data(r.data));
export const undoOntologyEdit = (type: string, record: string, revision: number) =>
  client.post('/object-types/' + encodeURIComponent(type) + '/records/' + encodeURIComponent(record) + '/history/' + revision + '/undo', {}, config);
