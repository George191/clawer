import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { Button } from '@/components/ui';
import { fetchWeeklyJobEvidence } from '../../services/api';
import type { WeeklyJobEvidence, WeeklyJobQuality } from '../../services/api';
import { getSessionState } from '../../services/session';
import { canonicalTransferReceipt } from './transferReceiptFocus';

type Outcome = 'healthy' | 'unhealthy';
type Props = {
  schema: string; table: string; identity: string; generation: number; validUntil: string;
  outcome: Outcome; observation: NonNullable<WeeklyJobQuality['observation']>;
};

const validTime = (value: string) => typeof value === 'string' && Number.isFinite(Date.parse(value));

function UtcTime({ value }: { value: string }) {
  return <time dateTime={value}>{new Date(value).toLocaleString(undefined, { timeZone: 'UTC', timeZoneName: 'short' })}</time>;
}

export default function ExplorerWeeklyRunEvidence(props: Props) {
  const { observation } = props;
  return <RunEvidence key={JSON.stringify([
    props.schema, props.table, props.identity, props.generation, props.validUntil, props.outcome, observation,
  ])} {...props} />;
}

function RunEvidence({ schema, table, identity, generation, validUntil, outcome, observation }: Props) {
  const pending = useRef<AbortController>();
  const [evidence, setEvidence] = useState<WeeklyJobEvidence>();
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  useEffect(() => () => pending.current?.abort(), []);

  const live = (signal?: AbortSignal) => {
    const session = getSessionState();
    return !signal?.aborted && session.authenticated && !session.locked && session.identity === identity &&
      validTime(validUntil) && Date.now() < Date.parse(validUntil);
  };
  const query = async () => {
    if (pending.current || !live()) return;
    const read = new AbortController();
    pending.current = read;
    setEvidence(undefined); setFailed(false); setBusy(true);
    try {
      const result = await fetchWeeklyJobEvidence(schema, table, generation, observation.slot, read.signal);
      if (!live(read.signal)) return;
      const sameSlot = result.slot === observation.slot && result.finishedAt === observation.finishedAt &&
        validTime(result.slot) && validTime(result.finishedAt) && canonicalTransferReceipt(result.jobId);
      const matches = outcome === 'healthy' ? result.kind === 'receipt' && canonicalTransferReceipt(result.receiptId) &&
        result.receiptId === observation.receiptId && validTime(result.recordedAt) && Number.isSafeInteger(result.rowsAffected) &&
        result.rowsAffected > 0 && result.rowsAffected === observation.rowsAffected :
        result.kind === 'target_constraint' && canonicalTransferReceipt(result.attemptId) && validTime(result.startedAt) && validTime(result.attemptFinishedAt);
      if (!sameSlot || !matches) { setFailed(true); return; }
      setEvidence(result);
    } catch {
      if (live(read.signal)) setFailed(true);
    } finally {
      if (pending.current === read) pending.current = undefined;
      if (live(read.signal)) setBusy(false);
    }
  };

  return <section aria-label="Verified weekly run evidence">
    <Button size="small" loading={busy} onClick={() => void query()}>Verify run evidence</Button>
    {failed && <p role="status">Verified run evidence is unavailable.</p>}
    {evidence?.kind === 'receipt' && <p>
      <strong>Verified committed receipt.</strong> Rows affected: {evidence.rowsAffected.toLocaleString()}.{' '}
      <Link className="flow-evidence-lineage-link" to={`/ontology/explorer?${new URLSearchParams({
        schema, table, view: 'evidence', transferReceipt: evidence.receiptId,
      })}`}>Open receipt in Explorer</Link>
    </p>}
    {evidence?.kind === 'target_constraint' && <>
      <p><strong>Verified rejection evidence.</strong> A target constraint rejected the attempt. This does not confirm data movement.</p>
      <dl className="flow-evidence-freshness-facts">
        <div><dt>Job ID</dt><dd><code>{evidence.jobId}</code></dd></div>
        <div><dt>UTC slot</dt><dd><UtcTime value={evidence.slot} /></dd></div>
        <div><dt>Job finished at (UTC)</dt><dd><UtcTime value={evidence.finishedAt} /></dd></div>
        <div><dt>Attempt ID</dt><dd><code>{evidence.attemptId}</code></dd></div>
        <div><dt>Attempt started at (UTC)</dt><dd><UtcTime value={evidence.startedAt} /></dd></div>
        <div><dt>Attempt finished at (UTC)</dt><dd><UtcTime value={evidence.attemptFinishedAt} /></dd></div>
      </dl>
    </>}
  </section>;
}
