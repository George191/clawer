import { graphRequest } from './ontology_graph';
import type { PublishedExplorerQuery } from './publishedExplorerHandoff';

export type OntologyPublication = {
  modelChanged?: boolean; id: string; name: string; revision: number; fields: string[]; query: PublishedExplorerQuery;
  validFrom: string; validUntil: string; keyStatus: 'active' | 'unissued' | 'revoked'; version: string; url: string;
};
export type PublicationInput = Pick<OntologyPublication, 'name' | 'fields' | 'query' | 'validFrom' | 'validUntil'> & {version?: string; modelRevision: number};
const base = (id: string) => '/object-types/' + encodeURIComponent(id) + '/publications';
export const listOntologyPublications = (id: string, signal: AbortSignal) =>
  graphRequest<{items: OntologyPublication[]; canManage: boolean}>('get', base(id), {signal});
export const saveOntologyPublication = (id: string, payload: PublicationInput, publication?: string, signal?: AbortSignal) =>
  graphRequest<OntologyPublication>(publication ? 'put' : 'post', base(id) + (publication ? '/' + encodeURIComponent(publication) : ''), {data: payload, signal});
export const deleteOntologyPublication = (id: string, publication: string, signal?: AbortSignal) =>
  graphRequest('delete', base(id) + '/' + encodeURIComponent(publication), {signal});
export const changeOntologyPublicationKey = (id: string, publication: string, revoke: boolean, signal?: AbortSignal) =>
  graphRequest<{id: string; keyStatus: OntologyPublication['keyStatus']; key?: string}>(revoke ? 'delete' : 'post',
    base(id) + '/' + encodeURIComponent(publication) + (revoke ? '/key' : '/rotate-key'), {signal});
