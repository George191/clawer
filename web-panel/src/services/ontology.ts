import client from './api';

export type SemanticSource = { mode: 'existing' | 'none'; schema?: string; table?: string };
export type SemanticProperty = { id: string; name: string; type: string; sourceColumn?: string; required: boolean };
export type Principal = { kind: 'user' | 'group' | 'organization'; id: string };
export type SemanticAction = { kind: 'create' | 'modify' | 'delete'; submitters: Principal[]; fields: string[] };
export type SemanticSecurity = { mode: 'inherit' | 'explicit'; objectReaders: Principal[]; fieldReaders: Record<string, Principal[]> };
export type SemanticDefinition = {
  id: string; name: string; pluralName: string; description: string; icon: string; color: string; groups: string[];
  projectId: string; source: SemanticSource; properties: SemanticProperty[]; primaryKey: string[]; titleKey: string;
  actions: SemanticAction[]; security: SemanticSecurity;
};
export type OntologyDraft = { id: string; revision: number; definition: SemanticDefinition };
export type OntologyIssue = { path: string; message: string };
export type OntologyValidation = { errors: OntologyIssue[]; warnings: OntologyIssue[] };
export type OntologyProject = { id: string; name: string; teamName: string; tenantId: string; ownerId: string | null; canName: boolean };
export type OntologyPrincipal = Principal & { name: string };
export type PublishedOntologyType = { id: string; revision: number; definition: SemanticDefinition };
const config = { baseURL: '/api/ontology/v1' };
const data = <T,>(body: T | { code: number; data: T }): T => body && typeof body === 'object' && 'code' in body && 'data' in body ? body.data : body as T;
const draftPath = (id: string) => '/drafts/' + encodeURIComponent(id);

export const listOntologyProjects = () => client.get<{items: OntologyProject[]}>('/projects', config).then(r => data(r.data).items);
export const createOntologyProject = (teamId: string, name: string) =>
  client.post<OntologyProject>('/projects', { teamId, name }, config).then(r => data(r.data));
export const listOntologyPrincipals = (projectId: string) => client.get<{items: OntologyPrincipal[]}>('/projects/' + encodeURIComponent(projectId) + '/principals', config).then(r => data(r.data).items);
export const listOntologyDrafts = () => client.get<{items: OntologyDraft[]}>('/drafts', config).then(r => data(r.data).items);
export const fetchOntologyDraft = (id: string) => client.get<OntologyDraft>(draftPath(id), config).then(r => data(r.data));
export const createOntologyDraft = (definition: SemanticDefinition) => client.post<OntologyDraft>('/drafts', definition, config).then(r => data(r.data));
export const updateOntologyDraft = (id: string, revision: number, definition: SemanticDefinition) => client.put<OntologyDraft>(draftPath(id), { revision, definition }, config).then(r => data(r.data));
export const validateOntologyDraft = (id: string, revision: number) => client.post<OntologyValidation>(draftPath(id) + '/validate', { revision }, config).then(r => data(r.data));
export const publishOntologyDraft = (id: string, revision: number) => client.post<{ id: string; revision: number; objectType: SemanticDefinition }>(draftPath(id) + '/publish', { revision }, config).then(r => data(r.data));
export const testOntologyPolicy = (definition: SemanticDefinition, userId: string) => client.post<{ visible: boolean; visibleFields: string[] }>('/security/preview', { projectId: definition.projectId, userId, sourceMode: definition.source.mode, properties: definition.properties.map(item => item.id), security: definition.security }, config).then(r => data(r.data));
export type PublishedOntologyPage = { items: PublishedOntologyType[]; hasMore: boolean };
export const listPublishedOntologyTypes = (limit: number, offset: number, q: string) =>
  client.get<PublishedOntologyPage>('/object-types', { ...config, params: { limit, offset, q } }).then(r => data(r.data));
export const fetchPublishedOntologyType = (id: string) => client.get<PublishedOntologyType>('/object-types/' + encodeURIComponent(id), config).then(r => data(r.data));


export type OntologyRecord = { id: string | null; revision: number | null; properties: Record<string, unknown>; title: unknown };
export type OntologyRecordPage = { items: OntologyRecord[]; hasMore: boolean; actions?: Array<{ kind: SemanticAction['kind']; fields: string[] }> };
type ActionInput = { values: Record<string, unknown> } | { recordId: string; expectedRevision: number; values?: Record<string, unknown> };
export const listOntologyRecords = (id: string, limit: number, offset: number) =>
  client.get<OntologyRecordPage>('/object-types/' + encodeURIComponent(id) + '/records', { ...config, params: { limit, offset } }).then(r => data(r.data));
export type OntologyRecordHistoryPage = { items: Array<{ kind: SemanticAction['kind']; revision: number; createdAt: string }>; hasMore: boolean };
export const listOntologyRecordHistory = (id: string, recordId: string, limit: number, offset: number, signal: AbortSignal) =>
  client.get<OntologyRecordHistoryPage>('/object-types/' + encodeURIComponent(id) + '/records/' + encodeURIComponent(recordId) + '/history', { ...config, params: { limit, offset }, signal }).then(r => data(r.data));
export const submitOntologyAction = (id: string, kind: SemanticAction['kind'], body: ActionInput) =>
  client.post<{ id: string; revision: number; deleted: boolean }>('/object-types/' + encodeURIComponent(id) + '/actions/' + kind, body, config).then(r => data(r.data));
