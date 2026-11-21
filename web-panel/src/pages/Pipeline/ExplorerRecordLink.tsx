import type { ReactNode } from 'react';
import { projectHref } from '../../services/projectDomains';

export function explorerRecordUrl(schema: string, table: string, identity: Record<string, unknown>, definitionId?: string) {
    const params = new URLSearchParams({ ...(definitionId ? { objectType: definitionId } : { schema, table }), key: JSON.stringify(identity) });
    return projectHref('/ontology/explorer/record?' + params);
}

export function ExplorerRecordLink({ schema = '', table = '', definitionId, identity, children }: {
    schema?: string; table?: string; definitionId?: string; identity: Record<string, unknown>; children?: ReactNode;
}) {
    if (!Object.keys(identity).length) return null;
    return <a href={explorerRecordUrl(schema, table, identity, definitionId)} target="_blank" rel="noopener noreferrer"
        onClick={event => event.stopPropagation()} onKeyDown={event => event.stopPropagation()}>
        {children ?? 'Open full view'}
    </a>;
}

export default ExplorerRecordLink;
