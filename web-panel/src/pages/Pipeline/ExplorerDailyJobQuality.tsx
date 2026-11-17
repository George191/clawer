import { useEffect, useId, useRef, useState } from 'react';
import { Button, Modal } from '@/components/ui';
import { acknowledgeDailyJobQualityIncident, fetchDailyJobQuality, reviewDailyJobQuality } from '../../services/api';
import type { AssetQualityIncident, DailyJobQuality, DailyJobQualityCandidate, DailyJobQualityReview } from '../../services/api';
import { getSessionState } from '../../services/session';
import ExplorerQualityWarnings from './ExplorerQualityWarnings';
import { sandboxScopeDenied, useSandboxIdentity } from './SandboxMarkings';

const labels: Record<DailyJobQuality['state'], string> = {
  disabled: 'Unavailable',
  not_configured: 'Not reviewed',
  not_checked: 'Not yet checked',
  healthy: 'Verified positive success',
  unhealthy: 'Confirmed failure',
  unknown: 'Unknown',
  superseded: 'Superseded · review needed',
};
const reasons: Partial<Record<string, string>> = {
  checking_disabled: 'Scheduled outcome evaluation is unavailable.',
  target_unavailable: 'The reviewed physical table is unavailable.',
  no_eligible_schedule: 'No eligible armed Daily UTC transfer is available under this table’s active freshness policy.',
  policy_changed: 'The freshness policy or transfer revision changed.',
  schedule_changed: 'The reviewed Daily UTC schedule changed.',
  target_changed: 'The reviewed physical table changed.',
  evaluation_stale: 'The last outcome evaluation expired. Refresh Evidence after the evaluator runs.',
  no_post_review_slot: 'No eligible UTC slot was observed after this review.',
  missing_slot: 'The latest reviewed UTC slot has no recorded job. This does not confirm a failure.',
  nonterminal: 'The recorded slot has not reached a terminal outcome.',
  expired: 'The slot expired. This is not a confirmed failure or success.',
  canceled: 'This waiting slot was canceled. It does not prove either a failed or successful write.',
  outcome_unknown: 'The outcome of this slot could not be verified.',
  zero_rows: 'A zero-row receipt does not verify a successful write.',
  unverified_receipt: 'No verified positive target receipt is linked to this slot.',
  unproven_rejection: 'This rejection does not prove failure at the reviewed target.',
  target_rejected: 'The reviewed scheduled job was definitively rejected by its target.',
};
const slotStates: Record<NonNullable<DailyJobQuality['observation']>['state'], string> = {
  pending: 'Pending',
  dispatched: 'Dispatched · outcome not verified',
  committed: 'Commit recorded',
  rejected: 'Rejection recorded',
  outcome_unknown: 'Outcome unknown',
  expired: 'Expired',
  canceled: 'Canceled',
};

function Time({ value, empty }: { value?: string | null; empty: string }) {
  return value && Number.isFinite(Date.parse(value))
    ? <time dateTime={value}>{new Date(value).toLocaleString(undefined, { timeZone: 'UTC', timeZoneName: 'short' })}</time> : <>{empty}</>;
}

type Props = { schema: string; table: string; version: number };
export default function ExplorerDailyJobQuality(props: Props) {
  const identity = useSandboxIdentity();
  return identity && props.schema === 'ts_transfer_sandbox'
    ? <DailyJobQualityCard key={`${identity}:${props.schema}:${props.table}:${props.version}`} {...props} identity={identity} />
    : null;
}

function DailyJobQualityCard({ schema, table, identity }: Props & { identity: string }) {
  const title = useId();
  const [snapshot, setSnapshot] = useState<DailyJobQuality>();
  const [loading, setLoading] = useState(true);
  const [expiredAt, setExpiredAt] = useState<number>();
  const [error, setError] = useState('');
  const [review, setReview] = useState<{ candidate: DailyJobQualityCandidate; request: DailyJobQualityReview }>();
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
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
    void fetchDailyJobQuality(schema, table, read.signal).then(value => {
      if (live(read.signal)) setSnapshot(value);
    }).catch(() => { if (live(read.signal)) setSnapshot(undefined); })
      .finally(() => { if (live(read.signal)) setLoading(false); });
    return () => read.abort();
  }, [schema, table, identity, revision]);
  const expiry = snapshot?.validUntil ? Date.parse(snapshot.validUntil) : NaN;
  useEffect(() => {
    if (!Number.isFinite(expiry)) return;
    const update = () => { if (Date.now() >= expiry) setExpiredAt(expiry); };
    update();
    const timer = window.setTimeout(update, Math.max(0, expiry - Date.now()));
    document.addEventListener('visibilitychange', update);
    return () => { window.clearTimeout(timer); document.removeEventListener('visibilitychange', update); };
  }, [expiry]);

  if (!snapshot?.available || snapshot.state === 'disabled') return null;
  const stale = Number.isFinite(expiry) ? expiry === expiredAt || Date.now() >= expiry :
    snapshot.state === 'healthy' || snapshot.state === 'unhealthy';
  const state = stale ? 'unknown' : snapshot.state;
  const { binding, candidate, observation } = snapshot;
  const alreadyReviewed = Boolean(binding && candidate && binding.planId === candidate.planId &&
    binding.planUpdatedAt === candidate.planUpdatedAt && binding.freshnessGeneration === candidate.freshnessGeneration
    && binding.scheduleGeneration === candidate.scheduleGeneration && binding.plannedTimeUtc === candidate.plannedTimeUtc);
  const canReview = Boolean(candidate && (!alreadyReviewed || snapshot.state === 'superseded') && !loading && !busy);
  const offerReview = () => {
    if (!candidate || !canReview) return;
    setReview({ candidate, request: {
      expectedGeneration: binding?.generation ?? null,
      planId: candidate.planId,
      expectedPlanUpdatedAt: candidate.planUpdatedAt,
      expectedFreshnessGeneration: candidate.freshnessGeneration,
      expectedScheduleGeneration: candidate.scheduleGeneration,
    } });
  };
  const confirm = async () => {
    if (!review || !live() || pending.current) return;
    pending.current = true; setBusy(true); setError('');
    try {
      const value = await reviewDailyJobQuality(schema, table, review.request);
      if (live()) setSnapshot(value);
    } catch (failure) {
      if (live()) {
        if (sandboxScopeDenied(failure)) setSnapshot(undefined);
        else setError('The outcome check could not be reviewed. Recheck the current schedule before retrying.');
        setRevision(value => value + 1);
      }
    } finally {
      pending.current = false;
      if (live()) { setBusy(false); setReview(undefined); }
    }
  };
  const acknowledge = async (incident: AssetQualityIncident) => {
    if (!binding || incident.generation !== binding.freshnessGeneration || !live() || pending.current) return;
    pending.current = true; setBusy(true); setError('');
    try {
      const result = await acknowledgeDailyJobQualityIncident(schema, table, incident.id, binding.generation);
      if (live()) setSnapshot(value => value && { ...value, incidents: { ...value.incidents,
        active: value.incidents.active.map(item => item.id === result.id ? result : item) } });
    } catch (failure) {
      if (live()) {
        if (sandboxScopeDenied(failure)) setSnapshot(undefined);
        else setError('Acknowledgment could not be verified. Review the current warnings before retrying.');
        setRevision(value => value + 1);
      }
    } finally {
      pending.current = false;
      if (live()) setBusy(false);
    }
  };

  const reason = stale ? reasons.evaluation_stale : snapshot.reason ? reasons[snapshot.reason] : undefined;
  return <section className="flow-evidence-card flow-evidence-freshness" aria-labelledby={title} aria-busy={loading || busy}>
    <header><h4 id={title}>Scheduled transfer health</h4><span>Reviewed Daily UTC schedule</span></header>
    <p>Checks the outcome of a reviewed schedule slot for this exact table. It does not start, pause, or change the schedule.</p>
    {error && <p role="alert">{error}</p>}
    <strong role="status">{labels[state]}</strong>
    {reason && <p>{reason}</p>}
    <dl className="flow-evidence-freshness-facts">
      <div><dt>Reviewed time (UTC)</dt><dd>{binding ? `Daily at ${binding.plannedTimeUtc} UTC` : 'Not reviewed'}</dd></div>
      <div><dt>UTC slot</dt><dd><Time value={observation?.slot} empty="No slot observed" /></dd></div>
      <div><dt>Last checked (UTC)</dt><dd><Time value={snapshot.checkedAt} empty="Not evaluated" /></dd></div>
      {binding && <div><dt>Reviewed at (UTC)</dt><dd><Time value={binding.reviewedAt} empty="Not recorded" /></dd></div>}
      {observation && <>
        <div><dt>{stale || state === 'superseded' ? 'Historical slot record' : 'Slot record'}</dt><dd>{slotStates[observation.state]}</dd></div>
        <div><dt>Finished at (UTC)</dt><dd><Time value={observation.finishedAt} empty="Not recorded" /></dd></div>
        {observation.rowsAffected != null && <div><dt>Rows affected on receipt</dt><dd>{observation.rowsAffected.toLocaleString()}</dd></div>}
        {observation.receiptId && <div><dt>Receipt ID</dt><dd><code>{observation.receiptId}</code></dd></div>}
      </>}
    </dl>
    {candidate && !alreadyReviewed && <p>Eligible schedule: daily at {candidate.plannedTimeUtc} UTC.</p>}
    {canReview && <div className="flow-evidence-freshness-actions"><Button size="small" onClick={offerReview}>Review scheduled outcome check</Button></div>}
    <ExplorerQualityWarnings incidents={snapshot.incidents} generation={binding?.freshnessGeneration} busy={busy || loading} onAcknowledge={acknowledge} />
    <Modal open={Boolean(review)} destroyOnHidden className="flow-surface-modal" title="Review scheduled outcome check?"
      onCancel={() => { if (!busy) setReview(undefined); }} onOk={confirm} confirmLoading={busy} okText="Confirm outcome check">
      {review && <>
        <p>Bind this check to the existing daily {review.candidate.plannedTimeUtc} UTC schedule for <code>{schema}.{table}</code>. Only eligible slots after this review can count.</p>
        <p>Transfer revision: <Time value={review.candidate.planUpdatedAt} empty="Not recorded" /> · Schedule generation {review.candidate.scheduleGeneration} · Freshness generation {review.candidate.freshnessGeneration}.</p>
        <p>This does not arm or run a transfer, change its schedule, or write to the table.</p>
      </>}
    </Modal>
  </section>;
}
