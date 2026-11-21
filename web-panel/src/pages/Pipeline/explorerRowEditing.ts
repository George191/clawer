export type RowDraft = Record<string, string | null>;

export function rowNullControl(key: string, value: string | null, remembered: RowDraft, onChange: (value: string | null) => void) {
    return { checked: value === null, onChange: (isNull: boolean) => {
        if (isNull) { remembered[key] = value; onChange(null); }
        else onChange(remembered[key] ?? '');
    } };
}

export function editorValue(type: string | undefined, value: unknown): string | null {
    if (value == null) return null;
    if (/^jsonb?(?:\s|$)|\[\]$/.test(type?.toLowerCase() || '') || typeof value === 'object') {
        return JSON.stringify(value, null, 2);
    }
    return String(value);
}

function parseField(name: string, value: string, type: string, original: unknown): unknown {
    if (/^jsonb?(?:\s|$)|\[\]$/.test(type) || (original !== null && typeof original === 'object')) {
        let parsed: unknown;
        try { parsed = JSON.parse(value); } catch { throw new Error(`${name} must be valid JSON`); }
        if (type.endsWith('[]') && !Array.isArray(parsed)) throw new Error(`${name} must be a JSON array`);
        return parsed;
    }
    if (typeof original === 'boolean' || type === 'boolean') {
        if (value !== 'true' && value !== 'false') throw new Error(`${name} must be true or false`);
        return value === 'true';
    }
    if (/^(smallint|integer|bigint)$/.test(type)) {
        if (!/^[+-]?\d+$/.test(value)) throw new Error(`${name} must be an integer`);
        const integer = Number(value);
        return Number.isSafeInteger(integer) ? integer : value;
    }
    if (/^(numeric|decimal)(?:\(|$)/.test(type)) {
        if (!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(value.trim())) {
            throw new Error(`${name} must be a number`);
        }
        return value;
    }
    if (typeof original === 'number' || /^(real|double precision)$/.test(type)) {
        const number = Number(value);
        if (!value.trim() || !Number.isFinite(number)) throw new Error(`${name} must be a number`);
        return number;
    }
    return value;
}

export function rowChanges(draft: RowDraft, original: Record<string, unknown>, types: Record<string, string>) {
    const changed: Record<string, unknown> = Object.create(null);
    for (const [name, value] of Object.entries(draft)) {
        const type = (types[name] || '').toLowerCase();
        if (value !== editorValue(type, original[name])) {
            changed[name] = value === null ? null : parseField(name, value, type, original[name]);
        }
    }
    return changed;
}
