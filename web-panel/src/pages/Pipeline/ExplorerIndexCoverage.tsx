import { useEffect, useRef, useState } from 'react';
import { Button } from '@/components/ui';
import { checkPreviewIndexCoverage } from '../../services/api';
import type { PreviewIndexCoverageOperator, PreviewIndexCoverageState } from '../../services/api';
import type { ExplorerFilter } from './explorerFilters';
import { useSandboxIdentity } from './SandboxMarkings';
import ExplorerIndexedIdSample from './ExplorerIndexedIdSample';

const operators: Partial<Record<string, PreviewIndexCoverageOperator>> = {
  '=': 'eq', '<': 'lt', '<=': 'lte', '>': 'gt', '>=': 'gte', '介于': 'between',
};
const eligibleType = /^(smallint|int2|integer|int4|bigint|int8|date|timestamp with time zone|timestamptz)$/i;
const labels: Record<PreviewIndexCoverageState, string> = {
  candidate: 'Leading index candidate found.',
  no_candidate: 'No supported leading index found. This filter may be slow.',
  unsupported: 'Index coverage is not assessed for this field or operator.',
  unavailable: 'Index coverage is unavailable for this selection.',
};

type Props = {
  schema: string; table: string; rules: ExplorerFilter[]; columnTypes?: Record<string, string>; conflictingOptions: boolean; allowSample?: boolean;
};
export default function ExplorerIndexCoverage({ schema, table, rules, columnTypes, conflictingOptions, allowSample }: Props) {
  const identity = useSandboxIdentity();
  const term = rules.length === 1 ? rules[0] : undefined;
  const operator = term && Object.prototype.hasOwnProperty.call(operators, term.operator) ? operators[term.operator] : undefined;
  const type = term && columnTypes?.[term.field]?.toLowerCase().replace(/\(\s*\d+(?:,\s*\d+)?\s*\)/g, '').trim();
  const supported = !conflictingOptions && term && term.active && term.join !== 'or' && !term.id.startsWith('group-') &&
    term.field && term.value !== '' && operator && type && eligibleType.test(type) &&
    (operator !== 'between' || term.value.split(',').length === 2 && term.value.split(',').every(item => item.trim()));
  if (!identity) return null;
  const input = supported && term && operator ? { field: term.field, operator } : undefined;
  // This key is only local UI state: changing a literal invalidates the prior check without sending the literal.
  return <>
    <CoverageCheck key={JSON.stringify([identity, schema, table, rules, type, conflictingOptions])}
      schema={schema} table={table} input={input} />
    {allowSample && <ExplorerIndexedIdSample key={JSON.stringify([identity, schema, table])} schema={schema} table={table}
      candidate={input && term ? { field: term.field, operator: term.operator, value: term.value } : undefined} />}
  </>;
}

function CoverageCheck({ schema, table, input }: {
  schema: string; table: string; input?: { field: string; operator: PreviewIndexCoverageOperator };
}) {
  const [state, setState] = useState<PreviewIndexCoverageState>();
  const [busy, setBusy] = useState(false);
  const pending = useRef<AbortController>();
  useEffect(() => () => pending.current?.abort(), []);
  const check = async () => {
    if (!input || pending.current) return;
    const request = new AbortController(); pending.current = request;
    setBusy(true); setState(undefined);
    try {
      const result = await checkPreviewIndexCoverage(schema, table, input, request.signal);
      if (!request.signal.aborted) setState(Object.prototype.hasOwnProperty.call(labels, result.state) ? result.state : 'unavailable');
    } catch {
      if (!request.signal.aborted) setState('unavailable');
    } finally {
      if (pending.current === request && !request.signal.aborted) { pending.current = undefined; setBusy(false); }
    }
  };
  return <section className="flow-filter-index-coverage" aria-label="Index coverage">
    <Button size="small" disabled={!input} loading={busy} onClick={() => void check()}>Check index coverage</Button>
    {!input && <span>Use one supported column condition without groups, OR, search, partition or custom sort.</span>}
    {busy && <span role="status">Checking catalog metadata…</span>}
    {state && <span role="status">{labels[state]}</span>}
    <p>Catalog metadata only. It does not run the filter, predict the query plan, or guarantee an index scan.</p>
  </section>;
}
