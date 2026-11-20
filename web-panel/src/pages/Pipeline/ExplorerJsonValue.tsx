import { useState } from 'react';
import { DownOutlined } from '@/components/ui/icons';
import './explorer-json-tree.css';

export function JsonTreeValue({ name, value, depth, comma, initiallyOpen }: {
    name: string; value: unknown; depth: number; comma: boolean; initiallyOpen?: boolean;
}) {
    const [open, setOpen] = useState(initiallyOpen ?? true);
    const type = value == null ? 'null' : Array.isArray(value) ? 'array' : typeof value;
    const nested = type === 'object' || type === 'array';
    const entries: Array<[string, unknown]> = type === 'array'
        ? (value as unknown[]).map((item, index) => [String(index), item])
        : type === 'object' ? Object.entries(value as Record<string, unknown>) : [];
    const opening = type === 'array' ? '[' : '{', closing = type === 'array' ? ']' : '}';
    const expandable = nested && entries.length > 0;
    const indent = { marginLeft: 20 + depth * 14 };
    return <>
        <div className="fjt-line explorer-json-line">
            {expandable && <button type="button" className={`explorer-json-toggle${open ? ' is-open' : ''}`} aria-expanded={open}
                aria-label={`${open ? 'Collapse' : 'Expand'} ${name || opening}`} onClick={() => setOpen(v => !v)}>
                <DownOutlined />
            </button>}
            <div className="explorer-json-content" style={indent}>
                {name !== '' && <><span className="fjt-key">{JSON.stringify(name)}</span><span className="fjt-punct">: </span></>}
                {!nested ? <span className={`fjt-${type}`}>{type === 'string' ? JSON.stringify(value) : type === 'null' ? 'null' : String(value)}</span>
                    : <span className="fjt-punct">{opening}{!expandable ? closing : !open ? <><span className="fjt-summary-count">… {entries.length} {type === 'array' ? 'items' : 'keys'}</span>{closing}</> : null}</span>}
                {comma && (!expandable || !open) && <span className="fjt-punct">,</span>}
            </div>
        </div>
        {open && expandable && <>
            {entries.map(([key, item], index) => <JsonTreeValue key={key} name={key} value={item} depth={depth + 1}
                comma={index < entries.length - 1} initiallyOpen={initiallyOpen === undefined ? undefined : false} />)}
            <div className="fjt-line explorer-json-line"><div className="explorer-json-content" style={indent}>
                <span className="fjt-punct">{closing}{comma ? ',' : ''}</span>
            </div></div>
        </>}
    </>;
}
