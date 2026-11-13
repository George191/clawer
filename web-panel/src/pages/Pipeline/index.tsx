import  React,  {  useEffect,  useLayoutEffect,  useRef,  useState  }  from  'react';
import  {  useLocation,  useNavigate  }  from  'react-router-dom';
import  {  Button,  Checkbox,  ConfigProvider,  Dropdown,  Input,  InputNumber,  Modal,  Select,  Space,  Spin,  Table,  Tag,  Tabs,  Upload,  message  }  from  '@/components/ui';
import  enUS  from  '@/components/ui/locale';
import  {  AlertOutlined,  ApartmentOutlined,  ArrowDownOutlined,  ArrowUpOutlined,  BranchesOutlined,  CaretRightOutlined,  CheckOutlined,  CloseOutlined,  CodeOutlined,  DatabaseOutlined,  DeleteOutlined,  DownOutlined,  DownloadOutlined,  EditOutlined,  ExperimentOutlined,  PlusOutlined,  RightOutlined,  SearchOutlined,  SettingOutlined,  UploadOutlined  }  from  '@/components/ui/icons';
import  '../ProductWorkspace/workspace.css';
import CdcStatusPanel from './CdcStatusPanel';
import OntologyExplorer, { schemaColor } from './OntologyExplorer';
import OntologyLineage from './OntologyLineage';
import './ontology-frame.css';
import './ontology-workspace.css';
import  {  TemplateGlyph,  inferSiteKind  }  from  '../AICollect/WorkspaceDock';
import { fetchCurrentUserContext, fetchLayers, fetchLayerTables, fetchTableData, fetchHandlerCode, queryEtlTable, fetchTablePartitions, fetchTableStream, fetchLayerScript, setTableOffset, validateHandlerCode, saveHandlerCode, fetchEtlConnections, createEtlConnection, updateEtlConnection, deleteEtlConnection, updateEtlSchema, deleteEtlSchema, createEtlTable, updateEtlRow, deleteEtlRow, fetchEtlApis, createEtlApi, updateEtlApi, deleteEtlApi, fetchVulnerabilityKeywords, uploadVulnerabilityKeywords, activateVulnerabilityKeywordVersion, downloadVulnerabilityKeywordVersion, downloadCurrentVulnerabilityKeywords } from '../../services/api';
import type { EtlConnection, EtlDataQuery, EtlPublishedApi, VulnerabilityKeywordConfig } from '../../services/api';
import  type  {  LayerNode,  LayerTable,  QueryResult,  EtlPartition,  EtlStreamState  }  from  '../../services/types';

type  FilterCheckboxChange  =  {  target:  {  checked:  boolean  }  };
const  FilterCheckbox:  React.FC<{  checked:  boolean;  onChange:  (event:  FilterCheckboxChange)  =>  void;  mode?:  'single'  |  'multiple'  }>  =  ({  checked,  onChange,  mode = 'multiple'  })  =>  <span  className={`flow-filter-custom-checkbox  ${checked  ?  'is-checked'  :  ''}`}  data-mode={mode}  role="checkbox"  aria-checked={checked}  tabIndex={0}  onClick={()  =>  onChange({  target:  {  checked:  !checked  }  })}  onKeyDown={(event)  =>  {  if  (event.key  ===  'Enter'  ||  event.key  ===  ' ')  {  event.preventDefault();  onChange({  target:  {  checked:  !checked  }  });  }  }}><svg  viewBox="0 0 12 12"  aria-hidden="true"><rect  x=".5"  y=".5"  width="11"  height="11"  rx="2"  fill={checked  ?  'currentColor'  :  'none'}  opacity={checked  ?  1  :  0}  /><path  d="m3.1 6.1 1.8 1.8 4-4"  fill="none"  stroke="#151a21"  strokeWidth="1.6"  strokeLinecap="round"  strokeLinejoin="round"  opacity={checked  ?  1  :  0}  /></svg></span>;
const  FilterAddIcon:  React.FC  =  ()  =>  <svg  className="flow-filter-mini-icon"  viewBox="0  0  18  14"  aria-hidden="true"><rect  x=".75"  y=".75"  width="16.5"  height="12.5"  rx="1"  fill="none"  stroke="currentColor"  strokeWidth="1"  /><path  d="M9  4.8v4.4M6.8  7h4.4"  fill="none"  stroke="currentColor"  strokeWidth="1"  strokeLinecap="round"  /></svg>;
const  SortDirectionIcon:  React.FC<{  direction:  'asc'  |  'desc'  }>  =  ({  direction  })  =>  <svg  className="flow-filter-sort-direction-icon"  viewBox="0  0  16  16"  aria-hidden="true"><path  d={direction  ===  'asc'  ?  'M4 12V4m0 0L2 6m2-2 2 2M8 5h6M8 8h4M8 11h2'  :  'M4 4v8m0 0-2-2m2 2 2-2M8 5h2M8 8h4M8 11h6'}  fill="none"  stroke="currentColor"  strokeWidth="1.25"  strokeLinecap="round"  strokeLinejoin="round"  /></svg>;
const  SortMenuChevronIcon:  React.FC  =  ()  =>  <svg  viewBox="0 0 12 12"  width="12"  height="12"  aria-hidden="true"><path  d="m3 4.5 3 3 3-3"  fill="none"  stroke="currentColor"  strokeWidth="1.2"  strokeLinecap="round"  strokeLinejoin="round"  /></svg>;
const  flowDropdownEvent =  'flow-dropdown-open';
const randomId = (): string => {
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
const JsonTreeValue: React.FC<{ name: string; value: unknown; depth: number; comma: boolean }> = ({ name, value, depth, comma }) => {
    const [open, setOpen] = useState(true);
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
                <JsonTreeValue key={key} name={key} value={item} depth={depth + 1} comma={index < count - 1} />
            )) : null}
            {open && count > 0 ? (
                <div className="fjt-line" style={{ paddingLeft: depth * 14 }}>
                    <span className="fjt-punct">{closeBracket}</span>{comma ? <span className="fjt-punct">,</span> : null}
                </div>
            ) : null}
        </>
    );
};
const  FilterFieldLink:  React.FC<{  value:  string;  onChange:  (value:  string)  =>  void  }>  =  ({  value,  onChange  })  =>  <Dropdown overlayClassName="flow-menu-popup"  trigger={['click']}  menu={{  items:  ['record_id','data_source','data_type','status','updated_at'].map((field)  =>  ({  key:  field,  label:  field  })),  onClick:  ({  key  })  =>  onChange(key)  }}><span  className="flow-filter-field-link">{value}</span></Dropdown>;
const  FilterFieldSpan:  React.FC<{  value:  string;  onChange:  (value:  string)  =>  void  }>  =  ({  value,  onChange  })  =>  <span  className="flow-filter-field-link"  role="button"  tabIndex={0}  onClick={()  =>  {  const  next  =  window.prompt('Filter  field',  value);  if  (next)  onChange(next);  }}  onKeyDown={(event)  =>  {  if  (event.key  ===  'Enter')  {  const  next  =  window.prompt('Filter  field',  value);  if  (next)  onChange(next);  }  }}>{value}</span>;
const  filterFields  =  ['record_id',  'data_source',  'data_type',  'raw_data',  'kafka_offset',  'kafka_partition',  'kafka_topic',  'created_at',  'updated_at'];
const  FilterFieldPicker:  React.FC<{  value:  string;  onChange:  (value:  string)  =>  void;  className?:  string;  labelClassName?:  string;  showArrow?:  boolean;  trailing?:  React.ReactNode; fields?: string[]  }>  =  ({  value,  onChange,  className,  labelClassName,  showArrow,  trailing, fields = filterFields  })  =>  {  const  [open, setOpen] = useFlowDropdown(); const  [search,  setSearch]  =  useState('');  const  visibleFields  =  fields.filter((field)  =>  field.toLowerCase().includes(search.toLowerCase()));  return  <Dropdown open={open} onOpenChange={setOpen} overlayClassName="flow-menu-popup flow-filter-field-dropdown"  trigger={['click']}  dropdownRender={()  =>  <div  className="flow-filter-field-popup"><div  className="flow-filter-field-options">{visibleFields.map((field)  =>  <button  type="button"  className={field  ===  value  ?  'is-selected'  :  ''}  key={field}  onClick={()  =>  {  onChange(field);  setOpen(false);  }}>{field}</button>)}</div><div  className="flow-filter-field-search"><Input  size="small"  prefix={<SearchOutlined  />}  value={search}  onChange={(event)  =>  setSearch(event.target.value)}  placeholder="Search"  /></div></div>}><span  className={['flow-filter-field-link', className].filter(Boolean).join(' ')}  role="button"  tabIndex={0}><span  className={labelClassName}>{value || 'Select field'}</span>{showArrow  &&  <DownOutlined  />}{trailing}</span></Dropdown>;  };
const  filterSuggestedValues  =  ['3486',  '3495',  '3526',  '3554',  '3573',  '3593',  '3611',  '3653',  '3714'];
const  FilterValuePicker:  React.FC<{  value:  string;  onChange:  (value:  string)  =>  void; suggestions?: string[]  }>  =  ({  value,  onChange, suggestions = filterSuggestedValues  })  =>  {  const  [open, setOpen] = useFlowDropdown();  const  [draft,  setDraft]  =  useState(value);  const  [search,  setSearch]  =  useState('');  const  [selected,  setSelected]  =  useState(value  ?  [value]  :  []);  const  visibleValues  =  suggestions.filter((item)  =>  item.includes(search));  const  close  =  ()  =>  {  setOpen(false);  setDraft(value);  setSelected(value  ?  [value]  :  []);  setSearch('');  };  return  <Dropdown  open={open}  onOpenChange={(next)  =>  {  if  (next)  {  setDraft(value);  setSelected(value  ?  [value]  :  []);  }  setOpen(next);  }}  overlayClassName="flow-menu-popup flow-filter-value-dropdown"  trigger={['click']}  dropdownRender={()  =>  <div  className="flow-filter-value-popup"><Input  size="small"  value={draft}  onChange={(event) => { setDraft(event.target.value); setSelected([]); }} autoFocus  /><strong>Suggested values</strong><div  className="flow-filter-value-options">{visibleValues.map((item)  =>  <label  key={item}><FilterCheckbox  mode="multiple"  checked={selected.includes(item)}  onChange={(event)  =>  setSelected((items)  =>  event.target.checked  ?  [...new  Set([...items,  item])]  :  items.filter((entry)  =>  entry  !==  item))}  /><span  className="flow-filter-value-label">{item}</span></label>)}</div><div  className="flow-filter-field-search"><Input  size="small"  prefix={<SearchOutlined  />}  value={search}  onChange={(event)  =>  setSearch(event.target.value)}  placeholder="Search"  /></div><div  className="flow-filter-value-actions"><Button  size="small"  onClick={()  =>  {  onChange(selected.join(',  ')  ||  draft);  setOpen(false);  }}>Apply</Button><Button  size="small"  onClick={close}>Cancel</Button></div></div>}><span  className="flow-filter-value"  role="button"  tabIndex={0}>{value  ||  '<?>'}</span></Dropdown>;  };
const filterOperators = [
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
const  FilterOperatorSpan:  React.FC<{  value:  string;  onChange:  (value:  string)  =>  void  }>  =  ({  value,  onChange  })  =>  { const [open, setOpen] = useFlowDropdown(); return <Dropdown open={open} onOpenChange={setOpen} overlayClassName="flow-menu-popup flow-filter-operator-dropdown" trigger={['click']} menu={{ items: filterOperators.map((operator) => ({ key: operator.value, label: operator.label })), onClick: ({ key }) => { onChange(key); setOpen(false); } }}><span className="flow-filter-op" role="button" tabIndex={0}>{filterOperators.find((operator) => operator.value === value)?.label || value}</span></Dropdown>; };
const  filterNestingDepth  =  (id:  string)  =>  (id.match(/nested-/g)  ||  []).length;
type SortCondition = { field: string; direction: 'asc' | 'desc' };
type OpenApiAccess = EtlPublishedApi;
const  filterGroupBounds  =  (rows:  Array<{  id:  string  }>,  index:  number)  =>  {  const  stack:  number[]  =  [];  const  pairs:  Array<{  start:  number;  end:  number  }>  =  [];  rows.forEach((item,  rowIndex)  =>  {  if  (item.id.startsWith('group-open-'))  stack.push(rowIndex);  else  if  (item.id.startsWith('group-close-')  &&  stack.length)  pairs.push({  start:  stack.pop()  as  number,  end:  rowIndex  });  });  return  pairs.filter((pair)  =>  pair.start  <=  index  &&  index  <=  pair.end).sort((x,  y)  =>  (x.end  -  x.start)  -  (y.end  -  y.start))[0]  ||  null;  };
const  findContainingClose  =  (rows:  Array<{  id:  string  }>,  index:  number)  =>  {  let  depth  =  0;  for  (let  i  =  index  +  1;  i  <  rows.length;  i  +=  1)  {  if  (rows[i].id.startsWith('group-open-'))  depth  +=  1;  else  if  (rows[i].id.startsWith('group-close-'))  {  if  (depth  ===  0)  return  i;  depth  -=  1;  }  }  return  -1;  };
const  normalizeFilterDepth  =  <T extends { id: string }>(rows: T[])  =>  {  let  depth  =  0;  return  rows.map((item)  =>  {  const  raw  =  item.id.replace(/^group-(?:open|close|filter)-/,  '').replace(/^(nested-)+/,  '');  if  (item.id.startsWith('group-open-'))  {  const  next  =  {  ...item,  id:  `group-open-${'nested-'.repeat(depth)}${raw}`  };  depth  +=  1;  return  next;  }  if  (item.id.startsWith('group-close-'))  {  depth  =  Math.max(0,  depth  -  1);  return  {  ...item,  id:  `group-close-${'nested-'.repeat(depth)}${raw}`  };  }  return  depth  >  0  ?  {  ...item,  id:  `group-filter-${'nested-'.repeat(depth)}${raw}`  }  :  {  ...item,  id:  raw  };  });  };
function  cleanEmptyFilterGroups<T extends { id: string }>(rows: T[])  {  let  next  =  rows;  let  changed  =  true;  while  (changed)  {  changed  =  false;  for  (let  i  =  0;  i  <  next.length;  i  += 1)  {  if  (!next[i].id.startsWith('group-open-'))  continue;  const  group  =  filterGroupBounds(next,  i);  if  (!group)  continue;  const  hasCondition  =  next.slice(group.start  +  1,  group.end).some((item)  =>  !item.id.startsWith('group-open-')  &&  !item.id.startsWith('group-close-'));  if  (!hasCondition)  {  next  =  next.filter((_,  rowIndex)  =>  rowIndex  <  group.start  ||  rowIndex  >  group.end);  changed  =  true;  break;  }  }  }  return  next;  }
function  moveFilterRows<T extends { id: string }>(rows: T[], index: number, direction: 'up' | 'down')  {  const  isGroupBoundary  =  rows[index]?.id.startsWith('group-open-')  ||  rows[index]?.id.startsWith('group-close-');  const  bounds  =  isGroupBoundary  ?  filterGroupBounds(rows,  index)  :  null;  const  start  =  bounds?.start  ??  index;  const  end  =  bounds?.end  ??  index;  const  target  =  direction  ===  'up'  ?  start  -  1  :  end  +  1;  if  (target  <  0  ||  target  >=  rows.length)  return  rows;  const  block  =  rows.slice(start,  end  +  1);  const  next  =  [...rows];  next.splice(start,  block.length);  const  insertAt  =  direction  ===  'up'  ?  start  -  1  :  start  +  1;  next.splice(insertAt,  0,  ...block);  return  cleanEmptyFilterGroups(normalizeFilterDepth(next));  };
function  removeFilterRow<T extends { id: string }>(rows: T[], index: number)  {  const  bounds  =  filterGroupBounds(rows,  index);  if  (!bounds)  return  rows.filter((_,  rowIndex)  =>  rowIndex  !==  index);  let  next  =  rows[index].id.startsWith('group-open-')  ||  rows[index].id.startsWith('group-close-')  ?  rows.filter((_,  rowIndex)  =>  rowIndex  <  bounds.start  ||  rowIndex  >  bounds.end)  :  rows.filter((_,  rowIndex)  =>  rowIndex  !==  index);  let  changed  =  true;  while  (changed)  {  changed  =  false;  for  (let  i  =  0;  i  <  next.length;  i  +=  1)  {  if  (!next[i].id.startsWith('group-open-'))  continue;  const  group  =  filterGroupBounds(next,  i);  if  (!group)  continue;  const  hasCondition  =  next.slice(group.start  +  1,  group.end).some((item)  =>  !item.id.startsWith('group-open-')  &&  !item.id.startsWith('group-close-'));  if  (!hasCondition)  {  next  =  next.filter((_,  rowIndex)  =>  rowIndex  <  group.start  ||  rowIndex  >  group.end);  changed  =  true;  break;  }  }  }  return  next;  };
const  FilterGroupIcon:  React.FC  =  ()  =>  <svg  className="flow-filter-mini-icon"  viewBox="0  0  18  14"  aria-hidden="true"><rect  x=".75"  y=".75"  width="16.5"  height="12.5"  rx="1"  fill="none"  stroke="currentColor"  strokeWidth="1"  /><path  d="M6.8  4.2C5.8  4.7  5.8  8.3  6.8  8.8M10.2  4.2C11.2  4.7  11.2  8.3  10.2  8.8M13.4  7.7v2.2M12.3  8.8h2.2"  fill="none"  stroke="currentColor"  strokeWidth="1"  strokeLinecap="round"  strokeLinejoin="round"  /></svg>;
const  PostgresIcon:  React.FC<{  className?:  string  }>  =  ({  className  })  =>  <svg  className={className}  viewBox="0  0  24  24"  role="img"  aria-label="Postgres"  fill="currentColor"><path  fillRule="evenodd"  clipRule="evenodd"  d="M23.5594  14.7228a.5269.5269  0  0  0-.0563-.1191c-.139-.2632-.4768-.3418-1.0074-.2321-1.6533.3411-2.2935.1312-2.5256-.0191  1.342-2.0482  2.445-4.522  3.0411-6.8297.2714-1.0507.7982-3.5237.1222-4.7316a1.5641  1.5641  0  0  0-.1509-.235C21.6931.9086  19.8007.0248  17.5099.0005c-1.4947-.0158-2.7705.3461-3.1161.4794a9.449  9.449  0  0  0-.5159-.0816  8.044  8.044  0  0  0-1.3114-.1278c-1.1822-.0184-2.2038.2642-3.0498.8406-.8573-.3211-4.7888-1.645-7.2219.0788C.9359  2.1526.3086  3.8733.4302  6.3043c.0409.818.5069  3.334  1.2423  5.7436.4598  1.5065.9387  2.7019  1.4334  3.582.553.9942  1.1259  1.5933  1.7143  1.7895.4474.1491  1.1327.1441  1.8581-.7279.8012-.9635  1.5903-1.8258  1.9446-2.2069.4351.2355.9064.3625  1.39.3772a.0569.0569  0  0  0  .0004.0041  11.0312  11.0312  0  0  0-.2472.3054c-.3389.4302-.4094.5197-1.5002.7443-.3102.064-1.1344.2339-1.1464.8115-.0025.1224.0329.2309.0919.3268.2269.4231.9216.6097  1.015.6331  1.3345.3335  2.5044.092  3.3714-.6787-.017  2.231.0775  4.4174.3454  5.0874.2212.5529.7618  1.9045  2.4692  1.9043.2505  0  .5263-.0291.8296-.0941  1.7819-.3821  2.5557-1.1696  2.855-2.9059.1503-.8707.4016-2.8753.5388-4.1012.0169-.0703.0357-.1207.057-.1362.0007-.0005.0697-.0471.4272.0307a.3673.3673  0  0  0  .0443.0068l.2539.0223.0149.001c.8468.0384  1.9114-.1426  2.5312-.4308.6438-.2988  1.8057-1.0323  1.5951-1.6698zM2.371  11.8765c-.7435-2.4358-1.1779-4.8851-1.2123-5.5719-.1086-2.1714.4171-3.6829  1.5623-4.4927  1.8367-1.2986  4.8398-.5408  6.108-.13-.0032.0032-.0066.0061-.0098.0094-2.0238  2.044-1.9758  5.536-1.9708  5.7495-.0002.0823.0066.1989.0162.3593.0348.5873.0996  1.6804-.0735  2.9184-.1609  1.1504.1937  2.2764.9728  3.0892.0806.0841.1648.1631.2518.2374-.3468.3714-1.1004  1.1926-1.9025  2.1576-.5677.6825-.9597.5517-1.0886.5087-.3919-.1307-.813-.5871-1.2381-1.3223-.4796-.839-.9635-2.0317-1.4155-3.5126zm6.0072  5.0871c-.1711-.0428-.3271-.1132-.4322-.1772.0889-.0394.2374-.0902.4833-.1409  1.2833-.2641  1.4815-.4506  1.9143-1.0002.0992-.126.2116-.2687.3673-.4426a.3549.3549  0  0  0  .0737-.1298c.1708-.1513.2724-.1099.4369-.0417.156.0646.3078.26.3695.4752.0291.1016.0619.2945-.0452.4444-.9043  1.2658-2.2216  1.2494-3.1676  1.0128zm2.094-3.988-.0525.141c-.133.3566-.2567.6881-.3334  1.003-.6674-.0021-1.3168-.2872-1.8105-.8024-.6279-.6551-.9131-1.5664-.7825-2.5004.1828-1.3079.1153-2.4468.079-3.0586-.005-.0857-.0095-.1607-.0122-.2199.2957-.2621  1.6659-.9962  2.6429-.7724.4459.1022.7176.4057.8305.928.5846  2.7038.0774  3.8307-.3302  4.7363-.084.1866-.1633.3629-.2311.5454zm7.3637  4.5725c-.0169.1768-.0358.376-.0618.5959l-.146.4383a.3547.3547  0  0  0-.0182.1077c-.0059.4747-.054.6489-.115.8693-.0634.2292-.1353.4891-.1794  1.0575-.11  1.4143-.8782  2.2267-2.4172  2.5565-1.5155.3251-1.7843-.4968-2.0212-1.2217a6.5824  6.5824  0  0  0-.0769-.2266c-.2154-.5858-.1911-1.4119-.1574-2.5551.0165-.5612-.0249-1.9013-.3302-2.6462.0044-.2932.0106-.5909.019-.8918a.3529.3529  0  0  0-.0153-.1126  1.4927  1.4927  0  0  0-.0439-.208c-.1226-.4283-.4213-.7866-.7797-.9351-.1424-.059-.4038-.1672-.7178-.0869.067-.276.1831-.5875.309-.9249l.0529-.142c.0595-.16.134-.3257.213-.5012.4265-.9476  1.0106-2.2453.3766-5.1772-.2374-1.0981-1.0304-1.6343-2.2324-1.5098-.7207.0746-1.3799.3654-1.7088.5321a5.6716  5.6716  0  0  0-.1958.1041c.0918-1.1064.4386-3.1741  1.7357-4.4823a4.0306  4.0306  0  0  1  .3033-.276.3532.3532  0  0  0  .1447-.0644c.7524-.5706  1.6945-.8506  2.802-.8325.4091.0067.8017.0339  1.1742.081  1.939.3544  3.2439  1.4468  4.0359  2.3827.8143.9623  1.2552  1.9315  1.4312  2.4543-1.3232-.1346-2.2234.1268-2.6797.779-.9926  1.4189.543  4.1729  1.2811  5.4964.1353.2426.2522.4522.2889.5413.2403.5825.5515.9713.7787  1.2552.0696.087.1372.1714.1885.245-.4008.1155-1.1208.3825-1.0552  1.717-.0123.1563-.0423.4469-.0834.8148-.0461.2077-.0702.4603-.0994.7662zm.8905-1.6211c-.0405-.8316.2691-.9185.5967-1.0105a2.8566  2.8566  0  0  0  .135-.0406  1.202  1.202  0  0  0  .1342.103c.5703.3765  1.5823.4213  3.0068.1344-.2016.1769-.5189.3994-.9533.6011-.4098.1903-1.0957.333-1.7473.3636-.7197.0336-1.0859-.0807-1.1721-.151zm.5695-9.2712c-.0059.3508-.0542.6692-.1054  1.0017-.055.3576-.112.7274-.1264  1.1762-.0142.4368.0404.8909.0932  1.3301.1066.887.216  1.8003-.2075  2.7014a3.5272  3.5272  0  0  1-.1876-.3856c-.0527-.1276-.1669-.3326-.3251-.6162-.6156-1.1041-2.0574-3.6896-1.3193-4.7446.3795-.5427  1.3408-.5661  2.1781-.463zm.2284  7.0137a12.3762  12.3762  0  0  0-.0853-.1074l-.0355-.0444c.7262-1.1995.5842-2.3862.4578-3.4385-.0519-.4318-.1009-.8396-.0885-1.2226.0129-.4061.0666-.7543.1185-1.0911.0639-.415.1288-.8443.1109-1.3505.0134-.0531.0188-.1158.0118-.1902-.0457-.4855-.5999-1.938-1.7294-3.253-.6076-.7073-1.4896-1.4972-2.6889-2.0395.5251-.1066  1.2328-.2035  2.0244-.1859  2.0515.0456  3.6746.8135  4.8242  2.2824a.908.908  0  0  1  .0667.1002c.7231  1.3556-.2762  6.2751-2.9867  10.5405zm-8.8166-6.1162c-.025.1794-.3089.4225-.6211.4225a.5821.5821  0  0  1-.0809-.0056c-.1873-.026-.3765-.144-.5059-.3156-.0458-.0605-.1203-.178-.1055-.2844.0055-.0401.0261-.0985.0925-.1488.1182-.0894.3518-.1226.6096-.0867.3163.0441.6426.1938.6113.4186zm7.9305-.4114c.0111.0792-.049.201-.1531.3102-.0683.0717-.212.1961-.4079.2232a.5456.5456  0  0  1-.075.0052c-.2935  0-.5414-.2344-.5607-.3717-.024-.1765.2641-.3106.5611-.352.297-.0414.6111.0088.6356.1851z"  /></svg>;
const  MongoDbIcon:  React.FC<{  className?:  string  }>  =  ({  className  })  =>  <svg  className={className}  viewBox="0  0  24  24"  role="img"  aria-label="MongoDB"  fill="currentColor"><path  d="M17.193  9.555c-1.264-5.58-4.252-7.414-4.573-8.115-.28-.394-.53-.954-.735-1.44-.036.495-.055.685-.523  1.184-.723.566-4.438  3.682-4.74  10.02-.282  5.912  4.27  9.435  4.888  9.884l.07.05A73.49  73.49  0  0  1  11.91  24h.481c.114-1.032.284-2.056.51-3.07.417-.296.604-.463.85-.693a11.342  11.342  0  0  0  3.639-8.464c.01-.814-.103-1.662-.197-2.218zm-5.336  8.195s0-8.291.275-8.29c.213  0  .49  10.695.49  10.695-.381-.045-.765-1.76-.765-2.405z"  /></svg>;
const  ElasticsearchIcon:  React.FC<{  className?:  string  }>  =  ({  className  })  =>  <svg  className={className}  viewBox="0  0  512  512"  role="img"  aria-label="Elasticsearch"  fillRule="evenodd"  clipRule="evenodd"  strokeLinejoin="round"><path  d="M21.625  256c0  21.62  3.035  42.495  8.195  62.5h304.304c34.516  0  62.5-27.985  62.5-62.5  0-34.516-27.984-62.5-62.5-62.5H29.82a249.101  249.101  0  0  0-8.195  62.5"  fill="#343741"  /><path  d="M442.308  125.718a240.051  240.051  0  0  0  24.324-25.968C420.796  42.637  350.527  6  271.624  6  172.855  6  87.863  63.46  47.304  146.625h341.664a78.627  78.627  0  0  0  53.328-20.907"  fill="#fec514"  /><path  d="M388.968  365.374H47.316C87.88  448.538  172.856  506  271.624  506c78.907  0  149.172-36.652  195.008-93.75a238.559  238.559  0  0  0-24.324-25.968  78.682  78.682  0  0  0-53.34-20.907"  fill="#00bfb3"  /></svg>;
const  S3Icon:  React.FC<{  className?:  string  }>  =  ({  className  })  =>  <svg  className={className}  viewBox="0  0  512  512"  role="img"  aria-label="S3"><rect  width="512"  height="512"  rx="15%"  fill="#fff"/><path  fill="#e05243"  d="M260  348l-137  33V131l137  32z"/><path  fill="#8c3123"  d="M256  349l133  32V131l-133  32v186"/><path  fill="#e05243"  d="M256  64v97l58  14V93zm133  67v250l26-13V143zm-133  77v97l58-8v-82zm58  129l-58  14v97l58-29z"/><path  fill="#8c3123"  d="M256  448V351l-58-14v82zm-133-67V131l-26  13v238zm133-77v-97l-58  8v82zm-58-129l58-14V64l-58  29z"/><path  fill="#5e1f18"  d="M314  175l-58  11-58-11  58-15  58  15"/><path  fill="#f2b0a9"  d="M314  337l-58-11-58  11  58  16  58-16"/></svg>;
const  BackIcon:  React.FC<{  className?:  string  }>  =  ({  className  })  =>  <svg  className={className}  viewBox="0  0  24  24"  role="img"  aria-label="Back"  fill="none"><path  d="M15.5  6.5L10  12l5.5  5.5M10.5  12H19"  stroke="currentColor"  strokeWidth="1.7"  strokeLinecap="round"  strokeLinejoin="round"/></svg>;
const  CloseIcon:  React.FC<{  className?:  string  }>  =  ({  className  })  =>  <svg  className={className}  viewBox="0  0  24  24"  role="img"  aria-label="Close"  fill="none"><path  d="M7  7l10  10M17  7L7  17"  stroke="currentColor"  strokeWidth="1.7"  strokeLinecap="round"/></svg>;
const  EditIcon:  React.FC<{  className?:  string  }>  =  ({  className  })  =>  <svg  className={className}  viewBox="0  0  24  24"  role="img"  aria-label="Edit"  fill="none"><path  d="M5.5  17.8V19h1.2l9.9-9.9-1.2-1.2-9.9  9.9Z"  stroke="currentColor"  strokeWidth="1.6"  strokeLinejoin="round"/><path  d="m14.7  7.9  1.4-1.4a1.7  1.7  0  0  1  2.4  2.4l-1.4  1.4"  stroke="currentColor"  strokeWidth="1.6"  strokeLinecap="round"/></svg>;
const  DeleteIcon:  React.FC<{  className?:  string  }>  =  ({  className  })  =>  <svg  className={className}  viewBox="0  0  24  24"  role="img"  aria-label="Delete"  fill="none"><path  d="M6.5  7.5h11M9.5  7.5V5.8h5v1.7m-6.7  0  .7  11h5l.7-11"  stroke="currentColor"  strokeWidth="1.6"  strokeLinecap="round"  strokeLinejoin="round"/></svg>;
const  SqlPreview:  React.FC<{  sql:  string  }>  =  React.memo(({  sql  })  =>  {
    const  tokens  =  sql.split(/(--.*?$|'(?:''|[^'])*'|\b(?:CREATE|TABLE|PRIMARY|KEY|NOT|NULL|DEFAULT|UNIQUE|REFERENCES|CONSTRAINT|CHECK|ON|DELETE|UPDATE|CASCADE|RESTRICT|SET|TEXT|BIGINT|INTEGER|BOOLEAN|TIMESTAMPTZ|JSONB)\b|\b\d+(?:\.\d+)?\b)/gim);
    return  <pre  className="flow-td-sql-preview">{tokens.map((token,  index)  =>  {
        const  className  =  /^--/.test(token)  ?  'is-comment'
            :  /^'/.test(token)  ?  'is-string'
                :  /^\d/.test(token)  ?  'is-number'
                    :  /^(CREATE|TABLE|PRIMARY|KEY|NOT|NULL|DEFAULT|UNIQUE|REFERENCES|CONSTRAINT|CHECK|ON|DELETE|UPDATE|CASCADE|RESTRICT|SET|TEXT|BIGINT|INTEGER|BOOLEAN|TIMESTAMPTZ|JSONB)$/i.test(token)  ?  'is-keyword'
                        :  undefined;
        return  <span  className={className}  key={`${index}-${token}`}>{token}</span>;
    })}</pre>;
});

const flowNavigation = [
    { path: '/flow', label: 'Discover', icon: <SearchOutlined /> },
    { path: '/flow/layers', label: 'Schemas', icon: <DatabaseOutlined /> },
    { path: '/flow/lineage', label: 'Lineage', icon: <BranchesOutlined /> },
    { path: '/flow/transforms', label: 'Transformations', icon: <CodeOutlined /> },
    { path: '/flow/runtime', label: 'Runtime', icon: <ExperimentOutlined /> },
];

const Pipeline: React.FC = () => {
    const { pathname } = useLocation();
    const navigate = useNavigate();
    const rootRef = useRef<HTMLElement>(null);
    const [refreshToken, setRefreshToken] = useState(0);
    const find = () => {
        const input = rootRef.current?.querySelector<HTMLInputElement>('[data-testid="ontology-search"], input[aria-label="Search lineage nodes"], input[aria-label="Search schemas"]');
        input?.focus(); input?.select();
    };
    return <ConfigProvider locale={enUS}><main ref={rootRef} className="flow-console flow-console--ontology">
        <div className="flow-domain-nav" data-testid="flow-domain-nav" role="toolbar" aria-label="Flow toolbar">
            <span className="flow-domain-context">{flowNavigation.find((item) => item.path === pathname)?.label}</span>
            <div className="flow-domain-links">
                <Dropdown overlayClassName="flow-menu-popup" trigger={['click']} menu={{ selectedKeys: [pathname], items: flowNavigation.map((item) => ({ key: item.path, label: item.label, icon: item.icon })), onClick: ({ key }) => navigate(key) }}><button type="button" className="flow-domain-command" aria-label="View" aria-haspopup="menu">View<DownOutlined /></button></Dropdown>
                <button type="button" className="flow-domain-command" onClick={() => setRefreshToken((value) => value + 1)}>Refresh</button>
                <button type="button" className="flow-domain-command" disabled={pathname === '/flow/runtime'} onClick={find}>Find</button>
            </div>
        </div>
        <section className="flow-core">
            {pathname === '/flow' ? <OntologyExplorer refreshToken={refreshToken} />
                : pathname === '/flow/lineage' ? <OntologyLineage refreshToken={refreshToken} />
                    : pathname === '/flow/runtime' ? <FlowRuntime refreshToken={refreshToken} />
                        : <DataLayersWorkspace refreshToken={refreshToken} />}
        </section>
    </main></ConfigProvider>;
};

const FlowRuntime: React.FC<{ refreshToken: number }> = ({ refreshToken }) => {
    const [allowed, setAllowed] = useState<boolean>();
    const [error, setError] = useState(false);
    const [version, setVersion] = useState(0);
    useEffect(() => {
        let active = true;
        setError(false);
        fetchCurrentUserContext().then((context) => { if (active) setAllowed(context.user.is_superuser); })
            .catch(() => { if (active) setError(true); });
        return () => { active = false; };
    }, [version, refreshToken]);
    return <div className="flow-ontology-runtime" data-testid="ontology-runtime"><div className="flow-ontology-page-heading"><span>OBSERVABILITY</span><h1>Runtime status</h1><p>Inspect the current ingestion and consumption infrastructure.</p></div>
        {error ? <div role="alert">Unable to verify your access.<Button onClick={() => setVersion((value) => value + 1)}>Retry</Button></div>
            : allowed === undefined ? <Spin />
                : allowed ? <CdcStatusPanel key={refreshToken} /> : <div className="flow-ontology-message">Runtime diagnostics are available to administrators. You can continue exploring data assets and tables.</div>}
    </div>;
};
const  DataLayersWorkspace:  React.FC<{ refreshToken: number }>  =  ({ refreshToken })  =>  {
    const { search, pathname } = useLocation();
    const workspacePath = pathname === '/flow/transforms' ? '/flow/transforms' : '/flow/layers';
    const  navigate  =  useNavigate();
    const [canManage, setCanManage] = useState(false);
    const managePermission = useRef(false);
    managePermission.current = canManage;
    const [catalogLoaded, setCatalogLoaded] = useState(false);
    const [schemaSearch, setSchemaSearch] = useState('');
    const [schemas, setSchemas] = useState<LayerNode[]>([]);
    const [connections, setConnections] = useState<EtlConnection[]>([]);
    const [serverTables, setServerTables] = useState<LayerTable[]>([]);
    const [partitions, setPartitions] = useState<EtlPartition[]>([]);
    const [data, setData] = useState<QueryResult>();
    const [stream, setStream] = useState<EtlStreamState>();
    const [loading, setLoading] = useState(false);
    const [tablesLoading, setTablesLoading] = useState(false);
    const [tablesLoadedSchema, setTablesLoadedSchema] = useState<string>();
    const [requestError, setRequestError] = useState('');
    const [streamError, setStreamError] = useState('');
    const [scriptNote, setScriptNote] = useState('');
    const [mutationLoading, setMutationLoading] = useState(false);
    const [keywordsOpen, setKeywordsOpen] = useState(false);
    const [keywordConfig, setKeywordConfig] = useState<VulnerabilityKeywordConfig>();
    const [keywordsLoading, setKeywordsLoading] = useState(false);
    const [keywordsError, setKeywordsError] = useState('');
    const [keywordsSaving, setKeywordsSaving] = useState(false);
    const [keywordDownload, setKeywordDownload] = useState<string>();
    const [refreshVersion, setRefreshVersion] = useState(0);
    const [pageNumber, setPageNumber] = useState(1);
    const [pageSize, setPageSize] = useState(20);
    const [offsetPartition, setOffsetPartition] = useState<number>();
    const [topicDraft, setTopicDraft] = useState('');
    const [referenceColumns, setReferenceColumns] = useState<Record<string, string[]>>({});
    const [appliedQuery, setAppliedQuery] = useState<Pick<EtlDataQuery, 'filters' | 'sorts' | 'search'>>({ filters: [], sorts: [], search: '' });
    const tableRequest = useRef(0);
    const loadedSchema = useRef('');
    const dataRequest = useRef(0);
    const metadataRequest = useRef(0);
    const apiRequest = useRef(0);
    const streamRequest = useRef(0);
    const selection = useRef('');
    const catalogRequest = useRef(0);
    const workspaceActive = useRef(true);
    const mutationPending = useRef(false);
    const validationPending = useRef(false);
    const errorText = (error: unknown) => {
        const response = (error as { response?: { data?: { errors?: unknown; detail?: unknown; message?: unknown } }; message?: string })?.response?.data;
        const detail = response?.errors ?? response?.detail ?? response?.message;
        return typeof detail === 'string' ? detail : detail ? JSON.stringify(detail) : (error as Error)?.message || 'Request failed';
    };
    const beginMutation = () => {
        if (!workspaceActive.current || !managePermission.current || mutationPending.current) return false;
        mutationPending.current = true; setMutationLoading(true); return true;
    };
    const endMutation = () => { mutationPending.current = false; if (workspaceActive.current) setMutationLoading(false); };
    const refreshCatalog = async () => {
        const version = ++catalogRequest.current;
        try {
            const [layers, resources, context] = await Promise.all([fetchLayers(), fetchEtlConnections(), fetchCurrentUserContext()]);
            if (version !== catalogRequest.current || !workspaceActive.current) return;
            setCanManage(context.user.is_superuser === true);
            const visibleLayers = layers.map((item) => ({ ...item, key: item.schemaName || (item.key.startsWith('ts_') ? item.key : `ts_${item.key}`) })).filter((item) => item.key !== 'ts_meta');
            setSchemas(visibleLayers); setConnections(resources);
            setDatabaseAliases(Object.fromEntries(resources.map((item) => [item.id, item.name])));
            setSchemaAliases(Object.fromEntries(visibleLayers.map((item) => [item.key, item.alias || item.label])));
            setCatalogLoaded(true);
        } catch (error) { if (version === catalogRequest.current && workspaceActive.current) throw error; }
    };

    const  [collectionOpen,  setCollectionOpen]  =  useState(false);
    const  [connectorStep,  setConnectorStep]  =  useState<'type'  |  'config'>('type');
    const  [connectorDatabase,  setConnectorDatabase]  =  useState<'postgres'  |  'mongodb'  |  'elasticsearch'  |  's3'>('postgres');
    const  [connectorSelectionActive,  setConnectorSelectionActive]  =  useState(false);
    const  [databaseSearch,  setDatabaseSearch]  =  useState('');
    const  [databaseName,  setDatabaseName]  =  useState('');
    const  [expandedDatabases,  setExpandedDatabases]  =  useState<Record<string,  boolean>>({});
    const  [selectedSchema,  setSelectedSchema]  =  useState('');
    const  [connectorSelectedSchema,  setConnectorSelectedSchema]  =  useState('');
    const  [schemaNameDraft,  setSchemaNameDraft]  =  useState('');
    const [schemaAliasDraft, setSchemaAliasDraft] = useState('');
    const  [schemaEditing,  setSchemaEditing]  =  useState(true);
    const  [resourcePanel,  setResourcePanel]  =  useState<'none'  |  'create'  |  'edit'>('none');
    const  [resourceConnectionName,  setResourceConnectionName]  =  useState('');
    const  [resourceDatabase,  setResourceDatabase]  =  useState('');
    const  [resourceSchema,  setResourceSchema]  =  useState('');
    const  [resourceSchemas,  setResourceSchemas]  =  useState<string[]>([]);
    const  [databaseAliases,  setDatabaseAliases]  =  useState<Record<string,  string>>({});
    const  [schemaAliases,  setSchemaAliases]  =  useState<Record<string,  string>>({});
    const [table, setTable] = useState<LayerTable>({ name: '', rowCount: 0, size: '', updatedAt: '' });
    const  [partition,  setPartition]  =  useState<string>();
    const  [offsetOpen,  setOffsetOpen]  =  useState(false);
    const  [offset,  setOffset]  =  useState<number>();
    const  [offsets,  setOffsets]  =  useState<Array<{ partition: number; offset: number }>>([]);
    const [script, setScript] = useState('');
    const [savedScript, setSavedScript] = useState('');
    const [scriptLoaded, setScriptLoaded] = useState(false);
    const [scriptValidating, setScriptValidating] = useState(false);
    const scriptContent = useRef('');
    scriptContent.current = script;
    const  [createOpen,  setCreateOpen]  =  useState(false);
    const  [draftExists,  setDraftExists]  =  useState(false);
    const  [inputTopics,  setInputTopics]  =  useState<string[]>([]);
    const  [activeTopicIndex,  setActiveTopicIndex]  =  useState(0);
    const  [scriptOpen,  setScriptOpen]  =  useState(false);
    const  [tableSearch,  setTableSearch]  =  useState('');
    const  [closedTables,  setClosedTables]  =  useState<Set<string>>(new  Set());
    const  [editingRowKey,  setEditingRowKey]  =  useState<string>();
    const  [editingDraft,  setEditingDraft]  =  useState<Record<string,  unknown>  |  null>(null);
    const  [jsonRow,  setJsonRow]  =  useState<Record<string,  unknown>  |  null>(null);
    const  tableTabsRef  =  useRef<HTMLDivElement>(null);
    const  [filterOpen,  setFilterOpen]  =  useState(false);
    const  [filterText,  setFilterText]  =  useState('');
    const  [filterRules,  setFilterRules]  =  useState([{  id:  'rule-1',  field:  'record_id',  operator:  '=',  value:  '',  active:  true,  join:  'and'  }]);  const  [filterMoving,  setFilterMoving]  =  useState<{  id:  string;  peerId:  string;  direction:  'up'  |  'down'  }  |  null>(null);  const  [filterGroupOpen,  setFilterGroupOpen]  =  useState(false);
    const  [sortField,  setSortField]  =  useState('record_id');
    const  [sortDirection,  setSortDirection]  =  useState<'asc'  |  'desc'>('asc');
    const  [sortConditions,  setSortConditions]  =  useState<SortCondition[]>([]);
    const  sortConditionsRef  =  useRef<HTMLDivElement>(null);
    const  [sortConditionsOverflow,  setSortConditionsOverflow]  =  useState(false);
    const  previousSortConditionCount  =  useRef(sortConditions.length);
    useLayoutEffect(()  =>  {  const  element  =  sortConditionsRef.current;  if  (!element)  return;  const  update  =  ()  =>  setSortConditionsOverflow(element.scrollWidth  >  element.clientWidth  +  1);  update();  const  observer  =  new  ResizeObserver(update);  observer.observe(element);  return  ()  =>  observer.disconnect();  },  [sortConditions.length, sortField]);
    useLayoutEffect(()  =>  {  if  (sortConditions.length  >  previousSortConditionCount.current)  sortConditionsRef.current?.scrollTo({ left: sortConditionsRef.current.scrollWidth, behavior: 'smooth' });  previousSortConditionCount.current = sortConditions.length;  },  [sortConditions.length]);
    useEffect(()  =>  {
        // Wheel over the tab strip scrolls horizontally; the scrollbar stays hidden.
        const  node  =  tableTabsRef.current;
        if  (!node)  return;
        const  onWheel  =  (event:  WheelEvent)  =>  {
            if  (node.scrollWidth  <=  node.clientWidth  +  1)  return;
            const  vertical  =  Math.abs(event.deltaY)  >  Math.abs(event.deltaX);
            if  (!vertical)  return;
            event.preventDefault();
            node.scrollLeft  +=  event.deltaY;
        };
        node.addEventListener('wheel',  onWheel,  {  passive:  false  });
        return  ()  =>  node.removeEventListener('wheel',  onWheel);
    },  []);
    useEffect(()  =>  {  if  (!sortConditionsOverflow)  return;  const  frame  =  window.requestAnimationFrame(()  =>  sortConditionsRef.current?.scrollTo({ left: sortConditionsRef.current.scrollWidth, behavior: 'smooth' }));  return  ()  =>  window.cancelAnimationFrame(frame);  },  [sortConditionsOverflow]);
    const  [partitionOpen,  setPartitionOpen]  =  useState(false);
    const  [analysisOpen,  setAnalysisOpen]  =  useState(false);
    const  activateTool  =  (tool:  'filter'  |  'partition'  |  'script'  |  'analysis')  =>  {
        if (tool === 'script' && scriptOpen) { closeScript(); return; }
        const  next  =  (tool  ===  'filter'  &&  !filterOpen)  ||  (tool  ===  'partition'  &&  !partitionOpen)  ||  (tool  ===  'script'  &&  !scriptOpen)  ||  (tool  ===  'analysis'  &&  !analysisOpen);
        setFilterOpen(next  &&  tool  ===  'filter');  setPartitionOpen(next  &&  tool  ===  'partition');  setScriptOpen(next  &&  tool  === 'script');  setAnalysisOpen(next  &&  tool  === 'analysis');
    };
    const [openApis, setOpenApis] = useState<OpenApiAccess[]>([]);
    const [apisDirty, setApisDirty] = useState(false);
    const [apisLoading, setApisLoading] = useState(false);
    const  apiCount  =  openApis.length;
    const  [apiListOpen,  setApiListOpen]  =  useState(false);
    const addApi = async () => {
        if (!selectedTableAvailable || apisLoading || !beginMutation()) return;
        const target = selection.current; apiRequest.current++;
        try {
            const now = new Date();
            const api = await createEtlApi(selectedSchema, table.name, {
                validFrom: now.toISOString(), validUntil: new Date(now.getTime() + 30 * 86400000).toISOString(),
                filterType: [], filterSource: [], ...appliedQuery, partition,
            });
            if (selection.current === target) setOpenApis((current) => [...current, api]);
            message.success('Read API published');
        } catch (error) { message.error(errorText(error)); } finally { endMutation(); }
    };
    const updateApi = (id: string, changes: Partial<Omit<OpenApiAccess, 'id'>>) => { setApisDirty(true); setOpenApis((current) => current.map((api) => api.id === id ? { ...api, ...changes } : api)); };
    const saveApis = async () => {
        if (!selectedTableAvailable || apisLoading || !openApis.length || !beginMutation()) return;
        const target = selection.current; apiRequest.current++;
        let savedCount = 0;
        try {
            if (openApis.some((api) => !Number.isFinite(Date.parse(api.validFrom)) || !Number.isFinite(Date.parse(api.validUntil)) || Date.parse(api.validUntil) <= Date.parse(api.validFrom))) throw new Error('API expiry must be later than its valid-from date');
            for (const api of openApis) {
                const saved = await updateEtlApi(selectedSchema, table.name, api.id, {
                    validFrom: api.validFrom, validUntil: api.validUntil, filterType: api.filterType, filterSource: api.filterSource,
                    filters: api.filters || [], sorts: api.sorts || [], search: api.search || '', partition: api.partition,
                });
                savedCount++;
                if (selection.current === target) setOpenApis((current) => current.map((item) => item.id === saved.id ? saved : item));
            }
            if (selection.current === target) setApisDirty(false);
            message.success('API access settings saved');
        } catch (error) { message.error(`${savedCount ? `${savedCount} API settings saved before the failure. ` : ''}${errorText(error)}`); } finally { endMutation(); }
    };
    const removeApi = (api: OpenApiAccess) => {
        if (!canManage || !selectedTableAvailable || apisLoading || mutationPending.current) return;
        const target = selection.current;
        Modal.confirm({ className: 'flow-surface-modal', title: 'Revoke this read API?', content: api.url, onOk: async () => {
            if (selection.current !== target || !beginMutation()) return;
            apiRequest.current++;
            try { await deleteEtlApi(selectedSchema, table.name, api.id); if (selection.current === target) { setOpenApis((current) => current.filter((item) => item.id !== api.id)); if (openApis.length === 1) setApisDirty(false); } }
            catch (error) { message.error(errorText(error)); throw error; } finally { endMutation(); }
        } });
    };
    const uploadKeywords = async (file: File) => {
        if (!canManage || keywordsSaving || mutationPending.current) return;
        if (file.size > 1024 * 1024) { message.error('YAML files must be at most 1 MiB'); return; }
        if (!beginMutation()) return;
        setKeywordsSaving(true);
        try {
            setKeywordConfig(await uploadVulnerabilityKeywords(file));
            setKeywordsError(''); message.success('YAML uploaded and activated; changes take effect within 30 seconds');
        } catch (error) { message.error(errorText(error)); } finally { if (workspaceActive.current) setKeywordsSaving(false); endMutation(); }
    };
    const activateKeywords = async (version: string) => {
        if (!canManage || keywordsSaving || mutationPending.current) return;
        if (!beginMutation()) return;
        setKeywordsSaving(true);
        try {
            setKeywordConfig(await activateVulnerabilityKeywordVersion(version));
            setKeywordsError(''); message.success('Version activated; changes take effect within 30 seconds');
        } catch (error) { message.error(errorText(error)); } finally { if (workspaceActive.current) setKeywordsSaving(false); endMutation(); }
    };
    const downloadKeywords = async (version?: string) => {
        setKeywordDownload(version || 'current');
        try {
            const blob = version ? await downloadVulnerabilityKeywordVersion(version) : await downloadCurrentVulnerabilityKeywords();
            const url = URL.createObjectURL(blob);
            const link = document.createElement('a');
            link.href = url; link.download = `satellite-vulnerability-keywords${version ? `-${version}` : ''}.yaml`; link.click();
            window.setTimeout(() => URL.revokeObjectURL(url), 1000);
        } catch (error) { message.error(errorText(error)); } finally { setKeywordDownload(undefined); }
    };
    const  [newTableName,  setNewTableName]  =  useState('');
    const  [newTableDescription,  setNewTableDescription]  =  useState('');
    const  [fields,  setFields]  =  useState([{  id:  'field-1',  name:  'record_id',  type:  'TEXT',  length:  '',  nullable:  false,  primary:  true,  comment:  'Business  record  identifier'  },  {  id:  'field-empty',  name:  '',  type:  'TEXT',  length:  '',  nullable:  true,  primary:  false,  comment:  ''  }]);
    const  [indexes,  setIndexes]  =  useState([{  id:  'idx-empty',  name:  '',  type:  'BTREE',  unique:  false,  fields:  '',  comment:  ''  }]);
    const  [foreignKeys,  setForeignKeys]  =  useState([{  id:  'fk-empty',  name:  '',  fields:  '',  refTable:  '',  refFields:  '',  onDelete:  'RESTRICT',  onUpdate:  'RESTRICT'  }]);
    const  [checks,  setChecks]  =  useState([{  id:  'chk-empty',  name:  '',  expression:  '',  comment:  ''  }]);
    const  [tableOptions,  setTableOptions]  =  useState({ comment: '' });
    const  [ddlText,  setDdlText]  =  useState('');
    const tables = serverTables.filter((item) => !closedTables.has(item.name));
    const schemaNode = schemas.find((item) => item.key === selectedSchema);
    const requestedSchema = new URLSearchParams(search).get('schema');
    const selectedSchemaAvailable = catalogLoaded && !!schemaNode && (!requestedSchema || requestedSchema === selectedSchema);
    const visibleSchemas = schemas.filter((item) => `${item.key} ${item.label} ${item.alias || ''} ${item.database || ''}`.toLowerCase().includes(schemaSearch.trim().toLowerCase()));
    const selectedTableAvailable = selectedSchemaAvailable && tablesLoadedSchema === selectedSchema && !closedTables.has(table.name) && serverTables.some((item) => item.name === table.name);
    useEffect(() => {
        setScriptOpen(pathname === '/flow/transforms' && selectedTableAvailable);
    }, [pathname, selectedSchema, table.name, selectedTableAvailable]);
    const scriptDirty = scriptLoaded && script !== savedScript;
    const closeScript = () => {
        if (mutationPending.current) return;
        if (scriptDirty) Modal.confirm({ className: 'flow-surface-modal', title: 'Discard unsaved script changes?', content: 'Your changes have not been saved as a draft.', okText: 'Discard changes', onOk: () => { setScript(savedScript); setScriptOpen(false); } });
        else setScriptOpen(false);
    };
    useEffect(() => {
        if (!scriptDirty) return;
        const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ''; };
        window.addEventListener('beforeunload', warn); return () => window.removeEventListener('beforeunload', warn);
    }, [scriptDirty]);
    const fieldCount = data?.columns.length ?? 0;
    const tableDescription = `${selectedSchema}.${table.name || 'No table selected'}`;
    const dataRows = data?.rows ?? [];
    const rowKey = (row: Record<string, unknown>, index?: number) => data?.primaryKey?.length ? JSON.stringify(data.primaryKey.map((key) => row[key])) : `${index ?? ''}:${JSON.stringify((data?.columns || []).map((key) => row[key]))}`;
    const primaryKey = (row: Record<string, unknown>) => Object.fromEntries((data?.primaryKey || []).map((key) => [key, row[key]]));
    const formatCell = (value: unknown) => value == null ? 'null' : typeof value === 'object' ? JSON.stringify(value) : String(value);
    const editCell = (key: string, value: unknown): string | null => value == null ? null : /^(jsonb?)(?:\s|$)|\[\]$/.test(data?.columnTypes?.[key]?.toLowerCase() || '') ? JSON.stringify(value) : typeof value === 'object' ? JSON.stringify(value) : String(value);
    const startRowEdit = (key: string) => {
        if (!canManage || !selectedTableAvailable || loading || mutationPending.current) return;
        if (!data?.primaryKey?.length) { message.warning('This table has no primary key and cannot be edited safely'); return; }
        const row = dataRows.find((item) => rowKey(item) === key);
        if (!row) return;
        if (data.primaryKey.some((column) => typeof row[column] === 'number' && Number.isInteger(row[column]) && !Number.isSafeInteger(row[column]))) { message.error('Primary key exceeds safe numeric precision'); return; }
        if (editingRowKey && editingRowKey !== key) { message.warning('Save or cancel the current row edit first'); return; }
        setEditingRowKey(key); setEditingDraft(Object.fromEntries(Object.entries(row).map(([column, value]) => [column, editCell(column, value)])));
    };
    const cancelRowEdit = () => { if (!mutationPending.current) { setEditingRowKey(undefined); setEditingDraft(null); } };
    const saveRowEdit = async () => {
        if (!selectedTableAvailable || !editingDraft || !data?.primaryKey?.length || !beginMutation()) return;
        const target = selection.current;
        const original = dataRows.find((item) => rowKey(item) === editingRowKey);
        try {
            if (!original) return;
            const values = Object.fromEntries(Object.entries(editingDraft).filter(([key, value]) => value !== editCell(key, original[key])).map(([key, value]) => {
                const type = data?.columnTypes?.[key]?.toLowerCase() || '';
                if (value === null) return [key, null];
                const text = String(value);
                if (/^jsonb?(?:\s|$)|\[\]$/.test(type) || original[key] !== null && typeof original[key] === 'object') {
                    let parsed: unknown;
                    try { parsed = JSON.parse(text); } catch { throw new Error(`${key} must be valid JSON`); }
                    if (/\[\]$/.test(type) && !Array.isArray(parsed)) throw new Error(`${key} must be a JSON array`);
                    return [key, parsed];
                }
                if (typeof original[key] === 'boolean' || type === 'boolean') {
                    if (text !== 'true' && text !== 'false') throw new Error(`${key} must be true or false`);
                    return [key, text === 'true'];
                }
                if (/^(smallint|integer|bigint)$/.test(type)) {
                    if (!/^[+-]?\d+$/.test(text)) throw new Error(`${key} must be an integer`);
                    const parsed = Number(text); return [key, Number.isSafeInteger(parsed) ? parsed : text];
                }
                if (typeof original[key] === 'number' || /^(numeric|decimal|real|double precision)(?:\(|$)/.test(type)) {
                    const parsed = Number(text);
                    if (!text.trim() || !Number.isFinite(parsed)) throw new Error(`${key} must be a number`);
                    return [key, /^(numeric|decimal)/.test(type) ? text : parsed];
                }
                return [key, value];
            }));
            if (Object.keys(values).length) await updateEtlRow(selectedSchema, table.name, primaryKey(original), values);
            if (selection.current === target) { setEditingRowKey(undefined); setEditingDraft(null); if (Object.keys(values).length) setRefreshVersion((value) => value + 1); }
            if (Object.keys(values).length) message.success('Row saved');
        } catch (error) { message.error(errorText(error)); } finally { endMutation(); }
    };
    const removeRow = (row: Record<string, unknown>) => {
        if (!canManage || !selectedTableAvailable || !data?.primaryKey?.length || mutationPending.current) return;
        if (data.primaryKey.some((key) => typeof row[key] === 'number' && Number.isInteger(row[key]) && !Number.isSafeInteger(row[key]))) { message.error('Primary key exceeds safe numeric precision'); return; }
        const target = selection.current;
        Modal.confirm({ className: 'flow-surface-modal', title: 'Delete this row?', content: JSON.stringify(primaryKey(row)), onOk: async () => {
            if (selection.current !== target || !beginMutation()) return;
            try { await deleteEtlRow(selectedSchema, table.name, primaryKey(row)); if (selection.current === target) setRefreshVersion((value) => value + 1); }
            catch (error) { message.error(errorText(error)); throw error; } finally { endMutation(); }
        } });
    };
    const appliedSort = appliedQuery.sorts[0];
    const toggleHeaderSort = (field: string) => {
        if (editingRowKey) { message.warning('Save or cancel the current row edit first'); return; }
        if (appliedSort?.field === field && appliedSort.direction === 'desc') { setSortField(''); setAppliedQuery((current) => ({ ...current, sorts: [...sortConditions.filter((item) => item.field)] })); }
        else {
            const direction = appliedSort?.field === field ? 'desc' : 'asc';
            setSortField(field); setSortDirection(direction);
            setAppliedQuery((current) => ({ ...current, sorts: [{ field, direction }, ...sortConditions.filter((item) => item.field)] }));
        }
        setPageNumber(1);
    };
    const dataColumns: any[] = (data?.columns || []).map((key) => ({ title: <span className="flow-col-head"><span className="flow-col-head-label">{key.toUpperCase()}</span><button type="button" className={`flow-col-sort-btn${appliedSort?.field === key ? ` is-active is-${appliedSort.direction}` : ''}`} aria-label={`Sort by ${key}`} title="Sort" onClick={(event) => { event.stopPropagation(); toggleHeaderSort(key); }}><SortDirectionIcon direction={appliedSort?.field === key ? appliedSort.direction : 'asc'} /></button></span>, dataIndex: key, key, render: (value: unknown, row: Record<string, unknown>) => editingRowKey === rowKey(row) && editingDraft ? <Input size="small" aria-label={`Edit ${key}`} disabled={mutationLoading} value={editingDraft[key] == null ? '' : String(editingDraft[key])} placeholder={editingDraft[key] === null ? 'SQL NULL' : undefined} onKeyDown={(event) => { if (event.key === 'Enter') { event.preventDefault(); void saveRowEdit(); } else if (event.key === 'Escape') { event.preventDefault(); cancelRowEdit(); } }} addonAfter={<button type="button" disabled={mutationLoading} aria-label={`Set ${key} to null`} title="Set SQL NULL" onClick={() => setEditingDraft((current) => current ? { ...current, [key]: null } : current)}>NULL</button>} onChange={(event) => setEditingDraft((current) => current ? { ...current, [key]: event.target.value } : current)} /> : formatCell(value) }));
    dataColumns.push({ title: 'ACTION', key: 'action', fixed: 'right', width: 112, render: (_: unknown, row: Record<string, unknown>) => editingRowKey === rowKey(row) ? <span className="flow-row-actions is-editing"><button type="button" aria-label="Save row" disabled={mutationLoading} onClick={saveRowEdit}><CheckOutlined /></button><button type="button" aria-label="Cancel row edit" disabled={mutationLoading} onClick={cancelRowEdit}><CloseOutlined /></button></span> : <span className="flow-row-actions flow-row-hover-actions"><button type="button" aria-label="Preview row JSON" title="Preview row JSON" onClick={() => setJsonRow(row)}><CodeOutlined /></button>{canManage && data?.primaryKey?.length ? <><button type="button" aria-label="Edit row" disabled={mutationLoading || loading} onClick={() => startRowEdit(rowKey(row))}><EditOutlined /></button><button type="button" aria-label="Delete row" disabled={mutationLoading || loading} onClick={() => removeRow(row)}><DeleteOutlined /></button></> : null}</span> });

    useEffect(() => {
        workspaceActive.current = true;
        refreshCatalog().catch((error) => { if (workspaceActive.current) setRequestError(errorText(error)); });
        return () => { workspaceActive.current = false; catalogRequest.current++; selection.current = ''; };
    }, []);
    const previousRefresh = useRef(refreshToken);
    useEffect(() => {
        if (previousRefresh.current === refreshToken) return;
        previousRefresh.current = refreshToken;
        if (mutationPending.current || editingRowKey || scriptDirty || apisDirty || draftExists) { message.warning(draftExists ? 'Save changes or close the draft tab before refreshing' : 'Save or cancel current changes before refreshing'); return; }
        setRefreshVersion((value) => value + 1);
        refreshCatalog().catch((error) => { if (workspaceActive.current) setRequestError(errorText(error)); });
    }, [refreshToken]);
    useEffect(() => {
        if (!catalogLoaded) return;
        const params = new URLSearchParams(search);
        const requested = params.get('schema');
        const schema = schemas.find((item) => item.key === requested)?.key || schemas[0]?.key || '';
        setSelectedSchema(schema); setSchemaNameDraft(schema);
        if (requested !== schema && (requested || schema)) {
            if (schema) params.set('schema', schema); else params.delete('schema');
            params.delete('table');
            navigate(`${workspacePath}${params.size ? `?${params.toString()}` : ''}`, { replace: true });
        }
    }, [search, schemas, catalogLoaded, navigate, workspacePath]);
    useEffect(() => {
        const requested = new URLSearchParams(search).get('resource');
        const resource = connections.find((item) => item.id === requested || item.database === requested)?.id || connections[0]?.id || '';
        setDatabaseName(resource); setExpandedDatabases((current) => ({ ...current, [resource]: true }));
    }, [search]);
    useEffect(() => {
        const requested = new URLSearchParams(search).get('resource');
        setDatabaseName((current) => connections.some((item) => item.id === current) ? current : connections.find((item) => item.id === requested || item.database === requested)?.id || connections[0]?.id || '');
    }, [connections]);
    useEffect(() => {
        const version = ++tableRequest.current;
        if (!selectedSchemaAvailable) {
            loadedSchema.current = '';
            setTablesLoading(false); setTablesLoadedSchema(undefined); setServerTables([]);
            setTable({ name: '', rowCount: 0, size: '', updatedAt: '' }); setData(undefined);
            return () => { tableRequest.current++; };
        }
        setTablesLoading(true); setTablesLoadedSchema(undefined); setRequestError('');
        if (loadedSchema.current !== selectedSchema) {
            loadedSchema.current = selectedSchema;
            setServerTables([]); setTable({ name: '', rowCount: 0, size: '', updatedAt: '' });
            setClosedTables(new Set()); setCreateOpen(false); setDraftExists(false); setPartition(undefined); setPageNumber(1); setEditingRowKey(undefined); setEditingDraft(null); setData(undefined); setStream(undefined); setOffsets([]); setInputTopics([]); setScript(''); setOpenApis([]);
        }
        fetchLayerTables(selectedSchema, 'current').then((items) => { if (version === tableRequest.current) { setServerTables(items); setTablesLoadedSchema(selectedSchema); } }).catch((error) => { if (version === tableRequest.current) setRequestError(errorText(error)); }).finally(() => { if (version === tableRequest.current) setTablesLoading(false); });
        return () => { tableRequest.current++; };
    }, [selectedSchema, refreshVersion, selectedSchemaAvailable]);
    useEffect(() => {
        const params = new URLSearchParams(search);
        if (tablesLoadedSchema !== selectedSchema || (params.get('schema') && params.get('schema') !== selectedSchema)) return;
        const name = params.get('table');
        const selected = params.has('table') ? serverTables.find((item) => item.name === name) : serverTables.find((item) => !closedTables.has(item.name));
        if (selected) setTable(selected);
        else {
            setTable({ name: '', rowCount: 0, size: '', updatedAt: '' });
            if (name && !tablesLoading) setRequestError(`Table ${name} was not found in ${selectedSchema}`);
        }
    }, [search, serverTables, tablesLoading, tablesLoadedSchema, selectedSchema, closedTables]);
    selection.current = `${selectedSchema}/${table.name}`;
    useEffect(() => {
        setKeywordsOpen(false);
        setEditingRowKey(undefined); setEditingDraft(null); setPartition(undefined); setPageNumber(1); setData(undefined); setPartitions([]); setScript(''); setSavedScript(''); setScriptLoaded(false); setScriptNote('Loading script…'); setOpenApis([]); setInputTopics([]); setActiveTopicIndex(0); setStream(undefined); setOffsets([]); setStreamError(''); setOffsetPartition(undefined); setOffset(undefined); setOffsetOpen(false);
        setFilterText(''); setReferenceColumns({}); setFilterRules([{ id: 'rule-1', field: '', operator: '=', value: '', active: true, join: 'and' }]); setSortField(''); setSortConditions([]); setAppliedQuery({ filters: [], sorts: [], search: '' });
        if (!selectedTableAvailable) return;
        const version = ++metadataRequest.current;
        const valid = () => version === metadataRequest.current;
        fetchTablePartitions(selectedSchema, table.name).then((items) => { if (valid()) setPartitions(items); }).catch((error) => { if (valid()) message.error(errorText(error)); });
        Promise.all([fetchLayerScript(selectedSchema, table.name), fetchHandlerCode(selectedSchema, table.name)]).then(([result, draft]) => { if (valid()) { const code = draft.code || result.code || ''; setScript(code); setSavedScript(code); setScriptLoaded(true); setScriptNote(draft.code && draft.code !== result.code ? 'Saved draft' : result.available ? result.path || 'Deployed handler' : result.reason || 'No deployed handler'); } }).catch((error) => { if (valid()) setScriptNote(errorText(error)); });
        return () => { metadataRequest.current++; };
    }, [selectedSchema, table.name, selectedTableAvailable]);
    useEffect(() => {
        if (!keywordsOpen) return;
        let active = true;
        setKeywordsLoading(true); setKeywordsError(''); setKeywordConfig(undefined);
        fetchVulnerabilityKeywords().then((config) => {
            if (active) setKeywordConfig(config);
        }).catch((error) => { if (active) setKeywordsError(errorText(error)); }).finally(() => { if (active) setKeywordsLoading(false); });
        return () => { active = false; };
    }, [keywordsOpen]);
    useEffect(() => {
        const version = ++apiRequest.current;
        setApisDirty(false);
        if (!canManage || !selectedTableAvailable) { setOpenApis([]); setApisLoading(false); return; }
        setApisLoading(true);
        fetchEtlApis(selectedSchema, table.name).then((items) => { if (version === apiRequest.current) setOpenApis(items); }).catch((error) => { if (version === apiRequest.current) message.error(errorText(error)); }).finally(() => { if (version === apiRequest.current) setApisLoading(false); });
        return () => { apiRequest.current++; };
    }, [selectedSchema, table.name, canManage, selectedTableAvailable]);
    useEffect(() => {
        const version = ++dataRequest.current;
        if (!selectedTableAvailable) { setLoading(false); return; }
        setLoading(true); setRequestError('');
        const timer = window.setTimeout(() => queryEtlTable(selectedSchema, table.name, { ...appliedQuery, limit: pageSize, offset: (pageNumber - 1) * pageSize, partition }).then((result) => { if (version === dataRequest.current) { if (!result.rows.length && pageNumber > 1) { setPageNumber(result.totalEstimated ? 1 : Math.max(1, Math.min(pageNumber - 1, Math.ceil((result.total ?? 0) / pageSize)))); } else setData(result); } }).catch((error) => { if (version === dataRequest.current) { setData(undefined); setRequestError(errorText(error)); } }).finally(() => { if (version === dataRequest.current) setLoading(false); }), 100);
        return () => { window.clearTimeout(timer); dataRequest.current++; };
    }, [selectedSchema, table.name, partition, appliedQuery, pageNumber, pageSize, refreshVersion, selectedTableAvailable]);
    useEffect(() => {
        if (!selectedTableAvailable) return;
        let active = true;
        let timer: number | undefined;
        const refresh = async () => {
            const version = ++streamRequest.current;
            try {
                const result = await fetchTableStream(selectedSchema, table.name, inputTopics[activeTopicIndex]);
                if (!active || version !== streamRequest.current) return;
                setStream(result); setOffsets(result.offsets || []); setStreamError('');
                setOffsetPartition((current) => result.offsets?.some((item) => item.partition === current) ? current : result.offsets?.[0]?.partition);
                if (result.topic) setInputTopics((current) => current.includes(result.topic!) ? current : [result.topic!]);
            } catch (error) { if (active && version === streamRequest.current) { setStream(undefined); setOffsets([]); setStreamError(errorText(error)); } }
            finally { if (active) timer = window.setTimeout(refresh, 5000); }
        };
        void refresh();
        return () => { active = false; streamRequest.current++; window.clearTimeout(timer); };
    }, [selectedSchema, table.name, inputTopics[activeTopicIndex], selectedTableAvailable]);
    const selectTable = (selected: LayerTable) => {
        if (editingRowKey) { message.warning('Save or cancel the current row edit first'); return; }
        setCreateOpen(false);
        setClosedTables((current) => { const next = new Set(current); next.delete(selected.name); return next; });
        const params = new URLSearchParams(search); params.set('schema', selectedSchema); params.set('table', selected.name);
        navigate(`${workspacePath}?${params.toString()}`, { replace: true });
    };
    const closeTable = (name: string) => {
        if (editingRowKey && table.name === name) { message.warning('Save or cancel the current row edit first'); return; }
        const remaining = tables.filter((item) => item.name !== name);
        setClosedTables((current) => new Set(current).add(name));
        if (table.name !== name || createOpen) return;
        if (remaining.length) selectTable(remaining[Math.min(tables.findIndex((item) => item.name === name), remaining.length - 1)]);
        else {
            setTable({ name: '', rowCount: 0, size: '', updatedAt: '' });
            const params = new URLSearchParams(search); params.set('table', '');
            navigate(`${workspacePath}?${params.toString()}`, { replace: true });
        }
    };
    const runDryRun = async () => {
        if (!selectedTableAvailable || !scriptLoaded || !script.trim() || validationPending.current) return;
        const target = selection.current; const code = script;
        validationPending.current = true; setScriptValidating(true);
        try {
            const result = await validateHandlerCode(selectedSchema, table.name, code);
            if (selection.current === target && scriptContent.current === code) { if (result.valid) message.success('Python syntax is valid'); else message.error(result.errors.join('; ')); }
        } catch (error) { if (selection.current === target) message.error(errorText(error)); }
        finally { validationPending.current = false; if (workspaceActive.current) setScriptValidating(false); }
    };
    const saveScript = async () => {
        if (!selectedTableAvailable || !scriptLoaded || !script.trim() || !beginMutation()) return;
        const target = selection.current; const code = script;
        try { await saveHandlerCode(selectedSchema, table.name, code); if (selection.current === target) { setSavedScript(code); setScriptNote('Saved draft'); } message.success('Draft saved; deployed worker code is unchanged'); }
        catch (error) { message.error(errorText(error)); } finally { endMutation(); }
    };
    const applyFilters = () => { if (editingRowKey) { message.warning('Save or cancel the current row edit first'); return; } setAppliedQuery({ filters: filterRules.filter((rule) => rule.field || rule.id.startsWith('group-')), sorts: [...(sortField ? [{ field: sortField, direction: sortDirection }] : []), ...sortConditions.filter((item) => item.field)], search: filterText }); setPageNumber(1); };
    const saveOffset = async () => {
        if (!selectedTableAvailable || !stream?.available || offsetPartition === undefined || offset === undefined || offset < 0 || !Number.isSafeInteger(offset) || !beginMutation()) return;
        const target = selection.current;
        try { await setTableOffset(selectedSchema, table.name, offsetPartition, offset, stream.topic); if (selection.current === target) { setOffsetOpen(false); setRefreshVersion((value) => value + 1); } message.success('Offset saved; worker restart required'); }
        catch (error) { message.error(errorText(error)); } finally { endMutation(); }
    };

    const  updateField  =  (id:  string,  key:  string,  value:  string  |  boolean)  =>  setFields((current)  =>  current.map((field)  =>  field.id  ===  id  ?  {  ...field,  [key]:  value  }  :  field));
    const  handleNameChange  =  (id:  string,  value:  string)  =>  {
        if  (!/^[a-zA-Z0-9_]*$/.test(value))  return;
        setFields((current)  =>  {
            const  idx  =  current.findIndex((f)  =>  f.id  ===  id);
            const  isLast  =  idx  ===  current.length  -  1;
            const  wasEmpty  =  !current[idx].name;
            const  next  =  current.map((f)  =>  f.id  ===  id  ?  {  ...f,  name:  value  }  :  f);
            if  (isLast  &&  wasEmpty  &&  value)  next.push({  id:  randomId(),  name:  '',  type:  'TEXT',  length:  '',  nullable:  true,  primary:  false,  comment:  ''  });
            return  next;
        });
    };
    const  handleLengthChange  =  (id:  string,  value:  string)  =>  {  if  (/^\d*$/.test(value))  updateField(id,  'length',  value);  };
    const  removeField  =  (id:  string)  =>  setFields((current)  =>  {  const  filtered  =  current.filter((f)  =>  f.id  !==  id);  if  (!filtered.length  ||  filtered[filtered.length  -  1]?.name)  filtered.push({  id:  randomId(),  name:  '',  type:  'TEXT',  length:  '',  nullable:  true,  primary:  false,  comment:  ''  });  return  filtered;  });
    const  moveField  =  (index:  number,  direction:  -1  |  1)  =>  setFields((current)  =>  {  const  target  =  index  +  direction;  if  (target  <  0  ||  target  >=  current.length  -  1)  return  current;  const  next  =  [...current];  [next[index],  next[target]]  =  [next[target],  next[index]];  return  next;  });
    const  updateIndex  =  (id:  string,  changes:  Partial<(typeof  indexes)[number]>)  =>  setIndexes((current)  =>  {
        const  next  =  current.map((row)  =>  row.id  ===  id  ?  {  ...row,  ...changes  }  :  row);
        if  (next[next.length  -  1]?.name)  next.push({  id:  randomId(),  name:  '',  type:  'BTREE',  unique:  false,  fields:  '',  comment:  ''  });
        return  next;
    });
    const  removeIndex  =  (id:  string)  =>  setIndexes((current)  =>  {
        const  next  =  current.filter((row)  =>  row.id  !==  id);
        if  (!next.length  ||  next[next.length  -  1]?.name)  next.push({  id:  randomId(),  name:  '',  type:  'BTREE',  unique:  false,  fields:  '',  comment:  ''  });
        return  next;
    });
    const  updateForeignKey  =  (id:  string,  changes:  Partial<(typeof  foreignKeys)[number]>)  =>  setForeignKeys((current)  =>  {
        const  next  =  current.map((row)  =>  row.id  ===  id  ?  {  ...row,  ...changes  }  :  row);
        if  (next[next.length  -  1]?.name)  next.push({  id:  randomId(),  name:  '',  fields:  '',  refTable:  '',  refFields:  '',  onDelete:  'RESTRICT',  onUpdate:  'RESTRICT'  });
        return  next;
    });
    const  removeForeignKey  =  (id:  string)  =>  setForeignKeys((current)  =>  {
        const  next  =  current.filter((row)  =>  row.id  !==  id);
        if  (!next.length  ||  next[next.length  -  1]?.name)  next.push({  id:  randomId(),  name:  '',  fields:  '',  refTable:  '',  refFields:  '',  onDelete:  'RESTRICT',  onUpdate:  'RESTRICT'  });
        return  next;
    });
    const  updateCheck  =  (id:  string,  changes:  Partial<(typeof  checks)[number]>)  =>  setChecks((current)  =>  {
        const  next  =  current.map((row)  =>  row.id  ===  id  ?  {  ...row,  ...changes  }  :  row);
        if  (next[next.length  -  1]?.name)  next.push({  id:  randomId(),  name:  '',  expression:  '',  comment:  ''  });
        return  next;
    });
    const  removeCheck  =  (id:  string)  =>  setChecks((current)  =>  {
        const  next  =  current.filter((row)  =>  row.id  !==  id);
        if  (!next.length  ||  next[next.length  -  1]?.name)  next.push({  id:  randomId(),  name:  '',  expression:  '',  comment:  ''  });
        return  next;
    });
    const startNewTable = () => {
        if (!canManage || !selectedSchemaAvailable || mutationPending.current) return;
        if (!draftExists) {
            setNewTableName(''); setNewTableDescription(''); setTableOptions({ comment: '' }); setDdlText(''); setReferenceColumns({});
            setFields([{ id: 'field-1', name: 'record_id', type: 'TEXT', length: '', nullable: false, primary: true, comment: 'Business record identifier' }, { id: 'field-empty', name: '', type: 'TEXT', length: '', nullable: true, primary: false, comment: '' }]);
            setIndexes([{ id: 'idx-empty', name: '', type: 'BTREE', unique: false, fields: '', comment: '' }]);
            setForeignKeys([{ id: 'fk-empty', name: '', fields: '', refTable: '', refFields: '', onDelete: 'RESTRICT', onUpdate: 'RESTRICT' }]);
            setChecks([{ id: 'chk-empty', name: '', expression: '', comment: '' }]);
        }
        setDraftExists(true); setCreateOpen(true);
    };
    const createTable = async () => {
        if (!selectedSchemaAvailable || !beginMutation()) return;
        const schema = selectedSchema;
        const name = newTableName.trim();
        try {
            if (!/^[a-z_][a-z0-9_]{0,62}$/.test(name) || !fields.some((field) => field.name.trim())) throw new Error('Table name and fields must use lowercase PostgreSQL identifiers');
            const defined = fields.filter((field) => field.name.trim());
            if (defined.some((field) => !/^[a-z_][a-z0-9_]{0,62}$/.test(field.name)) || new Set(defined.map((field) => field.name)).size !== defined.length) throw new Error('Field names must be valid and unique');
            const created = await createEtlTable(schema, { name, description: newTableDescription || tableOptions.comment,
                fields: defined, indexes: indexes.filter((item) => item.name.trim()), foreignKeys: foreignKeys.filter((item) => item.name.trim()), checks: checks.filter((item) => item.name.trim()), options: tableOptions });
            if (workspaceActive.current && loadedSchema.current === schema) {
                setDraftExists(false); setCreateOpen(false); setNewTableName(''); setNewTableDescription(''); setRefreshVersion((value) => value + 1);
                setServerTables((current) => [...current.filter((item) => item.name !== created.name), created]); selectTable(created);
            }
            await refreshCatalog(); message.success('Table created');
        } catch (error) { message.error(errorText(error)); } finally { endMutation(); }
    };
    const copySql = async () => {
        const text = ddlText || ddlPreview;
        try {
            if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(text);
            else {
                const input = document.createElement('textarea'); input.value = text; input.style.position = 'fixed'; input.style.opacity = '0'; document.body.append(input); input.select();
                try { if (!document.execCommand('copy')) throw new Error('Clipboard is unavailable'); } finally { input.remove(); }
            }
            message.success('SQL copied');
        } catch { message.error('SQL could not be copied. Select the preview text and copy it manually.'); }
    };
    const definedFields = fields.filter((field) => field.name.trim());
    const primaryFields = definedFields.filter((field) => field.primary);
    const ddlPreview = `CREATE TABLE "${selectedSchema}"."${newTableName || 'new_table'}" (\n${[...definedFields.map((field) => `    "${field.name}" ${field.type}${field.type === 'VARCHAR' && field.length ? `(${field.length})` : ''}${field.nullable ? '' : ' NOT NULL'}`), ...(primaryFields.length ? [`    PRIMARY KEY (${primaryFields.map((field) => `"${field.name}"`).join(', ')})`] : [])].join(',\n')}\n);`;
    const  databaseOptions  =  [
        {  key:  'postgres'  as  const,  label:  'Postgres',  icon:  <PostgresIcon  className="flow-database-logo  flow-postgres-logo"  />  },
        {  key:  'mongodb'  as  const,  label:  'MongoDB',  icon:  <MongoDbIcon  className="flow-database-logo  flow-mongodb-logo"  />  },
        {  key:  'elasticsearch'  as  const,  label:  'Elasticsearch',  icon:  <ElasticsearchIcon  className="flow-database-logo  flow-elasticsearch-logo"  />  },
        {  key:  's3'  as  const,  label:  'S3',  icon:  <S3Icon  className="flow-database-logo  flow-s3-logo"  />  },
    ];
    const  visibleDatabaseOptions  =  databaseOptions.filter(({  label  })  =>  label.toLowerCase().includes(databaseSearch.trim().toLowerCase()));
    const  selectedDatabaseOption  =  databaseOptions.find(({  key  })  =>  key  ===  connectorDatabase)  ??  databaseOptions[0];
    const  changeConnectorStep  =  (step:  'type'  |  'config',  database  =  connectorDatabase)  =>  {
        const  update  =  ()  =>  {  setConnectorDatabase(database);  setConnectorSelectionActive(step  ===  'config');  setConnectorSelectedSchema('');  if  (step  ===  'config')  setDatabaseName('');  setConnectorStep(step);  };
        const  documentWithTransitions  =  document  as  Document  &  {  startViewTransition?:  (callback:  ()  =>  void)  =>  void  };
        if  (documentWithTransitions.startViewTransition)  documentWithTransitions.startViewTransition(update);
        else  update();
    };
    const removeResource = () => {
        const connection = connections.find((item) => item.id === databaseName);
        if (!canManage || !connection || mutationPending.current) return;
        Modal.confirm({ className: 'flow-surface-modal', title: 'Delete this connection?', content: connection.name, onOk: async () => {
            if (!beginMutation()) return;
            try { await deleteEtlConnection(connection.id); if (workspaceActive.current) { setDatabaseName(''); setConnectorSelectedSchema(''); } await refreshCatalog(); }
            catch (error) { message.error(errorText(error)); throw error; } finally { endMutation(); }
        } });
    };
    const renameSchema = async () => {
        if (!schemaEditing || !connectorSelectedSchema || !schemaNameDraft.trim() || !beginMutation()) return;
        const previous = connectorSelectedSchema; const name = schemaNameDraft.trim(); const target = selection.current;
        try {
            await updateEtlSchema(previous, { name, alias: schemaAliasDraft });
            if (!workspaceActive.current) return;
            setSchemas((current) => current.map((item) => item.key === previous ? { ...item, key: name, schemaName: name, alias: schemaAliasDraft } : item));
            setSchemaAliases((current) => ({ ...current, [name]: schemaAliasDraft || current[previous] || name }));
            setConnectorSelectedSchema(name); setSchemaEditing(false);
            if (selectedSchema === previous && selection.current === target) { const params = new URLSearchParams(search); params.set('schema', name); navigate(`${workspacePath}?${params}`, { replace: true }); }
            await refreshCatalog(); message.success('Schema saved');
        } catch (error) { message.error(errorText(error)); } finally { endMutation(); }
    };
    const deleteSchema = () => {
        if (!canManage || !connectorSelectedSchema || mutationPending.current) return;
        const schema = connectorSelectedSchema;
        Modal.confirm({ className: 'flow-surface-modal', title: 'Delete this empty schema?', content: schema, onOk: async () => {
            if (!beginMutation()) return;
            try { await deleteEtlSchema(schema); if (workspaceActive.current) setConnectorSelectedSchema(''); await refreshCatalog(); if (workspaceActive.current) setRefreshVersion((value) => value + 1); }
            catch (error) { message.error(errorText(error)); throw error; } finally { endMutation(); }
        } });
    };
    const openCreateResource = () => { if (!canManage || mutationPending.current) return; setConnectorSelectedSchema(''); setSchemaNameDraft(''); setResourceConnectionName(''); setResourceDatabase(connections[0]?.database || schemas.find((item) => item.database)?.database || ''); setResourceSchema(''); setResourceSchemas([]); setResourcePanel('create'); };
    const openEditResource = () => {
        const connection = connections.find((item) => item.id === databaseName); if (!canManage || !connection || mutationPending.current) return;
        setConnectorSelectedSchema(''); setSchemaNameDraft(''); setResourceConnectionName(connection.name); setResourceDatabase(connection.database); setResourceSchema(''); setResourceSchemas(connection.schemas); setResourcePanel('edit');
    };
    const saveResource = async () => {
        if (!beginMutation()) return;
        const database = resourceDatabase.trim();
        try {
            if (!database || !resourceConnectionName.trim()) throw new Error('Connection and database names are required');
            const payload = { name: resourceConnectionName.trim(), database, schemas: [...new Set([...resourceSchemas, resourceSchema.trim()].filter(Boolean))] };
            const connection = connections.find((item) => item.id === databaseName);
            const saved = resourcePanel === 'edit' && connection ? await updateEtlConnection(connection.id, payload) : await createEtlConnection(payload);
            await refreshCatalog();
            if (workspaceActive.current) { setDatabaseName(saved.id); setExpandedDatabases((current) => ({ ...current, [saved.id]: true })); setResourcePanel('none'); }
            message.success('Connection saved');
        } catch (error) { message.error(errorText(error)); } finally { endMutation(); }
    };
    useEffect(() => { if (connectorSelectedSchema) { setSchemaEditing(false); setSchemaAliasDraft(schemaAliases[connectorSelectedSchema] || ''); } }, [connectorSelectedSchema]);

    return  <div  className="flow-layers  flow-layers--tagged">
        <div  className="flow-layer-layout">
            <aside className="flow-layer-tree flow-ontology-schema-sidebar">
                <div className="flow-tree-label flow-tree-label--actions"><span>Schemas</span>{canManage && <button type="button" aria-label="Manage connections" title="Manage connections" onClick={() => { setConnectorStep('type'); setConnectorSelectionActive(false); setConnectorSelectedSchema(''); setDatabaseSearch(''); setCollectionOpen(true); }}><PlusOutlined /></button>}</div>
                <Input className="flow-ontology-schema-search" aria-label="Search schemas" placeholder="Search schemas" prefix={<SearchOutlined />} value={schemaSearch} onChange={(event) => setSchemaSearch(event.target.value)} allowClear />
                <div className="flow-ontology-schema-list">
                    {visibleSchemas.map((item) => <button type="button" className={`flow-connector-tree-item flow-ontology-schema-item ${selectedSchema === item.key ? 'is-active' : ''}`} key={item.key} style={{ '--schema-color': schemaColor(item.key) } as React.CSSProperties} aria-current={selectedSchema === item.key ? 'page' : undefined} onClick={() => navigate(`${workspacePath}?schema=${encodeURIComponent(item.key)}`)}><i className="flow-schema-color-dot" aria-hidden="true" /><span className="flow-connector-tree-name"><strong>{item.alias || item.label}</strong><small>{item.key}</small></span><em>{item.tables ?? 0}</em></button>)}
                    {!visibleSchemas.length && <p className="flow-schema-search-empty">{!catalogLoaded && !requestError ? 'Loading schemas…' : schemas.length ? 'No matching schemas' : 'No schemas available'}</p>}
                </div>
                <div className="flow-schema-sidebar-summary">{schemas.length} schema resources</div>
            </aside>
            <section  className="flow-layer-main">
                {(loading || tablesLoading) && <Spin size="small" />}
                {requestError && <div role="alert" style={{ padding: 12, color: '#ff7875' }}>{requestError} <Button size="small" onClick={() => { setRefreshVersion((value) => value + 1); refreshCatalog().catch((error) => setRequestError(errorText(error))); }}>Retry</Button></div>}
                <nav className="flow-asset-breadcrumb" aria-label="Breadcrumb"><button type="button" onClick={() => navigate('/flow')}>Flow</button><RightOutlined /><span>{pathname === '/flow/transforms' ? 'Transformations' : 'Schemas'}</span>{selectedSchema && <><RightOutlined /><strong>{selectedSchema}</strong></>}</nav>
                <div className="flow-layer-overview" style={{ '--schema-color': selectedSchema ? schemaColor(selectedSchema) : undefined } as React.CSSProperties}>
                    <div  className="flow-layer-glyph"><PostgresIcon  className="flow-layer-glyph-svg"  /></div>
                    <h3>{schemaAliases[selectedSchema] || selectedSchema || 'No schema selected'}<em>{selectedSchema}{schemaNode?.database ? ` · ${schemaNode.database}` : ''}</em></h3>
                    <div  className="flow-layer-overview-stats">
                        <span><b>{schemaNode?.tables ?? serverTables.length}</b> tables</span>
                        <i  />
                        <span><b>{schemaNode?.metricsAvailable ? (schemaNode.rateIn ?? 0).toLocaleString('en-US') : '—'}</b>/s in</span>
                        <i  />
                        <span><b>{schemaNode?.metricsAvailable ? (schemaNode.rateOut ?? 0).toLocaleString('en-US') : '—'}</b>/s out</span>
                        <i  />
                        <span><b>{schemaNode?.metricsAvailable ? schemaNode.lag : '—'}</b>ms lag</span>
                    </div>
                    <div  className="flow-layer-overview-health">
                        <i className={`flow-dot ${schemaNode?.status === 'running' ? 'flow-dot--green' : ''}`} /><span>{schemaNode?.status === 'running' ? 'Schema accessible' : schemaNode?.status || 'Unavailable'}</span>
                    </div>
                </div>
                <div  className="flow-table-switcher">
                <Dropdown  overlayClassName="flow-menu-popup flow-table-tab-dropdown"  trigger={['click']}  dropdownRender={()  =>  (
                    <div  className="flow-tab-search-panel">
                        <div  className="flow-tab-search-input-wrap">
                            <SearchOutlined  className="flow-tab-search-icon"  />
                            <input  className="flow-tab-search-input"  autoFocus  placeholder="Search  tables..."  value={tableSearch}  onChange={(e)  =>  setTableSearch(e.target.value)}  />
                        </div>
                        <div className="flow-tab-search-section-label">All tables</div>
                        <div  className="flow-tab-search-list">
                            {serverTables.filter((item)  =>  item.name.toLowerCase().includes(tableSearch.toLowerCase())).map((item)  =>  (
                                <button  key={item.name}  className={`flow-tab-search-item  ${table.name  ===  item.name  ?  'is-active'  :  ''}`}  onClick={()  =>  {  selectTable(item);  setTableSearch('');  }}>
                                    <span  className="flow-tab-search-icon-wrap"><TemplateGlyph  kind={inferSiteKind(item.dataType || '', item.name)}  source={item.name}  /></span>
                                    <span  className="flow-tab-search-name">{item.name}</span>
                                    <span  className="flow-tab-search-meta">{table.name  ===  item.name  ?  'current'  :  `${item.rowCount.toLocaleString('en-US')}  rows`}</span>
                                </button>
                            ))}
                            {serverTables.filter((item)  =>  item.name.toLowerCase().includes(tableSearch.toLowerCase())).length  ===  0  &&  <div  className="flow-tab-search-empty">No  tables  found</div>}
                        </div>
                    </div>
                )}>
                    <button  type="button"  className="flow-table-tab-menu"  aria-label="Select  open  table"><ArrowDownOutlined  /></button>
                </Dropdown>
                <div  className="flow-table-tabs"  ref={tableTabsRef}  role="tablist"  aria-label={`${selectedSchema}  tables`}>
                    {tables.map((item)  =>  {  const  active  =  !createOpen  &&  table.name  ===  item.name;  return  <button  type="button"  role="tab"  aria-selected={active}  className={active  ?  'is-active'  :  ''}  key={item.name}  title={item.name} onKeyDown={(event) => { if (event.key === 'Delete') { event.preventDefault(); closeTable(item.name); } }} onClick={() => selectTable(item)}><span  className="flow-tab-favicon"><TemplateGlyph  kind={inferSiteKind(item.dataType || '', item.name)}  source={item.name}  /></span><span  className="flow-tab-label">{item.name}</span><span  className="flow-tab-close"  title={`Close ${item.name} (Delete)`} onClick={(event) => { event.stopPropagation(); closeTable(item.name); }}>&times;</span></button>;  })}
                    {draftExists  &&  <button  type="button"  role="tab"  aria-selected={createOpen}  className={createOpen  ?  'is-active  is-draft'  :  'is-draft'}  onClick={()  =>  setCreateOpen(true)}><span  className="flow-tab-favicon"><EditOutlined  /></span><span  className="flow-tab-label">*  Untitled</span><span  className="flow-tab-close"  onClick={(e)  =>  {  e.stopPropagation();  setDraftExists(false);  setCreateOpen(false);  }}>&times;</span></button>}
                    {canManage && <button  type="button"  className="flow-table-add-tag"  aria-label="Create  table"  title="Create  table"  disabled={mutationLoading || !selectedSchemaAvailable} onClick={startNewTable}><PlusOutlined  /></button>}
                </div>
                </div>
                {createOpen  ?  <div  className="flow-inspector  flow-inline-designer-v2">
                    <div  className="flow-table-header">
                        <div  className="flow-table-header-left">
                            <h2>*  Untitled  <span  className="flow-td-schema-badge">{selectedSchema}</span></h2>
                        </div>
                        <div  className="flow-table-header-right">
                            <Button  size="small"  onClick={()  =>  setCreateOpen(false)}>Cancel</Button>
                            <Button  size="small"  type="primary"  loading={mutationLoading} onClick={createTable}>Save  table</Button>
                        </div>
                    </div>
                    <div  className="flow-inspector-body">
                        <div  className="flow-td-identity">
                            <div  className="flow-td-field"><label>Table  name</label><Input  value={newTableName}  onChange={(event)  =>  setNewTableName(event.target.value)}  placeholder="e.g.  rds_news"  /></div>
                            <div  className="flow-td-field"><label>Description</label><Input  value={newTableDescription}  onChange={(event)  =>  setNewTableDescription(event.target.value)}  placeholder="Brief  description  of  the  table"  /></div>
                        </div>
                        <Tabs  className="flow-td-tabs"  size="small"  items={[
                            {  key:  'fields',  label:  'Fields',  children:  <Table  className="flow-td-fields-table"  size="small"  pagination={false}  rowKey="id"  dataSource={fields}  columns={[
                                {  title:  '#',  width:  36,  render:  (_,  __,  index)  =>  <span  className="flow-td-row-idx">{index  +  1}</span>  },
                                {  title:  'Name',  dataIndex:  'name',  render:  (value,  row)  =>  <Input  size="small"  value={value}  onChange={(event)  =>  handleNameChange(row.id,  event.target.value)}  />  },
                                {  title:  'Type',  dataIndex:  'type',  width:  130,  render:  (value,  row)  =>  <Select popupClassName="flow-select-popup"  size="small"  value={value}  onChange={(next)  =>  updateField(row.id,  'type',  next)}  popupMatchSelectWidth={false}  options={['TEXT','VARCHAR','BIGINT','INTEGER','BOOLEAN','TIMESTAMPTZ','JSONB'].map((item)  =>  ({  value:  item,  label:  item  }))}  />  },
                                {  title:  'Length',  dataIndex:  'length',  width:  80,  render:  (value,  row)  =>  <InputNumber  size="small"  min={1} disabled={row.type !== 'VARCHAR'} value={value ? Number(value) : undefined}  onChange={(val)  =>  updateField(row.id,  'length',  val  !=  null  ?  String(val)  :  '')}  controls  />  },
                                {  title:  'Not  null',  dataIndex:  'nullable',  width:  70,  align:  'center',  render:  (value,  row)  =>  <FilterCheckbox  checked={!value}  onChange={(event)  =>  updateField(row.id,  'nullable',  !event.target.checked)}  />  },
                                {  title:  'PK',  dataIndex:  'primary',  width:  50,  align:  'center',  render:  (value,  row)  =>  <FilterCheckbox  checked={value}  onChange={(event)  =>  updateField(row.id,  'primary',  event.target.checked)}  />  },
                                {  title:  'Comment',  dataIndex:  'comment',  render:  (value,  row)  =>  <Input  size="small"  value={value}  onChange={(event)  =>  updateField(row.id,  'comment',  event.target.value)}  />  },
                                {  title:  '',  width:  60,  render:  (_,  row,  index)  =>  <span  className="flow-field-actions"><button  onClick={()  =>  moveField(index,  -1)}>↑</button><button  onClick={()  =>  moveField(index,  1)}>↓</button><button  onClick={()  =>  removeField(row.id)}>×</button></span>  },
                            ]}  />  },
                            {  key:  'indexes',  label:  'Indexes',  children:  <div>
                                <Table  className="flow-td-fields-table"  size="small"  pagination={false}  rowKey="id"  dataSource={indexes}  columns={[
                                    {  title:  '#',  width:  36,  render:  (_,  __,  i)  =>  <span  className="flow-td-row-idx">{i  +  1}</span>  },
                                    {  title:  'Name',  dataIndex:  'name',  render:  (v,  row)  =>  <Input  size="small"  value={v}  onChange={(e)  =>  updateIndex(row.id,  {  name:  e.target.value  })}  />  },
                                    {  title:  'Type',  dataIndex:  'type',  width:  100,  render:  (v,  row)  =>  <Select popupClassName="flow-select-popup"  size="small"  value={v}  onChange={(n)  =>  updateIndex(row.id,  {  type:  n  })}  popupMatchSelectWidth={false}  options={['BTREE','HASH','GIN','GIST','BRIN'].map((o)  =>  ({  value:  o,  label:  o  }))}  />  },
                                    {  title:  'Unique',  dataIndex:  'unique',  width:  60,  align:  'center',  render:  (v,  row)  =>  <FilterCheckbox  checked={v}  onChange={(e)  =>  updateIndex(row.id,  {  unique:  e.target.checked  })}  />  },
                                    {  title:  'Fields',  dataIndex:  'fields',  width:  180,  render:  (v,  row)  =>  <Select popupClassName="flow-select-popup"  className="flow-fields-select"  size="small"  mode="multiple"  value={v  ?  v.split(',').filter(Boolean)  :  []}  onChange={(vals)  =>  updateIndex(row.id,  {  fields:  vals.join(',')  })}  popupMatchSelectWidth={false}  options={fields.filter((f)  =>  f.name).map((f)  =>  ({  value:  f.name,  label:  f.name  }))}  />  },
                                    {  title:  'Comment',  dataIndex:  'comment',  width:  140,  render:  (v,  row)  =>  <Input  size="small"  value={v}  onChange={(e)  =>  updateIndex(row.id,  {  comment:  e.target.value  })}  />  },
                                    {  title:  '',  width:  40,  render:  (_,  row)  =>  <span  className="flow-field-actions"><button  onClick={()  =>  removeIndex(row.id)}>×</button></span>  },
                                ]}  />
                            </div>  },
                            {  key:  'foreign',  label:  'Foreign  keys',  children:  <div>
                                <Table  className="flow-td-fields-table"  size="small"  pagination={false}  rowKey="id"  dataSource={foreignKeys}  columns={[
                                    {  title:  '#',  width:  36,  render:  (_,  __,  i)  =>  <span  className="flow-td-row-idx">{i  +  1}</span>  },
                                    {  title:  'Name',  dataIndex:  'name',  render:  (v,  row)  =>  <Input  size="small"  value={v}  onChange={(e)  =>  updateForeignKey(row.id,  {  name:  e.target.value  })}  />  },
                                    {  title:  'Fields',  dataIndex:  'fields',  width:  180,  render:  (v,  row)  =>  <Select popupClassName="flow-select-popup"  className="flow-fields-select"  size="small"  mode="multiple"  value={v  ?  v.split(',').filter(Boolean)  :  []}  onChange={(vals)  =>  updateForeignKey(row.id,  {  fields:  vals.join(',')  })}  popupMatchSelectWidth={false}  options={fields.filter((f)  =>  f.name).map((f)  =>  ({  value:  f.name,  label:  f.name  }))}  />  },
                                    {  title:  'Ref.  table',  dataIndex:  'refTable',  width:  130,  render:  (v,  row)  =>  <Select popupClassName="flow-select-popup"  size="small"  value={v}  onChange={(n) => { updateForeignKey(row.id, { refTable: n, refFields: '' }); fetchTableData(selectedSchema, n, 1).then((result) => { if (workspaceActive.current && loadedSchema.current === selectedSchema) setReferenceColumns((current) => ({ ...current, [n]: result.columns })); }).catch((error) => message.error(errorText(error))); }}  popupMatchSelectWidth={false}  showSearch  options={tables.map((t)  =>  ({  value:  t.name,  label:  t.name  }))}  />  },
                                    {  title:  'Ref.  fields',  dataIndex:  'refFields',  width:  180,  render:  (v,  row)  =>  <Select popupClassName="flow-select-popup"  className="flow-fields-select"  size="small"  mode="multiple"  value={v  ?  v.split(',').filter(Boolean)  :  []}  onChange={(vals)  =>  updateForeignKey(row.id,  {  refFields:  vals.join(',')  })}  popupMatchSelectWidth={false}  disabled={!row.refTable} options={(referenceColumns[row.refTable] || []).map((name) => ({ value: name, label: name }))}  />  },
                                    {  title:  'On  Delete',  dataIndex:  'onDelete',  width:  100,  render:  (v,  row)  =>  <Select popupClassName="flow-select-popup"  size="small"  value={v}  onChange={(n)  =>  updateForeignKey(row.id,  {  onDelete:  n  })}  popupMatchSelectWidth={false}  options={['RESTRICT','CASCADE','SET  NULL','NO  ACTION','SET  DEFAULT'].map((o)  =>  ({  value:  o,  label:  o  }))}  />  },
                                    {  title:  'On  Update',  dataIndex:  'onUpdate',  width:  100,  render:  (v,  row)  =>  <Select popupClassName="flow-select-popup"  size="small"  value={v}  onChange={(n)  =>  updateForeignKey(row.id,  {  onUpdate:  n  })}  popupMatchSelectWidth={false}  options={['RESTRICT','CASCADE','SET  NULL','NO  ACTION','SET  DEFAULT'].map((o)  =>  ({  value:  o,  label:  o  }))}  />  },
                                    {  title:  '',  width:  40,  render:  (_,  row)  =>  <span  className="flow-field-actions"><button  onClick={()  =>  removeForeignKey(row.id)}>×</button></span>  },
                                ]}  />
                            </div>  },
                            {  key:  'checks',  label:  'Checks',  children:  <div>
                                <Table  className="flow-td-fields-table"  size="small"  pagination={false}  rowKey="id"  dataSource={checks}  columns={[
                                    {  title:  '#',  width:  36,  render:  (_,  __,  i)  =>  <span  className="flow-td-row-idx">{i  +  1}</span>  },
                                    {  title:  'Name',  dataIndex:  'name',  width:  150,  render:  (v,  row)  =>  <Input  size="small"  value={v}  onChange={(e)  =>  updateCheck(row.id,  {  name:  e.target.value  })}  />  },
                                    {  title:  'Expression',  dataIndex:  'expression',  render:  (v,  row)  =>  <Input  size="small"  value={v}  onChange={(e)  =>  updateCheck(row.id,  {  expression:  e.target.value  })}  style={{  fontFamily:  'ui-monospace'  }}  />  },
                                    {  title:  'Comment',  dataIndex:  'comment',  width:  140,  render:  (v,  row)  =>  <Input  size="small"  value={v}  onChange={(e)  =>  updateCheck(row.id,  {  comment:  e.target.value  })}  />  },
                                    {  title:  '',  width:  40,  render:  (_,  row)  =>  <span  className="flow-field-actions"><button  onClick={()  =>  removeCheck(row.id)}>×</button></span>  },
                                ]}  />
                            </div>  },
                            { key: 'options', label: 'Options', children: <div className="flow-td-options"><label>PostgreSQL table comment<Input value={tableOptions.comment} onChange={(event) => setTableOptions({ comment: event.target.value })} /></label></div> },
                            {  key:  'sql',  label:  'SQL',  children:  <div  className="flow-td-sql-wrap">
                                <div  className="flow-td-sql-header">
                                    <span>Field definition preview · constraints are applied on Save</span>
                                    <div  className="flow-td-sql-actions">
                                        <Button  size="small"  onClick={()  =>  {  setDdlText(ddlPreview);  message.success('Regenerated  from  table  definition');  }}>Generate</Button>
                                        <Button  size="small"  onClick={copySql}>Copy</Button>
                                    </div>
                                </div>
                                <SqlPreview  sql={ddlText  ||  ddlPreview}  />
                            </div>  },
                        ]}  />
                    </div>
                </div> : !table.name ? <div className="flow-workspace-empty" role="status"><DatabaseOutlined /><h3>{tablesLoading || !catalogLoaded && !requestError ? 'Loading schema resources…' : selectedSchema ? serverTables.length ? 'No table selected' : 'No tables in this schema' : 'No schema selected'}</h3><p>{schemaNode?.status === 'stopped' ? 'This schema is not available in the connected database.' : serverTables.length ? 'Use the table menu to open or reopen a table.' : 'Select a schema to explore its tables and data.'}</p></div> : <div className="flow-inspector">
                    <div  className="flow-table-header">
                        <div  className="flow-table-header-left">
                            <h2>{table.name}</h2>
                        </div>
                        <div  className="flow-stream-pipeline">
                            <div  className="flow-pipeline-node  flow-pipeline-node--input">
                                <div  className="flow-pipeline-line-row">
                                    <DownloadOutlined  className="flow-pipeline-icon  flow-pipeline-icon--in"  />
                                    <strong>{inputTopics[activeTopicIndex]  ||  'Not  configured'}</strong>
                                    <em title={stream?.throughputReason}>· {stream?.throughput == null ? 'Unavailable' : `${stream.throughput}/s`}</em>
                                    <code>[{offsets.map((item)  =>  `p${item.partition}:  ${item.offset.toLocaleString('en-US')}`).join('    ·    ')}]</code>
                                </div>
                            </div>
                            <div  className="flow-pipeline-node">
                                <div  className="flow-pipeline-line-row">
                                    <UploadOutlined  className="flow-pipeline-icon  flow-pipeline-icon--out"  />
                                    <strong>{table.name}</strong>
                                    <em>· {schemaNode?.metricsAvailable ? `${schemaNode.rateOut}/s` : 'Rate unavailable'}</em>
                                </div>
                            </div>
                        </div>
                        <div  className="flow-table-header-right">
                            <div className="flow-health-badge" title={stream?.reason || streamError}><i className={`flow-dot ${stream?.available || schemaNode?.status === 'running' ? 'flow-dot--green' : ''}`} /><span>{stream?.available ? 'Stream available' : schemaNode?.status === 'running' ? 'Schema accessible' : 'Unavailable'}</span></div>
                            {selectedSchema === 'ts_task' && table.name === 'task_vulnerability' && <Button size="small" onClick={() => setKeywordsOpen(true)}>Satellite extraction</Button>}
                            {canManage && <Button size="small" disabled={!stream?.available || !offsets.length} onClick={() => { setOffsetPartition(offsets[0]?.partition); setOffset(offsets[0]?.offset); setTopicDraft(stream?.topic || ''); setOffsetOpen(true); }}>Adjust offset</Button>}
                        </div>
                    </div>
                    <p className="flow-table-desc">{tableDescription}{streamError ? ` · ${streamError}` : !stream?.available ? ` · ${stream?.reason || 'Stream unavailable'}` : ''}</p>
                    <div  className="flow-inspector-body">
                        <div  className="flow-navicat-toolbar">
                                    <button type="button" className={`flow-tool ${filterOpen ? 'is-active' : ''}`} onClick={() => activateTool('filter')}>{canManage ? 'Filter & API' : 'Filter'}</button>
                                    <button type="button" className={`flow-tool ${scriptOpen ? 'is-active' : ''}`} onClick={() => activateTool('script')}>Layer script</button>
                            </div>
                            {filterOpen  &&  <div  className="flow-filter-card">
                                <Input.Search size="small" placeholder="Search all columns" value={filterText} onChange={(event) => setFilterText(event.target.value)} onSearch={applyFilters} /><div  className="flow-filter-rules">{filterRules.map((rule,  index)  =>  <div  className={['flow-filter-rule', rule.id.startsWith('group-open-') ? 'is-group-open' : '', rule.id.startsWith('group-close-') ? 'is-group-close' : '', rule.id.startsWith('group-filter-') ? 'is-nested' : '', rule.id.includes('nested-') ? 'is-group-nested' : '', `is-group-depth-${filterNestingDepth(rule.id)}`, filterMoving?.id === rule.id ? `is-moving is-moving-${filterMoving.direction}` : filterMoving?.peerId === rule.id ? `is-moving is-moving-${filterMoving.direction === 'up' ? 'down' : 'up'}` : '', rule.active === false ? 'is-inactive' : ''].filter(Boolean).join(' ')}  style={{  '--filter-depth':  filterNestingDepth(rule.id)  }  as  React.CSSProperties}  key={rule.id}><span  className="flow-filter-row-check">{index  ===  0  ?  <FilterCheckbox  checked={rule.active  !==  false}  onChange={(event)  =>  setFilterRules((rows)  =>  rows.map((item,  itemIndex)  =>  rule.id.startsWith('group-open-')  ?  (()  =>  {  const  ruleIndex  =  rows.findIndex((entry)  =>  entry.id  ===  rule.id);  const  bounds  =  filterGroupBounds(rows,  ruleIndex);  return  bounds  &&  ruleIndex  <=  itemIndex  &&  itemIndex  <=  bounds.end  ?  {  ...item,  active:  event.target.checked  }  :  item;  })()  :  item.id  ===  rule.id  ?  {  ...item,  active:  event.target.checked  }  :  item))}  />  :  <FilterCheckbox  checked={rule.active  !==  false}  onChange={(event)  =>  setFilterRules((rows)  =>  rows.map((item,  itemIndex)  =>  rule.id.startsWith('group-open-')  ?  (()  =>  {  const  ruleIndex  =  rows.findIndex((entry)  =>  entry.id  ===  rule.id);  const  bounds  =  filterGroupBounds(rows,  ruleIndex);  return  bounds  &&  ruleIndex  <=  itemIndex  &&  itemIndex  <=  bounds.end  ?  {  ...item,  active:  event.target.checked  }  :  item;  })()  :  item.id  ===  rule.id  ?  {  ...item,  active:  event.target.checked  }  :  item))}  />}</span><span  className="flow-filter-row-content"><FilterFieldPicker fields={data?.columns || []} value={rule.field}  onChange={(field)  =>  setFilterRules((rows)  =>  rows.map((item)  =>  item.id  ===  rule.id  ?  {  ...item,  field  }  :  item))}  /><FilterOperatorSpan  value={rule.operator}  onChange={(operator)  =>  setFilterRules((rows)  =>  rows.map((item)  =>  item.id  ===  rule.id  ?  {  ...item,  operator  }  :  item))}  /><FilterValuePicker suggestions={[...new Set(dataRows.map((row) => formatCell(row[rule.field])))]} value={rule.value}  onChange={(value)  =>  setFilterRules((rows)  =>  rows.map((item)  =>  item.id  ===  rule.id  ?  {  ...item,  value  }  :  item))}  />{index  <  filterRules.length  -  1  &&  !filterRules[index  +  1]?.id.startsWith('group-close-')  &&  <span  className="flow-filter-join"  role="button"  tabIndex={0}  onClick={()  =>  setFilterRules((rows)  =>  rows.map((item)  =>  item.id  ===  rule.id  ?  {  ...item,  join:  item.join  ===  'or'  ?  'and'  :  'or'  }  :  item))}>{rule.join  ||  'and'}</span>}</span>{(index  ===  filterRules.length  - 1  ||  rule.id.startsWith('group-close-')  ||  filterRules[index  + 1]?.id.startsWith('group-close-'))  &&  <span  className="flow-filter-row-actions">{rule.id.startsWith('group-close-')  &&  index  <  filterRules.length  -  1  &&  !filterRules[index  + 1]?.id.startsWith('group-close-')  &&  <span  className="flow-filter-join flow-filter-close-join"  role="button"  tabIndex={0}  onClick={()  =>  setFilterRules((rows)  =>  rows.map((item)  =>  item.id  ===  rule.id  ?  {  ...item,  join:  item.join  ===  'or'  ?  'and'  :  'or'  }  :  item))}>{rule.join  ||  'and'}</span>}<button  type="button"  className="flow-filter-mini"  onClick={()  =>  setFilterRules((rows)  =>  {  const  next  =  {  id:  ((rule.id.startsWith('group-filter-')  ||  rule.id.startsWith('group-close-'))  &&  filterNestingDepth(rule.id)  >  0)  ?  `group-filter-${'nested-'.repeat(filterNestingDepth(rule.id))}${randomId()}`  :  randomId(),  field: data?.columns[0] || '',  operator:  '=',  value:  '',  active:  true,  join:  'and'  };  const  cleanRows  =  rows.length  ===  1  &&  rows[0].id === 'placeholder-rule'  ?  []  :  rows;  const  closeIndex  =  rule.id.startsWith('group-close-')  ?  index  :  findContainingClose(cleanRows,  index);  if  (rule.id.startsWith('group-close-'))  return  [...cleanRows.slice(0, index  +  1), next, ...cleanRows.slice(index  +  1)];  if  (closeIndex  >= 0)  return  [...cleanRows.slice(0, closeIndex), next, ...cleanRows.slice(closeIndex)];  return  [...cleanRows, next];  })}><FilterAddIcon /></button><button  type="button"  className="flow-filter-mini"  aria-label="Add filter group"  onClick={()  =>  setFilterRules((rows)  =>  {  const  groupId  =  randomId();  const  cleanRows  =  rows.length  ===  1  &&  rows[0].id  ===  'placeholder-rule'  ?  []  :  rows;  const  nested  =  rule.id.startsWith('group-filter-')  ||  rule.id.startsWith('group-close-');  const  depth  =  rule.id.match(/nested-/g)?.length  ||  0;  const  currentPrefix  =  'nested-'.repeat(depth);  const  childPrefix  =  'nested-'.repeat(depth  +  1);  const  additions  =  [{  id:  `group-open-${currentPrefix}${groupId}`,  field:  '',  operator:  '=',  value:  '',  active:  true,  join:  'and'  },  {  id:  `group-filter-${childPrefix}${groupId}`,  field: data?.columns[0] || '',  operator:  '=',  value:  '',  active:  true,  join:  'and'  },  {  id:  `group-close-${currentPrefix}${groupId}`,  field:  '',  operator:  '=',  value:  '',  active:  true,  join:  'and'  }];  const  closeIndex  =  nested  ?  findContainingClose(cleanRows,  index)  :  -1;  return  closeIndex  >=  0  ?  [...cleanRows.slice(0, closeIndex), ...additions, ...cleanRows.slice(closeIndex)]  :  [...cleanRows, ...additions];  })}><FilterGroupIcon /></button>{rule.id === 'placeholder-rule' && <span className="flow-filter-placeholder">Select + to add a filter condition</span>}</span>}<span  className="flow-filter-row-end-actions"><button  type="button"  className="flow-filter-reorder"  aria-label="Move  up"  disabled={index  ===  0}  onClick={()  =>  {  if  (index  === 0)  return;  const  movingId  =  rule.id;  setFilterMoving({  id:  movingId,  peerId:  filterRules[index  -  1].id,  direction:  'up'  });  window.setTimeout(()  =>  {  setFilterRules((rows)  =>  {  const  currentIndex  =  rows.findIndex((item)  =>  item.id  ===  movingId);  if  (currentIndex  <=  0)  return  rows;  const  next  =  [...rows];  [next[currentIndex  -  1],  next[currentIndex]]  =  [next[currentIndex],  next[currentIndex  - 1]];  return  moveFilterRows(rows, currentIndex, 'up');  });  setFilterMoving(null);  },  180);  }}><ArrowUpOutlined  /></button><button  type="button"  className="flow-filter-reorder"  aria-label="Move  down"  disabled={index  ===  filterRules.length  -  1}  onClick={()  =>  {  if  (index  >=  filterRules.length  -  1)  return;  const  movingId  =  rule.id;  setFilterMoving({  id:  movingId,  peerId:  filterRules[index  +  1].id,  direction:  'down'  });  window.setTimeout(()  =>  {  setFilterRules((rows)  =>  moveFilterRows(rows, rows.findIndex((item)  =>  item.id  ===  movingId), 'down'));  setFilterMoving(null);  },  180);  }}><ArrowDownOutlined  /></button><button  type="button"  className="flow-filter-remove"  onClick={()  =>  setFilterRules((rows)  =>  {  if  (rows.length  ===  1)  return  [{  id:  'placeholder-rule',  field:  '',  operator:  '=',  value:  '',  active:  false,  join:  'and',  placeholder:  true  }];  const  next  =  removeFilterRow(rows, index);  return  next.length  ?  next  :  [{  id:  'placeholder-rule',  field:  '',  operator:  '=',  value:  '',  active:  false,  join:  'and',  placeholder:  true  }];  })}><CloseOutlined /></button></span></div>)}</div>
                                <div  className="flow-filter-sort"><strong>Sort by</strong><div className="flow-sort-conditions-viewport" ref={sortConditionsRef}><div className="flow-sort-conditions-track"><span  className="flow-filter-sort-field-wrap"><FilterFieldPicker fields={data?.columns || []} value={sortField}  onChange={(field) => { setSortField(field); setAppliedQuery((current) => ({ ...current, sorts: [...(field ? [{ field, direction: sortDirection }] : []), ...sortConditions.filter((item) => item.field)] })); setPageNumber(1); }}  className="flow-filter-sort-field"  labelClassName="flow-filter-sort-field-label"  trailing={<><button  type="button"  className="flow-filter-sort-direction-toggle"  aria-label={sortDirection  ===  'asc'  ?  'Switch to descending'  :  'Switch to ascending'}  onClick={(event)  =>  {  event.stopPropagation();  const  nextDirection:  'asc'  |  'desc'  =  sortDirection  ===  'asc'  ?  'desc'  :  'asc';  setSortDirection(nextDirection);  setAppliedQuery((current)  =>  ({  ...current,  sorts:  [...(sortField  ?  [{  field:  sortField,  direction:  nextDirection  }]  :  []),  ...sortConditions.filter((item)  =>  item.field)]  }));  setPageNumber(1);  }}><SortDirectionIcon  direction={sortDirection}  /></button><SortActionMenu onDelete={() => { setSortField(''); setAppliedQuery((current) => ({ ...current, sorts: [...sortConditions.filter((item) => item.field)] })); setPageNumber(1); }} onClear={() => { setSortField(''); setSortConditions([]); setAppliedQuery((current) => ({ ...current, sorts: [] })); setPageNumber(1); }} /></>}  /></span>{sortConditions.map((condition, index) => <span className="flow-filter-sort-field-wrap" key={`${condition.field}-${index}`}><FilterFieldPicker fields={data?.columns || []} value={condition.field} onChange={(field) => setSortConditions((items) => items.map((item, itemIndex) => itemIndex === index ? { ...item, field } : item))} className="flow-filter-sort-field" labelClassName="flow-filter-sort-field-label" trailing={<><button type="button" className="flow-filter-sort-direction-toggle" aria-label="Toggle sort direction" onClick={(event) => { event.stopPropagation(); setSortConditions((items) => items.map((item, itemIndex) => itemIndex === index ? { ...item, direction: item.direction === 'asc' ? 'desc' : 'asc' } : item)); }}><SortDirectionIcon direction={condition.direction} /></button><SortActionMenu onDelete={() => { const next = sortConditions.filter((_, itemIndex) => itemIndex !== index); setSortConditions(next); setAppliedQuery((current) => ({ ...current, sorts: [...(sortField ? [{ field: sortField, direction: sortDirection }] : []), ...next.filter((item) => item.field)] })); setPageNumber(1); }} onClear={() => { setSortField(''); setSortConditions([]); setAppliedQuery((current) => ({ ...current, sorts: [] })); setPageNumber(1); }} /></>} /></span>)}<button type="button" className="flow-filter-mini flow-filter-sort-add" aria-label="Add sort field" onClick={() => setSortConditions((conditions) => [...conditions, { field: data?.columns[0] || '', direction: 'asc' }])}><FilterAddIcon /></button></div></div>{sortConditionsOverflow && <div className="flow-sort-scroll-buttons"><button type="button" aria-label="Scroll left" onClick={() => sortConditionsRef.current?.scrollBy({ left: -140, behavior: 'smooth' })}>‹</button><button type="button" aria-label="Scroll right" onClick={() => sortConditionsRef.current?.scrollBy({ left: 140, behavior: 'smooth' })}>›</button></div>}</div>
                                <div  className="flow-filter-apply"><button type="button" className="flow-filter-apply-button" onClick={applyFilters}>Apply filters</button><span className="flow-filter-edited-hint">Edited criteria</span>{canManage && <span className="flow-api-count"><button type="button" className="flow-api-count-number" onClick={() => { setApiListOpen(true); setFilterOpen(true); }}>{apiCount}</button><span className="flow-api-count-label"> / APIs</span></span>}</div>
                                {canManage && apiListOpen && <section className="flow-filter-api-section">
                                    <div className="flow-filter-api-title"><strong>Open APIs</strong><span className="flow-api-status flow-api-status--enabled">Token required · Validity window enforced</span><button type="button" className="flow-filter-api-close" aria-label="Close APIs" onClick={() => setApiListOpen(false)}><CloseOutlined /></button></div>
                                    {apiCount === 0 ? <div className="flow-filter-api-empty"><span>No open APIs</span><button type="button" className="flow-filter-api-add" disabled={mutationLoading || apisLoading} onClick={addApi}><PlusOutlined /> Add API</button></div> : <>
                                        <div className="flow-filter-api-list">{openApis.map((api, index) => <div className="flow-filter-api-item" key={api.id}>
                                            <div className="flow-filter-api-item-head"><strong>API {index + 1}</strong><span>Token required</span><Button size="small" danger disabled={mutationLoading || apisLoading} onClick={() => removeApi(api)}>Revoke</Button></div>
                                            <div className="flow-filter-api-grid">
                                                <div className="flow-filter-api-token"><span>Auto-generated token</span><code>{api.token}</code></div>
                                                <label>Valid from<Input size="small" disabled={mutationLoading} type="datetime-local" value={api.validFrom ? new Date(new Date(api.validFrom).getTime() - new Date(api.validFrom).getTimezoneOffset() * 60000).toISOString().slice(0, 16) : ''} onChange={(event) => updateApi(api.id, { validFrom: event.target.value ? new Date(event.target.value).toISOString() : '' })} /></label>
                                                <label>Expires at<Input size="small" disabled={mutationLoading} type="datetime-local" value={api.validUntil ? new Date(new Date(api.validUntil).getTime() - new Date(api.validUntil).getTimezoneOffset() * 60000).toISOString().slice(0, 16) : ''} onChange={(event) => updateApi(api.id, { validUntil: event.target.value ? new Date(event.target.value).toISOString() : '' })} /></label>
                                            </div>
                                            <div className="flow-filter-api-range"><span>Data range</span><Select popupClassName="flow-select-popup" className="flow-api-filter" disabled={mutationLoading} mode="tags" size="small" placeholder="Data type" value={api.filterType} onChange={(value) => updateApi(api.id, { filterType: value })} options={[...new Set(dataRows.map((row) => String(row.data_type || '')).filter(Boolean))].map((value) => ({ value, label: value }))} allowClear /><Select popupClassName="flow-select-popup" className="flow-api-filter" disabled={mutationLoading} mode="tags" size="small" placeholder="Source" value={api.filterSource} onChange={(value) => updateApi(api.id, { filterSource: value })} options={[...new Set(dataRows.map((row) => String(row.source || row.data_source || '')).filter(Boolean))].map((value) => ({ value, label: value }))} allowClear /></div>
                                            <div className="flow-filter-api-permissions"><span>Permission scope</span><strong>Read data · filtered rows only</strong></div><div className="flow-api-preview">GET <a href={api.url} target="_blank" rel="noreferrer">{api.url}</a></div>
                                        </div>)}</div>
                                        <div className="flow-filter-api-add-row"><Button size="small" loading={mutationLoading} disabled={apisLoading || !openApis.length} onClick={saveApis}>Save API settings</Button><button type="button" className="flow-filter-api-add" disabled={mutationLoading || apisLoading} onClick={addApi}><PlusOutlined /> Add API</button></div>
                                    </>}
                                </section>}
                            </div>}
                            {partitionOpen  &&  <div  className="flow-partition-tags">{partitions.map((item)  =>  <Tag.CheckableTag  key={item.name}  checked={partition  ===  item.name}  onChange={() => { setPartition(partition === item.name ? undefined : item.name); setPageNumber(1); }}>{item.name.replace('p2026_',  '')}</Tag.CheckableTag>)}</div>}
                            <ConfigProvider locale={enUS}><Table className="flow-data-table" size="small" loading={loading} pagination={{ current: pageNumber, pageSize, total: data?.total ?? data?.rowCount ?? 0, size: 'small', showTotal: (total: number, range: [number, number]) => `${range[0]}–${range[1]} / ${data?.totalEstimated ? '~' : ''}${total.toLocaleString('en-US')} rows${data?.totalEstimated ? ' (estimated)' : ''} · ${table.size} · ${fieldCount} fields`, showSizeChanger: true, pageSizeOptions: [10, 20, 50], onChange: (page, size) => { if (editingRowKey) { message.warning('Save or cancel the current row edit first'); return; } setPageNumber(size !== pageSize ? 1 : page); setPageSize(size); } }} rowKey={rowKey} scroll={{ x: 'max-content' }} dataSource={dataRows} columns={dataColumns} onRow={(row) => ({ onDoubleClick: () => startRowEdit(rowKey(row)), className: editingRowKey === rowKey(row) ? 'flow-row-editing' : undefined })} /></ConfigProvider>
                    </div>
                    <Modal  className="flow-surface-modal flow-script-modal"  title="Layer  script"  open={scriptOpen} onCancel={closeScript} keyboard={!mutationLoading} maskClosable={!mutationLoading}  footer={null}  width={720}>
                        <div  className="flow-script-editor">
                            <div  className="flow-script-note">{scriptNote}. Drafts are stored separately and are not deployed to workers.</div>
                            <Input.TextArea aria-label="Layer script code" readOnly={!canManage || !scriptLoaded || mutationLoading} value={script}  onChange={(event)  =>  setScript(event.target.value)}  autoSize={{  minRows:  12,  maxRows:  22  }}  />
                            <div  className="flow-script-actions"><Button type="primary" onClick={runDryRun} loading={scriptValidating} disabled={!scriptLoaded || !script.trim() || mutationLoading}>Validate syntax</Button>{canManage && <Button onClick={saveScript} loading={mutationLoading} disabled={!scriptLoaded || !script.trim() || !scriptDirty}>Save draft</Button>}<span>AST validation only; code is not executed</span></div>

                        </div>
                    </Modal>
                    <Modal className="flow-surface-modal" title="Satellite vulnerability extraction" open={keywordsOpen} onCancel={() => { if (!keywordsSaving) setKeywordsOpen(false); }} footer={<Button disabled={keywordsSaving} onClick={() => setKeywordsOpen(false)}>Close</Button>} width={760}>
                        <p>Keywords select satellite-related vulnerability records. Upload a YAML file with a keywords array (up to 1 MiB, 1,000 keywords, 200 characters each). Matching ignores case, uses word boundaries, and supports multiple keywords per record. An empty array disables matching.</p>
                        <p>Activating a version takes effect within 30 seconds for subsequent processing. Rerun historical records to update their results.</p>
                        <div className="flow-script-actions">
                            <Button icon={<DownloadOutlined />} loading={keywordDownload === 'current'} disabled={!!keywordDownload || !keywordConfig?.current_version} onClick={() => downloadKeywords()}>Download current YAML</Button>
                            {canManage && <Upload accept=".yaml,.yml" showUploadList={false} disabled={keywordsLoading || keywordsSaving} beforeUpload={(file) => { void uploadKeywords(file); return false; }}><Button type="primary" icon={<UploadOutlined />} loading={keywordsSaving} disabled={keywordsLoading}>Upload and activate YAML</Button></Upload>}
                        </div>
                        {keywordsError && <p role="alert">{keywordsError}</p>}
                        {keywordsLoading ? <Spin size="small" /> : keywordConfig && <>
                            <p>Current version: <strong>{keywordConfig.current_version || 'None'}</strong> · {keywordConfig.keywords.length} keywords</p>
                            <div aria-label="Current keywords" style={{ maxHeight: 160, overflowY: 'auto', marginBottom: 16 }}>{keywordConfig.keywords.length ? keywordConfig.keywords.map((keyword) => <Tag key={keyword}>{keyword}</Tag>) : <span>No active keywords</span>}</div>
                            <Table className="flow-data-table" size="small" rowKey="version" dataSource={keywordConfig.versions} pagination={{ pageSize: 5 }} columns={[
                                { title: 'Version', dataIndex: 'version', key: 'version', ellipsis: true, render: (version: string) => <span>{version}{version === keywordConfig.current_version && <Tag color="blue">Current</Tag>}</span> },
                                { title: 'Created', dataIndex: 'created_at', key: 'created_at', render: (value: string) => new Date(value).toLocaleString('en-US') },
                                { title: 'Size', dataIndex: 'size', key: 'size', render: (size: number) => `${size.toLocaleString('en-US')} B` },
                                { title: 'Actions', key: 'actions', width: 180, render: (_: unknown, row: VulnerabilityKeywordConfig['versions'][number]) => <><Button size="small" loading={keywordDownload === row.version} disabled={!!keywordDownload} onClick={() => downloadKeywords(row.version)}>Download</Button>{canManage && row.version !== keywordConfig.current_version && <Button size="small" disabled={keywordsSaving} onClick={() => activateKeywords(row.version)}>Activate</Button>}</> },
                            ]} />
                        </>}
                    </Modal>
                </div>}
            </section>
        </div>
        <Modal  className="flow-surface-modal flow-offset-modal"  title="Adjust  Kafka  offset"  open={offsetOpen}  onCancel={() => { if (!mutationPending.current) setOffsetOpen(false); }}  cancelText="Cancel"  onOk={saveOffset} confirmLoading={mutationLoading} okButtonProps={{ disabled: !canManage || !stream?.available || offsetPartition === undefined || offset === undefined || offset < 0 || !Number.isSafeInteger(offset) }}  okText="Confirm  SET  OFFSET"  width={460}>
            <div  className="flow-offset-modal-body">
                <div  className="flow-offset-modal-info">
                    <div  className="flow-offset-modal-info-row"><span>Consumer  group</span><strong>{stream?.consumerGroup || 'Unavailable'}</strong></div>
                </div>
                <div className="flow-offset-modal-section-label">Input topic</div>
                <Input.Search value={topicDraft} onChange={(event) => setTopicDraft(event.target.value)} onSearch={(topic) => { if (topic.trim()) { setInputTopics([topic.trim()]); setActiveTopicIndex(0); setOffset(undefined); setOffsetPartition(undefined); setStream(undefined); setOffsets([]); setStreamError(''); } }} enterButton="Load topic" />
                <div className="flow-offset-modal-section-label">Partition</div>
                <Select popupClassName="flow-select-popup" style={{ width: '100%' }} value={offsetPartition} options={offsets.map((item) => ({ value: item.partition, label: `Partition ${item.partition}` }))} onChange={(value) => { setOffsetPartition(value); setOffset(offsets.find((item) => item.partition === value)?.offset); }} />
                {!stream?.available && <div role="alert">{streamError || stream?.reason || 'Stream unavailable'}</div>}
                <div  className="flow-offset-modal-section-label">Current  offsets  <em>live</em></div>
                <div  className="flow-offset-modal-grid">{offsets.map((item)  =>  <div  key={item.partition}  className="flow-offset-modal-cell"><small>Partition  {item.partition}</small><strong>{item.offset.toLocaleString('en-US')}</strong><i  className="flow-dot  flow-dot--green"  /></div>)}</div>
                <div  className="flow-offset-modal-section-label">New  offset</div>
                <InputNumber  min={0}  value={offset}  onChange={(value)  =>  setOffset(value  ??  undefined)}  placeholder="Enter  new  Kafka  offset"  style={{  width:  '100%'  }}  formatter={(value)  =>  `${value  ??  ''}`.replace(/[^\d]/g,  '')}  onKeyDown={(e)  =>  {  const  allowed  =  ['Backspace',  'Delete',  'ArrowLeft',  'ArrowRight',  'ArrowUp',  'ArrowDown',  'Tab',  'Enter',  'Home',  'End'];  if  (allowed.includes(e.key)  ||  e.ctrlKey  ||  e.metaKey)  return;  if  (!/^\d$/.test(e.key))  e.preventDefault();  }}  />
                <div  className="flow-offset-modal-warn"><AlertOutlined  />  Changes  apply  after  the  worker  restarts.  The  consumer  group  will  resume  from  the  new  position.</div>
            </div>
        </Modal>
        <Modal className="flow-surface-modal"  title="Data  analysis"  open={analysisOpen}  onCancel={()  =>  setAnalysisOpen(false)}  footer={<Button  onClick={()  =>  setAnalysisOpen(false)}>Close</Button>}><div  className="flow-analysis-grid"><div><span>Visible  rows</span><strong>{dataRows.length}</strong></div><div><span>Partitions</span><strong>{new  Set(dataRows.map((row)  =>  row.partition)).size}</strong></div><div><span>Ready</span><strong>{dataRows.filter((row)  =>  row.status  ===  'ready').length}</strong></div></div></Modal>
        <Modal  className="flow-surface-modal flow-json-preview-modal"  title={jsonRow  ?  `Row  JSON  ·  ${table.name}`  :  'Row  JSON'}  open={!!jsonRow}  onCancel={()  =>  setJsonRow(null)}  footer={<Button  onClick={()  =>  setJsonRow(null)}>Close</Button>}  width={720}>
            <div  className="flow-json-tree">{jsonRow  ?  <JsonTreeValue  name=""  value={jsonRow}  depth={0}  comma={false}  />  :  null}</div>
        </Modal>
        <Modal  className="flow-surface-modal flow-connector-modal"  closable={false}  title={<div  className="flow-modal-title-row"><span  className={connectorStep  ===  'type'  ?  ''  :  'flow-modal-database-title'}  style={connectorStep  ===  'config'  ?  ({  viewTransitionName:  `flow-database-${connectorDatabase}`  }  as  React.CSSProperties)  :  undefined}>{connectorStep  ===  'type'  ?  'New  data  connector'  :  <>{selectedDatabaseOption.icon}<strong>{selectedDatabaseOption.label}</strong></>}</span><div  className="flow-modal-title-actions">{connectorStep  ===  'config'  &&  <button  type="button"  aria-label="Back  to  database  selection"  onClick={()  =>  changeConnectorStep('type')}><BackIcon  className="flow-modal-action-icon"  /></button>}<button  type="button"  aria-label="Close"  disabled={mutationLoading} onClick={() => setCollectionOpen(false)}><CloseIcon  className="flow-modal-action-icon"  /></button></div></div>}  open={collectionOpen}  onCancel={() => { if (!mutationPending.current) setCollectionOpen(false); }}  footer={null}  width={connectorStep  ===  'config'  ?  760  :  560}>
            {connectorStep  ===  'type'  ?  <div  className="flow-connector-type-step"><Input  className="flow-database-search"  prefix={<SearchOutlined  />}  allowClear  value={databaseSearch}  onChange={(event)  =>  setDatabaseSearch(event.target.value)}  placeholder="Search  databases"  /><div  className="flow-connector-picker">{visibleDatabaseOptions.map(({  key,  label,  icon  })  =>  <button  key={key}  type="button"  style={{  viewTransitionName:  `flow-database-${key}`  }  as  React.CSSProperties}  className={`flow-database-option  ${connectorSelectionActive  &&  connectorDatabase  ===  key  ?  'is-selected'  :  ''}`}  disabled={key !== 'postgres'} title={key !== 'postgres' ? 'Only the configured PostgreSQL database is supported' : undefined} onClick={()  =>  changeConnectorStep('config',  key)}>{icon}<strong>{label}</strong></button>)}</div></div>  :  <div  className={`flow-connector-config${connectorSelectedSchema  ?  '  has-schema-detail'  :  ''}${resourcePanel  !==  'none'  ?  '  has-resource-panel'  :  ''}`}><div  className="flow-tree-toolbar"><button  type="button"  title="Add  resource"  onClick={openCreateResource}><PlusOutlined  /></button>{databaseName  &&  <><button  type="button"  title="Edit  resource"  onClick={openEditResource}><EditOutlined  /></button><button  type="button"  title="Delete  resource"  className="is-danger"  onClick={removeResource}><DeleteOutlined  /></button></>}</div><div  className="flow-resource-tree  flow-database-list"  onClick={()  =>  {  setDatabaseName('');  setConnectorSelectedSchema('');  setResourcePanel('none');  }}>{Object.keys(databaseAliases).map((value)  =>  <React.Fragment  key={value}><div  className={`flow-tree-resource-row  ${databaseName  ===  value  ?  'is-selected'  :  ''}`}  onClick={(event)  =>  {  event.stopPropagation();  setDatabaseName(value);  setConnectorSelectedSchema('');  setSchemaNameDraft('');  setSchemaEditing(false);  setResourcePanel('none');  setExpandedDatabases((current)  =>  ({  ...current,  [value]:  !current[value]  }));  }}><button  type="button"  className="flow-tree-expand"  aria-label={`${expandedDatabases[value]  ?  'Collapse'  :  'Expand'}  ${value}`}><CaretRightOutlined  rotate={expandedDatabases[value]  ?  90  :  0}  /></button><button  type="button"  className="flow-tree-resource-main"><DatabaseOutlined  className="flow-navicat-connection-icon"  style={{ fontSize: 18 }}  /><strong>{connections.find((item) => item.id === value)?.database || value}</strong><small>{databaseAliases[value]}</small><em>{connections.find((item) => item.id === value)?.schemas.filter((schema) => schema !== 'ts_meta').length || 0}</em></button></div>{expandedDatabases[value]  &&  <div  className="flow-tree-children">{(connections.find((item) => item.id === value)?.schemas || []).filter((schema) => schema !== 'ts_meta').map((schema)  =>  <div  className={`flow-tree-resource-row  flow-tree-schema-row  ${connectorSelectedSchema  ===  schema  ?  'is-selected'  :  ''}`}  key={`${value}-${schema}`}  onClick={(event)  =>  event.stopPropagation()}><span  className="flow-tree-branch"  /><ApartmentOutlined  /><button  type="button"  onClick={()  =>  {  setConnectorSelectedSchema(schema);  setSchemaNameDraft(schema);  setSchemaEditing(false);  setResourcePanel('none');  }}><strong>{schema}</strong><small>{schemaAliases[schema]}</small></button></div>)}</div>}</React.Fragment>)}</div>{resourcePanel  !==  'none'  &&  <aside  className="flow-resource-panel"><div  className="flow-resource-panel-head"><strong>{resourcePanel  ===  'create'  ?  'New PostgreSQL connection'  :  'Edit PostgreSQL connection'}</strong><button  type="button"  onClick={()  =>  setResourcePanel('none')}  aria-label="Close">×</button></div><label>Connection  name<Input  size="small"  disabled={mutationLoading} value={resourceConnectionName}  onChange={(event)  =>  setResourceConnectionName(event.target.value)}  placeholder="Postgres  connection"  /></label><label>Database<Input  size="small"  disabled={mutationLoading} value={resourceDatabase}  onChange={(event)  =>  setResourceDatabase(event.target.value)}  placeholder="Database  name"  /></label><div  className="flow-resource-schema-editor"><span>Schema  (optional)</span><div  className="flow-resource-schema-input"><Input  size="small"  disabled={mutationLoading} value={resourceSchema}  onChange={(event)  =>  setResourceSchema(event.target.value)}  placeholder="Add  schema"  /><Button  size="small"  onClick={()  =>  {  const  schema  =  resourceSchema.trim();  if  (schema  &&  !resourceSchemas.includes(schema))  {  setResourceSchemas((current)  =>  [...current,  schema]);  setResourceSchema('');  }  }}><PlusOutlined  /></Button></div>{resourceSchemas.some((schema) => schema !== 'ts_meta')  &&  <div  className="flow-resource-schema-tags">{resourceSchemas.filter((schema) => schema !== 'ts_meta').map((schema)  =>  <span  key={schema}>{schema}<button  type="button"  onClick={()  =>  setResourceSchemas((current)  =>  current.filter((item)  =>  item  !==  schema))}>×</button></span>)}</div>}</div><Button type="primary" size="small" loading={mutationLoading} onClick={saveResource}>Save</Button></aside>}{connectorSelectedSchema  &&  resourcePanel  ===  'none'  &&  <aside  className="flow-schema-detail"><div  className="flow-resource-panel-head"><strong>Schema  details</strong><div  className="flow-schema-detail-actions"><button  type="button"  className="flow-schema-action-icon"  aria-label="Edit schema" title="Edit schema"  onClick={()  =>  setSchemaEditing(true)}><EditOutlined  /></button><button  type="button"  className="flow-schema-action-icon  is-danger"  aria-label="Delete  schema"  title="Delete  schema"  onClick={deleteSchema}><DeleteOutlined  /></button><button  type="button"  className="flow-schema-action-icon"  onClick={()  =>  setConnectorSelectedSchema('')}  aria-label="Close"><CloseIcon  className="flow-modal-action-icon"  /></button></div></div><label  className="flow-schema-detail-field">Name<Input  size="small"  readOnly={!schemaEditing || mutationLoading}  value={schemaNameDraft}  onChange={(event)  =>  setSchemaNameDraft(event.target.value)}  onPressEnter={renameSchema}  /></label><label  className="flow-schema-detail-field">Alias<Input  size="small"  readOnly={!schemaEditing || mutationLoading}  value={schemaAliasDraft} onChange={(event) => setSchemaAliasDraft(event.target.value)}  /></label><Button type="primary" size="small" loading={mutationLoading} disabled={!schemaEditing} onClick={renameSchema}>Save</Button></aside>}<Button  className="flow-create-connector"  size="small"  type="primary"  disabled={mutationLoading} onClick={() => { if (resourcePanel !== 'none') void saveResource(); else setCollectionOpen(false); }}>Save  connection</Button></div>}
        </Modal>
    </div>;
};
export  default  Pipeline;













