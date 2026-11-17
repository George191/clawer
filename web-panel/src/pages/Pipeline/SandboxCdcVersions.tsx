import { useEffect, useRef, useState } from 'react';
import { Button, Modal } from '@/components/ui';
import { compareSandboxCdcVersions, fetchSandboxCdcVersion, fetchSandboxCdcVersions } from '../../services/api';
import type { SandboxCdcVersion, SandboxCdcVersionComparison, SandboxCdcVersionDetail, SandboxCdcVersionList } from '../../services/api';
import { getSessionState } from '../../services/session';
import { SandboxMarkings, sandboxScopeDenied } from './SandboxMarkings';

const changeLabels: Record<SandboxCdcVersionComparison['changes'][number]['kind'], string> = {
  inserted: 'Inserted', updated: 'Updated', deleted: 'Deleted',
};
const dateText = (value: string) => new Date(value).toLocaleString();
function SnapshotValue({ value }: { value: string | null }) {
  return value === null ? <span>Not present</span> : value === '' ? <code>""</code> : <code>{value}</code>;
}

function VersionFacts({ version }: { version: SandboxCdcVersion }) {
  return <>
    <strong>Captured target version</strong>
    <span>Applied checkpoint: <code>{version.appliedThrough}</code> · Generation {version.generation}</span>
    <span>Receipt ID: <code>{version.receiptId}</code></span>
    <span>Recorded at: <time dateTime={version.recordedAt}>{dateText(version.recordedAt)}</time></span>
    <span>{version.rowsCaptured.toLocaleString()} captured rows · {version.bytesCaptured.toLocaleString()} bytes</span>
    <SandboxMarkings markings={version.markings} />
  </>;
}

export default function SandboxCdcVersions({ revision, onScopeDenied }: { revision: number; onScopeDenied?: () => void }) {
  const [open, setOpen] = useState(false);
  return <section className="ontology-cdc-versions" aria-label="CDC dataset versions">
    <header><div><h4>CDC dataset versions</h4><p>Up to 25 recent read-only snapshots captured with committed synthetic target receipts. These are not live table reads.</p></div>
      <Button size="small" aria-expanded={open} onClick={() => setOpen(value => !value)}>{open ? 'Close version history' : 'Open version history'}</Button>
    </header>
    {open && <VersionWorkspace key={revision} onScopeDenied={onScopeDenied} />}
  </section>;
}

function VersionWorkspace({ onScopeDenied }: { onScopeDenied?: () => void }) {
  const identity = useRef(getSessionState().identity).current;
  const [list, setList] = useState<SandboxCdcVersionList>();
  const [listFailed, setListFailed] = useState(false);
  const [revision, setRevision] = useState(0);
  const [inspecting, setInspecting] = useState<SandboxCdcVersion>();
  const [detail, setDetail] = useState<SandboxCdcVersionDetail>();
  const [detailFailed, setDetailFailed] = useState(false);
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [comparison, setComparison] = useState<SandboxCdcVersionComparison>();
  const [compareFailed, setCompareFailed] = useState(false);
  const [comparing, setComparing] = useState(false);
  const compareRequest = useRef<AbortController>();
  const live = (signal: AbortSignal) => {
    const session = getSessionState();
    return !signal.aborted && session.authenticated && !session.locked && session.identity === identity;
  };
  const invalidate = () => {
    compareRequest.current?.abort(); compareRequest.current = undefined;
    setList(undefined); setListFailed(true); setInspecting(undefined); setDetail(undefined);
    setFrom(''); setTo(''); setComparison(undefined); setComparing(false);
    onScopeDenied?.();
  };

  useEffect(() => {
    const controller = new AbortController();
    setList(undefined); setListFailed(false);
    void fetchSandboxCdcVersions(controller.signal).then(value => { if (live(controller.signal)) setList(value); })
      .catch(() => { if (live(controller.signal)) setListFailed(true); });
    return () => { controller.abort(); compareRequest.current?.abort(); compareRequest.current = undefined; };
  }, [identity, revision]);

  useEffect(() => {
    if (!inspecting) return;
    const controller = new AbortController();
    void fetchSandboxCdcVersion(inspecting.id, controller.signal).then(value => {
      if (!live(controller.signal)) return;
      if (value.id === inspecting.id) setDetail(value); else setDetailFailed(true);
    }).catch(failure => {
      if (live(controller.signal)) {
        if (sandboxScopeDenied(failure)) invalidate(); else setDetailFailed(true);
      }
    });
    return () => controller.abort();
  }, [identity, inspecting]);

  const select = (side: 'from' | 'to', value: string) => {
    compareRequest.current?.abort(); compareRequest.current = undefined;
    setComparison(undefined); setCompareFailed(false); setComparing(false);
    (side === 'from' ? setFrom : setTo)(value);
  };
  const compare = async () => {
    if (!list?.available || !from || !to || from === to || compareRequest.current) return;
    const controller = new AbortController();
    compareRequest.current = controller; setComparing(true); setCompareFailed(false); setComparison(undefined);
    try {
      const result = await compareSandboxCdcVersions(from, to, controller.signal);
      if (!live(controller.signal)) return;
      if (result.fromVersionId === from && result.toVersionId === to) setComparison(result);
      else setCompareFailed(true);
    } catch (failure) {
      if (live(controller.signal)) {
        if (sandboxScopeDenied(failure)) invalidate(); else setCompareFailed(true);
      }
    } finally {
      if (compareRequest.current === controller) { compareRequest.current = undefined; setComparing(false); }
    }
  };

  return <>
    {listFailed ? <><p role="alert">Version history could not be loaded.</p>
      <Button size="small" onClick={() => setRevision(value => value + 1)}>Retry version history</Button></>
      : !list ? <p role="status">Loading captured versions…</p>
        : !list.available ? <p>Version capture is unavailable in this environment.</p>
          : !list.versions.length ? <p>No captured versions. Existing receipts are not reconstructed from the current table.</p> : <>
            <ol className="ontology-source-sync__items" aria-label="Captured target versions">
              {list.versions.map(version => <li key={version.id}>
                <VersionFacts version={version} />
                <Button size="small" onClick={() => { setDetail(undefined); setDetailFailed(false); setInspecting(version); }}>Inspect version</Button>
              </li>)}
            </ol>
            {list.versions.length > 1 && <section aria-label="Version comparison" className="ontology-cdc-versions__compare">
              <h5>Compare captured versions</h5>
              <div className="ontology-cdc-versions__select">
                {(['from', 'to'] as const).map(side => <label key={side}>{side === 'from' ? 'From version' : 'To version'}
                  <select aria-label={side === 'from' ? 'From version' : 'To version'} value={side === 'from' ? from : to}
                    onChange={event => select(side, event.target.value)}>
                    <option value="">Select a captured version</option>
                    {list.versions.map(version => <option key={version.id} value={version.id}>Checkpoint {version.appliedThrough} · {dateText(version.recordedAt)}</option>)}
                  </select>
                </label>)}
              </div>
              <Button size="small" disabled={!from || !to || from === to} loading={comparing} onClick={compare}>Compare versions</Button>
              {compareFailed && <p role="alert">The selected snapshots could not be compared.</p>}
              {comparison && <Comparison result={comparison} />}
            </section>}
          </>}
    <Modal open={Boolean(inspecting)} destroyOnHidden className="flow-surface-modal" title="Historical target snapshot"
      onCancel={() => setInspecting(undefined)} footer={null} width={720}>
      {detailFailed ? <p role="alert">This captured version could not be verified.</p>
        : !detail ? <p role="status">Loading historical snapshot…</p> : <>
          <div className="ontology-cdc-versions__detail"><VersionFacts version={detail} /></div>
          <p>Values show the table as captured for this receipt, not the live destination.</p>
          {detail.rows.length ? <div className="ontology-cdc-versions__table"><table aria-label="Captured snapshot rows">
            <thead><tr><th scope="col">Row ID</th><th scope="col">Captured value</th></tr></thead>
            <tbody>{detail.rows.map(row => <tr key={row.id}><th scope="row"><code>{row.id}</code></th><td><SnapshotValue value={row.value} /></td></tr>)}</tbody>
          </table></div> : <p>The captured snapshot contained no rows.</p>}
        </>}
    </Modal>
  </>;
}

function Comparison({ result }: { result: SandboxCdcVersionComparison }) {
  const counts = { inserted: 0, updated: 0, deleted: 0 };
  result.changes.forEach(change => { counts[change.kind]++; });
  return <>
  <SandboxMarkings markings={result.markings} />
  {result.changes.length ? <>
    <p role="status">{counts.inserted} inserted · {counts.updated} updated · {counts.deleted} deleted</p>
    <p>From receipt <code>{result.fromVersionId}</code> to receipt <code>{result.toVersionId}</code>. No live rows were compared or changed.</p>
    <div className="ontology-cdc-versions__table"><table aria-label="Captured version differences">
      <thead><tr><th scope="col">Row ID</th><th scope="col">Change</th><th scope="col">Before</th><th scope="col">After</th></tr></thead>
      <tbody>{result.changes.map(change => <tr key={change.id}><th scope="row"><code>{change.id}</code></th>
        <td>{changeLabels[change.kind]}</td><td><SnapshotValue value={change.before} /></td><td><SnapshotValue value={change.after} /></td></tr>)}</tbody>
    </table></div>
  </> : <p role="status">No differences between these captured versions.</p>}
  </>;
}
