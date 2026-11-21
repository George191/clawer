import type { ResourceView } from '../pages/Pipeline/ExplorerWorkbenchHeader';
import type { SemanticSource } from './ontology';

export function explorerResourceCapabilities(source: SemanticSource, canManage: boolean): ResourceView[] {
  if (source.mode !== 'existing' || !source.schema || !source.table) return ['preview'];
  return ['preview', 'stream', 'evidence', 'schema', ...(canManage ? ['api' as const] : [])];
}
