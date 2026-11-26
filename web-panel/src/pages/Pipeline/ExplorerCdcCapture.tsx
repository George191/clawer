import { useEffect, useState } from 'react';
import { fetchTableCapture, type CdcTableCapture } from '../../services/api';
import useExplorerErrorToast from './useExplorerErrorToast';

export default function ExplorerCdcCapture({ schema, table, connectionId, version = 0 }: { connectionId?: string; schema: string; table: string; version?: number }) {
    const [capture, setCapture] = useState<CdcTableCapture>();
    const [failed, setFailed] = useState(false);
    const [retry, setRetry] = useState(0);
    useExplorerErrorToast(failed ? 'The CDC evidence request failed.' : undefined,
        'Change capture unavailable', () => setRetry(value => value + 1));
    useEffect(() => {
        const controller = new AbortController();
        setCapture(undefined); setFailed(false);
        fetchTableCapture(schema, table, controller.signal, connectionId).then(result => {
            if (!controller.signal.aborted) setCapture(result);
        }).catch(() => {
            if (!controller.signal.aborted) setFailed(true);
        });
        return () => controller.abort();
    }, [schema, table, connectionId, version, retry]);
    return <section className="flow-evidence-card" aria-label="Change capture evidence">
        <header><h4>Change capture</h4><span>{capture?.state === 'observed' ? 'Observed in Kafka' : 'Kafka observation'}</span></header>
        {failed ? null : !capture ? <p role="status">Inspecting retained changes…</p> : capture.state === 'observed' ? <>
            <div className="flow-evidence-path">
                <div className="flow-evidence-node"><span>PostgreSQL table</span><code>{schema}.{table}</code></div>
                <span className="flow-evidence-arrow" aria-hidden="true">→</span>
                <div className="flow-evidence-node"><span>Debezium topic</span><code>{capture.topic}</code></div>
            </div>
            <dl><div><dt>Change</dt><dd>{capture.operation}</dd></div>
                <div><dt>Partition / offset</dt><dd>{capture.partition} / {capture.offset}</dd></div>
                <div><dt>Source WAL position</dt><dd>{capture.sourceLsn}</dd></div>
                {capture.eventTimeMs != null && <div><dt>Source event time</dt><dd>{new Date(capture.eventTimeMs).toLocaleString()}</dd></div>}
                <div><dt>Checked at</dt><dd>{new Date(capture.checkedAt).toLocaleString()}</dd></div></dl>
            <p>The retained event identifies this database, schema and table. Downstream jobs have separate run receipts.</p>
        </> : <p role="status">{capture.reason}</p>}
        {capture?.recordLink && <><h4>Persisted source reference</h4>
            {capture.recordLink.state === 'matched' ? <>
                <div className="flow-evidence-path">
                    <div className="flow-evidence-node"><span>Source</span><code>{capture.recordLink.source}</code></div>
                    <span className="flow-evidence-arrow" aria-hidden="true">→</span>
                    <div className="flow-evidence-node"><span>Destination</span><code>{capture.recordLink.target}</code></div>
                </div>
                <p>The sampled destination references the same source event ID, business identity, revision, observation time and ETL version.</p>
                <dl><div><dt>Event observed</dt><dd>{new Date(capture.recordLink.eventTime).toLocaleString()}</dd></div>
                    <div><dt>Reference checked</dt><dd>{new Date(capture.recordLink.checkedAt).toLocaleString()}</dd></div></dl>
            </> : <p>{capture.recordLink.reason}</p>}
        </>}
    </section>;
}
