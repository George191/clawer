import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { fetchTableExportLineage, type TableExportLineage, type TableExportLineageReceipt } from '../../services/api';
import './table-evidence.css';

export const TABLE_EXPORT_SOURCE = { schema: 'ts_table_export_sandbox', table: 'records' } as const;
export const tableExportLineagePath = `/ontology/lineage?${new URLSearchParams(TABLE_EXPORT_SOURCE)}`;
const manageExportPath = '/ontology/connections?tableExport=1';
const stateLabels = { enabled: 'Configured · execution enabled', inactive: 'Configured · execution disabled', stale: 'Blocked · review is stale' };
const reviewLabels: Record<TableExportLineageReceipt['reviewRelation'], string> = {
  current_review: 'Current review', earlier_review: 'Earlier review', different_binding: 'Different receiver binding',
};
const displayTime = (value: string) => new Date(value).toLocaleString();

export default function TableExportEvidence({ mode, version }: { mode: 'explorer' | 'lineage'; version: number }) {
  const [record, setRecord] = useState<TableExportLineage>();
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    const request = new AbortController();
    setRecord(undefined);
    setFailed(false);
    void fetchTableExportLineage(request.signal).then(value => {
      if (!request.signal.aborted) setRecord(value);
    }).catch(() => { if (!request.signal.aborted) setFailed(true); });
    return () => request.abort();
  }, [version]);

  const hasCurrentObservation = Boolean(record?.observed && record.latestObservation?.observed &&
    record.latestObservation.rowsAffected > 0 && record.latestObservation.reviewRelation === 'current_review');
  const observation = hasCurrentObservation ? record?.latestObservation : undefined;

  return <section className={`flow-table-export-evidence${mode === 'lineage' ? ' is-compact' : ''}`} aria-label="Table export evidence">
    <div className="flow-evidence-grid">
      <section className="flow-evidence-card" aria-label="Export lineage">
        <header><h4>Table export lineage</h4><span>{failed ? 'Evidence unavailable' : !record ? 'Checking evidence' : observation ? 'Observed export' : 'No observed edge'}</span></header>
        {failed ? <p role="alert">Export lineage could not be loaded. Refresh to retry.</p> : !record ? <p role="status">Loading export lineage…</p> : <>
          <strong>{record.declaration ? stateLabels[record.declaration.state] : 'No saved export review'}</strong>
          {record.declaration && <p>{record.declaration.name} · Reviewed <time dateTime={record.declaration.reviewedAt}>{displayTime(record.declaration.reviewedAt)}</time></p>}
          <div className="flow-evidence-path flow-table-export-path" data-observed={Boolean(observation)} role="group" aria-label={observation ? 'Observed export edge' : 'Fixed outbound scope without observed edge'}>
            <div className="flow-evidence-node flow-evidence-current"><span>Source table</span><code>{record.source.schema}.{record.source.table}</code></div>
            {observation && <span className="flow-evidence-arrow" aria-hidden="true">→</span>}
            <div className="flow-evidence-node"><span>{record.destination.label}</span><code>{record.destination.schema}.{record.destination.table}</code></div>
          </div>
          {observation ? <p><strong>{observation.rowsAffected.toLocaleString()} rows affected</strong> · Receiver recorded <time dateTime={observation.recordedAt}>{displayTime(observation.recordedAt)}</time>. Sandbox evidence only.</p>
            : <p>No positive receiver-confirmed receipt for the current review. Configuration, attempts and zero-row receipts do not establish an observed edge.</p>}
        </>}
        <div className="flow-table-export-links">
          {mode === 'explorer' && <Link className="flow-evidence-lineage-link" to={tableExportLineagePath}>Open table export in Lineage</Link>}
          <Link className="flow-evidence-lineage-link" to={manageExportPath}>Manage table export</Link>
        </div>
      </section>
      <section className="flow-evidence-card" aria-label="Receiver receipt history">
        <header><h4>Receiver receipt history</h4><span>Confirmed receipts only</span></header>
        {failed ? <p>Receiver receipts could not be loaded.</p> : !record ? <p role="status">Loading receiver receipts…</p>
          : record.receipts.length ? <ul className="flow-evidence-receipts">{record.receipts.map(receipt => <li key={receipt.id}>
            <div><strong>{receipt.rowsAffected.toLocaleString()} rows affected</strong><span>{reviewLabels[receipt.reviewRelation]}</span></div>
            <div><span>Receiver recorded</span><time dateTime={receipt.recordedAt}>{displayTime(receipt.recordedAt)}</time></div>
            {receipt.rowsAffected === 0 && <span>No observed data movement</span>}
            <code title="Receipt ID">{receipt.id}</code>
          </li>)}</ul> : <p>No receiver-confirmed receipts.</p>}
      </section>
    </div>
  </section>;
}
