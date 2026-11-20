import React, { useState } from 'react';
export const JsonTreeValue: React.FC<{ name: string; value: unknown; depth: number; comma: boolean; initiallyOpen?: boolean }> = ({ name, value, depth, comma, initiallyOpen }) => {
    const [open, setOpen] = useState(initiallyOpen ?? true);
    const type = value === null || value === undefined ? 'null' : Array.isArray(value) ? 'array' : typeof value;
    const label = name === '' ? null : <span className="fjt-key">&quot;{name}&quot;</span>;
    if (type !== 'object' && type !== 'array') {
        const text = type === 'string' ? JSON.stringify(value) : type === 'null' ? 'null' : String(value);
        return (
            <div className="fjt-line" style={{ paddingLeft: depth * 14 }}>
                {label}{label ? <span className="fjt-punct">: </span> : null}
                <span className={`fjt-${type}`}>{text}</span>{comma ? <span className="fjt-punct">,</span> : null}
            </div>
        );
    }
    const entries: Array<[string, unknown]> = type === 'array'
        ? (value as unknown[]).map((item, index) => [String(index), item])
        : Object.entries(value as Record<string, unknown>);
    const count = entries.length;
    const openBracket = type === 'array' ? '[' : '{';
    const closeBracket = type === 'array' ? ']' : '}';
    return (
        <>
            <div className="fjt-line" style={{ paddingLeft: depth * 14 }}>
                {count > 0 ? <button type="button" className={`fjt-toggle${open ? ' is-open' : ''}`} aria-label={open ? `Collapse ${name || openBracket}` : `Expand ${name || openBracket}`} onClick={() => setOpen((current) => !current)} /> : <span className="fjt-toggle is-leaf" />}
                {label}{label ? <span className="fjt-punct">: </span> : null}
                {count === 0 ? <><span className="fjt-punct">{openBracket}{closeBracket}</span>{comma ? <span className="fjt-punct">,</span> : null}</>
                    : open ? <span className="fjt-punct">{openBracket}</span>
                        : <button type="button" className="fjt-summary" onClick={() => setOpen(true)}>{openBracket}<span className="fjt-summary-count">… {count} {type === 'array' ? 'items' : 'keys'}</span>{closeBracket}</button>}
                {!open && count > 0 && comma ? <span className="fjt-punct">,</span> : null}
            </div>
            {open && count > 0 ? entries.map(([key, item], index) => (
                <JsonTreeValue key={key} name={key} value={item} depth={depth + 1} comma={index < count - 1} initiallyOpen={initiallyOpen === undefined ? undefined : false} />
            )) : null}
            {open && count > 0 ? (
                <div className="fjt-line" style={{ paddingLeft: depth * 14 }}>
                    <span className="fjt-punct">{closeBracket}</span>{comma ? <span className="fjt-punct">,</span> : null}
                </div>
            ) : null}
        </>
    );
};
