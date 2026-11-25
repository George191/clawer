import { useEffect, useId, useState } from 'react';
import { fetchCollectedAssetStatistics } from '../../services/api';
import type { CollectedAssetStatistics, CollectedStatisticsSample } from '../../services/api';
import { useSandboxIdentity } from './SandboxMarkings';
import useExplorerErrorToast from './useExplorerErrorToast';

const reasonLabels: Record<NonNullable<CollectedAssetStatistics['reason']>, string> = {
    checking_disabled: 'Scheduled collection is unavailable.',
    target_unavailable: 'The reviewed physical table is unavailable.',
    target_changed: 'The physical table changed. Earlier samples do not describe this table.',
    policy_changed: 'The reviewed policy or transfer plan revision changed.',
    policy_paused: 'The collection policy is paused.',
    stats_reset: 'PostgreSQL reset its reported statistics after this sample.',
    observation_stale: 'The last observation has expired.',
    probe_failed: 'The latest scheduled catalog observation did not complete.',
};

function RecordedTime({ value, empty }: { value?: string | null; empty: string }) {
    return value && Number.isFinite(Date.parse(value)) ? <time dateTime={value}>{new Date(value).toLocaleString()}</time> : <>{empty}</>;
}

function StatisticsFacts({ sample, uncollected = false }: { sample: CollectedStatisticsSample | null; uncollected?: boolean }) {
    const estimate = sample?.estimatedRows ?? null;
    return <dl className="flow-evidence-statistics__facts">
        <div><dt>Estimated rows (PostgreSQL)</dt><dd>{estimate ?? 'Unknown'}</dd></div>
        <div><dt>Collected at</dt><dd><RecordedTime value={sample?.capturedAt} empty={uncollected ? 'Not collected' : 'No current observation'} /></dd></div>
        <div><dt>Last reported analyze</dt><dd><RecordedTime value={sample?.lastAnalyzedAt} empty="Not reported" /></dd></div>
    </dl>;
}

export default function ExplorerAssetStatistics({ schema, table, version }: { schema: string; table: string; version: number }) {
    const identity = useSandboxIdentity();
    return identity
        ? <CollectedStatistics key={`${identity}:${schema}:${table}:${version}`} schema={schema} table={table} />
        : null;
}

function CollectedStatistics({ schema, table }: { schema: string; table: string }) {
    const title = useId();
    const [snapshot, setSnapshot] = useState<CollectedAssetStatistics>();
    const [expired, setExpired] = useState(false);
    const [failed, setFailed] = useState(false);
    const [retry, setRetry] = useState(0);
    useExplorerErrorToast(failed ? 'Collected PostgreSQL statistics could not be loaded.' : undefined,
        'Statistics unavailable', () => setRetry(value => value + 1));
    useEffect(() => {
        const request = new AbortController();
        setFailed(false);
        void fetchCollectedAssetStatistics(schema, table, request.signal)
            .then(value => { if (!request.signal.aborted) setSnapshot(value); })
            .catch(() => { if (!request.signal.aborted) { setSnapshot(undefined); setFailed(true); } });
        return () => request.abort();
    }, [schema, table, retry]);
    const expiresAt = snapshot?.validUntil ? Date.parse(snapshot.validUntil) : NaN;
    useEffect(() => {
        if (!Number.isFinite(expiresAt)) return;
        const update = () => { if (Date.now() >= expiresAt) setExpired(true); };
        update();
        const timer = window.setTimeout(update, Math.max(0, expiresAt - Date.now()));
        document.addEventListener('visibilitychange', update);
        return () => { window.clearTimeout(timer); document.removeEventListener('visibilitychange', update); };
    }, [expiresAt]);
    if (!snapshot?.available || snapshot.state === 'unavailable' || snapshot.state === 'not_configured') return null;
    const sampled = snapshot.state === 'current' || snapshot.state === 'no_stats';
    const stale = sampled && (expired || !Number.isFinite(expiresAt) || Date.now() >= expiresAt);
    const current = sampled && !stale ? snapshot.current : null;
    const historical = stale ? snapshot.current ?? snapshot.historical : snapshot.historical;
    const status = snapshot.state === 'not_collected' ? 'Not yet collected'
        : current && snapshot.state === 'current' && current.estimatedRows !== null ? 'Estimate available'
        : 'Unknown';
    const reason = stale ? reasonLabels.observation_stale : snapshot.reason ? reasonLabels[snapshot.reason] : undefined;
    return <section className="flow-evidence-statistics" aria-labelledby={title}>
        <h5 id={title}>PostgreSQL statistics</h5>
        <p role="status"><strong>{status}</strong>{reason && <> · {reason}</>}</p>
        <StatisticsFacts sample={current} uncollected={snapshot.state === 'not_collected'} />
        {current && snapshot.state === 'no_stats' && <p>PostgreSQL did not report a usable row estimate for this observation.</p>}
        <p>This is a PostgreSQL estimate, not an exact row count. An estimate of 0 does not establish that the table is empty.</p>
        {historical && <details>
            <summary>Historical sample · not current</summary>
            <StatisticsFacts sample={historical} />
        </details>}
    </section>;
}
