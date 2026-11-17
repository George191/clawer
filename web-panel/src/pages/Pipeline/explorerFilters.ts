import type { EtlDataQuery } from '../../services/api';

export type ExplorerFilter = EtlDataQuery['filters'][number];
const openGroup = (row: ExplorerFilter) => row.id.startsWith('group-open-');
const closeGroup = (row: ExplorerFilter) => row.id.startsWith('group-close-');
const unary = (operator: string) => /^(是\s+null|不是\s+null|是空的|不是空的)$/.test(operator);

interface FilterTerm {
    start: number;
    end: number;
    join: string;
    rule?: ExplorerFilter;
    children?: FilterTerm[];
}

/** Mirror the query compiler: each term's join points to the next effective term. */
export function explorerFilterTerms(rows: ExplorerFilter[]): FilterTerm[] {
    const root: FilterTerm[] = [];
    const stack = [{ start: -1, active: true, terms: root }];
    rows.forEach((rule, index) => {
        const parent = stack[stack.length - 1];
        if (openGroup(rule)) {
            stack.push({ start: index, active: parent.active && rule.active, terms: [] });
        } else if (closeGroup(rule)) {
            if (stack.length === 1) return;
            const group = stack.pop()!;
            if (group.active && group.terms.length) {
                stack[stack.length - 1].terms.push({ start: group.start, end: index, join: rule.join, children: group.terms });
            }
        } else if (parent.active && rule.active && rule.field && (rule.value !== '' || unary(rule.operator))) {
            parent.terms.push({ start: index, end: index, join: rule.join, rule });
        }
    });
    return root;
}

export const hasOr = (terms: FilterTerm[]): boolean => terms.slice(0, -1).some((term) => term.join === 'or');
export const countFilterTerms = (terms: FilterTerm[]): number =>
    terms.reduce((total, term) => total + (term.children ? countFilterTerms(term.children) : 1), 0);

export const blankExplorerFilter = (id: string): ExplorerFilter => ({ id, field: '', operator: '=', value: '', active: true, join: 'and' });

/** Parentheses are required when appending AND to a root expression with OR. */
export function appendAndFilters(rows: ExplorerFilter[], additions: ExplorerFilter[], groupId: string): ExplorerFilter[] {
    const terms = explorerFilterTerms(rows);
    if (!terms.length) return [...rows, ...additions];
    if (hasOr(terms)) {
        const group = blankExplorerFilter(groupId);
        return [{ ...group, id: `group-open-${groupId}` }, ...rows, { ...group, id: `group-close-${groupId}` }, ...additions];
    }
    // Trailing disabled or blank rules aren't SQL terms. Normalize the last real connector.
    const end = terms[terms.length - 1].end;
    return [...rows.map((row, index) => index === end ? { ...row, join: 'and' } : row), ...additions];
}

export function exactCellValue(value: unknown, dbType: string | undefined): string | null | undefined {
    const type = dbType?.toLowerCase().replace(/\(\d+(?:,\s*\d+)?\)/g, '').trim();
    if (!type || !/^(text|character varying|character|varchar|char|citext|boolean|smallint|integer|bigint|real|double precision|numeric|decimal|uuid|date|timestamp(?: with(?:out)? time zone)?|time(?: with(?:out)? time zone)?|inet|cidr|macaddr|macaddr8)$/.test(type)) return undefined;
    if (value === null) return null;
    if (typeof value === 'string' || typeof value === 'boolean') return String(value);
    if (typeof value === 'number' && /^(numeric|decimal)$/.test(type) && !Number.isSafeInteger(value)) return undefined;
    if (typeof value === 'number' && Number.isFinite(value) && (!Number.isInteger(value) || Number.isSafeInteger(value))) return String(value);
    return undefined;
}

export function cellFilterRows(field: string, value: string | null, exclude: boolean, id: string): ExplorerFilter[] {
    const row = { ...blankExplorerFilter(id), field, value: value ?? '' };
    if (value === null) return [{ ...row, operator: exclude ? '不是  null' : '是  null' }];
    if (!exclude) return [{ ...row, operator: value === '' ? '是空的' : '=' }];
    // SQL <> does not match NULL. Exclusion should remove exactly the chosen non-null value.
    return [
        blankExplorerFilter(`group-open-${id}`),
        { ...row, id: `group-filter-${id}-different`, operator: value === '' ? '不是空的' : '!=', join: 'or' },
        { ...row, id: `group-filter-${id}-null`, operator: '是  null', value: '' },
        blankExplorerFilter(`group-close-${id}`),
    ];
}
