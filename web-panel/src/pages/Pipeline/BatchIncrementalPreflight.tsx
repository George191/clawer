import { useEffect, useRef, useState } from 'react';
import { Button } from '@/components/ui';
import { verifyNextCommittedBatch } from '../../services/api';
import type { BatchIncrementalPreflight as Preflight } from '../../services/api';
import { getSessionState } from '../../services/session';

const decimal = (value: unknown): value is string => typeof value === 'string' && /^(0|[1-9]\d{0,18})$/.test(value) &&
  BigInt(value) <= 9223372036854775807n;

function valid(value: Preflight) {
  if (!decimal(value.observedAppliedThrough) || !decimal(value.sourcePublishedThrough) ||
    typeof value.observedAt !== 'string' || !Number.isFinite(Date.parse(value.observedAt))) return false;
  const checkpoint = BigInt(value.observedAppliedThrough), published = BigInt(value.sourcePublishedThrough);
  return value.status === 'no_new_data' ? published === checkpoint && value.nextOrdinal == null && value.nextRowCount == null :
    value.status === 'matched' && decimal(value.nextOrdinal) && BigInt(value.nextOrdinal) === checkpoint + 1n &&
    BigInt(value.nextOrdinal) <= published && typeof value.nextRowCount === 'number' &&
    Number.isSafeInteger(value.nextRowCount) && value.nextRowCount > 0 && value.nextRowCount <= 50;
}

export default function BatchIncrementalPreflight({ planId, expectedUpdatedAt, expectedSourceUpdatedAt, identity }: {
  planId: string; expectedUpdatedAt: string; expectedSourceUpdatedAt: string; identity: string;
}) {
  const [result, setResult] = useState<Preflight>();
  const [busy, setBusy] = useState(false);
  const [unavailable, setUnavailable] = useState(false);
  const pending = useRef<AbortController>();
  useEffect(() => () => pending.current?.abort(), []);
  const live = (signal?: AbortSignal) => {
    const session = getSessionState();
    return !signal?.aborted && session.authenticated && !session.locked && identity === session.identity;
  };
  const verify = async () => {
    if (pending.current || !live()) return;
    const read = new AbortController(); pending.current = read;
    setBusy(true); setResult(undefined); setUnavailable(false);
    try {
      const value = await verifyNextCommittedBatch(planId, { expectedUpdatedAt, expectedSourceUpdatedAt }, read.signal);
      if (live(read.signal)) { if (valid(value)) setResult(value); else setUnavailable(true); }
    } catch {
      if (live(read.signal)) setUnavailable(true);
    } finally {
      if (pending.current === read) { pending.current = undefined; if (live(read.signal)) setBusy(false); }
    }
  };
  return <section aria-label="Next committed batch check">
    <Button size="small" loading={busy} disabled={busy} onClick={() => void verify()}>Verify next committed batch</Button>
    {unavailable && <p role="status">The next committed batch could not be verified. No data was changed.</p>}
    {result && <>
      <p role="status">{result.status === 'matched' ? 'Next committed batch verified.' : 'No new data as of this check.'}</p>
      <dl className="ontology-connections-summary">
        <div><dt>Observed target checkpoint</dt><dd><code>{result.observedAppliedThrough}</code></dd></div>
        <div><dt>Observed source published through</dt><dd><code>{result.sourcePublishedThrough}</code></dd></div>
        {result.status === 'matched' && <>
          <div><dt>Next batch ordinal</dt><dd><code>{result.nextOrdinal}</code></dd></div>
          <div><dt>Rows in next batch</dt><dd>{result.nextRowCount}</dd></div>
        </>}
        <div><dt>Observed at (UTC)</dt><dd><time dateTime={result.observedAt}>
          {new Date(result.observedAt).toLocaleString(undefined, { timeZone: 'UTC', timeZoneName: 'short' })}
        </time></dd></div>
      </dl>
      <p>This was a read-only observation. It does not reserve a batch, approve or guarantee a run, or show data movement.</p>
    </>}
  </section>;
}
