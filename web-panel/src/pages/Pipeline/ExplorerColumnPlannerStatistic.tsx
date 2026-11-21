import { useEffect, useRef, useState } from 'react';
import { Button, Modal } from '@/components/ui';
import { BarChartOutlined, InfoCircleOutlined, ReloadOutlined } from '@/components/ui/icons';
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
    <Button size="small" className="physical-planner-trigger" icon={<BarChartOutlined/>} aria-label={`Planner statistics: ${column}`} onClick={() => void inspect()}>Planner statistics</Button>
    <Modal open={open} destroyOnHidden className="flow-surface-modal physical-planner-statistics-modal" title="Planner statistics" width={520} onCancel={cancel} footer={<Button onClick={cancel}>Done</Button>}>
      <div className="physical-planner-context"><BarChartOutlined/><div><strong>{column}</strong><code>{schema}.{table}</code></div><span className="physical-planner-source">Stored metadata</span></div>
      {!result ? <div className="physical-planner-state" role="status"><ReloadOutlined spin/>Reading stored planner metadata…</div> : result.status === 'unavailable'
        ? <div className="physical-planner-state" role="status"><InfoCircleOutlined/><div><strong>Statistics unavailable</strong><p>Stored planner metadata could not be read for this column.</p></div></div>
        : <dl className="physical-planner-metrics">
          <div><dt>Estimated null fraction</dt><dd>{percent.format(result.nullFraction)}</dd></div>
          <div><dt>Observed at (UTC)</dt><dd><time dateTime={result.observedAt}>
            {new Date(result.observedAt).toLocaleString(undefined, { timeZone: 'UTC', timeZoneName: 'short' })}
          </time></dd></div>
        </dl>}
      <p className="physical-planner-help">Planner estimates may be outdated and are not exact measurements. Opening this view does not recompute statistics.</p>
      {result?.status === 'observed' && <p className="physical-planner-help">Observed at records when this metadata was read, not when PostgreSQL analyzed the column.</p>}
    </Modal>
  </>;
}
