import type { ExplorerRowLinkPage } from './api';
import type { ExplorerDetailSource, PublishedRelationship } from './publishedExplorerDetails';

export type LinkSelectionRelation = {
  id: string; relationId: string; name: string; direction: 'incoming' | 'outgoing';
  target: ExplorerDetailSource; canLink: boolean;
};

export const storageLinkRelation = (relation: ExplorerRowLinkPage['relations'][number]): LinkSelectionRelation => ({
  id: relation.id, relationId: relation.relationId, name: relation.name, direction: relation.direction,
  target: { kind: 'backed', schema: relation.schema, table: relation.table }, canLink: relation.canLink,
});

export const publishedLinkRelation = (relation: PublishedRelationship, canManage: boolean): LinkSelectionRelation => ({
  id: relation.id + ':' + relation.direction, relationId: relation.id, name: relation.name, direction: relation.direction,
  target: { kind: 'published', definitionId: relation.targetDefinitionId }, canLink: canManage,
});
