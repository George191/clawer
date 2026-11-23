import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { Button, Dropdown, Input } from '@/components/ui';
import { ArrowDownOutlined, ArrowUpOutlined, CloseOutlined, DownOutlined, SearchOutlined } from '@/components/ui/icons';
import type { ExplorerFilter } from './explorerFilters';
import { availableExplorerSortFields, firstIncompleteExplorerFilter, firstIncompleteExplorerSort, nextUnusedExplorerField, type ExplorerFilterPart } from './explorerFilterDraft';
import './explorer-filter-editor.css';

type  FilterCheckboxChange  =  {  target:  {  checked:  boolean  }  };
const  FilterCheckbox:  React.FC<{  checked:  boolean;  onChange:  (event:  FilterCheckboxChange)  =>  void;  mode?:  'single'  |  'multiple'  }>  =  ({  checked,  onChange,  mode = 'multiple'  })  =>  <span  className={`flow-filter-custom-checkbox  ${checked  ?  'is-checked'  :  ''}`}  data-mode={mode}  role="checkbox"  aria-checked={checked}  tabIndex={0}  onClick={()  =>  onChange({  target:  {  checked:  !checked  }  })}  onKeyDown={(event)  =>  {  if  (event.key  ===  'Enter'  ||  event.key  ===  ' ')  {  event.preventDefault();  onChange({  target:  {  checked:  !checked  }  });  }  }}><svg  viewBox="0 0 12 12"  aria-hidden="true"><rect  x=".5"  y=".5"  width="11"  height="11"  rx="2"  fill={checked  ?  'currentColor'  :  'none'}  opacity={checked  ?  1  :  0}  /><path  d="m3.1 6.1 1.8 1.8 4-4"  fill="none"  stroke="#151a21"  strokeWidth="1.6"  strokeLinecap="round"  strokeLinejoin="round"  opacity={checked  ?  1  :  0}  /></svg></span>;
const  FilterAddIcon:  React.FC  =  ()  =>  <svg  className="flow-filter-mini-icon"  viewBox="0  0  18  14"  aria-hidden="true"><rect  x=".75"  y=".75"  width="16.5"  height="12.5"  rx="1"  fill="none"  stroke="currentColor"  strokeWidth="1"  /><path  d="M9  4.8v4.4M6.8  7h4.4"  fill="none"  stroke="currentColor"  strokeWidth="1"  strokeLinecap="round"  /></svg>;
export const  SortDirectionIcon:  React.FC<{  direction:  'asc'  |  'desc'  }>  =  ({  direction  })  =>  <svg  className="flow-filter-sort-direction-icon"  viewBox="0  0  16  16"  aria-hidden="true"><path  d={direction  ===  'asc'  ?  'M4 12V4m0 0L2 6m2-2 2 2M8 5h6M8 8h4M8 11h2'  :  'M4 4v8m0 0-2-2m2 2 2-2M8 5h2M8 8h4M8 11h6'}  fill="none"  stroke="currentColor"  strokeWidth="1.25"  strokeLinecap="round"  strokeLinejoin="round"  /></svg>;
const  SortMenuChevronIcon:  React.FC  =  ()  =>  <svg  viewBox="0 0 12 12"  width="12"  height="12"  aria-hidden="true"><path  d="m3 4.5 3 3 3-3"  fill="none"  stroke="currentColor"  strokeWidth="1.2"  strokeLinecap="round"  strokeLinejoin="round"  /></svg>;
const  flowDropdownEvent =  'flow-dropdown-open';
export const randomId = (): string => {
    const c = globalThis.crypto as Crypto & { randomUUID?: () => string } | undefined;
    if (typeof c?.randomUUID === 'function') return c.randomUUID();
    const bytes = new Uint8Array(16);
    c?.getRandomValues(bytes);
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, '0'));
    return `${hex.slice(0, 4).join('')}-${hex.slice(4, 6).join('')}-${hex.slice(6, 8).join('')}-${hex.slice(8, 10).join('')}-${hex.slice(10, 16).join('')}`;
};
const  useFlowDropdown =  ()  =>  {  const  id = useRef(randomId());  const  [open, setOpen] = useState(false);  useEffect(() => { const close = (event: Event) => { if ((event as CustomEvent<string>).detail !== id.current) setOpen(false); }; window.addEventListener(flowDropdownEvent, close); return () => window.removeEventListener(flowDropdownEvent, close); }, []); const setDropdownOpen = (next: boolean) => { setOpen(next); if (next) window.dispatchEvent(new CustomEvent(flowDropdownEvent, { detail: id.current })); }; return [open, setDropdownOpen] as const; };
const SortActionMenu: React.FC<{ onDelete: () => void; onClear: () => void }> = ({ onDelete, onClear }) => {
    const [open, setOpen] = useFlowDropdown();
    return <Dropdown overlayClassName="flow-menu-popup" open={open} onOpenChange={setOpen} trigger={['click']} menu={{ items: [{ key: 'delete', label: 'Delete sort' }, { key: 'clear', label: 'Clear all sorts' }], onClick: ({ key }) => { (key === 'delete' ? onDelete : onClear)(); setOpen(false); } }}><button type="button" className="flow-filter-sort-menu-toggle" aria-label="Sort options" onClick={(event) => event.stopPropagation()}><SortMenuChevronIcon /></button></Dropdown>;
};
const  FilterFieldPicker:  React.FC<{  value:  string;  onChange:  (value:  string)  =>  void;  className?:  string;  labelClassName?:  string;  showArrow?:  boolean;  trailing?:  React.ReactNode; fields: string[]; explorer?: boolean  }>  =  ({  value,  onChange,  className,  labelClassName,  showArrow,  trailing, fields, explorer  })  =>  { const prefix = explorer ? 'explorer-filter' : 'flow-filter';  const  [open, setOpen] = useFlowDropdown(); const  [search,  setSearch]  =  useState('');  const  visibleFields  =  fields.filter((field)  =>  field.toLowerCase().includes(search.toLowerCase()));  return  <Dropdown open={open} onOpenChange={setOpen} overlayClassName={`flow-menu-popup ${prefix}-field-dropdown${explorer ? ' explorer-filter-popup' : ''}`}  trigger={['click']}  dropdownRender={()  =>  <div  className={`${prefix}-field-popup`}><div  className={`${prefix}-field-options`}>{visibleFields.map((field)  =>  <button  type="button"  className={field  ===  value  ?  'is-selected'  :  ''}  key={field}  onClick={()  =>  {  onChange(field);  setOpen(false);  }}>{field}</button>)}</div><div  className={`${prefix}-field-search`}><Input  size="small"  prefix={<SearchOutlined  />}  aria-label={explorer ? 'Find property' : undefined} value={search}  onChange={(event)  =>  setSearch(event.target.value)}  placeholder="Search"  /></div></div>}><span  className={[prefix + '-field-link', className].filter(Boolean).join(' ')}  role="button"  tabIndex={0}><span  className={labelClassName}>{value || 'Select field'}</span>{showArrow  &&  <DownOutlined  />}{trailing}</span></Dropdown>;  };
const  FilterValuePicker:  React.FC<{  value:  string;  onChange:  (value:  string)  =>  void; suggestions: string[]; explorer?: boolean  }>  =  ({  value,  onChange, suggestions, explorer  })  =>  { const prefix = explorer ? 'explorer-filter' : 'flow-filter';  const  [open, setOpen] = useFlowDropdown();  const  [draft,  setDraft]  =  useState(value);  const  [search,  setSearch]  =  useState('');  const  [selected,  setSelected]  =  useState(value  ?  [value]  :  []);  const  visibleValues  =  suggestions.filter((item)  =>  item.includes(search));  const  close  =  ()  =>  {  setOpen(false);  setDraft(value);  setSelected(value  ?  [value]  :  []);  setSearch('');  };  return  <Dropdown  open={open}  onOpenChange={(next)  =>  {  if  (next)  {  setDraft(value);  setSelected(value  ?  [value]  :  []);  }  setOpen(next);  }}  overlayClassName={`flow-menu-popup ${prefix}-value-dropdown${explorer ? ' explorer-filter-popup' : ''}`}  trigger={['click']}  dropdownRender={()  =>  <div  className={`${prefix}-value-popup`}><Input  size="small" aria-label={explorer ? 'Filter value' : undefined} value={draft}  onChange={(event) => { setDraft(event.target.value); setSelected([]); }} autoFocus  /><strong>Suggested values</strong><div  className={`${prefix}-value-options`}>{visibleValues.map((item)  =>  <label  key={item}><FilterCheckbox  mode="multiple"  checked={selected.includes(item)}  onChange={(event)  =>  setSelected((items)  =>  event.target.checked  ?  [...new  Set([...items,  item])]  :  items.filter((entry)  =>  entry  !==  item))}  /><span  className={`${prefix}-value-label`}>{item}</span></label>)}</div><div  className={`${prefix}-field-search`}><Input  size="small"  prefix={<SearchOutlined  />} aria-label={explorer ? 'Search suggested values' : undefined} value={search}  onChange={(event)  =>  setSearch(event.target.value)}  placeholder="Search"  /></div><div  className={`${prefix}-value-actions`}><Button  size="small"  onClick={()  =>  {  onChange(selected.join(',  ')  ||  draft);  setOpen(false);  }}>Apply</Button><Button  size="small"  onClick={close}>Cancel</Button></div></div>}><span  className={`${prefix}-value`}  role="button"  tabIndex={0}>{value || (explorer ? 'Enter value' : '<?>')}</span></Dropdown>;  };
export const filterOperators = [
    { value: "=", label: "Equals" },
    { value: "!=", label: "Does not equal" },
    { value: "<", label: "Less than" },
    { value: "<=", label: "Less than or equal" },
    { value: ">", label: "Greater than" },
    { value: ">=", label: "Greater than or equal" },
    { value: "\u5305\u542b", label: "Contains" },
    { value: "\u5305\u542b\uff08\u4e0d\u533a\u5206\u5927\u5c0f\u5199\uff09", label: "Contains (case insensitive)" },
    { value: "\u4e0d\u5305\u542b", label: "Does not contain" },
    { value: "\u4e0d\u5305\u542b\uff08\u4e0d\u533a\u5206\u5927\u5c0f\u5199\uff09", label: "Does not contain (case insensitive)" },
    { value: "\u5f00\u59cb\u4ee5", label: "Starts with" },
    { value: "\u4e0d\u662f\u5f00\u59cb\u4e8e", label: "Does not start with" },
    { value: "ends  with", label: "Ends with" },
    { value: "\u4e0d\u662f\u7ed3\u675f\u4e8e", label: "Does not end with" },
    { value: "\u662f  null", label: "Is null" },
    { value: "\u4e0d\u662f  null", label: "Is not null" },
    { value: "\u662f\u7a7a\u7684", label: "Is empty" },
    { value: "\u4e0d\u662f\u7a7a\u7684", label: "Is not empty" },
    { value: "\u4ecb\u4e8e", label: "Between" },
    { value: "\u4e0d\u4ecb\u4e8e", label: "Not between" },
    { value: "\u5728\u5217\u8868", label: "In list" },
    { value: "\u4e0d\u5728\u5217\u8868", label: "Not in list" },
];
export function filterOperatorLabel(value: string): string {
    return ['=', '!=', '<', '<=', '>', '>='].includes(value) ? value : filterOperators.find(operator => operator.value === value)?.label || value;
}
const  FilterOperatorSpan:  React.FC<{  value:  string;  onChange:  (value:  string)  =>  void; explorer?: boolean  }>  =  ({  value,  onChange, explorer  })  =>  { const prefix = explorer ? 'explorer-filter' : 'flow-filter'; const [open, setOpen] = useFlowDropdown(); return <Dropdown open={open} onOpenChange={setOpen} overlayClassName={`flow-menu-popup ${prefix}-operator-dropdown${explorer ? ' explorer-filter-popup' : ''}`} trigger={['click']} menu={{ items: filterOperators.map((operator) => ({ key: operator.value, label: operator.label })), onClick: ({ key }) => { onChange(key); setOpen(false); } }}><span className={`${prefix}-op`} role="button" tabIndex={0}>{filterOperatorLabel(value)}</span></Dropdown>; };
const  filterNestingDepth  =  (id:  string)  =>  (id.match(/nested-/g)  ||  []).length;
export type ExplorerSort = { field: string; direction: 'asc' | 'desc' };
const  filterGroupBounds  =  (rows:  Array<{  id:  string  }>,  index:  number)  =>  {  const  stack:  number[]  =  [];  const  pairs:  Array<{  start:  number;  end:  number  }>  =  [];  rows.forEach((item,  rowIndex)  =>  {  if  (item.id.startsWith('group-open-'))  stack.push(rowIndex);  else  if  (item.id.startsWith('group-close-')  &&  stack.length)  pairs.push({  start:  stack.pop()  as  number,  end:  rowIndex  });  });  return  pairs.filter((pair)  =>  pair.start  <=  index  &&  index  <=  pair.end).sort((x,  y)  =>  (x.end  -  x.start)  -  (y.end  -  y.start))[0]  ||  null;  };
const  findContainingClose  =  (rows:  Array<{  id:  string  }>,  index:  number)  =>  {  let  depth  =  0;  for  (let  i  =  index  +  1;  i  <  rows.length;  i  +=  1)  {  if  (rows[i].id.startsWith('group-open-'))  depth  +=  1;  else  if  (rows[i].id.startsWith('group-close-'))  {  if  (depth  ===  0)  return  i;  depth  -=  1;  }  }  return  -1;  };
export const  normalizeFilterDepth  =  <T extends { id: string }>(rows: T[])  =>  {  let  depth  =  0;  return  rows.map((item)  =>  {  const  raw  =  item.id.replace(/^group-(?:open|close|filter)-/,  '').replace(/^(nested-)+/,  '');  if  (item.id.startsWith('group-open-'))  {  const  next  =  {  ...item,  id:  `group-open-${'nested-'.repeat(depth)}${raw}`  };  depth  +=  1;  return  next;  }  if  (item.id.startsWith('group-close-'))  {  depth  =  Math.max(0,  depth  -  1);  return  {  ...item,  id:  `group-close-${'nested-'.repeat(depth)}${raw}`  };  }  return  depth  >  0  ?  {  ...item,  id:  `group-filter-${'nested-'.repeat(depth)}${raw}`  }  :  {  ...item,  id:  raw  };  });  };
function  cleanEmptyFilterGroups<T extends { id: string }>(rows: T[])  {  let  next  =  rows;  let  changed  =  true;  while  (changed)  {  changed  =  false;  for  (let  i  =  0;  i  <  next.length;  i  += 1)  {  if  (!next[i].id.startsWith('group-open-'))  continue;  const  group  =  filterGroupBounds(next,  i);  if  (!group)  continue;  const  hasCondition  =  next.slice(group.start  +  1,  group.end).some((item)  =>  !item.id.startsWith('group-open-')  &&  !item.id.startsWith('group-close-'));  if  (!hasCondition)  {  next  =  next.filter((_,  rowIndex)  =>  rowIndex  <  group.start  ||  rowIndex  >  group.end);  changed  =  true;  break;  }  }  }  return  next;  }
function  moveFilterRows<T extends { id: string }>(rows: T[], index: number, direction: 'up' | 'down')  {  const  isGroupBoundary  =  rows[index]?.id.startsWith('group-open-')  ||  rows[index]?.id.startsWith('group-close-');  const  bounds  =  isGroupBoundary  ?  filterGroupBounds(rows,  index)  :  null;  const  start  =  bounds?.start  ??  index;  const  end  =  bounds?.end  ??  index;  const  target  =  direction  ===  'up'  ?  start  -  1  :  end  +  1;  if  (target  <  0  ||  target  >=  rows.length)  return  rows;  const  block  =  rows.slice(start,  end  +  1);  const  next  =  [...rows];  next.splice(start,  block.length);  const  insertAt  =  direction  ===  'up'  ?  start  -  1  :  start  +  1;  next.splice(insertAt,  0,  ...block);  return  cleanEmptyFilterGroups(normalizeFilterDepth(next));  };
function  removeFilterRow<T extends { id: string }>(rows: T[], index: number)  {  const  bounds  =  filterGroupBounds(rows,  index);  if  (!bounds)  return  rows.filter((_,  rowIndex)  =>  rowIndex  !==  index);  let  next  =  rows[index].id.startsWith('group-open-')  ||  rows[index].id.startsWith('group-close-')  ?  rows.filter((_,  rowIndex)  =>  rowIndex  <  bounds.start  ||  rowIndex  >  bounds.end)  :  rows.filter((_,  rowIndex)  =>  rowIndex  !==  index);  let  changed  =  true;  while  (changed)  {  changed  =  false;  for  (let  i  =  0;  i  <  next.length;  i  +=  1)  {  if  (!next[i].id.startsWith('group-open-'))  continue;  const  group  =  filterGroupBounds(next,  i);  if  (!group)  continue;  const  hasCondition  =  next.slice(group.start  +  1,  group.end).some((item)  =>  !item.id.startsWith('group-open-')  &&  !item.id.startsWith('group-close-'));  if  (!hasCondition)  {  next  =  next.filter((_,  rowIndex)  =>  rowIndex  <  group.start  ||  rowIndex  >  group.end);  changed  =  true;  break;  }  }  }  return  next;  };
const  FilterGroupIcon:  React.FC  =  ()  =>  <svg  className="flow-filter-mini-icon"  viewBox="0  0  18  14"  aria-hidden="true"><rect  x=".75"  y=".75"  width="16.5"  height="12.5"  rx="1"  fill="none"  stroke="currentColor"  strokeWidth="1"  /><path  d="M6.8  4.2C5.8  4.7  5.8  8.3  6.8  8.8M10.2  4.2C11.2  4.7  11.2  8.3  10.2  8.8M13.4  7.7v2.2M12.3  8.8h2.2"  fill="none"  stroke="currentColor"  strokeWidth="1"  strokeLinecap="round"  strokeLinejoin="round"  /></svg>;

export interface ExplorerFilterEditorProps {
    rules: ExplorerFilter[];
    setRules: React.Dispatch<React.SetStateAction<ExplorerFilter[]>>;
    search?: string;
    onSearchChange?: (value: string) => void;
    /** The first entry is the primary sort, whose field can be empty. */
    sorts: ExplorerSort[];
    onSortsChange: (sorts: ExplorerSort[], apply: boolean) => void;
    fields: string[];
    sortFields?: string[];
    rows: Record<string, unknown>[];
    formatValue: (value: unknown) => string;
    onApply: () => void;
    columnMode?: boolean;
    validationRequest?: number;
    renderRuleNotice?: (rule: ExplorerFilter) => React.ReactNode;
    renderSortNotice?: (sorts: ExplorerSort[]) => React.ReactNode;
    footerAccessory?: React.ReactNode;
    children?: React.ReactNode;
}

export default function ExplorerFilterEditor({ rules, setRules, search, onSearchChange, sorts, onSortsChange, fields, sortFields = fields, rows, formatValue, onApply, columnMode = true, validationRequest, renderRuleNotice, renderSortNotice, footerAccessory, children }: ExplorerFilterEditorProps) {
    const [filterMoving, setFilterMoving] = useState<{ id: string; peerId: string; direction: 'up' | 'down' } | null>(null);
    const rulesRef = useRef<HTMLDivElement>(null);
    const [invalidFilter, setInvalidFilter] = useState<{ id: string; parts: ExplorerFilterPart[] }>();
    const [invalidSort, setInvalidSort] = useState<{ index: number }>();
    const sortConditionsRef = useRef<HTMLDivElement>(null);
    const [sortConditionsOverflow, setSortConditionsOverflow] = useState(false);
    const sortField = sorts[0]?.field || '';
    const sortDirection = sorts[0]?.direction || 'asc';
    const sortConditions = sorts.slice(1);
    const previousSortConditionCount = useRef(sortConditions.length);
    useLayoutEffect(() => {
        if (sortConditions.length > previousSortConditionCount.current) sortConditionsRef.current?.scrollTo({ left: sortConditionsRef.current.scrollWidth, behavior: 'smooth' });
        previousSortConditionCount.current = sortConditions.length;
    }, [sortConditions.length]);
    useLayoutEffect(() => {
        const element = sortConditionsRef.current;
        if (!element) return;
        const update = () => setSortConditionsOverflow(element.scrollWidth > element.clientWidth + 1);
        update();
        const observer = new ResizeObserver(update);
        observer.observe(element);
        return () => observer.disconnect();
    }, [sortConditions.length, sortField]);
    useEffect(() => {
        if (!sortConditionsOverflow) return;
        const frame = window.requestAnimationFrame(() => sortConditionsRef.current?.scrollTo({ left: sortConditionsRef.current.scrollWidth, behavior: 'smooth' }));
        return () => window.cancelAnimationFrame(frame);
    }, [sortConditionsOverflow]);
    useEffect(() => {
        if (!columnMode || rules.length !== 1 || rules[0].active === false || rules[0].id === 'placeholder-rule' ||
            rules[0].id.startsWith('group-') || rules[0].field || rules[0].value || !fields[0]) return;
        setRules(current => current.length === 1 && current[0].id === rules[0].id && current[0].active !== false && !current[0].field && !current[0].value
            ? [{ ...current[0], field: fields[0] }] : current);
    }, [columnMode, rules, fields, setRules]);
    useEffect(() => setInvalidFilter(undefined), [rules]);
    useEffect(() => {
        if (!invalidFilter) return;
        const row = Array.from(rulesRef.current?.querySelectorAll<HTMLElement>('.flow-filter-rule') || [])
            .find(element => element.id === `explorer-filter-${invalidFilter.id}`);
        const targets = invalidFilter.parts.map(part => row?.querySelector<HTMLElement>(`.explorer-filter-${part === 'operator' ? 'op' : part === 'field' ? 'field-link' : 'value'}`));
        targets[0]?.scrollIntoView({ block: 'nearest' });
        targets[0]?.focus({ preventScroll: true });
        if (!window.matchMedia('(prefers-reduced-motion: reduce)').matches) targets.forEach(target => target?.animate([
            { transform: 'translateX(0)' }, { transform: 'translateX(-4px)' }, { transform: 'translateX(4px)' },
            { transform: 'translateX(-3px)' }, { transform: 'translateX(3px)' }, { transform: 'translateX(0)' },
        ], { duration: 320, easing: 'ease-in-out' }));
    }, [invalidFilter]);
    const canAddFilter = () => {
        if (!columnMode) return true;
        const incomplete = firstIncompleteExplorerFilter(rules);
        if (!incomplete) return true;
        setInvalidFilter(incomplete);
        return false;
    };
    useEffect(() => { if (validationRequest) canAddFilter(); }, [validationRequest]);
    const nextFilterField = (rows: ExplorerFilter[], rule: ExplorerFilter, index: number) => {
        const preceding = rows.slice(0, index + 1).filter(row => !/^group-(open|close)-/.test(row.id));
        const afterField = rule.field || preceding[preceding.length - 1]?.field;
        return columnMode ? nextUnusedExplorerField(fields, rows.map(row => row.field), afterField) : fields[0] || '';
    };
    const addFilterCondition = (rule: ExplorerFilter, index: number) => {
        if (!canAddFilter()) return;
        setRules(rows => {
            const next = { id: ((rule.id.startsWith('group-filter-') || rule.id.startsWith('group-close-')) && filterNestingDepth(rule.id) > 0)
                ? `group-filter-${'nested-'.repeat(filterNestingDepth(rule.id))}${randomId()}` : randomId(),
                field: nextFilterField(rows, rule, index), operator: '=', value: '', active: true, join: 'and' };
            const cleanRows = rows.length === 1 && rows[0].id === 'placeholder-rule' ? [] : rows;
            const closeIndex = rule.id.startsWith('group-close-') ? index : findContainingClose(cleanRows, index);
            if (rule.id.startsWith('group-close-')) return [...cleanRows.slice(0, index + 1), next, ...cleanRows.slice(index + 1)];
            if (closeIndex >= 0) return [...cleanRows.slice(0, closeIndex), next, ...cleanRows.slice(closeIndex)];
            return [...cleanRows, next];
        });
    };
    const addFilterGroup = (rule: ExplorerFilter, index: number) => {
        if (!canAddFilter()) return;
        setRules(rows => {
            const groupId = randomId();
            const cleanRows = rows.length === 1 && rows[0].id === 'placeholder-rule' ? [] : rows;
            const nested = rule.id.startsWith('group-filter-') || rule.id.startsWith('group-close-');
            const depth = filterNestingDepth(rule.id), currentPrefix = 'nested-'.repeat(depth), childPrefix = 'nested-'.repeat(depth + 1);
            const additions = [
                { id: `group-open-${currentPrefix}${groupId}`, field: '', operator: '=', value: '', active: true, join: 'and' },
                { id: `group-filter-${childPrefix}${groupId}`, field: nextFilterField(rows, rule, index), operator: '=', value: '', active: true, join: 'and' },
                { id: `group-close-${currentPrefix}${groupId}`, field: '', operator: '=', value: '', active: true, join: 'and' },
            ];
            const closeIndex = nested ? findContainingClose(cleanRows, index) : -1;
            return closeIndex >= 0 ? [...cleanRows.slice(0, closeIndex), ...additions, ...cleanRows.slice(closeIndex)] : [...cleanRows, ...additions];
        });
    };
    const nextSortField = nextUnusedExplorerField(sortFields, sorts.map(sort => sort.field));
    const incompleteSort = firstIncompleteExplorerSort(sorts);
    useEffect(() => setInvalidSort(undefined), [sorts]);
    useEffect(() => {
        if (!invalidSort) return;
        const target = sortConditionsRef.current?.querySelectorAll<HTMLElement>('.explorer-filter-sort-field')[invalidSort.index];
        target?.scrollIntoView({ block:'nearest', inline:'nearest' });
        target?.focus({ preventScroll:true });
        if (!window.matchMedia('(prefers-reduced-motion: reduce)').matches) target?.animate([
            { transform:'translateX(0)' }, { transform:'translateX(-4px)' }, { transform:'translateX(4px)' },
            { transform:'translateX(-3px)' }, { transform:'translateX(3px)' }, { transform:'translateX(0)' },
        ], { duration:320, easing:'ease-in-out' });
    }, [invalidSort]);
    const addSortField = () => {
        if (columnMode) {
            if (incompleteSort !== undefined) { setInvalidSort({ index:incompleteSort }); return; }
            if (nextSortField) onSortsChange([...sorts.filter(sort => sort.field), { field: nextSortField, direction: 'asc' }], false);
        } else onSortsChange([{ field: sortField, direction: sortDirection }, ...sortConditions, { field: sortFields[0] || '', direction: 'asc' }], false);
    };
    const clearSorts = () => onSortsChange([{ field: '', direction: sortDirection }], true);
    return <div className={`flow-filter-card${columnMode ? ' explorer-filter-editor' : ''}`}>
        {!columnMode && onSearchChange && <div><Input.Search aria-label="Search all columns" size="small" placeholder="Search all columns" value={search || ''} onChange={event => onSearchChange(event.target.value)} onSearch={onApply} /></div>}
        {columnMode && <div className="flow-schema-filter-label">Filter</div>}
                                <div className="flow-filter-rules" ref={rulesRef}>{rules.map((rule,  index)  =>  <div  className={['flow-filter-rule', rule.id.startsWith('group-open-') ? 'is-group-open' : '', rule.id.startsWith('group-close-') ? 'is-group-close' : '', rule.id.startsWith('group-filter-') ? 'is-nested' : '', rule.id.includes('nested-') ? 'is-group-nested' : '', `is-group-depth-${filterNestingDepth(rule.id)}`, filterMoving?.id === rule.id ? `is-moving is-moving-${filterMoving.direction}` : filterMoving?.peerId === rule.id ? `is-moving is-moving-${filterMoving.direction === 'up' ? 'down' : 'up'}` : '', rule.active === false ? 'is-inactive' : '', invalidFilter?.id === rule.id ? invalidFilter.parts.map(part => `has-invalid-${part}`).join(' ') : ''].filter(Boolean).join(' ')}  style={{  '--filter-depth':  filterNestingDepth(rule.id)  }  as  React.CSSProperties}  key={rule.id} id={columnMode ? `explorer-filter-${rule.id}` : undefined}><span  className="flow-filter-row-check"><FilterCheckbox  checked={rule.active  !==  false}  onChange={(event)  =>  setRules((rows)  =>  rows.map((item,  itemIndex)  =>  rule.id.startsWith('group-open-')  ?  (()  =>  {  const  ruleIndex  =  rows.findIndex((entry)  =>  entry.id  ===  rule.id);  const  bounds  =  filterGroupBounds(rows,  ruleIndex);  return  bounds  &&  ruleIndex  <=  itemIndex  &&  itemIndex  <=  bounds.end  ?  {  ...item,  active:  event.target.checked  }  :  item;  })()  :  item.id  ===  rule.id  ?  {  ...item,  active:  event.target.checked  }  :  item))}  /></span><span  className="flow-filter-row-content"><FilterFieldPicker explorer={columnMode} fields={fields} value={rule.field}  onChange={(field)  =>  setRules((rows)  =>  rows.map((item)  =>  item.id  ===  rule.id  ?  {  ...item,  field  }  :  item))}  /><FilterOperatorSpan explorer={columnMode} value={rule.operator}  onChange={(operator)  =>  setRules((rows)  =>  rows.map((item)  =>  item.id  ===  rule.id  ?  {  ...item,  operator  }  :  item))}  /><FilterValuePicker explorer={columnMode} suggestions={[...new Set(rows.map((row) => formatValue(row[rule.field])))]} value={rule.value}  onChange={(value)  =>  setRules((rows)  =>  rows.map((item)  =>  item.id  ===  rule.id  ?  {  ...item,  value  }  :  item))}  />{index  <  rules.length  -  1  &&  !rules[index  +  1]?.id.startsWith('group-close-')  &&  <button type="button" className="flow-filter-join"  onClick={()  =>  setRules((rows)  =>  rows.map((item)  =>  item.id  ===  rule.id  ?  {  ...item,  join:  item.join  ===  'or'  ?  'and'  :  'or'  }  :  item))}>{rule.join  ||  'and'}</button>}</span>{(index  ===  rules.length  - 1  ||  rule.id.startsWith('group-close-')  ||  rules[index  + 1]?.id.startsWith('group-close-'))  &&  <span  className={columnMode ? 'flow-filter-row-actions explorer-filter-rule-actions' : 'flow-filter-row-actions'}>{rule.id.startsWith('group-close-')  &&  index  <  rules.length  -  1  &&  !rules[index  + 1]?.id.startsWith('group-close-')  &&  <button type="button" className="flow-filter-join flow-filter-close-join"  onClick={()  =>  setRules((rows)  =>  rows.map((item)  =>  item.id  ===  rule.id  ?  {  ...item,  join:  item.join  ===  'or'  ?  'and'  :  'or'  }  :  item))}>{rule.join  ||  'and'}</button>}<button  type="button"  className={columnMode ? 'explorer-filter-mini' : 'flow-filter-mini'} aria-label="Add filter condition" title="Add filter condition" onClick={() => addFilterCondition(rule, index)}><FilterAddIcon /></button><button  type="button"  className={columnMode ? 'explorer-filter-mini' : 'flow-filter-mini'}  aria-label="Add filter group" title="Add filter group" onClick={() => addFilterGroup(rule, index)}><FilterGroupIcon /></button>{rule.id === 'placeholder-rule' && <span className="flow-filter-placeholder">Select + to add a filter condition</span>}</span>}{renderRuleNotice?.(rule)}<span  className="flow-filter-row-end-actions"><button  type="button"  className="flow-filter-reorder"  aria-label="Move  up"  disabled={index  ===  0}  onClick={()  =>  {  if  (index  === 0)  return;  const  movingId  =  rule.id;  setFilterMoving({  id:  movingId,  peerId:  rules[index  -  1].id,  direction:  'up'  });  window.setTimeout(()  =>  {  setRules((rows)  =>  {  const  currentIndex  =  rows.findIndex((item)  =>  item.id  ===  movingId);  if  (currentIndex  <=  0)  return  rows;  return  moveFilterRows(rows, currentIndex, 'up');  });  setFilterMoving(null);  },  180);  }}><ArrowUpOutlined  /></button><button  type="button"  className="flow-filter-reorder"  aria-label="Move  down"  disabled={index  ===  rules.length  -  1}  onClick={()  =>  {  if  (index  >=  rules.length  -  1)  return;  const  movingId  =  rule.id;  setFilterMoving({  id:  movingId,  peerId:  rules[index  +  1].id,  direction:  'down'  });  window.setTimeout(()  =>  {  setRules((rows)  =>  moveFilterRows(rows, rows.findIndex((item)  =>  item.id  ===  movingId), 'down'));  setFilterMoving(null);  },  180);  }}><ArrowDownOutlined  /></button><button  type="button"  className="flow-filter-remove" aria-label="Remove filter condition" title="Remove filter condition" onClick={()  =>  setRules((rows)  =>  {  if  (rows.length  ===  1)  return  [{  id:  'placeholder-rule',  field:  '',  operator:  '=',  value:  '',  active:  false,  join:  'and',  placeholder:  true  }];  const  next  =  removeFilterRow(rows, index);  return  next.length  ?  next  :  [{  id:  'placeholder-rule',  field:  '',  operator:  '=',  value:  '',  active:  false,  join:  'and',  placeholder:  true  }];  })}><CloseOutlined /></button></span></div>)}</div>

        <div className="flow-filter-sort"><strong>Sort by</strong>
            <div className="flow-sort-conditions-viewport" ref={sortConditionsRef}><div className="flow-sort-conditions-track">
                <span className={columnMode ? 'explorer-filter-sort-field-wrap' : 'flow-filter-sort-field-wrap'}><FilterFieldPicker explorer={columnMode} fields={columnMode ? availableExplorerSortFields(sortFields, sorts, 0) : sortFields} value={sortField}
                    onChange={field => onSortsChange([{ field, direction: sortDirection }, ...sortConditions], !columnMode)}
                    className={columnMode ? `explorer-filter-sort-field${invalidSort?.index === 0 ? ' has-invalid-sort' : ''}` : 'flow-filter-sort-field'} labelClassName="flow-filter-sort-field-label" trailing={<>
                        <button type="button" className="flow-filter-sort-direction-toggle" aria-label={sortDirection === 'asc' ? 'Switch to descending' : 'Switch to ascending'} onClick={event => {
                            event.stopPropagation(); onSortsChange([{ field: sortField, direction: sortDirection === 'asc' ? 'desc' : 'asc' }, ...sortConditions], !columnMode);
                        }}><SortDirectionIcon direction={sortDirection} /></button>
                        {columnMode ? sortField && <button type="button" className="explorer-filter-sort-remove" aria-label={'Remove sort ' + sortField} title="Remove sort" onClick={event => {
                            event.stopPropagation(); onSortsChange(sorts.slice(1), true);
                        }}><CloseOutlined /></button> : <SortActionMenu onDelete={() => onSortsChange([{ field: '', direction: sortDirection }, ...sortConditions], true)} onClear={clearSorts} />}
                    </>} />
                </span>
                {sortConditions.map((condition, index) => <span className={columnMode ? 'explorer-filter-sort-field-wrap' : 'flow-filter-sort-field-wrap'} key={index}>
                    <FilterFieldPicker explorer={columnMode} fields={columnMode ? availableExplorerSortFields(sortFields, sorts, index + 1) : sortFields} value={condition.field}
                        onChange={field => onSortsChange([sorts[0], ...sortConditions.map((item, itemIndex) => itemIndex === index ? { ...item, field } : item)], false)}
                        className={columnMode ? `explorer-filter-sort-field${invalidSort?.index === index + 1 ? ' has-invalid-sort' : ''}` : 'flow-filter-sort-field'} labelClassName="flow-filter-sort-field-label" trailing={<>
                            <button type="button" className="flow-filter-sort-direction-toggle" aria-label="Toggle sort direction" onClick={event => {
                                event.stopPropagation(); onSortsChange([sorts[0], ...sortConditions.map<ExplorerSort>((item, itemIndex) => itemIndex === index ? { ...item, direction: item.direction === 'asc' ? 'desc' : 'asc' } : item)], false);
                            }}><SortDirectionIcon direction={condition.direction} /></button>
                            {columnMode ? <button type="button" className="explorer-filter-sort-remove" aria-label={'Remove sort ' + (condition.field || 'condition ' + (index + 2))} title="Remove sort" onClick={event => {
                                event.stopPropagation(); onSortsChange(sorts.filter((_, itemIndex) => itemIndex !== index + 1), true);
                            }}><CloseOutlined /></button> : <SortActionMenu onDelete={() => onSortsChange([sorts[0], ...sortConditions.filter((_, itemIndex) => itemIndex !== index)], true)} onClear={clearSorts} />}
                        </>} />
                </span>)}
                <button type="button" className={columnMode ? 'explorer-filter-mini explorer-filter-sort-add' : 'flow-filter-mini flow-filter-sort-add'} aria-label="Add sort field" disabled={columnMode && !nextSortField && incompleteSort === undefined} onClick={addSortField}><FilterAddIcon /></button>
            </div></div>
            {sortConditionsOverflow && <div className={columnMode ? 'explorer-sort-scroll-buttons' : 'flow-sort-scroll-buttons'}>
                <button type="button" aria-label="Scroll left" onClick={() => sortConditionsRef.current?.scrollBy({ left: -140, behavior: 'smooth' })}>‹</button>
                <button type="button" aria-label="Scroll right" onClick={() => sortConditionsRef.current?.scrollBy({ left: 140, behavior: 'smooth' })}>›</button>
            </div>}
            {renderSortNotice?.(sorts)}
        </div>
        <div className="flow-filter-apply"><button type="button" className={columnMode ? 'explorer-filter-apply-button' : 'flow-filter-apply-button'} onClick={onApply}>Apply filters</button><span className="flow-filter-edited-hint">Apply to update filters and pending sorts</span>{footerAccessory}</div>
        {children}
    </div>;
}
