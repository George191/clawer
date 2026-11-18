import type { ReactNode } from 'react';

export function explorerRecordUrl(schema: string, table: string, identity: Record<string, unknown>) {
    const params = new URLSearchParams({ schema, table, key: JSON.stringify(identity) });
    return '/ontology/explorer/record?' + params;
}

export function ExplorerRecordLink({ schema, table, identity, children }: {
    schema: string; table: string; identity: Record<string, unknown>; children?: ReactNode;
}) {
    if (!Object.keys(identity).length) return null;
    return <a href={explorerRecordUrl(schema, table, identity)} target="_blank" rel="noopener noreferrer"
        onClick={event => event.stopPropagation()} onKeyDown={event => event.stopPropagation()}>
        {children ?? 'Open full view'}
    </a>;
}

export default ExplorerRecordLink;
