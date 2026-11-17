import { useEffect, useId, useRef, useState } from 'react';
import { Button, Input, Modal } from '@/components/ui';
import { acknowledgeFieldFreshnessIncident, fetchFieldFreshness, reviewFieldFreshness } from '../../services/api';
import type { AssetQualityIncident, FieldFreshness, FieldFreshnessReview } from '../../services/api';
import { getSessionState } from '../../services/session';
import ExplorerQualityWarnings from './ExplorerQualityWarnings';
import { sandboxScopeDenied, useSandboxIdentity } from './SandboxMarkings';

const labels: Record<FieldFreshness['state'], string> = {
  unconfigured: 'Not configured', fresh: 'Within field lag threshold', stale: 'Field lag exceeds threshold',
  unknown: 'Unknown', superseded: 'Superseded · review needed',
};
const reasons: Partial<Record<string, string>> = {
  no_eligible_policy: 'No eligible active transfer freshness review is available for this fixture.',
  index_unavailable: 'The scheduled check could not verify the required index. No unindexed fallback was used.',
  review_required: 'Review the saved lag threshold before evaluating this fixture.',
  scope_changed: 'The reviewed policy, target, or field changed. Earlier observations do not describe the current binding.',
  no_verified_receipt: 'No matching verified positive sync has been recorded.',
  observation_expired: 'The last field observation expired. Wait for the existing evaluator and refresh Evidence.',
  field_lag: 'The difference between the confirmed sync time and the data-field timestamp is above the reviewed lag threshold.',
};
const MIN = 60;
const MAX = 604800;
const utc = (value: string) => new Date(value).toLocaleString(undefined, { timeZone: 'UTC', timeZoneName: 'short' });
function Time({ value, empty }: { value?: string | null; empty: string }) {
  return value && Number.isFinite(Date.parse(value)) ? <time dateTime={value}>{utc(value)}</time> : <>{empty}</>;
}

export default function ExplorerFieldFreshness({ version }: { version: number }) {
  const identity = useSandboxIdentity();
  return identity ? <Workspace key={`${identity}:${version}`} identity={identity} /> : null;
}

function Workspace({ identity }: { identity: string }) {
  const title = useId();
  const [status, setStatus] = useState<FieldFreshness>();
  const [lag, setLag] = useState('');
  const [review, setReview] = useState<FieldFreshnessReview>();
  const [expired, setExpired] = useState<number>();
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  const [working, setWorking] = useState(false);
  const [loading, setLoading] = useState(true);
  const active = useRef(false);
  const pending = useRef(false);
  const live = (signal?: AbortSignal) => {
    const session = getSessionState();
    return active.current && !signal?.aborted && session.authenticated && !session.locked && session.identity === identity;
  };
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  useEffect(() => {
    const read = new AbortController();
    setLoading(true);
    void fetchFieldFreshness(read.signal).then(result => {
      if (!live(read.signal)) return;
      setStatus(result); setLag(result.maxLagSeconds == null ? '' : String(result.maxLagSeconds));
    }).catch(() => { if (live(read.signal)) setStatus(undefined); })
      .finally(() => { if (live(read.signal)) setLoading(false); });
    return () => read.abort();
  }, [identity, revision]);

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
    : status.state === 'fresh' || status.state === 'stale';
  const state = isExpired ? 'unknown' : status.state;
  const reportFacts = !isExpired && state !== 'superseded';
  const reason = isExpired ? reasons.observation_expired : status.reason ? reasons[status.reason] : undefined;
  const maxLag = Number(lag);
  const valid = /^[1-9]\d{0,5}$/.test(lag) && maxLag >= MIN && maxLag <= MAX;
  const canReview = !loading && !working && valid && status.policyGeneration != null &&
    (maxLag !== status.maxLagSeconds || status.state === 'superseded' || status.reason === 'review_required');
  const activeIncident = status.incident && (status.incident.status === 'open' || status.incident.status === 'acknowledged') ? status.incident : null;
  const operations = async (action: () => Promise<void>, failureText: string) => {
    if (pending.current || !live()) return;
    pending.current = true; setWorking(true); setError('');
    try { await action(); }
    catch (failure) {
      if (live()) {
        if (sandboxScopeDenied(failure)) setStatus(undefined);
        else setError(failureText);
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
      const updated = await reviewFieldFreshness(review);
      if (live()) { setStatus(updated); setLag(updated.maxLagSeconds == null ? '' : String(updated.maxLagSeconds)); }
    }, 'The review could not be confirmed. Check the refreshed field freshness policy before retrying.');
    if (live()) setReview(undefined);
  };
  const acknowledge = async (incident: AssetQualityIncident) => {
    if (!activeIncident || incident.id !== activeIncident.id || incident.generation !== status.policyGeneration) return;
    await operations(async () => {
      const updated = await acknowledgeFieldFreshnessIncident(incident.id, status.generation);
      if (live()) setStatus(value => value && { ...value, incident: updated });
    }, 'Acknowledgment could not be confirmed. Check the refreshed warning before retrying.');
  };
  return <section className="flow-evidence-card flow-evidence-freshness" aria-labelledby={title} aria-busy={loading || working}>
    <header><h4 id={title}>Field data freshness</h4><span>Fixed local fixture · collected evidence</span></header>
    <p>Compares a field timestamp with the recorded time of a verified sync. It does not show that the field caused the sync.</p>
    <strong role="status">{labels[state]}</strong>
    {reason && <p>{reason}</p>}
    {error && <p role="alert">{error}</p>}
    <dl className="flow-evidence-freshness-facts">
      <div><dt>Data-field timestamp</dt><dd><Time value={reportFacts ? status.fieldRecordedAt : null} empty="Not verified" /></dd></div>
      <div><dt>Confirmed sync recorded at</dt><dd><Time value={reportFacts ? status.receiptRecordedAt : null} empty="No verified sync shown" /></dd></div>
      <div><dt>Last checked</dt><dd><Time value={status.checkedAt} empty="Not evaluated" /></dd></div>
      <div><dt>Observed field lag</dt><dd>{reportFacts && status.lagSeconds != null ? `${status.lagSeconds.toLocaleString()} seconds` : 'Unknown'}</dd></div>
      <div><dt>Reviewed threshold</dt><dd>{status.maxLagSeconds == null ? 'Not configured' : `${status.maxLagSeconds.toLocaleString()} seconds`}</dd></div>
    </dl>
    {status.policyGeneration != null && <div className="flow-evidence-freshness-form">
      <label>Maximum lag (seconds)<Input type="number" aria-label="Maximum lag (seconds)" min={MIN} max={MAX} step={1} value={lag}
        onChange={event => setLag(event.target.value)} disabled={loading || working} /></label>
      <p>Use a whole number from {MIN.toLocaleString()} to {MAX.toLocaleString()} seconds.</p>
      <div className="flow-evidence-freshness-actions"><Button size="small" disabled={!canReview} onClick={() => {
        if (canReview && status.policyGeneration != null) setReview({ expectedGeneration: status.generation, policyGeneration: status.policyGeneration, maxLagSeconds: maxLag });
      }}>Review field freshness check</Button></div>
    </div>}
    <p>Only the existing evaluator collects this check. Opening Evidence does not query rows or run a sync.</p>
    {status.incident && <ExplorerQualityWarnings incidents={{
      active: activeIncident ? [activeIncident] : [], history: activeIncident ? [] : [status.incident],
    }} generation={status.policyGeneration ?? undefined} busy={working || loading} onAcknowledge={acknowledge} />}
    <Modal open={Boolean(review)} destroyOnHidden className="flow-surface-modal" title="Review field freshness check?"
      onCancel={() => { if (!working) setReview(undefined); }} onOk={applyReview} confirmLoading={working} okText="Confirm field freshness check">
      {review && <><p>Review a maximum lag of <strong>{review.maxLagSeconds.toLocaleString()} seconds</strong> for the fixed fixture <code>ts_transfer_sandbox.field_freshness_fixture</code>.</p>
        <p>The review uses the current transfer freshness policy, generation {review.policyGeneration}. It does not read rows, start a sync, or write data.</p></>}
    </Modal>
  </section>;
}
