import { LoadingIndicator } from '@/components/ui/loading';
import { useEffect, useRef, useState } from 'react';
import { Button, Modal } from '@/components/ui';
import { BarChartOutlined, InfoCircleOutlined } from '@/components/ui/icons';
import { fetchColumnPlannerStatistic } from '../../services/api';
import type { ColumnPlannerStatistic } from '../../services/api';
import { getSessionState } from '../../services/session';
import { ontologyError } from '../../services/ontology';
import useExplorerErrorToast from './useExplorerErrorToast';

const percent = new Intl.NumberFormat(undefined, { style: 'percent', maximumSignificantDigits: 6 });

export default function ExplorerColumnPlannerStatistic({ schema, table, column, identity }: {
  schema: string; table: string; column: string; identity: string;
}) {
  const [open, setOpen] = useState(false);
  const [result, setResult] = useState<ColumnPlannerStatistic>();
  const [error, setError] = useState('');
  const pending = useRef<AbortController>();
  useEffect(() => () => pending.current?.abort(), []);
  const live = (signal?: AbortSignal) => {
    const session = getSessionState();
    return !signal?.aborted && session.authenticated && !session.locked && session.identity === identity;
  };
  const cancel = () => {
    pending.current?.abort(); pending.current = undefined;
    setResult(undefined); setError(''); setOpen(false);
  };
  const inspect = async () => {
    if (pending.current || !live()) return;
    const read = new AbortController(); pending.current = read;
    setResult(undefined); setError(''); setOpen(true);
    try {
      const value = await fetchColumnPlannerStatistic(schema, table, column, read.signal);
      if (!live(read.signal)) return;
      setResult(value.status === 'observed' && typeof value.nullFraction === 'number' && Number.isFinite(value.nullFraction) &&
        value.nullFraction >= 0 && value.nullFraction <= 1 && typeof value.observedAt === 'string' &&
        Number.isFinite(Date.parse(value.observedAt)) ? value : { status: 'unavailable' });
    } catch (failure) {
      if (live(read.signal)) setError(ontologyError(failure, 'Stored planner metadata could not be read.'));
    } finally {
      if (pending.current === read) pending.current = undefined;
    }
  };
  useExplorerErrorToast(error, 'Unable to load planner statistics', () => void inspect());
  return <>
    <Button size="small" className="physical-planner-trigger" icon={<BarChartOutlined/>} aria-label={`Planner statistics: ${column}`} onClick={() => void inspect()}>Planner statistics</Button>
    <Modal open={open} destroyOnHidden className="flow-surface-modal physical-planner-statistics-modal" title="Planner statistics" width={520} onCancel={cancel} footer={<Button onClick={cancel}>Done</Button>}>
      <div className="physical-planner-context"><div className="physical-planner-heading"><BarChartOutlined/><strong>{column}</strong><span className="physical-planner-source">Stored metadata</span></div><code>{schema}.{table}</code></div>
      {error ? null : !result ? <div className="physical-planner-state" role="status"><LoadingIndicator size={20}>Reading stored planner metadata…</LoadingIndicator></div> : result.status === 'unavailable'
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
