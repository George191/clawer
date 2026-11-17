import { useEffect, useId, useRef, useState } from 'react';
import { Button, Modal } from '@/components/ui';
import { fetchSyntheticPushInbox, setSyntheticPushIntakePaused } from '../../services/api';
import type { SyntheticPushInboxStatus } from '../../services/api';
import { getSessionState } from '../../services/session';
import { useSandboxIdentity } from './SandboxMarkings';
import SyntheticPushApply from './SyntheticPushApply';

export default function SyntheticPushInbox() {
  const identity = useSandboxIdentity();
  return identity ? <PushInboxStatus key={identity} identity={identity} /> : null;
}

function AcceptedAt({ value }: { value?: string }) {
  return value && Number.isFinite(Date.parse(value)) ? <time dateTime={value}>{new Date(value).toLocaleString()}</time> : <>Not recorded</>;
}

function PushInboxStatus({ identity }: { identity: string }) {
  const title = useId();
  const [snapshot, setSnapshot] = useState<SyntheticPushInboxStatus>();
  const [refreshing, setRefreshing] = useState(false);
  const [revision, setRevision] = useState(0);
  const [applyRevision, setApplyRevision] = useState(0);
  const [decision, setDecision] = useState<{ paused: boolean; expectedGeneration: number }>();
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState('');
  const [noticeIsError, setNoticeIsError] = useState(false);
  const action = useRef<AbortController>();
  const current = (signal: AbortSignal) => {
    const session = getSessionState();
    return !signal.aborted && session.authenticated && !session.locked && session.identity === identity;
  };
  useEffect(() => () => action.current?.abort(), []);
  const refresh = (includeApply: boolean) => {
    setRefreshing(true);
    setRevision(value => value + 1);
    if (includeApply) setApplyRevision(value => value + 1);
  };

  useEffect(() => {
    const request = new AbortController();
    setRefreshing(true);
    void fetchSyntheticPushInbox(request.signal).then(value => {
      if (current(request.signal)) setSnapshot(value);
    }).catch(() => {
      if (current(request.signal)) setSnapshot(undefined);
    }).finally(() => {
      if (current(request.signal)) setRefreshing(false);
    });
    return () => request.abort();
  }, [revision]);

  if (!snapshot?.available || snapshot.state === 'disabled' || snapshot.state === 'unconfigured') return null;
  const generation = snapshot.intakeGeneration;
  const controllable = snapshot.intakeControlAvailable === true && typeof generation === 'number' && Number.isSafeInteger(generation) && generation >= 0;
  const checking = saving || refreshing && (controllable || noticeIsError || snapshot.state === 'paused');
  const paused = snapshot.state === 'paused';
  const confirm = async () => {
    if (action.current || !decision || !controllable || refreshing || decision.expectedGeneration !== generation || decision.paused === paused) return;
    const controller = new AbortController();
    if (!current(controller.signal)) return;
    action.current = controller;
    setSaving(true);
    setNotice('');
    try {
      const value = await setSyntheticPushIntakePaused(decision.expectedGeneration, decision.paused, controller.signal);
      if (!current(controller.signal)) return;
      if (!value.available || (value.state === 'paused') !== decision.paused) throw new Error('Unconfirmed intake state');
      setSnapshot(value);
      setNoticeIsError(false);
      setNotice(decision.paused ? 'Intake paused.' : 'Intake resumed.');
    } catch (failure) {
      if (!current(controller.signal)) return;
      const status = (failure as { response?: { status?: number } })?.response?.status;
      setNoticeIsError(true);
      setNotice(status === 409
        ? 'Inbox status changed. Review the refreshed status before making another change.'
        : 'The update could not be confirmed. Check the refreshed inbox status before trying again.');
      refresh(false);
    } finally {
      action.current = undefined;
      if (current(controller.signal)) { setDecision(undefined); setSaving(false); }
    }
  };
  const [latest, ...earlier] = snapshot.recent;
  return <section className="ontology-source-catalog ontology-source-sync" aria-labelledby={title} aria-busy={refreshing}>
    <header>
      <div><h3 id={title}>Synthetic Push inbox</h3><p>{controllable || paused ? 'Intake status for local synthetic events; separate from application and CDC replay.' : 'Read-only intake status for local synthetic events; separate from CDC replay.'}</p></div>
      <Button size="small" loading={refreshing} disabled={saving || Boolean(decision)} onClick={() => { setNotice(''); setNoticeIsError(false); refresh(true); }}>Refresh inbox status</Button>
    </header>
    <div className="ontology-source-sync__body">
      <p role="status">{checking ? 'Checking intake status…' : paused ? 'Intake paused' : snapshot.state === 'full' ? 'Inbox full' : 'Inbox available'} · Acceptance only</p>
      {paused && !checking && <p>Paused at <AcceptedAt value={snapshot.pausedAt} />. No new events are accepted while paused. Existing receipts and independently eligible application are unchanged.</p>}
      {controllable && <div><Button size="small" disabled={refreshing || saving || Boolean(decision)} onClick={() => setDecision({ paused: !paused, expectedGeneration: generation })}>{paused ? 'Resume intake' : 'Pause intake'}</Button></div>}
      {notice && <p role={noticeIsError ? 'alert' : 'status'}>{notice}</p>}
      <dl className="ontology-connections-summary">
        <div><dt>Accepted events</dt><dd>{snapshot.acceptedCount.toLocaleString()}</dd></div>
        <div><dt>Latest accepted receipt</dt><dd>{latest ? <code>{latest.id}</code> : 'None recorded'}</dd></div>
        <div><dt>Accepted at</dt><dd><AcceptedAt value={latest?.acceptedAt} /></dd></div>
      </dl>
      {earlier.length > 0 && <details>
        <summary>Earlier accepted receipts</summary>
        <ol className="ontology-source-sync__items">{earlier.map(receipt => <li key={receipt.id}>
          <span>Receipt <code>{receipt.id}</code></span><span>Accepted at <AcceptedAt value={receipt.acceptedAt} /></span>
        </li>)}</ol>
      </details>}
      <p><strong>Acceptance is not application.</strong> Inbox acceptance alone does not show that any target changed or data flowed downstream.</p>
      <SyntheticPushApply identity={identity} version={applyRevision} />
    </div>
    <Modal open={Boolean(decision)} destroyOnHidden className="flow-surface-modal" confirmLoading={saving}
      title={decision?.paused ? 'Pause synthetic intake?' : 'Resume synthetic intake?'}
      okText={decision?.paused ? 'Confirm pause' : 'Confirm resume'}
      onOk={() => void confirm()} onCancel={() => !saving && setDecision(undefined)} closable={!saving} keyboard={!saving} maskClosable={false}>
      {decision?.paused
        ? 'Pause acceptance of new local synthetic events. Previously accepted signed receipts and independently eligible application remain unchanged. This does not apply any data.'
        : 'Allow new local synthetic events to be accepted under the existing signing and capacity rules. This does not resend events or apply any data.'}
    </Modal>
  </section>;
}
