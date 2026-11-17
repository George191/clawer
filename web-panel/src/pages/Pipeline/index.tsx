import  React,  {  useEffect,  useLayoutEffect,  useRef,  useState  }  from  'react';
import  {  Link,  useLocation,  useNavigate  }  from  'react-router-dom';
import  {  Button,  Checkbox,  ConfigProvider,  Dropdown,  Input,  InputNumber,  Modal,  Select,  Space,  Spin,  Table,  Tag,  Upload,  message  }  from  '@/components/ui';
import  enUS  from  '@/components/ui/locale';
import  {  AlertOutlined,  ArrowDownOutlined,  ArrowUpOutlined,  CheckOutlined,  CloseOutlined,  CodeOutlined,  DatabaseOutlined,  DeleteOutlined,  DownOutlined,  DownloadOutlined,  EditOutlined,  PlusOutlined,  ReloadOutlined,  RightOutlined,  SearchOutlined,  SettingOutlined,  UploadOutlined  }  from  '@/components/ui/icons';
import  '../ProductWorkspace/workspace.css';
import CdcStatusPanel from './CdcStatusPanel';
import OntologyExplorer, { schemaColor } from './OntologyExplorer';
import DataConnections from './DataConnections';
import OntologyLineage from './OntologyLineage';
import PostgresIcon from './PostgresGlyph';
import './ontology-frame.css';
import './foundry-workbench-tokens.css';
import './ontology-workspace.css';
import ExplorerCatalog from './ExplorerCatalog';
import NewObjectType from './NewObjectType';
import ExplorerTableSchema from './ExplorerTableSchema';
import ExplorerTableEvidence from './ExplorerTableEvidence';
import SandboxPipelineBuilder from './SandboxPipelineBuilder';
import ExplorerIndexCoverage from './ExplorerIndexCoverage';
import SandboxIndexPreview, { INDEX_PREVIEW_SCHEMA, INDEX_PREVIEW_TABLE } from './SandboxIndexPreview';
import { appendAndFilters, blankExplorerFilter, cellFilterRows, countFilterTerms, exactCellValue, explorerFilterTerms, hasOr } from './explorerFilters';
import type { ExplorerFilter } from './explorerFilters';
import  {  TemplateGlyph,  inferSiteKind  }  from  '../AICollect/WorkspaceDock';
import { fetchCurrentUserContext, fetchLayers, fetchLayerTables, fetchTableData, fetchHandlerCode, queryEtlTable, queryExplorerPreview, fetchEtlTableColumns, fetchTablePartitions, fetchTableStream, fetchLayerScript, setTableOffset, validateHandlerCode, saveHandlerCode, updateEtlRow, deleteEtlRow, fetchEtlApis, createEtlApi, updateEtlApi, deleteEtlApi, fetchVulnerabilityKeywords, uploadVulnerabilityKeywords, activateVulnerabilityKeywordVersion, downloadVulnerabilityKeywordVersion, downloadCurrentVulnerabilityKeywords } from '../../services/api';
import type { EtlDataQuery, EtlPublishedApi, VulnerabilityKeywordConfig } from '../../services/api';
import  type  {  LayerNode,  LayerTable,  QueryResult,  ExplorerPreviewResult,  EtlPartition,  EtlStreamState  }  from  '../../services/types';

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
const  EditIcon:  React.FC<{  className?:  string  }>  =  ({  className  })  =>  <svg  className={className}  viewBox="0  0  24  24"  role="img"  aria-label="Edit"  fill="none"><path  d="M5.5  17.8V19h1.2l9.9-9.9-1.2-1.2-9.9  9.9Z"  stroke="currentColor"  strokeWidth="1.6"  strokeLinejoin="round"/><path  d="m14.7  7.9  1.4-1.4a1.7  1.7  0  0  1  2.4  2.4l-1.4  1.4"  stroke="currentColor"  strokeWidth="1.6"  strokeLinecap="round"/></svg>;
const  DeleteIcon:  React.FC<{  className?:  string  }>  =  ({  className  })  =>  <svg  className={className}  viewBox="0  0  24  24"  role="img"  aria-label="Delete"  fill="none"><path  d="M6.5  7.5h11M9.5  7.5V5.8h5v1.7m-6.7  0  .7  11h5l.7-11"  stroke="currentColor"  strokeWidth="1.6"  strokeLinecap="round"  strokeLinejoin="round"/></svg>;
const Pipeline: React.FC = () => {
    const { pathname } = useLocation();
    return <ConfigProvider locale={enUS}><main className="flow-console flow-console--ontology">
        <section className="flow-core">
            {pathname === '/ontology' ? <OntologyExplorer refreshToken={0} />
                : pathname === '/ontology/connections' ? <DataConnections />
                : pathname === '/ontology/lineage' ? <OntologyLineage refreshToken={0} />
                    : pathname === '/ontology/runtime' ? <FlowRuntime />
                        : pathname === '/ontology/transforms' ? <TransformWorkspace /> : <DataLayersWorkspace />}
        </section>
    </main></ConfigProvider>;
};

const TransformWorkspace: React.FC = () => {
    const { pathname, search } = useLocation();
    const builder = new URLSearchParams(search).get('mode') === 'builder';
    const modeLink = (showBuilder: boolean) => {
        const params = new URLSearchParams(search);
        if (showBuilder) params.set('mode', 'builder'); else params.delete('mode');
        const query = params.toString();
        return query ? `${pathname}?${query}` : pathname;
    };
    return <div className="flow-transform-workspace">
        <nav aria-label="Transform modes" className="flow-transform-modes">
            <Link to={modeLink(false)} aria-current={!builder ? 'page' : undefined}>Handler</Link>
            <Link to={modeLink(true)} aria-current={builder ? 'page' : undefined}>Sandbox Pipeline Builder</Link>
        </nav>
        <div className="flow-transform-workspace__content">{builder ? <SandboxPipelineBuilder /> : <DataLayersWorkspace />}</div>
    </div>;
};

const FlowRuntime: React.FC = () => {
    const [allowed, setAllowed] = useState<boolean>();
    const [error, setError] = useState(false);
    const [version, setVersion] = useState(0);
    useEffect(() => {
        let active = true;
        setError(false);
        fetchCurrentUserContext().then((context) => { if (active) setAllowed(context.user.is_superuser); })
            .catch(() => { if (active) setError(true); });
        return () => { active = false; };
    }, [version]);
    return <div className="flow-ontology-runtime" data-testid="ontology-runtime"><div className="flow-ontology-page-heading"><span><Link to="/ontology" style={{ color: 'inherit' }}>Object types</Link></span><h1>Runtime status</h1><p>Inspect the current ingestion and consumption infrastructure.</p></div>
        {error ? <div role="alert">Unable to verify your access.<Button onClick={() => setVersion((value) => value + 1)}>Retry</Button></div>
            : allowed === undefined ? <Spin />
                : allowed ? <CdcStatusPanel /> : <div className="flow-ontology-message">Runtime diagnostics are available to administrators. You can continue exploring data assets and tables.</div>}
    </div>;
};
const  DataLayersWorkspace:  React.FC  =  ()  =>  {
    const { search, pathname } = useLocation();
    const isSchemas = pathname === '/ontology/explorer';
    const workspacePath = pathname === '/ontology/transforms' ? '/ontology/transforms' : '/ontology/explorer';
    const requestedView = new URLSearchParams(search).get('view');
    const  navigate  =  useNavigate();
    const [canManage, setCanManage] = useState(false);
    const managePermission = useRef(false);
    managePermission.current = canManage;
    const [catalogLoaded, setCatalogLoaded] = useState(false);
    const [schemaSearch, setSchemaSearch] = useState('');
    const [schemas, setSchemas] = useState<LayerNode[]>([]);
    const [serverTables, setServerTables] = useState<LayerTable[]>([]);
    const [partitions, setPartitions] = useState<EtlPartition[]>([]);
    const [data, setData] = useState<QueryResult>();
    const [previewPage, setPreviewPage] = useState<{ offset: number; limit: number; result: ExplorerPreviewResult }>();
    const previewViewport = useRef<HTMLDivElement>(null);
    const [stream, setStream] = useState<EtlStreamState>();
    const [loading, setLoading] = useState(false);
    const [previewError, setPreviewError] = useState('');
    const [previewUnavailable, setPreviewUnavailable] = useState(false);
    const [previewRevision, setPreviewRevision] = useState(0);
    const [repairFields, setRepairFields] = useState<string[]>([]);
    const [tablesLoading, setTablesLoading] = useState(false);
    const [tablesLoadedSchema, setTablesLoadedSchema] = useState<string>();
    const [requestError, setRequestError] = useState('');
    const [streamError, setStreamError] = useState('');
    const [streamLoading, setStreamLoading] = useState(false);
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
            const [layers, context] = await Promise.all([fetchLayers(), fetchCurrentUserContext()]);
            if (version !== catalogRequest.current || !workspaceActive.current) return;
            setCanManage(context.user.is_superuser === true);
            const visibleLayers = layers.map((item) => ({ ...item, key: item.schemaName || (item.key.startsWith('ts_') ? item.key : `ts_${item.key}`) })).filter((item) => item.key !== 'ts_meta');
            setSchemas(visibleLayers);
            setSchemaAliases(Object.fromEntries(visibleLayers.map((item) => [item.key, item.alias || item.label])));
            setCatalogLoaded(true);
        } catch (error) { if (version === catalogRequest.current && workspaceActive.current) throw error; }
    };

    const [selectedSchema, setSelectedSchema] = useState('');
    const [createTableSchema, setCreateTableSchema] = useState('');
    const creationSchemas = schemas.filter((item) => item.status === 'running' && !['ts_meta', 'ts_meta_hist'].includes(item.key));
    const canCreateTable = isSchemas && canManage && creationSchemas.some((item) => item.key === selectedSchema);
    const openTableWizard = () => {
        if (!canCreateTable) return;
        if (editingRowKey || scriptDirty || apisDirty || mutationPending.current) { message.warning('Save or cancel current changes before creating a table'); return; }
        setCreateTableSchema(selectedSchema);
    };
    const openWizardTable = (schema: string, name: string) => {
        setCreateTableSchema('');
        setClosedTables((current) => { const next = new Set(current); next.delete(name); return next; });
        setRefreshVersion((value) => value + 1);
        refreshCatalog().catch((error) => { if (workspaceActive.current) setRequestError(errorText(error)); });
        const params = new URLSearchParams(search);
        params.delete('create'); params.set('schema', schema); params.set('table', name);
        navigate(`${workspacePath}?${params.toString()}`);
    };
    const [schemaAliases, setSchemaAliases] = useState<Record<string, string>>({});
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
    const tables = serverTables.filter((item) => !closedTables.has(item.name));
    const schemaNode = schemas.find((item) => item.key === selectedSchema);
    const requestedSchema = new URLSearchParams(search).get('schema');
    const selectedSchemaAvailable = catalogLoaded && !!schemaNode && (!requestedSchema || requestedSchema === selectedSchema);
    const visibleSchemas = schemas.filter((item) => `${item.key} ${item.label} ${item.alias || ''} ${item.database || ''}`.toLowerCase().includes(schemaSearch.trim().toLowerCase()));
    const requestedTable = new URLSearchParams(search).get('table');
    const selectedTableAvailable = selectedSchemaAvailable && tablesLoadedSchema === selectedSchema
        && (!isSchemas || requestedTable === null || requestedTable === table.name)
        && !closedTables.has(table.name) && serverTables.some((item) => item.name === table.name);
    const resourceView = isSchemas && (requestedView === 'schema' || requestedView === 'stream' || requestedView === 'api' || requestedView === 'evidence') ? requestedView : 'preview';
    useLayoutEffect(()  =>  {  const  element  =  sortConditionsRef.current;  if  (!element)  return;  const  update  =  ()  =>  setSortConditionsOverflow(element.scrollWidth  >  element.clientWidth  +  1);  update();  const  observer  =  new  ResizeObserver(update);  observer.observe(element);  return  ()  =>  observer.disconnect();  },  [sortConditions.length, sortField, filterOpen, resourceView]);
    useEffect(() => {
        setScriptOpen(pathname === '/ontology/transforms' && selectedTableAvailable);
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
    const appliedTerms = explorerFilterTerms(appliedQuery.filters);
    const filterCount = countFilterTerms(appliedTerms) + Number(Boolean(appliedQuery.search?.trim()));
    const sortCount = appliedQuery.sorts.length;
    const indexPreviewAsset = selectedSchema === INDEX_PREVIEW_SCHEMA && table.name === INDEX_PREVIEW_TABLE;
    const showIndexPreview = isSchemas && resourceView === 'preview' && indexPreviewAsset;
    const showPreview = (!isSchemas || resourceView === 'preview') && !indexPreviewAsset;
    const showStream = !isSchemas || resourceView === 'stream';
    const fieldCount = data?.columns.length ?? 0;
    const filterFields = data?.columns.length ? data.columns : repairFields;
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
    const canChangePreview = () => {
        if (editingRowKey || mutationPending.current) {
            message.warning('Save or cancel the current row edit first'); return false;
        }
        return selectedTableAvailable && !loading;
    };
    const filterDraft = (rows: ExplorerFilter[]) => rows.filter((rule) => rule.field || /^group-(open|close)-/.test(rule.id));
    const hasFilterDraft = () => JSON.stringify(filterDraft(filterRules)) !== JSON.stringify(filterDraft(appliedQuery.filters));
    const requireAppliedFilterDraft = () => {
        if (!canChangePreview()) return false;
        if (!hasFilterDraft()) return true;
        setFilterOpen(true); setPartitionOpen(false);
        message.warning('Apply your pending column filter edits before using this shortcut');
        return false;
    };
    const setExplorerFilters = (rows: ExplorerFilter[]) => {
        if (rows.length > 100) { message.warning('The table supports up to 100 filter rows'); return; }
        const normalized = normalizeFilterDepth(rows);
        setAppliedQuery((current) => ({ ...current, filters: normalized }));
        setFilterRules(normalized.length ? normalized : [blankExplorerFilter('rule-1')]);
        setPageNumber(1);
    };
    const addColumnFilter = (field: string) => {
        if (!canChangePreview()) return;
        const id = randomId();
        const next = appendAndFilters(filterDraft(filterRules), [{ ...blankExplorerFilter(id), field }], randomId());
        if (next.length > 100) { message.warning('The table supports up to 100 filter rows'); return; }
        setFilterRules(normalizeFilterDepth(next));
        setFilterOpen(true); setPartitionOpen(false);
        window.requestAnimationFrame(() => document.getElementById(`explorer-filter-${id}`)?.scrollIntoView({ block: 'nearest' }));
    };
    const quickCellFilter = (field: string, value: string | null, exclude: boolean) => {
        if (!requireAppliedFilterDraft()) return;
        const id = randomId();
        setExplorerFilters(appendAndFilters(appliedQuery.filters, cellFilterRows(field, value, exclude, id), `${id}-previous`));
    };
    const updateExplorerSorts = (sorts: SortCondition[]) => {
        setAppliedQuery((current) => ({ ...current, sorts }));
        setSortField(sorts[0]?.field || '');
        setSortDirection(sorts[0]?.direction || 'asc');
        setSortConditions(sorts.slice(1));
        setPageNumber(1);
    };
    const requireAppliedSortDraft = () => {
        if (!canChangePreview()) return false;
        const draft = [...(sortField ? [{ field: sortField, direction: sortDirection }] : []), ...sortConditions.filter((item) => item.field)];
        if (JSON.stringify(draft) === JSON.stringify(appliedQuery.sorts)) return true;
        setFilterOpen(true); setPartitionOpen(false);
        message.warning('Apply your pending sort edits before using this shortcut');
        return false;
    };
    const setHeaderSort = (field: string, direction: 'asc' | 'desc') => {
        if (!requireAppliedSortDraft()) return;
        updateExplorerSorts([{ field, direction }, ...appliedQuery.sorts.filter((sort) => sort.field !== field)]);
    };
    const describeTerm = (term: (typeof appliedTerms)[number]): string => {
        if (term.children) return `(${describeExpression(term.children)})`;
        const rule = term.rule!;
        const operator = filterOperators.find((item) => item.value === rule.operator)?.label ?? rule.operator;
        return `${rule.field} ${operator.toLowerCase()}${rule.value === '' ? '' : ` ${JSON.stringify(rule.value)}`}`;
    };
    const describeExpression = (terms: typeof appliedTerms): string =>
        terms.map((term, index) => `${index ? ` ${terms[index - 1].join.toUpperCase()} ` : ''}${describeTerm(term)}`).join('');
    const removeFilterChip = (start: number, end: number) => {
        if (requireAppliedFilterDraft()) setExplorerFilters(appliedQuery.filters.filter((_, index) => index < start || index > end));
    };
    const queryChip = (key: string, kind: string, label: string, remove: () => void) =>
        <span className="flow-query-chip" key={key}><span title={label}>{label}</span><button type="button" aria-label={`Remove ${kind} ${label}`} title={`Remove ${kind}`} onClick={remove}><CloseOutlined /></button></span>;
    const appliedChips = hasOr(appliedTerms) ? [
        queryChip('root-expression', 'filter group', describeExpression(appliedTerms), () => removeFilterChip(0, appliedQuery.filters.length - 1)),
    ] : appliedTerms.map((term) => queryChip(appliedQuery.filters[term.start].id, term.children ? 'filter group' : 'filter', describeTerm(term), () => removeFilterChip(term.start, term.end)));

    const dataColumns: any[] = (data?.columns || []).map((key) => {
        const sort = appliedQuery.sorts.find((item) => item.field === key);
        return {
            title: isSchemas ? <Dropdown overlayClassName="flow-menu-popup flow-explorer-menu" trigger={['click']} menu={{
                items: [
                    { key: 'asc', label: 'Sort ascending', icon: <ArrowUpOutlined /> },
                    { key: 'desc', label: 'Sort descending', icon: <ArrowDownOutlined /> },
                    { key: 'filter', label: 'Add filter', icon: <PlusOutlined /> },
                ],
                selectedKeys: sort ? [sort.direction] : [],
                onClick: ({ key: action }) => action === 'filter' ? addColumnFilter(key) : setHeaderSort(key, action === 'desc' ? 'desc' : 'asc'),
            }}><button type="button" className={`flow-column-menu${sort ? ' is-sorted' : ''}`} aria-label={`Column actions for ${key}`} title={key}><span>{key}</span>{sort ? <SortDirectionIcon direction={sort.direction} /> : <DownOutlined />}</button></Dropdown> : <span className="flow-col-head"><span className="flow-col-head-label" title={key}>{isSchemas ? key : key.toUpperCase()}</span><button type="button" className={`flow-col-sort-btn${appliedSort?.field === key ? ` is-active is-${appliedSort.direction}` : ''}`} aria-label={`Sort by ${key}`} title="Sort" onClick={(event) => { event.stopPropagation(); toggleHeaderSort(key); }}><SortDirectionIcon direction={appliedSort?.field === key ? appliedSort.direction : 'asc'} /></button></span>,
            dataIndex: key, key,
            render: (value: unknown, row: Record<string, unknown>) => {
                if (editingRowKey === rowKey(row) && editingDraft) return <Input size="small" aria-label={`Edit ${key}`} disabled={mutationLoading} value={editingDraft[key] == null ? '' : String(editingDraft[key])} placeholder={editingDraft[key] === null ? 'SQL NULL' : undefined} onKeyDown={(event) => { if (event.key === 'Escape') { event.preventDefault(); cancelRowEdit(); } }} addonAfter={<button type="button" disabled={mutationLoading} aria-label={`Set ${key} to null`} title="Set SQL NULL" onClick={() => setEditingDraft((current) => current ? { ...current, [key]: null } : current)}>NULL</button>} onChange={(event) => setEditingDraft((current) => current ? { ...current, [key]: event.target.value } : current)} />;
                const shown = formatCell(value);
                const scalar = isSchemas ? exactCellValue(value, data?.columnTypes?.[key]) : undefined;
                if (scalar === undefined) return shown;
                return <Dropdown overlayClassName="flow-menu-popup flow-explorer-menu" trigger={['click']} menu={{
                    items: [{ key: 'include', label: 'Include this value' }, { key: 'exclude', label: 'Exclude this value' }],
                    onClick: ({ key: action }) => quickCellFilter(key, scalar, action === 'exclude'),
                }}><button type="button" className="flow-cell-menu" title={`Filter ${key} by ${shown || 'empty string'}`}><span>{shown || 'Empty'}</span><DownOutlined /></button></Dropdown>;
            },
        };
    });
    dataColumns.push({
        title: isSchemas ? 'Row' : 'ACTION', key: 'action', fixed: 'right', width: isSchemas ? 68 : 112,
        render: (_: unknown, row: Record<string, unknown>) => {
            if (editingRowKey === rowKey(row)) return <span className="flow-row-actions is-editing"><button type="button" aria-label="Save row" disabled={mutationLoading} onClick={saveRowEdit}><CheckOutlined /></button><button type="button" aria-label="Cancel row edit" disabled={mutationLoading} onClick={cancelRowEdit}><CloseOutlined /></button></span>;
            if (!isSchemas) return <span className="flow-row-actions flow-row-hover-actions"><button type="button" aria-label="Preview row JSON" title="Preview row JSON" onClick={() => setJsonRow(row)}><CodeOutlined /></button>{canManage && data?.primaryKey?.length ? <><button type="button" aria-label="Edit row" disabled={mutationLoading || loading} onClick={() => startRowEdit(rowKey(row))}><EditOutlined /></button><button type="button" aria-label="Delete row" disabled={mutationLoading || loading} onClick={() => removeRow(row)}><DeleteOutlined /></button></> : null}</span>;
            return <Dropdown overlayClassName="flow-menu-popup flow-explorer-menu" trigger={['click']} placement="bottomRight" menu={{
                items: [
                    { key: 'json', label: 'Preview row JSON', icon: <CodeOutlined /> },
                    ...(canManage && data?.primaryKey?.length ? [
                        { key: 'edit', label: 'Edit row', icon: <EditOutlined />, disabled: mutationLoading || loading },
                        { key: 'delete', label: 'Delete row', icon: <DeleteOutlined />, disabled: mutationLoading || loading, danger: true },
                    ] : []),
                ],
                onClick: ({ key: action }) => {
                    if (action === 'json') setJsonRow(row);
                    if (action === 'edit') startRowEdit(rowKey(row));
                    if (action === 'delete') removeRow(row);
                },
            }}><button type="button" className="flow-row-menu" aria-label="Row actions" title="Row actions"><span aria-hidden="true">⋯</span></button></Dropdown>;
        },
    });

    useEffect(() => {
        workspaceActive.current = true;
        refreshCatalog().catch((error) => { if (workspaceActive.current) setRequestError(errorText(error)); });
        return () => { workspaceActive.current = false; catalogRequest.current++; selection.current = ''; };
    }, []);
    const refreshWorkspace = () => {
        if (mutationPending.current || editingRowKey || scriptDirty || apisDirty) { message.warning('Save or cancel current changes before refreshing'); return; }
        setRefreshVersion((value) => value + 1);
        refreshCatalog().catch((error) => { if (workspaceActive.current) setRequestError(errorText(error)); });
    };
    useEffect(() => {
        if (!catalogLoaded) return;
        const params = new URLSearchParams(search);
        const requested = params.get('schema');
        const schema = schemas.find((item) => item.key === requested)?.key || schemas[0]?.key || '';
        setSelectedSchema(schema);
        if (requested !== schema && (requested || schema)) {
            if (params.get('create') === 'table') params.delete('create');
            if (schema) params.set('schema', schema); else params.delete('schema');
            params.delete('table');
            navigate(`${workspacePath}${params.size ? `?${params.toString()}` : ''}`, { replace: true });
        }
    }, [search, schemas, catalogLoaded, navigate, workspacePath]);
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
            setClosedTables(new Set()); setPartition(undefined); setPageNumber(1); setEditingRowKey(undefined); setEditingDraft(null); setData(undefined); setStream(undefined); setOffsets([]); setInputTopics([]); setScript(''); setOpenApis([]);
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
        setFilterText(''); setFilterRules([{ id: 'rule-1', field: '', operator: '=', value: '', active: true, join: 'and' }]); setSortField(''); setSortConditions([]); setAppliedQuery({ filters: [], sorts: [], search: '' });
        setPreviewError(''); setPreviewUnavailable(false); setPreviewPage(undefined); setRepairFields([]);
    }, [selectedSchema, table.name, selectedTableAvailable]);
    useEffect(() => {
        if (!selectedTableAvailable || !showPreview) return;
        let active = true;
        fetchTablePartitions(selectedSchema, table.name)
            .then((items) => { if (active) setPartitions(items); })
            .catch((error) => { if (active) message.error(errorText(error)); });
        return () => { active = false; };
    }, [selectedSchema, table.name, selectedTableAvailable, showPreview]);
    useEffect(() => {
        if (!selectedTableAvailable || (isSchemas && !scriptOpen) || scriptLoaded || scriptDirty) return;
        const version = ++metadataRequest.current;
        const valid = () => version === metadataRequest.current;
        Promise.all([fetchLayerScript(selectedSchema, table.name), fetchHandlerCode(selectedSchema, table.name)]).then(([result, draft]) => { if (valid()) { const code = draft.code || result.code || ''; setScript(code); setSavedScript(code); setScriptLoaded(true); setScriptNote(draft.code && draft.code !== result.code ? 'Saved draft' : result.available ? result.path || 'Deployed handler' : result.reason || 'No deployed handler'); } }).catch((error) => { if (valid()) setScriptNote(errorText(error)); });
        return () => { metadataRequest.current++; };
    }, [selectedSchema, table.name, selectedTableAvailable, isSchemas, scriptOpen, scriptLoaded, scriptDirty]);
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
        if (!selectedTableAvailable || !showPreview) { setLoading(false); return; }
        setLoading(true); setPreviewError(''); setPreviewUnavailable(false);
        const controller = new AbortController();
        const offset = (pageNumber - 1) * pageSize;
        const query = { ...appliedQuery, limit: pageSize, offset, partition };
        const timer = window.setTimeout(async () => {
            try {
                if (isSchemas) {
                    const result = await queryExplorerPreview(selectedSchema, table.name, query, controller.signal);
                    if (version !== dataRequest.current) return;
                    setData(result); setPreviewPage({ offset, limit: pageSize, result });
                } else {
                    const result = await queryEtlTable(selectedSchema, table.name, query, controller.signal);
                    if (version !== dataRequest.current) return;
                    if (!result.rows.length && pageNumber > 1) {
                        setPageNumber(result.totalEstimated ? 1 : Math.max(1, Math.min(pageNumber - 1, Math.ceil((result.total ?? 0) / pageSize))));
                    } else setData(result);
                }
            } catch (error) {
                if (version !== dataRequest.current || controller.signal.aborted) return;
                if (isSchemas) {
                    setData((current) => current ? { ...current, rows: [], rowCount: 0 } : undefined);
                    setPreviewUnavailable((error as { response?: { status?: number } }).response?.status === 503);
                } else setData(undefined);
                setPreviewPage(undefined); setPreviewError(errorText(error));
            } finally {
                if (version === dataRequest.current) setLoading(false);
            }
        }, 100);
        return () => { window.clearTimeout(timer); controller.abort(); dataRequest.current++; };
    }, [selectedSchema, table.name, partition, appliedQuery, pageNumber, pageSize, refreshVersion, previewRevision, selectedTableAvailable, showPreview, isSchemas]);
    useEffect(() => {
        if (!isSchemas || !showPreview || !selectedTableAvailable || !previewError || filterFields.length) return;
        const controller = new AbortController();
        fetchEtlTableColumns(selectedSchema, table.name, controller.signal).then((columns) => {
            if (!controller.signal.aborted) setRepairFields(columns.map((column) => column.name));
        }).catch(() => { /* The existing preview error remains visible; metadata cannot repair it. */ });
        return () => controller.abort();
    }, [isSchemas, showPreview, selectedTableAvailable, selectedSchema, table.name, previewError, filterFields.length]);
    useEffect(() => {
        const scroll = previewViewport.current?.querySelector('.ui-table-content');
        if (previewPage && scroll) scroll.scrollTop = 0;
    }, [previewPage]);
    useEffect(() => {
        if (!selectedTableAvailable || !showStream) { setStreamLoading(false); return; }
        setStreamLoading(true);
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
            finally { if (active) { setStreamLoading(false); timer = window.setTimeout(refresh, 5000); } }
        };
        void refresh();
        return () => { active = false; streamRequest.current++; window.clearTimeout(timer); };
    }, [selectedSchema, table.name, inputTopics[activeTopicIndex], selectedTableAvailable, showStream]);
    const selectResourceView = (view: typeof resourceView) => {
        if (editingRowKey) { message.warning('Save or cancel the current row edit first'); return; }
        if (view === resourceView) return;
        const params = new URLSearchParams(search);
        if (view === 'preview') params.delete('view');
        else params.set('view', view);
        navigate(`${workspacePath}?${params.toString()}`);
    };
    const selectSchema = (schema: string) => {
        if (isSchemas && (editingRowKey || scriptDirty || apisDirty || mutationPending.current)) {
            message.warning('Save or cancel current changes before switching schemas'); return;
        }
        const params = new URLSearchParams(search);
        params.set('schema', schema);
        params.delete('table');
        navigate(`${workspacePath}?${params.toString()}`);
    };
    const selectTable = (selected: LayerTable) => {
        if (isSchemas && (scriptDirty || apisDirty || mutationPending.current)) {
            message.warning('Save or cancel current changes before switching tables'); return;
        }
        if (editingRowKey) { message.warning('Save or cancel the current row edit first'); return; }
        setClosedTables((current) => { const next = new Set(current); next.delete(selected.name); return next; });
        const params = new URLSearchParams(search); params.delete('create'); params.set('schema', selectedSchema); params.set('table', selected.name);
        navigate(`${workspacePath}?${params.toString()}`, { replace: true });
    };
    const closeTable = (name: string) => {
        if (editingRowKey && table.name === name) { message.warning('Save or cancel the current row edit first'); return; }
        const remaining = tables.filter((item) => item.name !== name);
        setClosedTables((current) => new Set(current).add(name));
        if (table.name !== name) return;
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

    const apiPanel = (
        <section className={`flow-filter-api-section${isSchemas ? ' is-docked' : ''}`}>
            <div className="flow-filter-api-title"><strong>{isSchemas ? 'Published APIs' : 'Open APIs'}</strong><span className="flow-api-status flow-api-status--enabled">Token required · Validity window enforced</span>{!isSchemas && <button type="button" className="flow-filter-api-close" aria-label="Close APIs" onClick={() => setApiListOpen(false)}><CloseOutlined /></button>}</div>
            {apiCount === 0 ? <div className="flow-filter-api-empty"><span>{apisLoading ? 'Loading published APIs…' : 'No published APIs'}</span><button type="button" className="flow-filter-api-add" disabled={mutationLoading || apisLoading} onClick={addApi}><PlusOutlined /> Add API</button></div> : <>
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
        </section>
    );

    return <div className={`flow-layers flow-layers--tagged${isSchemas ? ' flow-schema-workbench' : ''}`}>
        <div  className="flow-layer-layout">
            {isSchemas ? <ExplorerCatalog schemas={schemas} tables={tablesLoadedSchema === selectedSchema ? serverTables : []}
                selectedSchema={selectedSchema} selectedTable={table.name} catalogLoaded={catalogLoaded} tablesLoading={tablesLoading}
                requestError={requestError} onCreateTable={canCreateTable ? openTableWizard : undefined} onRefresh={refreshWorkspace} onSchema={selectSchema} onTable={selectTable} /> :
            <aside className="flow-layer-tree flow-ontology-schema-sidebar">
                <div className="flow-tree-label flow-tree-label--actions"><span>Schemas</span><button type="button" aria-label="Refresh schemas" title="Refresh schemas" onClick={refreshWorkspace}><ReloadOutlined /></button></div>
                <Input className="flow-ontology-schema-search" aria-label="Search schemas" placeholder="Search schemas" prefix={<SearchOutlined />} value={schemaSearch} onChange={(event) => setSchemaSearch(event.target.value)} allowClear />
                <div className="flow-ontology-schema-list">
                    {visibleSchemas.map((item) => <button type="button" className={`flow-connector-tree-item flow-ontology-schema-item ${selectedSchema === item.key ? 'is-active' : ''}`} key={item.key} style={{ '--schema-color': schemaColor(item.key) } as React.CSSProperties} aria-current={selectedSchema === item.key ? 'page' : undefined} onClick={() => selectSchema(item.key)}><i className="flow-schema-color-dot" aria-hidden="true" /><span className="flow-connector-tree-name"><strong>{item.alias || item.label}</strong><small>{item.key}</small></span><em>{item.tables ?? 0}</em></button>)}
                    {!visibleSchemas.length && <p className="flow-schema-search-empty">{!catalogLoaded && !requestError ? 'Loading schemas…' : schemas.length ? 'No matching schemas' : 'No schemas available'}</p>}
                </div>
                <div className="flow-schema-sidebar-summary">{schemas.length} schema resources</div>
            </aside>}
            <section  className="flow-layer-main">
                {(loading || tablesLoading) && <Spin size="small" />}
                {requestError && <div role="alert" style={{ padding: 12, color: '#ff7875' }}>{requestError} <Button size="small" onClick={() => { setRefreshVersion((value) => value + 1); refreshCatalog().catch((error) => setRequestError(errorText(error))); }}>Retry</Button></div>}
                <nav className="flow-asset-breadcrumb" aria-label="Breadcrumb"><button type="button" onClick={() => navigate('/ontology')}>Ontology Foundry</button><RightOutlined /><span>{pathname === '/ontology/transforms' ? 'Transformations' : 'Explorer'}</span>{selectedSchema && <><RightOutlined /><strong>{selectedSchema}</strong></>}{isSchemas && table.name && <><RightOutlined /><strong aria-current="page" title={table.name}>{table.name}</strong></>}</nav>
                {(!isSchemas || !table.name) && <div className="flow-layer-overview" style={{ '--schema-color': selectedSchema ? schemaColor(selectedSchema) : undefined } as React.CSSProperties}>
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
                </div>}
                {!isSchemas && <div className="flow-table-switcher">
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
                    <button  type="button"  className="flow-table-tab-menu"  aria-label={isSchemas ? 'Browse tables' : 'Select open table'}>{isSchemas ? <><SearchOutlined /><span>Browse tables</span></> : <ArrowDownOutlined />}</button>
                </Dropdown>
                <div  className="flow-table-tabs"  ref={tableTabsRef}  role="tablist"  aria-label={`${selectedSchema}  tables`}>
                    {tables.map((item)  =>  {  const  active  =  table.name  ===  item.name;  return  <button  type="button"  role="tab"  aria-selected={active}  className={active  ?  'is-active'  :  ''}  key={item.name}  title={item.name} onKeyDown={(event) => { if (event.key === 'Delete') { event.preventDefault(); closeTable(item.name); } }} onClick={() => selectTable(item)}><span  className="flow-tab-favicon"><TemplateGlyph  kind={inferSiteKind(item.dataType || '', item.name)}  source={item.name}  /></span><span  className="flow-tab-label">{item.name}</span><span  className="flow-tab-close"  title={`Close ${item.name} (Delete)`} onClick={(event) => { event.stopPropagation(); closeTable(item.name); }}>&times;</span></button>;  })}
                </div>
                </div>}
                {!selectedTableAvailable ? <div className="flow-workspace-empty" role="status"><DatabaseOutlined /><h3>{tablesLoading || !catalogLoaded && !requestError ? 'Loading schema resources…' : selectedSchema ? serverTables.length ? 'No table selected' : 'No tables in this schema' : 'No schema selected'}</h3><p>{schemaNode?.status === 'stopped' ? 'This schema is not available in the connected database.' : serverTables.length ? 'Select a table from the catalog.' : 'Select a schema to explore its tables and data.'}</p></div> : <div className="flow-inspector">
                    <div  className="flow-table-header">
                        <div className="flow-table-header-left">
                            {isSchemas && <div className="flow-schema-asset-icon"><TemplateGlyph kind={inferSiteKind(table.dataType || '', table.name)} source={table.name} /></div>}
                            <div><h2>{table.name}</h2>{isSchemas && <p className="flow-schema-asset-source">{schemaNode?.database ? <span>{schemaNode.database}</span> : null}<code>{tableDescription}</code></p>}</div>
                        </div>
                        {!isSchemas && <div className="flow-stream-pipeline">
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
                        </div>}
                        <div className="flow-table-header-right">
                            <div className="flow-health-badge" title={isSchemas ? undefined : stream?.reason || streamError}><i className={`flow-dot ${(!isSchemas && stream?.available) || schemaNode?.status === 'running' ? 'flow-dot--green' : ''}`} /><span>{!isSchemas && stream?.available ? 'Stream available' : schemaNode?.status === 'running' ? 'Schema accessible' : 'Unavailable'}</span></div>
                            {selectedSchema === 'ts_task' && table.name === 'task_vulnerability' && <Button size="small" onClick={() => setKeywordsOpen(true)}>Satellite extraction</Button>}
                            {canManage && !isSchemas && <Button size="small" disabled={!stream?.available || !offsets.length} onClick={() => { setOffsetPartition(offsets[0]?.partition); setOffset(offsets[0]?.offset); setTopicDraft(stream?.topic || ''); setOffsetOpen(true); }}>Adjust offset</Button>}
                        </div>
                    </div>
                    {!isSchemas && <p className="flow-table-desc">{tableDescription}{streamError ? ` · ${streamError}` : !stream?.available ? ` · ${stream?.reason || 'Stream unavailable'}` : ''}</p>}
                    {isSchemas && <nav className="flow-schema-view-tabs" aria-label="Table view">
                        <button type="button" aria-pressed={resourceView === 'preview'} onClick={() => selectResourceView('preview')}><DatabaseOutlined /> Data preview</button>
                        <button type="button" aria-pressed={resourceView === 'schema'} onClick={() => selectResourceView('schema')}><SettingOutlined /> Schema</button>
                        <button type="button" aria-pressed={resourceView === 'stream'} onClick={() => selectResourceView('stream')}><DownloadOutlined /> Stream</button>
                        <button type="button" aria-pressed={resourceView === 'evidence'} onClick={() => selectResourceView('evidence')}><AlertOutlined /> Evidence</button>
                        {canManage && <button type="button" aria-pressed={resourceView === 'api'} onClick={() => selectResourceView('api')}><CodeOutlined /> Published APIs</button>}
                    </nav>}
                    <div className="flow-inspector-body" ref={previewViewport}>
                        {showIndexPreview && (canManage ? <SandboxIndexPreview /> :
                            <div className="flow-schema-state" role="status">Index-aware sandbox preview is available to administrators only.</div>)}
                        {showPreview && <div className="flow-navicat-toolbar">
                            <button type="button" className={`flow-tool ${filterOpen ? 'is-active' : ''}`} aria-expanded={filterOpen} onClick={() => activateTool('filter')}>
                                {isSchemas && <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.4" aria-hidden="true"><path d="M2 3h12L9 8.5v4l-2 1v-5L2 3Z" strokeLinejoin="round" /></svg>}
                                {isSchemas ? 'Filter & sort' : canManage ? 'Filter & API' : 'Filter'}
                                {isSchemas && filterCount + sortCount > 0 && <span className="flow-schema-filter-count">{filterCount + sortCount}</span>}
                            </button>
                            {!isSchemas && <button type="button" className={`flow-tool ${scriptOpen ? 'is-active' : ''}`} onClick={() => activateTool('script')}>Layer script</button>}
                            {isSchemas && <>
                                {partitions.length > 0 && <button type="button" className={`flow-tool ${partitionOpen || partition ? 'is-active' : ''}`} aria-expanded={partitionOpen} onClick={() => activateTool('partition')}>{partition ? `Partition: ${partition}` : 'Partitions'}</button>}
                                <span className="flow-schema-preview-summary" role="status">{loading ? 'Loading preview…' : data ? `${dataRows.length.toLocaleString('en-US')} shown · ${fieldCount.toLocaleString('en-US')} columns` : 'Preview unavailable'}</span>
                                <button type="button" className="flow-tool flow-schema-reload" aria-label="Refresh table preview" title="Refresh table preview" onClick={refreshWorkspace}><ReloadOutlined /></button>
                            </>}
                        </div>}
                        {isSchemas && showPreview && (filterCount > 0 || sortCount > 0) && <div className="flow-schema-applied flow-query-chips" aria-label="Applied conditions">
                            <span>Applied: {filterCount} {filterCount === 1 ? 'filter' : 'filters'} · {sortCount} {sortCount === 1 ? 'sort' : 'sorts'}</span>
                            {appliedQuery.search?.trim() && queryChip('search', 'search', `Search: ${appliedQuery.search}`, () => {
                                if (!canChangePreview()) return;
                                if (filterText === appliedQuery.search) setFilterText('');
                                setAppliedQuery((current) => ({ ...current, search: '' })); setPageNumber(1);
                            })}
                            {appliedChips}
                            {appliedQuery.sorts.map((sort) => queryChip(`sort-${sort.field}`, 'sort', `${sort.field} ${sort.direction === 'asc' ? '↑' : '↓'}`, () => {
                                if (requireAppliedSortDraft()) updateExplorerSorts(appliedQuery.sorts.filter((item) => item.field !== sort.field));
                            }))}
                        </div>}
                        {isSchemas && resourceView === 'schema' && <ExplorerTableSchema key={`${selectedSchema}.${table.name}`} schema={selectedSchema} table={table.name} />}
                        {isSchemas && resourceView === 'evidence' && <ExplorerTableEvidence key={tableDescription} schema={selectedSchema} table={table.name} canManage={canManage} />}
                        {isSchemas && resourceView === 'stream' && <section className="flow-schema-details flow-explorer-stream" aria-label="Stream status">
                            <header>
                                <div><h3>Stream</h3><p>Consumer status and partition offsets for this table.</p></div>
                                <div className="flow-explorer-stream-actions">
                                    <Button size="small" icon={<CodeOutlined />} onClick={() => activateTool('script')}>Layer script</Button>
                                    {canManage && <Button size="small" disabled={!stream?.available || !offsets.length} onClick={() => { setOffsetPartition(offsets[0]?.partition); setOffset(offsets[0]?.offset); setTopicDraft(stream?.topic || ''); setOffsetOpen(true); }}>Adjust offset</Button>}
                                </div>
                            </header>
                            {streamLoading && !stream && !streamError ? <div className="flow-schema-state" role="status">Checking stream status…</div>
                                : streamError ? <div className="flow-schema-state" role="alert"><strong>Stream status unavailable</strong><p>{streamError}</p></div>
                                : !stream?.available ? <div className="flow-schema-state" role="status"><strong>{stream?.topic ? 'Stream unavailable' : 'No active stream'}</strong><p>{stream?.reason || 'There is no stream information for this table.'}</p></div>
                                : <>
                                    <dl className="flow-explorer-stream-meta"><div><dt>Topic</dt><dd>{stream.topic || 'Not reported'}</dd></div><div><dt>Consumer group</dt><dd>{stream.consumerGroup || 'Not reported'}</dd></div><div><dt>Throughput</dt><dd title={stream.throughputReason}>{stream.throughput == null ? 'Unavailable' : `${stream.throughput.toLocaleString()} events/s`}</dd></div></dl>
                                    {offsets.length ? <table><thead><tr><th scope="col">Partition</th><th scope="col">Offset</th></tr></thead><tbody>{offsets.map((item) => <tr key={item.partition}><td>{item.partition}</td><td>{item.offset.toLocaleString()}</td></tr>)}</tbody></table> : <div className="flow-schema-state">No consumer offsets reported.</div>}
                                </>}
                        </section>}
                        {showPreview && previewError && <div className="flow-schema-state flow-preview-error" role="alert">
                            <strong>Data preview unavailable</strong><p>{previewError}</p>
                            {previewUnavailable && <p>For an expensive query, narrow the filters or select a partition before trying again.</p>}
                            <Button size="small" onClick={isSchemas ? () => setPreviewRevision((revision) => revision + 1) : refreshWorkspace}>Retry preview</Button>
                            {isSchemas && pageNumber > 1 && <Button size="small" onClick={() => setPageNumber(1)}>Back to first page</Button>}
                        </div>}
                        {showPreview && filterOpen && <div className="flow-filter-card">
                                <div className={isSchemas ? 'flow-schema-filter-search' : undefined}>
                                    {isSchemas && <span>Search across columns</span>}
                                    <Input.Search aria-label="Search all columns" size="small" placeholder="Search all columns" value={filterText} onChange={(event) => setFilterText(event.target.value)} onSearch={applyFilters} />
                                </div>
                                {isSchemas && <div className="flow-schema-filter-label">Column filters</div>}
                                <div className="flow-filter-rules">{filterRules.map((rule,  index)  =>  <div  className={['flow-filter-rule', rule.id.startsWith('group-open-') ? 'is-group-open' : '', rule.id.startsWith('group-close-') ? 'is-group-close' : '', rule.id.startsWith('group-filter-') ? 'is-nested' : '', rule.id.includes('nested-') ? 'is-group-nested' : '', `is-group-depth-${filterNestingDepth(rule.id)}`, filterMoving?.id === rule.id ? `is-moving is-moving-${filterMoving.direction}` : filterMoving?.peerId === rule.id ? `is-moving is-moving-${filterMoving.direction === 'up' ? 'down' : 'up'}` : '', rule.active === false ? 'is-inactive' : ''].filter(Boolean).join(' ')}  style={{  '--filter-depth':  filterNestingDepth(rule.id)  }  as  React.CSSProperties}  key={rule.id} id={isSchemas ? `explorer-filter-${rule.id}` : undefined}><span  className="flow-filter-row-check">{index  ===  0  ?  <FilterCheckbox  checked={rule.active  !==  false}  onChange={(event)  =>  setFilterRules((rows)  =>  rows.map((item,  itemIndex)  =>  rule.id.startsWith('group-open-')  ?  (()  =>  {  const  ruleIndex  =  rows.findIndex((entry)  =>  entry.id  ===  rule.id);  const  bounds  =  filterGroupBounds(rows,  ruleIndex);  return  bounds  &&  ruleIndex  <=  itemIndex  &&  itemIndex  <=  bounds.end  ?  {  ...item,  active:  event.target.checked  }  :  item;  })()  :  item.id  ===  rule.id  ?  {  ...item,  active:  event.target.checked  }  :  item))}  />  :  <FilterCheckbox  checked={rule.active  !==  false}  onChange={(event)  =>  setFilterRules((rows)  =>  rows.map((item,  itemIndex)  =>  rule.id.startsWith('group-open-')  ?  (()  =>  {  const  ruleIndex  =  rows.findIndex((entry)  =>  entry.id  ===  rule.id);  const  bounds  =  filterGroupBounds(rows,  ruleIndex);  return  bounds  &&  ruleIndex  <=  itemIndex  &&  itemIndex  <=  bounds.end  ?  {  ...item,  active:  event.target.checked  }  :  item;  })()  :  item.id  ===  rule.id  ?  {  ...item,  active:  event.target.checked  }  :  item))}  />}</span><span  className="flow-filter-row-content"><FilterFieldPicker fields={filterFields} value={rule.field}  onChange={(field)  =>  setFilterRules((rows)  =>  rows.map((item)  =>  item.id  ===  rule.id  ?  {  ...item,  field  }  :  item))}  /><FilterOperatorSpan  value={rule.operator}  onChange={(operator)  =>  setFilterRules((rows)  =>  rows.map((item)  =>  item.id  ===  rule.id  ?  {  ...item,  operator  }  :  item))}  /><FilterValuePicker suggestions={[...new Set(dataRows.map((row) => formatCell(row[rule.field])))]} value={rule.value}  onChange={(value)  =>  setFilterRules((rows)  =>  rows.map((item)  =>  item.id  ===  rule.id  ?  {  ...item,  value  }  :  item))}  />{index  <  filterRules.length  -  1  &&  !filterRules[index  +  1]?.id.startsWith('group-close-')  &&  <span  className="flow-filter-join"  role="button"  tabIndex={0}  onClick={()  =>  setFilterRules((rows)  =>  rows.map((item)  =>  item.id  ===  rule.id  ?  {  ...item,  join:  item.join  ===  'or'  ?  'and'  :  'or'  }  :  item))}>{rule.join  ||  'and'}</span>}</span>{(index  ===  filterRules.length  - 1  ||  rule.id.startsWith('group-close-')  ||  filterRules[index  + 1]?.id.startsWith('group-close-'))  &&  <span  className="flow-filter-row-actions">{rule.id.startsWith('group-close-')  &&  index  <  filterRules.length  -  1  &&  !filterRules[index  + 1]?.id.startsWith('group-close-')  &&  <span  className="flow-filter-join flow-filter-close-join"  role="button"  tabIndex={0}  onClick={()  =>  setFilterRules((rows)  =>  rows.map((item)  =>  item.id  ===  rule.id  ?  {  ...item,  join:  item.join  ===  'or'  ?  'and'  :  'or'  }  :  item))}>{rule.join  ||  'and'}</span>}<button  type="button"  className="flow-filter-mini" aria-label="Add filter condition" title="Add filter condition" onClick={()  =>  setFilterRules((rows)  =>  {  const  next  =  {  id:  ((rule.id.startsWith('group-filter-')  ||  rule.id.startsWith('group-close-'))  &&  filterNestingDepth(rule.id)  >  0)  ?  `group-filter-${'nested-'.repeat(filterNestingDepth(rule.id))}${randomId()}`  :  randomId(),  field: filterFields[0] || '',  operator:  '=',  value:  '',  active:  true,  join:  'and'  };  const  cleanRows  =  rows.length  ===  1  &&  rows[0].id === 'placeholder-rule'  ?  []  :  rows;  const  closeIndex  =  rule.id.startsWith('group-close-')  ?  index  :  findContainingClose(cleanRows,  index);  if  (rule.id.startsWith('group-close-'))  return  [...cleanRows.slice(0, index  +  1), next, ...cleanRows.slice(index  +  1)];  if  (closeIndex  >= 0)  return  [...cleanRows.slice(0, closeIndex), next, ...cleanRows.slice(closeIndex)];  return  [...cleanRows, next];  })}><FilterAddIcon /></button><button  type="button"  className="flow-filter-mini"  aria-label="Add filter group" title="Add filter group" onClick={()  =>  setFilterRules((rows)  =>  {  const  groupId  =  randomId();  const  cleanRows  =  rows.length  ===  1  &&  rows[0].id  ===  'placeholder-rule'  ?  []  :  rows;  const  nested  =  rule.id.startsWith('group-filter-')  ||  rule.id.startsWith('group-close-');  const  depth  =  rule.id.match(/nested-/g)?.length  ||  0;  const  currentPrefix  =  'nested-'.repeat(depth);  const  childPrefix  =  'nested-'.repeat(depth  +  1);  const  additions  =  [{  id:  `group-open-${currentPrefix}${groupId}`,  field:  '',  operator:  '=',  value:  '',  active:  true,  join:  'and'  },  {  id:  `group-filter-${childPrefix}${groupId}`,  field: filterFields[0] || '',  operator:  '=',  value:  '',  active:  true,  join:  'and'  },  {  id:  `group-close-${currentPrefix}${groupId}`,  field:  '',  operator:  '=',  value:  '',  active:  true,  join:  'and'  }];  const  closeIndex  =  nested  ?  findContainingClose(cleanRows,  index)  :  -1;  return  closeIndex  >=  0  ?  [...cleanRows.slice(0, closeIndex), ...additions, ...cleanRows.slice(closeIndex)]  :  [...cleanRows, ...additions];  })}><FilterGroupIcon /></button>{rule.id === 'placeholder-rule' && <span className="flow-filter-placeholder">Select + to add a filter condition</span>}</span>}<span  className="flow-filter-row-end-actions"><button  type="button"  className="flow-filter-reorder"  aria-label="Move  up"  disabled={index  ===  0}  onClick={()  =>  {  if  (index  === 0)  return;  const  movingId  =  rule.id;  setFilterMoving({  id:  movingId,  peerId:  filterRules[index  -  1].id,  direction:  'up'  });  window.setTimeout(()  =>  {  setFilterRules((rows)  =>  {  const  currentIndex  =  rows.findIndex((item)  =>  item.id  ===  movingId);  if  (currentIndex  <=  0)  return  rows;  const  next  =  [...rows];  [next[currentIndex  -  1],  next[currentIndex]]  =  [next[currentIndex],  next[currentIndex  - 1]];  return  moveFilterRows(rows, currentIndex, 'up');  });  setFilterMoving(null);  },  180);  }}><ArrowUpOutlined  /></button><button  type="button"  className="flow-filter-reorder"  aria-label="Move  down"  disabled={index  ===  filterRules.length  -  1}  onClick={()  =>  {  if  (index  >=  filterRules.length  -  1)  return;  const  movingId  =  rule.id;  setFilterMoving({  id:  movingId,  peerId:  filterRules[index  +  1].id,  direction:  'down'  });  window.setTimeout(()  =>  {  setFilterRules((rows)  =>  moveFilterRows(rows, rows.findIndex((item)  =>  item.id  ===  movingId), 'down'));  setFilterMoving(null);  },  180);  }}><ArrowDownOutlined  /></button><button  type="button"  className="flow-filter-remove" aria-label="Remove filter condition" title="Remove filter condition" onClick={()  =>  setFilterRules((rows)  =>  {  if  (rows.length  ===  1)  return  [{  id:  'placeholder-rule',  field:  '',  operator:  '=',  value:  '',  active:  false,  join:  'and',  placeholder:  true  }];  const  next  =  removeFilterRow(rows, index);  return  next.length  ?  next  :  [{  id:  'placeholder-rule',  field:  '',  operator:  '=',  value:  '',  active:  false,  join:  'and',  placeholder:  true  }];  })}><CloseOutlined /></button></span></div>)}</div>
                                <div  className="flow-filter-sort"><strong>Sort by</strong><div className="flow-sort-conditions-viewport" ref={sortConditionsRef}><div className="flow-sort-conditions-track"><span  className="flow-filter-sort-field-wrap"><FilterFieldPicker fields={filterFields} value={sortField}  onChange={(field) => { setSortField(field); setAppliedQuery((current) => ({ ...current, sorts: [...(field ? [{ field, direction: sortDirection }] : []), ...sortConditions.filter((item) => item.field)] })); setPageNumber(1); }}  className="flow-filter-sort-field"  labelClassName="flow-filter-sort-field-label"  trailing={<><button  type="button"  className="flow-filter-sort-direction-toggle"  aria-label={sortDirection  ===  'asc'  ?  'Switch to descending'  :  'Switch to ascending'}  onClick={(event)  =>  {  event.stopPropagation();  const  nextDirection:  'asc'  |  'desc'  =  sortDirection  ===  'asc'  ?  'desc'  :  'asc';  setSortDirection(nextDirection);  setAppliedQuery((current)  =>  ({  ...current,  sorts:  [...(sortField  ?  [{  field:  sortField,  direction:  nextDirection  }]  :  []),  ...sortConditions.filter((item)  =>  item.field)]  }));  setPageNumber(1);  }}><SortDirectionIcon  direction={sortDirection}  /></button><SortActionMenu onDelete={() => { setSortField(''); setAppliedQuery((current) => ({ ...current, sorts: [...sortConditions.filter((item) => item.field)] })); setPageNumber(1); }} onClear={() => { setSortField(''); setSortConditions([]); setAppliedQuery((current) => ({ ...current, sorts: [] })); setPageNumber(1); }} /></>}  /></span>{sortConditions.map((condition, index) => <span className="flow-filter-sort-field-wrap" key={`${condition.field}-${index}`}><FilterFieldPicker fields={filterFields} value={condition.field} onChange={(field) => setSortConditions((items) => items.map((item, itemIndex) => itemIndex === index ? { ...item, field } : item))} className="flow-filter-sort-field" labelClassName="flow-filter-sort-field-label" trailing={<><button type="button" className="flow-filter-sort-direction-toggle" aria-label="Toggle sort direction" onClick={(event) => { event.stopPropagation(); setSortConditions((items) => items.map((item, itemIndex) => itemIndex === index ? { ...item, direction: item.direction === 'asc' ? 'desc' : 'asc' } : item)); }}><SortDirectionIcon direction={condition.direction} /></button><SortActionMenu onDelete={() => { const next = sortConditions.filter((_, itemIndex) => itemIndex !== index); setSortConditions(next); setAppliedQuery((current) => ({ ...current, sorts: [...(sortField ? [{ field: sortField, direction: sortDirection }] : []), ...next.filter((item) => item.field)] })); setPageNumber(1); }} onClear={() => { setSortField(''); setSortConditions([]); setAppliedQuery((current) => ({ ...current, sorts: [] })); setPageNumber(1); }} /></>} /></span>)}<button type="button" className="flow-filter-mini flow-filter-sort-add" aria-label="Add sort field" onClick={() => setSortConditions((conditions) => [...conditions, { field: filterFields[0] || '', direction: 'asc' }])}><FilterAddIcon /></button></div></div>{sortConditionsOverflow && <div className="flow-sort-scroll-buttons"><button type="button" aria-label="Scroll left" onClick={() => sortConditionsRef.current?.scrollBy({ left: -140, behavior: 'smooth' })}>‹</button><button type="button" aria-label="Scroll right" onClick={() => sortConditionsRef.current?.scrollBy({ left: 140, behavior: 'smooth' })}>›</button></div>}</div>
                                {isSchemas && !indexPreviewAsset && selectedTableAvailable && <ExplorerIndexCoverage schema={selectedSchema} table={table.name}
                                    rules={filterRules} columnTypes={data?.columnTypes} allowSample={canManage} conflictingOptions={Boolean(filterText || partition || sortField ||
                                        sortConditions.some(item => item.field) || appliedQuery.sorts.length)} />}
                                <div  className="flow-filter-apply"><button type="button" className="flow-filter-apply-button" onClick={applyFilters}>Apply filters</button><span className="flow-filter-edited-hint">Apply to update filters and pending sorts</span>{!isSchemas && canManage && <span className="flow-api-count"><button type="button" className="flow-api-count-number" onClick={() => { setApiListOpen(true); setFilterOpen(true); }}>{apiCount}</button><span className="flow-api-count-label"> / APIs</span></span>}</div>
                                {!isSchemas && canManage && apiListOpen && apiPanel}
                            </div>}
                            {isSchemas && resourceView === 'api' && (canManage ? apiPanel : <div className="flow-schema-state" role="status">Published APIs are available to administrators only.</div>)}
                            {showPreview && partitionOpen &&  <div  className="flow-partition-tags">{partitions.map((item)  =>  <Tag.CheckableTag  key={item.name}  checked={partition  ===  item.name}  onChange={() => { if (editingRowKey) { message.warning('Save or cancel the current row edit first'); return; } setPartition(partition === item.name ? undefined : item.name); setPageNumber(1); }}>{item.name.replace('p2026_',  '')}</Tag.CheckableTag>)}</div>}
                            {showPreview && !previewError && (isSchemas ? <>
                                <ConfigProvider locale={enUS}><Table className="flow-data-table" size="small" loading={loading} pagination={false} rowKey={rowKey} scroll={{ x: 'max-content' }} dataSource={dataRows} columns={dataColumns} onRow={(row) => ({ onDoubleClick: () => startRowEdit(rowKey(row)), className: editingRowKey === rowKey(row) ? 'flow-row-editing' : undefined })} /></ConfigProvider>
                                {previewPage && <footer className="flow-preview-pager">
                                    {(!previewPage.result.stableOrder || previewPage.result.hasMore && !previewPage.result.canPageFurther) && <div className="flow-preview-pager-notices" role="status">
                                        {!previewPage.result.stableOrder && <span>No stable primary key. Rows may repeat or be skipped when paging.</span>}
                                        {previewPage.result.hasMore && !previewPage.result.canPageFurther && <span>Interactive preview limit reached. Narrow filters or choose a partition to continue.</span>}
                                    </div>}
                                    <nav aria-label="Preview pagination">
                                        <span className="flow-preview-range" aria-live="polite">{loading ? 'Loading preview…' : previewPage.result.rowCount
                                            ? `Rows ${(previewPage.offset + 1).toLocaleString('en-US')}–${(previewPage.offset + previewPage.result.rowCount).toLocaleString('en-US')}`
                                            : 'No rows on this page'}</span>
                                        <div className="flow-preview-page-controls">
                                            <label>Rows per page <select aria-label="Rows per page" value={pageSize} disabled={loading} onChange={(event) => {
                                                if (!canChangePreview()) return;
                                                setPageSize(Number(event.target.value)); setPageNumber(1);
                                            }}>{[10, 20, 50].map((count) => <option key={count} value={count}>{count}</option>)}</select></label>
                                            <Button size="small" disabled={loading || pageNumber <= 1} onClick={() => { if (canChangePreview()) setPageNumber(pageNumber - 1); }}>Previous</Button>
                                            <Button size="small" disabled={loading || previewPage.offset !== (pageNumber - 1) * pageSize || previewPage.limit !== pageSize || !previewPage.result.canPageFurther} onClick={() => { if (canChangePreview()) setPageNumber(pageNumber + 1); }}>Next</Button>
                                        </div>
                                    </nav>
                                </footer>}
                            </> : <ConfigProvider locale={enUS}><Table className="flow-data-table" size="small" loading={loading} pagination={{ current: pageNumber, pageSize, total: data?.total ?? data?.rowCount ?? 0, size: 'small', showTotal: (total: number, range: [number, number]) => `${range[0]}–${range[1]} / ${data?.totalEstimated ? '~' : ''}${total.toLocaleString('en-US')} rows${data?.totalEstimated ? ' (estimated)' : ''} · ${table.size} · ${fieldCount} fields`, showSizeChanger: true, pageSizeOptions: [10, 20, 50], onChange: (page, size) => { if (editingRowKey) { message.warning('Save or cancel the current row edit first'); return; } setPageNumber(size !== pageSize ? 1 : page); setPageSize(size); } }} rowKey={rowKey} scroll={{ x: 'max-content' }} dataSource={dataRows} columns={dataColumns} onRow={(row) => ({ onDoubleClick: () => startRowEdit(rowKey(row)), className: editingRowKey === rowKey(row) ? 'flow-row-editing' : undefined })} /></ConfigProvider>)}
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
        {isSchemas && canManage && createTableSchema && creationSchemas.some((item) => item.key === createTableSchema) && <NewObjectType variant="storage" schemas={creationSchemas.filter((item) => item.key === createTableSchema)} initialSchema={createTableSchema} onCancel={() => setCreateTableSchema('')} onOpenExisting={(object) => openWizardTable(object.schema, object.table)} onCreated={(schema, created) => openWizardTable(schema, created.name)} />}
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
    </div>;
};
export  default  Pipeline;













