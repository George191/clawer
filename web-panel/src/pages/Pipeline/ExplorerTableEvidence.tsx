import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Button } from '@/components/ui';
import { ReloadOutlined } from '@/components/ui/icons';
import { fetchExplorerTableEvidence, type ExplorerTableEvidenceSnapshot, type TransferRunReceipt } from '../../services/api';
import TableExportEvidence from './TableExportEvidence';
import ExplorerAssetFreshness from './ExplorerAssetFreshness';
import ExplorerCdcCapture from './ExplorerCdcCapture';
import ExplorerDailyJobQuality from './ExplorerDailyJobQuality';
import ExplorerFieldFreshness from './ExplorerFieldFreshness';
import ExplorerMinimumRowHealth from './ExplorerMinimumRowHealth';
import ExplorerAssetStatistics from './ExplorerAssetStatistics';
import ExplorerTransferTimeline from './ExplorerTransferTimeline';
import useExplorerErrorToast from './useExplorerErrorToast';
import './table-evidence.css';

export default function ExplorerTableEvidence({ schema, table, connectionId, canManage }: { connectionId?: string; schema: string; table: string; canManage: boolean }) {
    const [version, setVersion] = useState(0);
    const [snapshot, setSnapshot] = useState<ExplorerTableEvidenceSnapshot>();
    const [failed, setFailed] = useState(false);
    const [latestCurrentReceipt, setLatestCurrentReceipt] = useState<TransferRunReceipt | null>(null);
    const showReceipts = canManage && snapshot?.transferSandbox;
    const showExport = canManage && snapshot?.exportSource;
    const tableLineage = showReceipts || (!snapshot?.transferSandbox && !snapshot?.indexPreview && snapshot?.tableRole === 'current');
    const refresh = () => {
        setSnapshot(undefined);
        setFailed(false);
        setLatestCurrentReceipt(null);
        setVersion((value) => value + 1);
    };
    useExplorerErrorToast(failed ? 'The evidence service did not return a result.' : undefined, 'Evidence unavailable', refresh);

    useEffect(() => {
        const request = new AbortController();
        setSnapshot(undefined); setFailed(false);
        fetchExplorerTableEvidence(schema, table, request.signal, connectionId)
            .then((value) => { if (!request.signal.aborted) setSnapshot(value); })
            .catch(() => { if (!request.signal.aborted) setFailed(true); });
        return () => request.abort();
    }, [schema, table, connectionId, version]);

    const observed = showReceipts && latestCurrentReceipt && latestCurrentReceipt.rowsAffected > 0 &&
        latestCurrentReceipt.targetSchema === schema && latestCurrentReceipt.targetTable === table && latestCurrentReceipt;

    return (
        <section className="flow-schema-details flow-table-evidence" aria-label="Table evidence">
            <header>
                <div><h3>Table evidence</h3><p>PostgreSQL catalog facts, plus run and lineage evidence where available.</p></div>
                <Button size="small" icon={<ReloadOutlined />} disabled={!snapshot && !failed} onClick={refresh}>{failed ? 'Retry evidence' : 'Refresh evidence'}</Button>
            </header>
            {failed ? null
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
                            {showReceipts && <ExplorerAssetStatistics schema={schema} table={table} version={version} />}
                        </section>
                        {showReceipts && snapshot.fieldPolicy && <>
                            <ExplorerFieldFreshness schema={schema} table={table} version={version} /><ExplorerMinimumRowHealth schema={schema} table={table} version={version} />
                        </>}
                        {showReceipts && <>
                            <ExplorerDailyJobQuality schema={schema} table={table} version={version} />
                            <ExplorerDailyJobQuality schema={schema} table={table} version={version} cadence="weekly" />
                        </>}
                        {showReceipts && <ExplorerAssetFreshness key={`${schema}.${table}`} schema={schema} table={table} version={version} />}
                        <ExplorerCdcCapture connectionId={connectionId} schema={schema} table={table} version={version} />
                        {showExport ? <TableExportEvidence mode="explorer" version={version} /> : <>
                        {showReceipts ? <ExplorerTransferTimeline key={`${schema}.${table}:${version}`} schema={schema} table={table} onLatestCurrentReceipt={setLatestCurrentReceipt} />
                        : <section className="flow-evidence-card" aria-labelledby="table-evidence-history">
                            <header><h4 id="table-evidence-history">History</h4><span>Run receipt unavailable</span></header>
                            <strong>{snapshot.lineage.status === 'declared' ? 'No verified migration receipt' : 'No verified run history'}</strong>
                            <p>{snapshot.lineage.status === 'declared'
                                ? 'This view cannot confirm when the migration ran, which batches completed, or how many rows were written.'
                                : 'No verified ingestion or transformation runs are available for this table.'}</p>
                        </section>}
                        <section className="flow-evidence-card flow-evidence-lineage" aria-labelledby="table-evidence-lineage">
                            <header><h4 id="table-evidence-lineage">Table-to-table lineage</h4><span>{observed ? 'Observed in sandbox' : snapshot.lineage.status === 'declared' ? 'Declared in code' : 'Transfer receipts'}</span></header>
                            {observed ? <>
                                <div className="flow-evidence-path">
                                    <div className="flow-evidence-node"><span>Source table</span><code>{observed.sourceSchema}.{observed.sourceTable}</code></div>
                                    <span className="flow-evidence-arrow" aria-hidden="true">→</span>
                                    <div className="flow-evidence-node"><span>Destination table</span><code>{observed.targetSchema}.{observed.targetTable}</code></div>
                                </div>
                                <p>Latest committed sandbox transfer affecting rows on this physical destination. This is not evidence of production data movement.</p>
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
                            </> : <p>No table-to-table transfer receipt was returned for this asset.</p>}
                            <Link className="flow-evidence-lineage-link" to={`/ontology/lineage?${new URLSearchParams({
                                    schema, ...(tableLineage ? { table, tableLineage: '1' } : {}),
                                })}`}>{tableLineage ? 'Open table in Lineage' : 'Open schema in Lineage'}</Link>
                        </section>
                        </>}
                    </div>}
        </section>
    );
}
