import { useEffect, useState } from 'react';
import { Tooltip } from '@/components/ui';
import { ReloadOutlined, WarningOutlined } from '@/components/ui/icons';
import { checkPreviewIndexCoverage, type PreviewIndexCoverageOperator, type PreviewIndexCoverageState } from '../../services/api';
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
const noIndexDescription = 'No supported leading B-tree index matches this field and operator. Other index types or combined conditions may still help. This is a catalog advisory, not a query plan.';

type Props = {
  schema: string; table: string; rule: ExplorerFilter;
  columnTypes?: Record<string, string>; assessable?: boolean;
};

export default function ExplorerFilterIndexNotice({ schema, table, rule, columnTypes, assessable = true }: Props) {
  const identity = useSandboxIdentity();
  const [read, setRead] = useState<{ key: string; state: PreviewIndexCoverageState }>();
  const [attempt, retry] = useState(0);
  const operator = rule.operator.replace(/\s+/g, ' ').trim();
  const hasValue = operator === '介于' ? rule.value.split(',').length === 2 && rule.value.split(',').every(value => value.trim()) : !!rule.value.trim();
  const complete = rule.active !== false && !!rule.field && !/^group-(open|close)-/.test(rule.id)
    && (hasValue || ['是 null', '不是 null', '是空的', '不是空的'].includes(operator));
  const type = columnTypes?.[rule.field]?.toLowerCase().replace(/\(\s*\d+(?:,\s*\d+)?\s*\)/g, '').trim();
  const operation = Object.prototype.hasOwnProperty.call(operators, operator) ? operators[operator] : undefined;
  const supported = assessable && !rule.id.startsWith('group-')
    && !!type && supportedType.test(type) && identifier.test(rule.field) && !!operation;
  const key = identity && schema && table && complete && supported
    ? JSON.stringify([identity, schema, table, rule.field, operation, attempt]) : '';
  const current = read?.key === key ? read : undefined;
  useEffect(() => {
    if (!key || !operation) { setRead(undefined); return; }
    const controller = new AbortController();
    setRead(undefined);
    const timer = window.setTimeout(() => {
      checkPreviewIndexCoverage(schema, table, { field: rule.field, operator: operation }, controller.signal).then(result => {
        const session = getSessionState();
        if (!controller.signal.aborted && session.authenticated && !session.locked && session.identity === identity) setRead({ key, state: result.state });
      }, () => {
        const session = getSessionState();
        if (!controller.signal.aborted && session.authenticated && !session.locked && session.identity === identity) setRead({ key, state: 'unavailable' });
      });
    }, 350);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [key, schema, table, rule.field, operation, identity]);
  if (!identity || !schema || !table || !complete) return null;
  if (!supported || current?.state === 'unsupported') return <Tooltip title={!assessable || rule.id.startsWith('group-') ? 'Grouped or OR filters require a combined assessment. This row advisory does not evaluate the full query plan or rule out an available index.' : unsupportedDescription}>
    <span className="explorer-filter-index-notice" role="status">Index not assessed</span>
  </Tooltip>;
  if (current?.state === 'candidate') return null;
  if (current?.state === 'no_candidate') return <Tooltip title={noIndexDescription}>
    <span className="explorer-filter-index-notice is-warning" role="status"><WarningOutlined /><span>No leading index</span></span>
  </Tooltip>;
  if (current?.state === 'unavailable') return <Tooltip title="Index metadata could not be checked. Retry this advisory without changing the filter.">
    <button type="button" className="explorer-filter-index-notice is-retry" aria-label={`Retry index check for ${rule.field}`} onClick={() => retry(value => value + 1)}><ReloadOutlined /><span>Index check unavailable</span></button>
  </Tooltip>;
  return <span className="explorer-filter-index-notice" role="status"><ReloadOutlined spin /><span>Checking index…</span></span>;
}
