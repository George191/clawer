import type { ExplorerFilter } from './explorerFilters';
import type { ExplorerSort } from '../../services/explorerLayouts';

export type ExplorerFilterPart = 'field' | 'operator' | 'value';

export function missingExplorerFilterParts(rule: ExplorerFilter): ExplorerFilterPart[] {
    const missing: ExplorerFilterPart[] = [];
    if (!rule.field.trim()) missing.push('field');
    const operator = rule.operator.replace(/\s+/g, ' ').trim();
    if (!operator) missing.push('operator');
    if (/^(是 null|不是 null|是空的|不是空的)$/.test(operator)) return missing;
    const values = rule.value.split(',');
    if (!rule.value.trim() || (['介于', '不介于'].includes(operator) &&
        (values.length !== 2 || values.some(value => !value.trim())))) missing.push('value');
    return missing;
}

export function firstIncompleteExplorerFilter(rows: ExplorerFilter[]): { id: string; parts: ExplorerFilterPart[] } | undefined {
    const enabled = [true];
    for (const rule of rows) {
        if (rule.id.startsWith('group-open-')) {
            enabled.push(enabled[enabled.length - 1] && rule.active !== false);
        } else if (rule.id.startsWith('group-close-')) {
            if (enabled.length > 1) enabled.pop();
        } else if (enabled[enabled.length - 1] && rule.active !== false &&
            (rule.id !== 'placeholder-rule' || rule.field || rule.value)) {
            const parts = missingExplorerFilterParts(rule);
            if (parts.length) return { id: rule.id, parts };
        }
    }
}

export function nextUnusedExplorerField(fields: string[], usedFields: string[], afterField = ''): string {
    const start = fields.indexOf(afterField) + 1;
    const ordered = [...fields.slice(start), ...fields.slice(0, start)];
    return ordered.find(field => !usedFields.includes(field)) || '';
}

export function availableExplorerSortFields(fields: string[], sorts: ExplorerSort[], index: number): string[] {
    return fields.filter(field => !sorts.some((sort, sortIndex) => sortIndex !== index && sort.field === field));
}
