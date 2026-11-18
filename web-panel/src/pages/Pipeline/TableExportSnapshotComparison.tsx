import { useEffect, useRef, useState } from 'react';
import { Button } from '@/components/ui';
import { fetchTableExportSnapshotCapability, previewTableExportSnapshot } from '../../services/api';
import type { TableExportSnapshotResult } from '../../services/api';
import { getSessionState } from '../../services/session';

const uuid = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/;
const metrics = [
  ['sourceRows', 'Source rows observed'], ['targetRows', 'Target rows observed'],
  ['added', 'Would add'], ['changed', 'Would change'], ['unchanged', 'Unchanged'], ['wouldRemove', 'Would remove'],
] as const;
const matches = (result: TableExportSnapshotResult, revision: string) =>
  result.configRevision === revision && metrics.every(([key]) => Number.isSafeInteger(result[key]) && result[key] >= 0) &&
  result.sourceRows === result.added + result.changed + result.unchanged &&
  result.targetRows === result.wouldRemove + result.changed + result.unchanged;

export default function TableExportSnapshotComparison({ identity }: { identity: string }) {
  const [revision, setRevision] = useState<string>();
  const [result, setResult] = useState<TableExportSnapshotResult>();
  const [loading, setLoading] = useState(false);
  const [notice, setNotice] = useState<'unavailable' | 'recheck'>();
  const [reload, setReload] = useState(0);
  const pending = useRef<AbortController>();
  const live = (signal?: AbortSignal) => {
    const session = getSessionState();
    return !signal?.aborted && session.authenticated && !session.locked && session.identity === identity;
  };
  useEffect(() => {
    const controller = new AbortController();
    setRevision(undefined);
    void fetchTableExportSnapshotCapability(controller.signal).then(value => {
      if (!live(controller.signal)) return;
      setRevision(value.available && typeof value.configRevision === 'string' && uuid.test(value.configRevision) ? value.configRevision : undefined);
      setNotice(undefined);
    }).catch(() => { if (live(controller.signal)) setRevision(undefined); });
    return () => controller.abort();
  }, [identity, reload]);
  useEffect(() => () => pending.current?.abort(), []);

  const preview = async () => {
    if (!revision || pending.current || !live()) return;
    const controller = new AbortController(); pending.current = controller;
    setLoading(true); setResult(undefined); setNotice(undefined);
    try {
      const value = await previewTableExportSnapshot(revision, controller.signal);
      if (!live(controller.signal)) return;
      if (matches(value, revision)) setResult(value);
      else { setRevision(undefined); setNotice('recheck'); }
    } catch (failure) {
      if (!live(controller.signal)) return;
      const status = (failure as { response?: { status?: number } })?.response?.status;
      if (status === 401 || status === 403 || status === 404) { setRevision(undefined); setNotice(undefined); }
      else if (status === 409) { setRevision(undefined); setNotice('recheck'); }
      else setNotice('unavailable');
    } finally {
      if (pending.current === controller) pending.current = undefined;
      if (live(controller.signal)) setLoading(false);
    }
  };

  if (!revision && !notice) return null;
  return <section className="ontology-source-sync__body ontology-table-export__mapping" aria-label="Snapshot comparison">
    <h4>Snapshot comparison</h4>
    <p>Read-only comparison of two independently observed snapshots for a separate fixed synthetic pilot. It does not use the selected export receiver or its approval.</p>
    {revision && <Button size="small" loading={loading} disabled={loading} onClick={preview}>Preview snapshot comparison</Button>}
    {notice === 'recheck' && <>
      <p role="status">The snapshot profile changed or could not be verified. Refresh its availability before previewing again.</p>
      <Button size="small" onClick={() => { setNotice(undefined); setResult(undefined); setReload(value => value + 1); }}>Refresh snapshot availability</Button>
    </>}
    {notice === 'unavailable' && <p role="status">Snapshot comparison could not be verified. No data was written.</p>}
    {result && <>
      <p role="status">Hypothetical changes only. No writes or removals occurred or were approved.</p>
      <dl className="ontology-connections-summary">
        {metrics.map(([key, label]) => <div key={key}><dt>{label}</dt><dd>{result[key].toLocaleString()}</dd></div>)}
      </dl>
    </>}
  </section>;
}
