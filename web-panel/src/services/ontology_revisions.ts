import client from './api';
import type { OntologyPrincipal, SemanticDefinition } from './ontology';

export type RevisionWorkspace = {
  revision: number; definition: SemanticDefinition; canEdit: boolean; canReview: boolean; canManageRoles: boolean;
};
export type DefinitionChange = { path: string; before: unknown; after: unknown };
export type RevisionSummary = {
  revision: number; createdAt: string; actorId: string; proposalId: string | null; restoreRevision: number | null;
};
export type RevisionDetails = { revision: number; definition: SemanticDefinition; changesFromCurrent: DefinitionChange[] };
export type RevisionProposal = {
  id: string; baseRevision: number; restoreRevision: number | null; isAuthor: boolean; stale: boolean;
  status: 'pending' | 'approved' | 'rejected' | 'merged'; summary: string; createdAt: string;
  authorId: string; reviewerId: string | null; reviewNote: string; reviewedAt: string | null; mergedRevision: number | null;
};
export type ProposalDetails = RevisionProposal & { definition: SemanticDefinition; changes: DefinitionChange[] };
export type RevisionRole = { id: string; name: string; manager: boolean; canEdit: boolean; canReview: boolean };
export type RevisionPage<T> = { items: T[]; hasMore: boolean };

const base = (id: string) => '/api/ontology/v1/object-types/' + encodeURIComponent(id);
const data = <T,>(body: T | { code: number; data: T }): T =>
  body && typeof body === 'object' && 'code' in body && 'data' in body ? body.data : body as T;
function get<T>(id: string, path: string, signal?: AbortSignal, params?: Record<string, string | number>) {
  return client.get<T>(base(id) + path, { baseURL: '', signal, params }).then(response => data(response.data));
}
function post<T>(id: string, path: string, payload: unknown) {
  return client.post<T>(base(id) + path, payload, { baseURL: '' }).then(response => data(response.data));
}
const proposal = (id: string) => '/revision-proposals/' + encodeURIComponent(id);

export const readRevisionWorkspace = (id: string, signal?: AbortSignal) =>
  get<RevisionWorkspace>(id, '/revision-workspace', signal);
export const readRevisionHistory = (id: string, limit: number, offset: number, signal: AbortSignal) =>
  get<RevisionPage<RevisionSummary>>(id, '/revisions', signal, { limit, offset });
export const readRevision = (id: string, revision: number, signal: AbortSignal) =>
  get<RevisionDetails>(id, '/revisions/' + revision, signal);
export const readRevisionProposals = (id: string, limit: number, offset: number, signal: AbortSignal) =>
  get<RevisionPage<RevisionProposal>>(id, '/revision-proposals', signal, { limit, offset });
export const readRevisionProposal = (id: string, proposalId: string, signal: AbortSignal) =>
  get<ProposalDetails>(id, proposal(proposalId), signal);
export const submitRevisionProposal = (id: string, revision: number, definition: SemanticDefinition, summary: string) =>
  post<RevisionProposal>(id, '/revision-proposals', { revision, definition, summary });
export const submitRestoreProposal = (id: string, revision: number, restoreRevision: number, summary: string) =>
  post<RevisionProposal>(id, '/revisions/' + restoreRevision + '/restore-proposals', { revision, summary });
export const reviewRevisionProposal = (id: string, proposalId: string, approve: boolean, note: string) =>
  post<RevisionProposal>(id, proposal(proposalId) + '/review', { approve, note });
export const mergeRevisionProposal = (id: string, proposalId: string, revision: number) =>
  post<RevisionProposal>(id, proposal(proposalId) + '/merge', { revision });
export const readRevisionRoles = (id: string, limit: number, offset: number, signal: AbortSignal) =>
  get<RevisionPage<RevisionRole>>(id, '/revision-roles', signal, { limit, offset });
export const setRevisionRoles = (id: string, member: RevisionRole) =>
  client.put<void>(base(id) + '/revision-roles/' + encodeURIComponent(member.id),
    { canEdit: member.canEdit, canReview: member.canReview }, { baseURL: '' });
export const readRevisionPrincipals = (id: string, q: string, signal: AbortSignal) =>
  get<{ items: OntologyPrincipal[] }>(id, '/revision-principals', signal, { q }).then(result => result.items);
