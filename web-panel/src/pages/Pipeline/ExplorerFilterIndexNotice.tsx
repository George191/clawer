import { useEffect, useState } from 'react';
import { Tooltip } from '@/components/ui';
import { ReloadOutlined, WarningOutlined } from '@/components/ui/icons';
import { checkPreviewIndexCoverage, type PreviewIndexCoverageInput, type PreviewIndexCoverageOperator, type PreviewIndexCoverageState } from '../../services/api';
import type { ExplorerSort } from '../../services/explorerLayouts';
import { getSessionState } from '../../services/session';
import type { ExplorerFilter } from './explorerFilters';
import { useSandboxIdentity } from './SandboxMarkings';
import './explorer-filter-index-notice.css';

const operators: Partial<Record<string, PreviewIndexCoverageOperator>> = {
  '=': 'eq', '<': 'lt', '<=': 'lte', '>': 'gt', '>=': 'gte', '介于': 'between',
};
const supportedType = /^(smallint|int2|integer|int4|bigint|int8|date|timestamp with time zone|timestamptz)$/;
const identifier = /^[a-z_][a-z0-9_]{0,62}$/;
const unsupportedDescription = 'This advisory checks integer, date and timezone-aware timestamp comparisons only. It does not assess this condition or rule out an available index.';
const noIndexDescription = 'No supported leading B-tree index matches this field and operator. Other index types or combined conditions may still help. This is a catalog advisory, not a query plan or a timeout prediction.';

type Scope = { schema: string; table: string };
type Props = Scope & { rule: ExplorerFilter; columnTypes?: Record<string, string>; assessable?: boolean };

export default function ExplorerFilterIndexNotice({ schema, table, rule, columnTypes, assessable = true }: Props) {
  const operator = rule.operator.replace(/\s+/g, ' ').trim();
  const hasValue = operator === '介于' ? rule.value.split(',').length === 2 && rule.value.split(',').every(value => value.trim()) : !!rule.value.trim();
  const complete = rule.active !== false && !!rule.field && !/^group-(open|close)-/.test(rule.id)
    && (hasValue || ['是 null', '不是 null', '是空的', '不是空的'].includes(operator));
  if (!complete) return null;
  const type = columnTypes?.[rule.field]?.toLowerCase().replace(/\(\s*\d+(?:,\s*\d+)?\s*\)/g, '').trim();
  const operation = Object.prototype.hasOwnProperty.call(operators, operator) ? operators[operator] : undefined;
  const supported = assessable && !rule.id.startsWith('group-')
    && !!type && supportedType.test(type) && identifier.test(rule.field) && !!operation;
  return <IndexNotice schema={schema} table={table} input={supported ? { field: rule.field, operator: operation! } : undefined}
    retryLabel={`Retry index check for ${rule.field}`} warning="No leading index · may be slow" noIndexDescription={noIndexDescription}
    unsupportedDescription={!assessable || rule.id.startsWith('group-') ? 'Grouped or OR filters require a combined assessment. This row advisory does not evaluate the full query plan or rule out an available index.' : unsupportedDescription} />;
}

export function ExplorerSortIndexNotice({ schema, table, sorts }: Scope & { sorts: ExplorerSort[] }) {
  const active = sorts.filter(sort => sort.field);
  if (!active.length) return null;
  const supported = active.length <= 8 && active.every(sort => identifier.test(sort.field))
    && new Set(active.map(sort => sort.field)).size === active.length;
  return <IndexNotice schema={schema} table={table} input={supported ? { sorts: active } : undefined}
    retryLabel="Retry sort index check" warning="No matching sort index · may be slow"
    noIndexDescription="No supported B-tree index matches the full sort prefix, direction and default null order. Filters or other indexes may still help. This is a catalog advisory, not a query plan or a timeout prediction."
    unsupportedDescription="Sort index metadata is not assessed for this selection. Use up to eight distinct, supported physical columns. This does not rule out an available index." />;
}

function IndexNotice({ schema, table, input, retryLabel, warning, noIndexDescription, unsupportedDescription }: Scope & {
  input?: PreviewIndexCoverageInput; retryLabel: string; warning: string; noIndexDescription: string; unsupportedDescription: string;
}) {
  const identity = useSandboxIdentity();
  const [read, setRead] = useState<{ key: string; state: PreviewIndexCoverageState }>();
  const [attempt, retry] = useState(0);
  const shape = input ? JSON.stringify(input) : '';
  const key = identity && schema && table && shape ? JSON.stringify([identity, schema, table, shape, attempt]) : '';
  const current = read?.key === key ? read : undefined;
  useEffect(() => {
    if (!key) { setRead(undefined); return; }
    const controller = new AbortController();
    setRead(undefined);
    const settle = (state: PreviewIndexCoverageState) => {
      const session = getSessionState();
      if (!controller.signal.aborted && session.authenticated && !session.locked && session.identity === identity) setRead({ key, state });
    };
    const timer = window.setTimeout(() => {
      checkPreviewIndexCoverage(schema, table, JSON.parse(shape) as PreviewIndexCoverageInput, controller.signal)
        .then(result => settle(result.state), () => settle('unavailable'));
    }, 350);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [key, shape, schema, table, identity]);
  if (!identity || !schema || !table) return null;
  if (!input || current?.state === 'unsupported') return <Tooltip title={unsupportedDescription}>
    <span className="explorer-filter-index-notice" role="status">Index not assessed</span>
  </Tooltip>;
  if (current?.state === 'candidate') return null;
  if (current?.state === 'no_candidate') return <Tooltip title={noIndexDescription}>
    <span className="explorer-filter-index-notice is-warning" role="status"><WarningOutlined /><span>{warning}</span></span>
  </Tooltip>;
  if (current?.state === 'unavailable') return <Tooltip title="Index metadata could not be checked. The check may have timed out or be unavailable. Retry to verify it; this does not mean an index is missing.">
    <button type="button" className="explorer-filter-index-notice is-warning is-retry" aria-label={retryLabel} onClick={() => retry(value => value + 1)}><WarningOutlined /><span>Index check unavailable</span></button>
  </Tooltip>;
  return <span className="explorer-filter-index-notice" role="status"><ReloadOutlined spin /><span>Checking index…</span></span>;
}
