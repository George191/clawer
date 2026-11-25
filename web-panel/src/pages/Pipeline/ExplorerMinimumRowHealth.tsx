import { useEffect, useId, useRef, useState } from 'react';
import { Button, Input, Modal } from '@/components/ui';
import { acknowledgeMinimumRowHealthIncident, fetchMinimumRowHealth, reviewMinimumRowHealth } from '../../services/api';
import type { AssetQualityIncident, MinimumRowHealth, MinimumRowHealthReview } from '../../services/api';
import { getSessionState } from '../../services/session';
import ExplorerQualityWarnings from './ExplorerQualityWarnings';
import { sandboxScopeDenied, useSandboxIdentity } from './SandboxMarkings';
import useExplorerErrorToast from './useExplorerErrorToast';

const labels: Record<MinimumRowHealth['state'], string> = {
  unconfigured: 'Not configured', healthy: 'Minimum verified', below_minimum: 'Below the reviewed minimum',
  unknown: 'Unknown', superseded: 'Superseded · review needed',
};
const reasons: Partial<Record<string, string>> = {
  no_eligible_policy: 'No eligible active transfer freshness review is available for this fixture.',
  review_required: 'Review the saved minimum before evaluating this fixture.',
  scope_changed: 'The reviewed policy or physical target changed. Previous observations do not describe the current binding.',
  no_verified_receipt: 'No matching verified positive sync has been recorded for this review.',
  awaiting_measurement: 'A newer verified sync was recorded, but its threshold observation is not available yet.',
  observation_expired: 'The last threshold observation expired. Wait for the existing evaluator and refresh Evidence.',
  row_threshold: 'The bounded observation did not reach the reviewed row threshold.',
  index_unavailable: 'The scheduled check could not verify its required bounded index path. No alternative query was used.',
};
const MIN = 1;
const MAX = 100;
const utc = (value: string) => new Date(value).toLocaleString(undefined, { timeZone: 'UTC', timeZoneName: 'short' });
function Time({ value, empty }: { value?: string | null; empty: string }) {
  return value && Number.isFinite(Date.parse(value)) ? <time dateTime={value}>{utc(value)}</time> : <>{empty}</>;
}

export default function ExplorerMinimumRowHealth({ schema, table, version }: { schema: string; table: string; version: number }) {
  const identity = useSandboxIdentity();
  return identity ? <Workspace key={JSON.stringify([identity, schema, table, version])} identity={identity} schema={schema} table={table} /> : null;
}

function Workspace({ identity, schema, table }: { identity: string; schema: string; table: string }) {
  const title = useId();
  const [status, setStatus] = useState<MinimumRowHealth>();
  const [threshold, setThreshold] = useState('');
  const [review, setReview] = useState<MinimumRowHealthReview>();
  const [expired, setExpired] = useState<number>();
  const [error, setError] = useState('');
  const [readFailed, setReadFailed] = useState(false);
  const [revision, setRevision] = useState(0);
  const [working, setWorking] = useState(false);
  const [loading, setLoading] = useState(true);
  const active = useRef(false);
  const pending = useRef(false);
  useExplorerErrorToast(error, 'Minimum row check update failed');
  useExplorerErrorToast(readFailed ? 'The minimum row policy could not be loaded.' : undefined,
    'Minimum row check unavailable', () => setRevision(value => value + 1));
  const live = (signal?: AbortSignal) => {
    const session = getSessionState();
    return active.current && !signal?.aborted && session.authenticated && !session.locked && session.identity === identity;
  };
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  useEffect(() => {
    const read = new AbortController();
    setLoading(true); setReadFailed(false);
    void fetchMinimumRowHealth(schema, table, read.signal).then(result => {
      if (!live(read.signal)) return;
      setStatus(result); setThreshold(result.minimumRows == null ? '' : String(result.minimumRows));
    }).catch(() => { if (live(read.signal)) { setStatus(undefined); setReadFailed(true); } })
      .finally(() => { if (live(read.signal)) setLoading(false); });
    return () => read.abort();
  }, [identity, revision, schema, table]);

  const expiry = status?.validUntil ? Date.parse(status.validUntil) : NaN;
  useEffect(() => {
    if (!Number.isFinite(expiry)) return;
    const update = () => { if (Date.now() >= expiry) setExpired(expiry); };
    update();
    const timer = window.setTimeout(update, Math.max(0, expiry - Date.now()));
    document.addEventListener('visibilitychange', update);
    return () => { window.clearTimeout(timer); document.removeEventListener('visibilitychange', update); };
  }, [expiry]);

  if (!status?.enabled) return null;
  const isExpired = Number.isFinite(expiry) ? expiry === expired || Date.now() >= expiry
    : status.state === 'healthy' || status.state === 'below_minimum';
  const state = isExpired ? 'unknown' : status.state;
  const reportFacts = !isExpired && state !== 'superseded';
  const reason = isExpired ? reasons.observation_expired : status.reason ? reasons[status.reason] : undefined;
  const minimum = Number(threshold);
  const valid = /^[1-9]\d{0,2}$/.test(threshold) && minimum >= MIN && minimum <= MAX;
  const canReview = !loading && !working && valid && status.policyGeneration != null &&
    (minimum !== status.minimumRows || status.state === 'superseded' || status.reason === 'review_required');
  const activeIncident = status.incident && (status.incident.status === 'open' || status.incident.status === 'acknowledged') ? status.incident : null;
  const operations = async (action: () => Promise<void>, failureText: string) => {
    if (pending.current || !live()) return;
    pending.current = true; setWorking(true); setError('');
    try { await action(); }
    catch (failure) {
      if (live()) {
        if (sandboxScopeDenied(failure)) setStatus(undefined);
        setError(failureText);
        setRevision(value => value + 1);
      }
    } finally {
      pending.current = false;
      if (live()) setWorking(false);
    }
  };
  const applyReview = async () => {
    if (!review) return;
    await operations(async () => {
      const updated = await reviewMinimumRowHealth(schema, table, review);
      if (live()) { setStatus(updated); setThreshold(updated.minimumRows == null ? '' : String(updated.minimumRows)); }
    }, 'The review could not be confirmed. Check the refreshed minimum row policy before retrying.');
    if (live()) setReview(undefined);
  };
  const acknowledge = async (incident: AssetQualityIncident) => {
    if (!activeIncident || incident.id !== activeIncident.id || incident.generation !== status.policyGeneration) return;
    await operations(async () => {
      const updated = await acknowledgeMinimumRowHealthIncident(schema, table, incident.id, status.generation);
      if (live()) setStatus(value => value && { ...value, incident: updated });
    }, 'Acknowledgment could not be confirmed. Check the refreshed warning before retrying.');
  };
  return <section className="flow-evidence-card flow-evidence-freshness" aria-labelledby={title} aria-busy={loading || working}>
    <header><h4 id={title}>Minimum row health</h4><span>Fixed local fixture · collected evidence</span></header>
    <p>The scheduled check verifies only whether the reviewed table meets its threshold after a confirmed sync. It does not report an exact total or expose row values.</p>
    <strong role="status">{state === 'healthy' && status.minimumRows != null ? `Verified at least ${status.minimumRows} rows`
      : state === 'below_minimum' && status.minimumRows != null ? `Verified fewer than ${status.minimumRows} rows` : labels[state]}</strong>
    {reason && <p>{reason}</p>}
    <dl className="flow-evidence-freshness-facts">
      <div><dt>Confirmed sync recorded at</dt><dd><Time value={reportFacts ? status.receiptRecordedAt : null} empty="No verified sync shown" /></dd></div>
      <div><dt>Last checked</dt><dd><Time value={status.checkedAt} empty="Not evaluated" /></dd></div>
      <div><dt>Reviewed threshold</dt><dd>{status.minimumRows == null ? 'Not configured' : `${status.minimumRows.toLocaleString()} rows`}</dd></div>
    </dl>
    {status.policyGeneration != null && <div className="flow-evidence-freshness-form">
      <label>Minimum rows<Input type="number" aria-label="Minimum rows" min={MIN} max={MAX} step={1} value={threshold}
        onChange={event => setThreshold(event.target.value)} disabled={loading || working} /></label>
      <p>Use a whole number from {MIN.toLocaleString()} to {MAX.toLocaleString()} rows.</p>
      <div className="flow-evidence-freshness-actions"><Button size="small" disabled={!canReview} onClick={() => {
        if (canReview && status.policyGeneration != null) setReview({ expectedGeneration: status.generation, policyGeneration: status.policyGeneration, minimumRows: minimum });
      }}>Review minimum row check</Button></div>
    </div>}
    <p>Only the existing evaluator collects this check. Opening Evidence does not query rows or run a sync.</p>
    {status.incident && <ExplorerQualityWarnings incidents={{
      active: activeIncident ? [activeIncident] : [], history: activeIncident ? [] : [status.incident],
    }} generation={status.policyGeneration ?? undefined} busy={working || loading} onAcknowledge={acknowledge} />}
    <Modal open={Boolean(review)} destroyOnHidden className="flow-surface-modal" title="Review minimum row check?"
      onCancel={() => { if (!working) setReview(undefined); }} onOk={applyReview} confirmLoading={working} okText="Confirm minimum row check">
      {review && <><p>Review a minimum of <strong>{review.minimumRows.toLocaleString()} rows</strong> for the fixed fixture <code>{schema}.{table}</code>.</p>
        <p>The review uses the current transfer freshness policy, generation {review.policyGeneration}. It does not read rows, start a sync, or write data.</p></>}
    </Modal>
  </section>;
}
