import { useEffect, useRef, useState } from 'react';
import { Button } from '@/components/ui';
import { checkPreviewIndexCoverage } from '../../services/api';
import type { PreviewIndexCoverageInput, PreviewIndexCoverageOperator, PreviewIndexCoverageState, PreviewIndexCoverageTerm } from '../../services/api';
import type { ExplorerFilter } from './explorerFilters';
import { useSandboxIdentity } from './SandboxMarkings';
import ExplorerIndexedCompoundSample, { compoundSamplePredicate } from './ExplorerIndexedCompoundSample';
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

const integerBounds: Readonly<Record<string, readonly [bigint, bigint] | undefined>> = {
  smallint: [-32768n, 32767n], int2: [-32768n, 32767n],
  integer: [-2147483648n, 2147483647n], int4: [-2147483648n, 2147483647n],
  bigint: [-9223372036854775808n, 9223372036854775807n], int8: [-9223372036854775808n, 9223372036854775807n],
};

// Reject ambiguous, malformed or reversed literals locally. The catalog endpoint never receives them.
function comparableLiteral(type: string, raw: string): bigint | undefined {
  const value = raw.trim();
  const bounds = integerBounds[type];
  if (bounds) {
    if (!/^[+-]?\d{1,19}$/.test(value)) return;
    const parsed = BigInt(value);
    return parsed >= bounds[0] && parsed <= bounds[1] ? parsed : undefined;
  }
  const parts = type === 'date' ? /^(\d{4}-\d{2}-\d{2})$/.exec(value) :
    /^(\d{4}-\d{2}-\d{2})[T ]((?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d)?)(?:\.(\d{1,6}))?(Z|[+-](?:0\d|1[0-5]):[0-5]\d)$/.exec(value);
  if (!parts || parts[1].startsWith('0000')) return;
  const day = Date.parse(`${parts[1]}T00:00:00Z`);
  if (!Number.isFinite(day) || new Date(day).toISOString().slice(0, 10) !== parts[1]) return;
  if (type === 'date') return BigInt(day);
  if (parts[3] && parts[2].length !== 8) return;
  const time = Date.parse(`${parts[1]}T${parts[2]}${parts[4]}`);
  if (!Number.isFinite(time)) return;
  return BigInt(time) * 1000n + BigInt((parts[3] || '').padEnd(6, '0'));
}

const ruleType = (columnTypes: Record<string, string> | undefined, field: string) =>
  Object.prototype.hasOwnProperty.call(columnTypes ?? {}, field) ?
    columnTypes?.[field]?.toLowerCase().replace(/\(\s*\d+(?:,\s*\d+)?\s*\)/g, '').trim() : undefined;

function coverageTerm(term: ExplorerFilter, type: string | undefined, composite: boolean): PreviewIndexCoverageTerm | undefined {
  const operator = Object.prototype.hasOwnProperty.call(operators, term.operator) ? operators[term.operator] : undefined;
  if (!term.active || term.join === 'or' || term.id.startsWith('group-') || !term.field ||
      term.value === '' || !operator || !type || !eligibleType.test(type)) return;
  const parts = operator === 'between' ? term.value.split(',') : [term.value];
  if (operator === 'between' && (parts.length !== 2 || !parts.every(item => item.trim()))) return;
  if (composite) {
    const parsed = parts.map(part => comparableLiteral(type, part));
    const [lower, upper] = parsed;
    if (parsed.some(value => value === undefined) || (lower !== undefined && upper !== undefined && lower > upper)) return;
  }
  return { field: term.field, operator };
}

type Props = {
  schema: string; table: string; rules: ExplorerFilter[]; columnTypes?: Record<string, string>; conflictingOptions: boolean; allowSample?: boolean;
};
export default function ExplorerIndexCoverage({ schema, table, rules, columnTypes, conflictingOptions, allowSample }: Props) {
  const identity = useSandboxIdentity();
  const types = rules.map(rule => ruleType(columnTypes, rule.field));
  const composite = rules.length > 1;
  const terms = !conflictingOptions && rules.length <= 3 ?
    rules.map((rule, index) => coverageTerm(rule, types[index], composite)).filter((term): term is PreviewIndexCoverageTerm => Boolean(term)) : [];
  if (!identity) return null;
  let input: PreviewIndexCoverageInput | undefined;
  if (terms.length === rules.length && terms.length === 1) input = terms[0];
  if (terms.length === rules.length && (terms.length === 2 || terms.length === 3) &&
      new Set(terms.map(term => term.field)).size === terms.length && terms.filter(term => term.operator !== 'eq').length <= 1) {
    input = { terms };
  }
  const sampleTerm = input && !('terms' in input) ? rules[0] : undefined;
  const compound = allowSample && input && 'terms' in input ? compoundSamplePredicate(rules, types) : undefined;
  // This key is only local UI state: changing a literal invalidates the prior check without sending the literal.
  return <>
    <CoverageCheck key={JSON.stringify([identity, schema, table, rules, types, conflictingOptions])}
      schema={schema} table={table} input={input} />
    {allowSample && <ExplorerIndexedIdSample key={JSON.stringify([identity, schema, table])} schema={schema} table={table}
      candidate={sampleTerm ? { field: sampleTerm.field, operator: sampleTerm.operator, value: sampleTerm.value } : undefined} />}
    {compound && <ExplorerIndexedCompoundSample key={JSON.stringify(['compound', identity, schema, table])}
      identity={identity} schema={schema} table={table} predicate={compound} reviewKey={JSON.stringify([rules, types])} />}
  </>;
}

function CoverageCheck({ schema, table, input }: {
  schema: string; table: string; input?: PreviewIndexCoverageInput;
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
  const composite = input && 'terms' in input;
  const resultCopy = !state ? '' : composite && state === 'candidate' ? 'Composite leading index candidate found.' :
    composite && state === 'no_candidate' ? 'No supported composite leading index found. This does not prove a full scan or rule out other indexes.' :
    composite && state === 'unsupported' ? 'Index coverage is not assessed for this combination.' : labels[state];
  return <section className="flow-filter-index-coverage" aria-label="Index coverage">
    <Button size="small" disabled={!input} loading={busy} onClick={() => void check()}>Check index coverage</Button>
    {!input && <span>Use one supported condition, or two to three distinct conditions joined by AND with at most one range. Groups, search, partitions and custom sort are not supported.</span>}
    {busy && <span role="status">Checking catalog metadata…</span>}
    {state && <span role="status">{resultCopy}</span>}
    <p>Catalog metadata only. It does not run the filter, predict the query plan, or guarantee an index scan.</p>
  </section>;
}
