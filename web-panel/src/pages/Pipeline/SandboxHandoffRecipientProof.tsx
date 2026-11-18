import { useEffect, useId, useRef, useState } from 'react';
import { Button, Input } from '@/components/ui';
import { fetchSandboxHandoffRecipientCapability, verifySandboxHandoffRecipientReceipt } from '../../services/api';
import type { SandboxHandoffRecipientEvidence, SandboxHandoffSink } from '../../services/api';
import { getSessionState } from '../../services/session';
import { SandboxMarkings, useSandboxIdentity } from './SandboxMarkings';
import { canonicalTransferReceipt } from './transferReceiptFocus';

function current(identity: string, signal?: AbortSignal) {
  const state = getSessionState();
  return !signal?.aborted && state.authenticated && !state.locked && state.identity === identity;
}

export default function SandboxHandoffRecipientProof({ authorizedIdentity }: { authorizedIdentity: string }) {
  const identity = useSandboxIdentity();
  return identity && identity === authorizedIdentity ? <RecipientProofScope key={identity} identity={identity} /> : null;
}

function RecipientProofScope({ identity }: { identity: string }) {
  const [sink, setSink] = useState<SandboxHandoffSink>();
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const read = new AbortController();
    void fetchSandboxHandoffRecipientCapability(read.signal).then(value => {
      if (current(identity, read.signal)) setSink(value.available && value.sink?.id === 'synthetic-review-sink' &&
        canonicalTransferReceipt(value.sink.generation) && value.sink.label ? value.sink : undefined);
    }).catch(() => { if (current(identity, read.signal)) setSink(undefined); });
    return () => read.abort();
  }, [identity, revision]);
  return sink ? <RecipientProofForm key={sink.generation} identity={identity} sink={sink}
    onRefresh={() => { setSink(undefined); setRevision(value => value + 1); }} /> : null;
}

function RecipientProofForm({ identity, sink, onRefresh }: { identity: string; sink: SandboxHandoffSink; onRefresh: () => void }) {
  const title = useId();
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const [receipt, setReceipt] = useState<SandboxHandoffRecipientEvidence>();
  const pending = useRef<AbortController>();
  useEffect(() => () => pending.current?.abort(), []);

  const id = input.trim().toLowerCase();
  const valid = canonicalTransferReceipt(id);
  const clear = () => {
    pending.current?.abort(); pending.current = undefined;
    setLoading(false); setFailed(false); setReceipt(undefined);
  };
  const verify = async () => {
    if (!valid || pending.current || !current(identity)) return;
    clear();
    const request = new AbortController(); pending.current = request;
    setLoading(true);
    try {
      const result = await verifySandboxHandoffRecipientReceipt(id, sink.generation, request.signal);
      if (!current(identity, request.signal)) return;
      const marks = result.markings;
      const validMarkings = Array.isArray(marks) && marks[0] === 'sandbox-synthetic' &&
        (marks.length === 1 || marks.length === 2 && marks[1] === 'sandbox-restricted');
      if (result.id !== id || result.sink?.id !== sink.id || result.sink.generation !== sink.generation ||
        !validMarkings || !Number.isSafeInteger(result.rowsRecorded) || result.rowsRecorded < 0 || !Number.isFinite(Date.parse(result.recordedAt))) {
        setFailed(true); return;
      }
      setReceipt(result);
    } catch {
      if (current(identity, request.signal)) setFailed(true);
    } finally {
      if (pending.current === request) {
        pending.current = undefined;
        if (current(identity, request.signal)) setLoading(false);
      }
    }
  };
  return <section className="ontology-source-catalog ontology-source-sync" aria-labelledby={title}>
    <header><div><h3 id={title}>Verify a handoff receipt</h3><p>Check one receipt ID you were given for {sink.label}. No receipt list or payload is available here.</p></div></header>
    <div className="ontology-source-sync__body">
      <form className="ontology-source-sync__form" onSubmit={event => { event.preventDefault(); void verify(); }}>
        <label>Handoff receipt ID<Input aria-label="Handoff receipt ID" autoComplete="off" maxLength={80} value={input}
          onChange={event => { clear(); setInput(event.target.value); }} /></label>
        <div className="ontology-source-sync__actions">
          <Button htmlType="submit" size="small" loading={loading} disabled={!valid || loading}>Verify receipt</Button>
          {failed && <Button size="small" onClick={() => { clear(); onRefresh(); }}>Recheck availability</Button>}
        </div>
      </form>
      {failed && <p role="status">Receipt could not be verified.</p>}
      {receipt && <section aria-label="Verified handoff receipt">
        <strong role="status">Stored delivery receipt verified.</strong>
        <dl className="ontology-connections-summary">
          <div><dt>Receipt ID</dt><dd><code>{receipt.id}</code></dd></div>
          <div><dt>Recorded at (UTC)</dt><dd><time dateTime={receipt.recordedAt}>
            {new Date(receipt.recordedAt).toLocaleString(undefined, { timeZone: 'UTC', timeZoneName: 'short' })}
          </time></dd></div>
          <div><dt>Rows recorded on receipt</dt><dd>{receipt.rowsRecorded.toLocaleString()}</dd></div>
        </dl>
        <SandboxMarkings markings={receipt.markings} />
        <p>This verifies stored delivery metadata. It does not read the payload or prove that any payload is still retained.</p>
      </section>}
    </div>
  </section>;
}
