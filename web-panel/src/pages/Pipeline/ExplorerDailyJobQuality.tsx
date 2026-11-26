import { useEffect, useId, useRef, useState } from 'react';
import { Button, Modal } from '@/components/ui';
import { acknowledgeDailyJobQualityIncident, fetchDailyJobQuality, reviewDailyJobQuality, acknowledgeWeeklyJobQualityIncident, fetchWeeklyJobQuality, reviewWeeklyJobQuality } from '../../services/api';
import type { AssetQualityIncident, DailyJobQuality, DailyJobQualityCandidate, DailyJobQualityReview, WeeklyJobQuality } from '../../services/api';
import { isoWeekdayName } from '../../services/scheduleTime';
import { getSessionState } from '../../services/session';
import ExplorerWeeklyRunEvidence from './ExplorerWeeklyRunEvidence';
import ExplorerQualityWarnings from './ExplorerQualityWarnings';
import { sandboxScopeDenied, useSandboxIdentity } from './SandboxMarkings';
import useExplorerErrorToast from './useExplorerErrorToast';

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

type Props = { schema: string; table: string; version: number; cadence?: 'daily' | 'weekly' };
export default function ExplorerDailyJobQuality(props: Props) {
  const identity = useSandboxIdentity();
  return identity
    ? <DailyJobQualityCard key={JSON.stringify([identity, props.schema, props.table, props.version, props.cadence])} {...props} identity={identity} />
    : null;
}

function DailyJobQualityCard({ schema, table, identity, cadence = 'daily' }: Props & { identity: string }) {
  const weekly = cadence === 'weekly';
  const title = useId();
  const fetchQuality = weekly ? fetchWeeklyJobQuality : fetchDailyJobQuality;
  const reviewQuality = weekly ? reviewWeeklyJobQuality : reviewDailyJobQuality;
  const acknowledgeWarning = weekly ? acknowledgeWeeklyJobQualityIncident : acknowledgeDailyJobQualityIncident;
  const reviewLabel = weekly ? 'Review weekly outcome check' : 'Review scheduled outcome check';
  const weekday = (config: DailyJobQualityCandidate) => 'plannedWeekdayUtc' in config && typeof config.plannedWeekdayUtc === 'number'
    ? isoWeekdayName(config.plannedWeekdayUtc) : undefined;
  const clock = (config: DailyJobQualityCandidate) => weekly
    ? `${weekday(config) ?? 'Unverified weekday'} at ${config.plannedTimeUtc} UTC`
    : `daily at ${config.plannedTimeUtc} UTC`;
  const [snapshot, setSnapshot] = useState<DailyJobQuality | WeeklyJobQuality>();
  const [loading, setLoading] = useState(true);
  const [expiredAt, setExpiredAt] = useState<number>();
  const [error, setError] = useState('');
  const [readFailed, setReadFailed] = useState(false);
  const [review, setReview] = useState<{ candidate: DailyJobQualityCandidate; request: DailyJobQualityReview }>();
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  const active = useRef(false);
  const pending = useRef(false);
  useExplorerErrorToast(error, 'Scheduled quality update failed');
  useExplorerErrorToast(readFailed ? 'The scheduled outcome check could not be loaded.' : undefined,
    'Scheduled quality unavailable', () => setRevision(value => value + 1));
  const live = (signal?: AbortSignal) => {
    const session = getSessionState();
    return active.current && !signal?.aborted && session.authenticated && !session.locked && session.identity === identity;
  };

  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  useEffect(() => {
    const read = new AbortController();
    setLoading(true); setReadFailed(false);
    void fetchQuality(schema, table, read.signal).then(value => {
      if (live(read.signal)) setSnapshot(value);
    }).catch(() => { if (live(read.signal)) { setSnapshot(undefined); setReadFailed(true); } })
      .finally(() => { if (live(read.signal)) setLoading(false); });
    return () => read.abort();
  }, [schema, table, identity, revision, fetchQuality]);
  const expiry = snapshot?.validUntil ? Date.parse(snapshot.validUntil) : NaN;
  useEffect(() => {
    if (!Number.isFinite(expiry)) return;
    const update = () => { if (Date.now() >= expiry) setExpiredAt(expiry); };
    update();
    const timer = window.setTimeout(update, Math.max(0, expiry - Date.now()));
    document.addEventListener('visibilitychange', update);
    return () => { window.clearTimeout(timer); document.removeEventListener('visibilitychange', update); };
  }, [expiry]);

  if (!snapshot?.available || (snapshot.state === 'disabled' &&
    (!weekly || (!snapshot.binding && !snapshot.observation && !snapshot.incidents.active.length && !snapshot.incidents.history.length)))) return null;
  const stale = Number.isFinite(expiry) ? expiry === expiredAt || Date.now() >= expiry :
    snapshot.state === 'healthy' || snapshot.state === 'unhealthy';
  const state = snapshot.state === 'disabled' ? 'disabled' : stale || (weekly && loading) ? 'unknown' : snapshot.state;
  const { binding, candidate, observation } = snapshot;
  const alreadyReviewed = Boolean(binding && candidate && binding.planId === candidate.planId &&
    binding.planUpdatedAt === candidate.planUpdatedAt && binding.freshnessGeneration === candidate.freshnessGeneration
    && binding.scheduleGeneration === candidate.scheduleGeneration && binding.plannedTimeUtc === candidate.plannedTimeUtc
    && (!weekly || weekday(binding) === weekday(candidate)));
  const canReview = Boolean(candidate && snapshot.state !== 'disabled' && (!weekly || weekday(candidate)) && (!alreadyReviewed || snapshot.state === 'superseded') && !loading && !busy);
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
    if (!review || !canReview || !live() || pending.current) return;
    pending.current = true; setBusy(true); setError('');
    try {
      const value = await reviewQuality(schema, table, review.request);
      if (live()) setSnapshot(value);
    } catch (failure) {
      if (live()) {
        if (sandboxScopeDenied(failure)) setSnapshot(undefined);
        setError('The outcome check could not be reviewed. Recheck the current schedule before retrying.');
        if (weekly) setLoading(true);
        setRevision(value => value + 1);
      }
    } finally {
      pending.current = false;
      if (live()) { setBusy(false); setReview(undefined); }
    }
  };
  const acknowledge = async (incident: AssetQualityIncident) => {
    if (!binding || (weekly && (loading || snapshot.state === 'disabled' || snapshot.state === 'superseded' ||
        !snapshot.incidents.active.some(item => item.id === incident.id && item.status === 'open'))) || incident.generation !== binding.freshnessGeneration || !live() || pending.current) return;
    pending.current = true; setBusy(true); setError('');
    try {
      const result = await acknowledgeWarning(schema, table, incident.id, binding.generation);
      if (live()) setSnapshot(value => value && { ...value, incidents: { ...value.incidents,
        active: value.incidents.active.map(item => item.id === result.id ? result : item) } });
    } catch (failure) {
      if (live()) {
        if (sandboxScopeDenied(failure)) setSnapshot(undefined);
        setError('Acknowledgment could not be verified. Review the current warnings before retrying.');
        if (weekly) setLoading(true);
        setRevision(value => value + 1);
      }
    } finally {
      pending.current = false;
      if (live()) setBusy(false);
    }
  };

  const reason = weekly && state === 'disabled' ? 'Weekly evaluation is inactive. Recorded history does not describe a current outcome.' : weekly && loading ? 'Rechecking the recorded outcome…' : stale ? reasons.evaluation_stale : snapshot.reason ? (weekly &&
    (snapshot.reason === 'no_eligible_schedule' || snapshot.reason === 'schedule_changed') ?
      reasons[snapshot.reason]?.replace('Daily UTC', 'Weekly UTC') : reasons[snapshot.reason]) : undefined;
  return <section className="flow-evidence-card flow-evidence-freshness" aria-labelledby={title} aria-busy={loading || busy}>
    <header><h4 id={title}>{weekly ? 'Weekly UTC job health' : 'Scheduled transfer health'}</h4><span>Reviewed {weekly ? 'Weekly' : 'Daily'} UTC schedule</span></header>
    <p>Checks the outcome of a reviewed schedule slot for this exact table. It does not start, pause, or change the schedule.</p>
    <strong role="status">{labels[state]}</strong>
    {reason && <p>{reason}</p>}
    <dl className="flow-evidence-freshness-facts">
      <div><dt>Reviewed time (UTC)</dt><dd>{binding ? weekly ? clock(binding) : `Daily at ${binding.plannedTimeUtc} UTC` : 'Not reviewed'}</dd></div>
      <div><dt>UTC slot</dt><dd><Time value={observation?.slot} empty="No slot observed" /></dd></div>
      <div><dt>Last checked (UTC)</dt><dd><Time value={snapshot.checkedAt} empty="Not evaluated" /></dd></div>
      {binding && <div><dt>Reviewed at (UTC)</dt><dd><Time value={binding.reviewedAt} empty="Not recorded" /></dd></div>}
      {observation && <>
        <div><dt>{stale || state === 'superseded' || (weekly && (loading || state === 'disabled')) ? 'Historical slot record' : 'Slot record'}</dt><dd>{slotStates[observation.state]}</dd></div>
        <div><dt>Finished at (UTC)</dt><dd><Time value={observation.finishedAt} empty="Not recorded" /></dd></div>
        {observation.rowsAffected != null && <div><dt>Rows affected on receipt</dt><dd>{observation.rowsAffected.toLocaleString()}</dd></div>}
        {observation.receiptId && <div><dt>Receipt ID</dt><dd><code>{observation.receiptId}</code></dd></div>}
      </>}
    </dl>
    {weekly && (state === 'healthy' || state === 'unhealthy') && !busy && binding && snapshot.validUntil && observation?.finishedAt &&
      <ExplorerWeeklyRunEvidence schema={schema} table={table} identity={identity} generation={binding.generation}
        validUntil={snapshot.validUntil} outcome={state} observation={observation} />}
    {candidate && !alreadyReviewed && (!weekly || state !== 'disabled' && !loading) && <p>Eligible schedule: {clock(candidate)}.</p>}
    {canReview && <div className="flow-evidence-freshness-actions"><Button size="small" onClick={offerReview}>{reviewLabel}</Button></div>}
    <ExplorerQualityWarnings incidents={snapshot.incidents} generation={weekly && (snapshot.state === 'disabled' || snapshot.state === 'superseded') ? undefined : binding?.freshnessGeneration} busy={busy || loading} onAcknowledge={acknowledge} />
    <Modal open={Boolean(review)} destroyOnHidden className="flow-surface-modal" title={`${reviewLabel}?`}
      onCancel={() => { if (!busy) setReview(undefined); }} onOk={confirm} confirmLoading={busy} okText="Confirm outcome check">
      {review && <>
        <p>Bind this check to the existing {weekly ? `weekly on ${clock(review.candidate)}` : clock(review.candidate)} schedule for <code>{schema}.{table}</code>. Only eligible slots after this review can count.</p>
        <p>Transfer revision: <Time value={review.candidate.planUpdatedAt} empty="Not recorded" /> · Schedule generation {review.candidate.scheduleGeneration} · Freshness generation {review.candidate.freshnessGeneration}.</p>
        <p>This does not arm or run a transfer, change its schedule, or write to the table.</p>
      </>}
    </Modal>
  </section>;
}
