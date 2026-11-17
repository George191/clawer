import { useEffect, useRef, useState } from 'react';
import { Button, Input, Modal } from '@/components/ui';
import { fetchAssetFreshness, fetchAssetFreshnessCandidates, updateAssetFreshness } from '../../services/api';
import type { AssetFreshness, AssetFreshnessCandidate, AssetFreshnessChange } from '../../services/api';

const stateLabels: Record<AssetFreshness['assessment']['state'], string> = {
  not_configured: 'Not configured',
  not_checked: 'Not checked',
  healthy: 'Within threshold',
  overdue: 'Overdue',
  unknown: 'Unknown',
  paused: 'Paused',
};
const minMinutes = 5;
const maxMinutes = 10080;
const reasonLabels: Record<string, string> = {
  checking_disabled: 'Scheduled freshness evaluation is disabled.',
  plan_changed: 'The reviewed transfer plan changed. Review the current plan before evaluating again.',
  target_changed: 'The physical destination table changed. Review the current plan before evaluating again.',
  evaluation_stale: 'The last evaluation has expired. Refresh Evidence after the scheduled evaluator runs.',
  no_verified_write: 'No positive verified write is available for this reviewed plan and destination.',
};

function RecordedTime({ value, missing }: { value: string | null; missing: string }) {
  return value ? <time dateTime={value}>{new Date(value).toLocaleString()}</time> : <>{missing}</>;
}

export default function ExplorerAssetFreshness({ schema, table, version }: { schema: string; table: string; version: number }) {
  const [snapshot, setSnapshot] = useState<AssetFreshness>();
  const [candidates, setCandidates] = useState<AssetFreshnessCandidate[]>();
  const [planId, setPlanId] = useState('');
  const [minutes, setMinutes] = useState('60');
  const [failed, setFailed] = useState(false);
  const [candidateFailed, setCandidateFailed] = useState(false);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  const [request, setRequest] = useState<AssetFreshnessChange>();
  const [busy, setBusy] = useState(false);
  const [expiredAssessment, setExpiredAssessment] = useState<AssetFreshness['assessment']>();
  const mounted = useRef(false);
  const pending = useRef(false);

  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    const controller = new AbortController();
    setSnapshot(undefined); setCandidates(undefined); setFailed(false); setCandidateFailed(false);
    void fetchAssetFreshness(schema, table, controller.signal).then(value => {
      if (controller.signal.aborted) return;
      setSnapshot(value);
      setPlanId(value.policy?.planId ?? '');
      setMinutes(String(value.policy?.thresholdMinutes ?? 60));
    }).catch(() => { if (!controller.signal.aborted) setFailed(true); });
    void fetchAssetFreshnessCandidates(schema, table, controller.signal)
      .then(value => { if (!controller.signal.aborted) setCandidates(value); })
      .catch(() => { if (!controller.signal.aborted) setCandidateFailed(true); });
    return () => controller.abort();
  }, [schema, table, version, revision]);

  const assessment = snapshot?.assessment;
  const expiry = assessment?.validUntil ? Date.parse(assessment.validUntil) : NaN;
  const evaluated = assessment?.state === 'healthy' || assessment?.state === 'overdue';
  const evaluationExpired = evaluated && (expiredAssessment === assessment || !Number.isFinite(expiry) || expiry <= Date.now());
  useEffect(() => {
    if (!assessment || !evaluated || !Number.isFinite(expiry) || expiry <= Date.now()) return;
    // Expire the on-screen snapshot without refreshing or resetting edits.
    const expire = () => setExpiredAssessment(assessment);
    const timer = window.setTimeout(expire, expiry - Date.now());
    const onVisibility = () => { if (Date.now() >= expiry) expire(); };
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      window.clearTimeout(timer);
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, [assessment, evaluated, expiry]);

  const policy = snapshot?.policy;
  const selected = candidates?.find(item => item.id === planId);
  const threshold = Number(minutes);
  const validThreshold = /^\d+$/.test(minutes) && threshold >= minMinutes && threshold <= maxMinutes;
  const choose = (enabled: boolean) => {
    if (!snapshot || pending.current) return;
    if (enabled) {
      if (!snapshot.available || !selected || !validThreshold) return;
      setRequest({ planId: selected.id, expectedPlanUpdatedAt: selected.updatedAt,
        expectedGeneration: policy?.generation ?? null, thresholdMinutes: threshold, enabled });
    } else if (policy?.enabled) {
      setRequest({ planId: policy.planId, expectedPlanUpdatedAt: policy.planUpdatedAt,
        expectedGeneration: policy.generation, thresholdMinutes: policy.thresholdMinutes, enabled });
    }
  };
  const submit = async () => {
    if (!request || pending.current) return;
    pending.current = true;
    setBusy(true); setError('');
    try {
      const result = await updateAssetFreshness(schema, table, request);
      if (mounted.current) {
        setSnapshot(result);
        setPlanId(result.policy?.planId ?? '');
        setMinutes(String(result.policy?.thresholdMinutes ?? 60));
      }
    } catch (failure) {
      if (mounted.current) {
        const status = (failure as { response?: { status?: number } })?.response?.status;
        setError(status === 409 ? 'The freshness policy or transfer plan changed. Review the refreshed configuration.'
          : 'The freshness policy could not be updated. Review the refreshed configuration before retrying.');
        setRevision(value => value + 1);
      }
    } finally {
      pending.current = false;
      if (mounted.current) { setBusy(false); setRequest(undefined); }
    }
  };

  return <section className="flow-evidence-card flow-evidence-freshness" aria-label="Transfer freshness">
    <header><h4>Transfer freshness</h4><span>Sandbox · confirmed writes</span></header>
    <p>Checks when the selected transfer last recorded a positive write for this table. This does not measure source data freshness.</p>
    {error && <p role="alert">{error}</p>}
    {failed ? <><p role="alert">Freshness check unavailable.</p><Button size="small" onClick={() => setRevision(value => value + 1)}>Retry freshness check</Button></>
      : !snapshot ? <p role="status">Loading freshness check…</p> : <>
        <strong role="status">{stateLabels[evaluationExpired ? 'unknown' : snapshot.assessment.state]}</strong>
        {evaluationExpired ? <p>{reasonLabels.evaluation_stale}</p>
          : snapshot.assessment.reason && <p>{reasonLabels[snapshot.assessment.reason] ?? snapshot.assessment.reason}</p>}
        <dl className="flow-evidence-freshness-facts">
          <div><dt>Threshold</dt><dd>{policy ? `${policy.thresholdMinutes.toLocaleString()} minutes` : 'Not configured'}</dd></div>
          <div><dt>Last confirmed write</dt><dd><RecordedTime value={evaluationExpired ? null : snapshot.assessment.receiptRecordedAt} missing="No verified write shown" /></dd></div>
          <div><dt>Last evaluation</dt><dd><RecordedTime value={snapshot.assessment.checkedAt} missing="Not evaluated" /></dd></div>
        </dl>
        {!snapshot.available && <p>Freshness evaluation is unavailable for this table or environment.</p>}
        <div className="flow-evidence-freshness-form">
          <label>Freshness transfer plan
            <select value={planId} onChange={event => setPlanId(event.target.value)}
              disabled={busy || !snapshot.available || !candidates?.length}>
              <option value="">Select a saved sandbox transfer</option>
              {planId && !selected && <option value={planId} disabled>Current binding · not available to select</option>}
              {candidates?.map(item => <option value={item.id} key={item.id}>{item.name}</option>)}
            </select>
          </label>
          <label>Threshold in minutes
            <Input aria-label="Threshold in minutes" type="number" min={minMinutes} max={maxMinutes} step={1}
              value={minutes} onChange={event => setMinutes(event.target.value)} disabled={busy || !snapshot.available} />
          </label>
          <div className="flow-evidence-freshness-actions">
            <Button size="small" disabled={busy || !snapshot.available || !validThreshold || !selected}
              onClick={() => choose(true)}>{policy && !policy.enabled ? 'Resume freshness check' : 'Save freshness check'}</Button>
            {policy?.enabled && <Button size="small" disabled={busy} onClick={() => choose(false)}>Pause freshness check</Button>}
          </div>
        </div>
        {snapshot.available && (candidateFailed ? <p role="alert">Transfer candidates could not be loaded. Refresh evidence to retry.</p>
          : !candidates ? <p role="status">Loading transfer candidates…</p>
            : !candidates.length && <p>No eligible saved transfer writes to this table.</p>)}
        <details>
          <summary>Freshness history</summary>
          {snapshot.history.length ? <ol className="flow-evidence-receipts" aria-label="Freshness transitions">
            {snapshot.history.map(item => <li key={item.id}>
              <div><strong>{stateLabels[item.state]}</strong><span>Policy generation {item.generation}</span></div>
              <div><span>Evaluated</span><RecordedTime value={item.checkedAt} missing="Not evaluated" /></div>
              <div><span>Confirmed write</span><RecordedTime value={item.receiptRecordedAt} missing="No verified write shown" /></div>
              {item.receiptId && <code title="Receipt ID">{item.receiptId}</code>}
              {item.reason && <p>{reasonLabels[item.reason] ?? item.reason}</p>}
            </li>)}</ol> : <p>No freshness transitions recorded.</p>}
        </details>
      </>}
    <Modal open={Boolean(request)} destroyOnHidden className="flow-surface-modal"
      title={request?.enabled ? 'Save transfer freshness check?' : 'Pause transfer freshness check?'}
      onCancel={() => { if (!busy) setRequest(undefined); }} onOk={submit} confirmLoading={busy}
      okText={request?.enabled ? 'Confirm save check' : 'Confirm pause check'}>
      {request?.enabled ? <p>Evaluate confirmed writes from <strong>{selected?.name}</strong> for
        <code> {schema}.{table}</code> with a threshold of <strong>{request.thresholdMinutes} minutes</strong>.
        Saving a check does not start a transfer or write to this table.</p> : <p>Pause future freshness evaluations.
        This preserves the check history and does not change transfers or data.</p>}
    </Modal>
  </section>;
}
