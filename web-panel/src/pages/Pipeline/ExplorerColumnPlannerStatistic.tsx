import { useEffect, useRef, useState } from 'react';
import { Button, Modal } from '@/components/ui';
import { fetchColumnPlannerStatistic } from '../../services/api';
import type { ColumnPlannerStatistic } from '../../services/api';
import { getSessionState } from '../../services/session';

const percent = new Intl.NumberFormat(undefined, { style: 'percent', maximumSignificantDigits: 6 });

export default function ExplorerColumnPlannerStatistic({ schema, table, column, identity }: {
  schema: string; table: string; column: string; identity: string;
}) {
  const [open, setOpen] = useState(false);
  const [result, setResult] = useState<ColumnPlannerStatistic>();
  const pending = useRef<AbortController>();
  useEffect(() => () => pending.current?.abort(), []);
  const live = (signal?: AbortSignal) => {
    const session = getSessionState();
    return !signal?.aborted && session.authenticated && !session.locked && session.identity === identity;
  };
  const cancel = () => {
    pending.current?.abort(); pending.current = undefined;
    setResult(undefined); setOpen(false);
  };
  const inspect = async () => {
    if (pending.current || !live()) return;
    const read = new AbortController(); pending.current = read;
    setResult(undefined); setOpen(true);
    try {
      const value = await fetchColumnPlannerStatistic(schema, table, column, read.signal);
      if (!live(read.signal)) return;
      setResult(value.status === 'observed' && typeof value.nullFraction === 'number' && Number.isFinite(value.nullFraction) &&
        value.nullFraction >= 0 && value.nullFraction <= 1 && typeof value.observedAt === 'string' &&
        Number.isFinite(Date.parse(value.observedAt)) ? value : { status: 'unavailable' });
    } catch {
      if (live(read.signal)) setResult({ status: 'unavailable' });
    } finally {
      if (pending.current === read) pending.current = undefined;
    }
  };
  return <>
    <Button size="small" aria-label={`Planner statistics: ${column}`} onClick={() => void inspect()}>Planner statistics</Button>
    <Modal open={open} destroyOnHidden className="flow-surface-modal" title="Stored planner statistic" onCancel={cancel} footer={null}>
      <p><code>{schema}.{table}.{column}</code></p>
      <p>Stored planner estimate; may be outdated. It is not an exact measurement, and opening this view does not recompute statistics.</p>
      {!result ? <p role="status">Reading stored planner metadata…</p> : result.status === 'unavailable'
        ? <p role="status">Stored planner statistic is unavailable.</p>
        : <dl className="flow-evidence-freshness-facts">
          <div><dt>Estimated null fraction</dt><dd>{percent.format(result.nullFraction)}</dd></div>
          <div><dt>Observed at (UTC)</dt><dd><time dateTime={result.observedAt}>
            {new Date(result.observedAt).toLocaleString(undefined, { timeZone: 'UTC', timeZoneName: 'short' })}
          </time></dd></div>
        </dl>}
      {result?.status === 'observed' && <p>The observation time records when this metadata was read, not when PostgreSQL analyzed the column.</p>}
    </Modal>
  </>;
}
