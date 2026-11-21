import { CloseOutlined } from '@/components/ui/icons';
import { countFilterTerms, explorerFilterTerms, hasOr, type ExplorerFilter } from './explorerFilters';
import { filterOperators } from './ExplorerFilterEditor';
import type { ExplorerSort } from '../../services/explorerLayouts';

export default function ExplorerAppliedConditions({ filters, search = '', sorts, names = {}, removeFilters, removeSearch, removeSort }: {
  filters: ExplorerFilter[]; search?: string; sorts: ExplorerSort[]; names?: Record<string, string>;
  removeFilters: (start: number, end: number) => void; removeSearch: () => void; removeSort: (field: string) => void;
}) {
  const terms = explorerFilterTerms(filters), count = countFilterTerms(terms) + Number(Boolean(search.trim()));
  if (!count && !sorts.length) return null;
  function describe(term: (typeof terms)[number]): string {
    if (term.children) return '(' + expression(term.children) + ')';
    const rule = term.rule!, label = filterOperators.find(item => item.value === rule.operator)?.label ?? rule.operator;
    return `${names[rule.field] || rule.field} ${label.toLowerCase()}${rule.value === '' ? '' : ` ${JSON.stringify(rule.value)}`}`;
  }
  function expression(items: typeof terms): string {
    return items.map((term, index) => `${index ? ` ${items[index - 1].join.toUpperCase()} ` : ''}${describe(term)}`).join('');
  }
  function chip(key: string, kind: string, label: string, remove: () => void) {
    return <span className="flow-query-chip" key={key}><span title={label}>{label}</span><button type="button" aria-label={`Remove ${kind} ${label}`} title={`Remove ${kind}`} onClick={remove}><CloseOutlined /></button></span>;
  }
  return <div className="flow-schema-applied flow-query-chips" aria-label="Applied conditions">
    <span>Applied: {count} {count === 1 ? 'filter' : 'filters'} · {sorts.length} {sorts.length === 1 ? 'sort' : 'sorts'}</span>
    {search.trim() && chip('search', 'search', `Search: ${search}`, removeSearch)}
    {hasOr(terms) ? chip('root-expression', 'filter group', expression(terms), () => removeFilters(0, filters.length - 1))
      : terms.map(term => chip(filters[term.start].id, term.children ? 'filter group' : 'filter', describe(term), () => removeFilters(term.start, term.end)))}
    {sorts.map(sort => chip('sort-' + sort.field, 'sort', `${names[sort.field] || sort.field} ${sort.direction === 'asc' ? '↑' : '↓'}`, () => removeSort(sort.field)))}
  </div>;
}
