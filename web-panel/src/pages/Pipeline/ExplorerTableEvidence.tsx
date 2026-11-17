import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Button } from '@/components/ui';
import { ReloadOutlined } from '@/components/ui/icons';
import { fetchExplorerTableEvidence, fetchExplorerTransferRuns, type ExplorerTableEvidenceSnapshot, type TransferRunReceipt } from '../../services/api';
import TableExportEvidence, { TABLE_EXPORT_SOURCE } from './TableExportEvidence';
import ExplorerAssetFreshness from './ExplorerAssetFreshness';
import './table-evidence.css';

export default function ExplorerTableEvidence({ schema, table, canManage }: { schema: string; table: string; canManage: boolean }) {
    const [version, setVersion] = useState(0);
    const [snapshot, setSnapshot] = useState<ExplorerTableEvidenceSnapshot>();
    const [failed, setFailed] = useState(false);
    const [receipts, setReceipts] = useState<TransferRunReceipt[]>();
    const [receiptsFailed, setReceiptsFailed] = useState(false);
    const showReceipts = canManage && schema === 'ts_transfer_sandbox';
    const showExport = canManage && schema === TABLE_EXPORT_SOURCE.schema && table === TABLE_EXPORT_SOURCE.table;
    const refresh = () => {
        setSnapshot(undefined);
        setFailed(false);
        setReceipts(undefined);
        setReceiptsFailed(false);
        setVersion((value) => value + 1);
    };

    useEffect(() => {
        const request = new AbortController();
        fetchExplorerTableEvidence(schema, table, request.signal)
            .then((value) => { if (!request.signal.aborted) setSnapshot(value); })
            .catch(() => { if (!request.signal.aborted) setFailed(true); });
        return () => request.abort();
    }, [schema, table, version]);

    useEffect(() => {
        if (!showReceipts) return;
        const request = new AbortController();
        fetchExplorerTransferRuns(schema, table, request.signal)
            .then(value => { if (!request.signal.aborted) setReceipts(value); })
            .catch(() => { if (!request.signal.aborted) setReceiptsFailed(true); });
        return () => request.abort();
    }, [schema, table, version, showReceipts]);

    const observed = showReceipts && receipts?.find(run => run.rowsAffected > 0);

    return (
        <section className="flow-schema-details flow-table-evidence" aria-label="Table evidence">
            <header>
                <div><h3>Table evidence</h3><p>PostgreSQL catalog facts, plus run and lineage evidence where available.</p></div>
                <Button size="small" icon={<ReloadOutlined />} disabled={!snapshot && !failed} onClick={refresh}>{failed ? 'Retry evidence' : 'Refresh evidence'}</Button>
            </header>
            {failed ? <div className="flow-schema-state" role="alert"><strong>Evidence unavailable</strong><p>The evidence service did not return a result. Try again or return to the table preview.</p></div>
                : !snapshot ? <div className="flow-schema-state" role="status">Loading table evidence…</div>
                    : <div className="flow-evidence-grid">
                        <section className="flow-evidence-card" aria-labelledby="table-evidence-quality">
                            <header><h4 id="table-evidence-quality">Quality</h4><span>PostgreSQL catalog</span></header>
                            <dl className="flow-evidence-catalog">
                                <div><dt>Columns</dt><dd>{snapshot.quality.columnCount.toLocaleString()}</dd></div>
                                <div><dt>Primary key columns</dt><dd>{snapshot.quality.primaryKeyColumnCount.toLocaleString()}</dd></div>
                                <div><dt>NOT NULL columns</dt><dd>{snapshot.quality.notNullColumnCount.toLocaleString()}</dd></div>
                            </dl>
                            <p>These schema counts do not measure missing values, duplicates, or completeness.</p>
                            {snapshot.observation !== 'metadata_only' && <>
                                <dl className="flow-evidence-observation">
                                    <div><dt>Latest filing date in this table</dt><dd className="flow-evidence-primary">{snapshot.observation === 'observed' && snapshot.latestFiledDate
                                        ? <time dateTime={snapshot.latestFiledDate}>{snapshot.latestFiledDate}</time>
                                        : snapshot.observation === 'no_filed_date' ? 'No filing date found' : 'Not checked'}</dd></div>
                                </dl>
                                {snapshot.observation === 'safe_plan_unavailable' && <p>A bounded lookup was not available. No full table scan was run.</p>}
                                {snapshot.observation === 'no_filed_date' && <p>No non-null filing date was found. This does not mean the table is empty.</p>}
                                <p>The filing date is when the source filing was submitted, not the reporting period or when the row was imported or updated.</p>
                            </>}
                            <dl><div><dt>Checked at</dt><dd><time dateTime={snapshot.observedAt}>{new Date(snapshot.observedAt).toLocaleString()}</time></dd></div></dl>
                        </section>
                        {showReceipts && <ExplorerAssetFreshness key={`${schema}.${table}`} schema={schema} table={table} version={version} />}
                        {showExport ? <TableExportEvidence mode="explorer" version={version} /> : <>
                        <section className="flow-evidence-card" aria-labelledby="table-evidence-history">
                            <header><h4 id="table-evidence-history">History</h4><span>{showReceipts ? 'Sandbox transfers' : 'Run receipt unavailable'}</span></header>
                            {showReceipts ? (
                                receiptsFailed ? <p role="alert">Transfer receipts could not be loaded. Refresh evidence to retry.</p>
                                    : !receipts ? <p role="status">Loading committed receipts…</p>
                                        : receipts.length ? <ul className="flow-evidence-receipts">{receipts.map(run => <li key={run.id}>
                                            <div><strong>{run.rowsAffected.toLocaleString()} rows affected</strong><span>Committed · {run.writeMode}</span></div>
                                            <code>{run.sourceSchema}.{run.sourceTable} → {run.targetSchema}.{run.targetTable}</code>
                                            <div><span>Receipt recorded</span><time dateTime={run.recordedAt}>{new Date(run.recordedAt).toLocaleString()}</time></div>
                                            <code title="Receipt ID">{run.id}</code>
                                        </li>)}</ul> : <p>No committed sandbox transfers for this table.</p>
                            ) : <><strong>{snapshot.lineage.status === 'declared' ? 'No verified migration receipt' : 'No verified run history'}</strong>
                            <p>{snapshot.lineage.status === 'declared'
                                ? 'This view cannot confirm when the migration ran, which batches completed, or how many rows were written.'
                                : 'No verified ingestion or transformation runs are available for this table.'}</p></>}
                        </section>
                        <section className="flow-evidence-card flow-evidence-lineage" aria-labelledby="table-evidence-lineage">
                            <header><h4 id="table-evidence-lineage">Lineage</h4><span>{observed ? 'Observed in sandbox' : snapshot.lineage.status === 'declared' ? 'Declared in code' : 'Evidence unavailable'}</span></header>
                            {observed ? <>
                                <div className="flow-evidence-path">
                                    <div className="flow-evidence-node"><span>Source table</span><code>{observed.sourceSchema}.{observed.sourceTable}</code></div>
                                    <span className="flow-evidence-arrow" aria-hidden="true">→</span>
                                    <div className="flow-evidence-node"><span>Destination table</span><code>{observed.targetSchema}.{observed.targetTable}</code></div>
                                </div>
                                <p>Latest committed sandbox transfer affecting rows. This is not evidence of production data movement.</p>
                            </> : snapshot.lineage.status === 'declared' ? <>
                                <div className="flow-evidence-path">
                                    <div className="flow-evidence-node"><span>MongoDB collection</span><code>{snapshot.lineage.mongoCollection}</code></div>
                                    <span className="flow-evidence-arrow" aria-hidden="true">→</span>
                                    <div className="flow-evidence-targets" aria-label="Parallel PostgreSQL destinations">
                                        <div className="flow-evidence-node"><span>RDS destination</span><code>{snapshot.lineage.rdsTable}</code></div>
                                        <div className="flow-evidence-node flow-evidence-current"><span>ODS destination · this table</span><code>{snapshot.lineage.odsTable}</code></div>
                                    </div>
                                </div>
                                <p>The migration code writes directly from MongoDB to each destination. This is not an observed RDS-to-ODS flow and does not verify a specific run or downstream consumer.</p>
                            </> : <><strong>No verified data flow</strong><p>No upstream or downstream flow is available as evidence for this table. This does not imply the table is disconnected.</p></>}
                            {schema.startsWith('ts_') && schema !== 'ts_meta' &&
                                <Link className="flow-evidence-lineage-link" to={`/ontology/lineage?${new URLSearchParams({ schema })}`}>Open schema in Lineage</Link>}
                        </section>
                        </>}
                    </div>}
        </section>
    );
}
