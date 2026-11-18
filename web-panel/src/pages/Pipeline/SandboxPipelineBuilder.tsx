import { useEffect, useRef, useState } from 'react';
import { Button, Input, Modal } from '@/components/ui';
import { ArrowRightOutlined } from '@/components/ui/icons';
import {
  fetchCurrentUserContext, fetchSandboxCdcVersions, fetchSandboxPipelineRevisions, fetchSandboxPipelineStatus,
  previewSandboxPipeline, restoreSandboxPipelineRevision, saveSandboxPipelineRevision,
} from '../../services/api';
import type {
  SandboxCdcVersion, SandboxPipelineColumn, SandboxPipelineConfig, SandboxPipelineFilter, SandboxPipelinePreview,
  SandboxPipelineRevision,
} from '../../services/api';
import { getSessionState } from '../../services/session';
import SandboxPipelineOutputs from './SandboxPipelineOutputs';
import { SandboxMarkings, sandboxScopeDenied, useSandboxIdentity } from './SandboxMarkings';
import './sandbox-pipeline-builder.css';

const fields: SandboxPipelineColumn[] = ['id', 'value'];
const idOperators = [['eq', 'Equals'], ['gte', 'At least'], ['lte', 'At most']] as const;
const textOperators = [['eq', 'Equals'], ['prefix', 'Starts with']] as const;
const initialConfig: SandboxPipelineConfig = { versionId: '', filters: [], projection: ['id', 'value'] };
const timeText = (value: string) => new Date(value).toLocaleString();

function validFilter(filter: SandboxPipelineFilter) {
  if (filter.field === 'value') return [...filter.value].length <= 128;
  if (!/^(0|-?[1-9]\d{0,18})$/.test(filter.value)) return false;
  const value = BigInt(filter.value);
  return value >= -9223372036854775808n && value <= 9223372036854775807n;
}

function sameConfig(left: SandboxPipelineConfig, right: SandboxPipelineConfig) {
  return left.versionId === right.versionId && (left.matchVersionId ?? null) === (right.matchVersionId ?? null) &&
    (left.additionalMarking ?? null) === (right.additionalMarking ?? null) &&
    left.projection.length === right.projection.length && left.projection.every((column, index) => column === right.projection[index]) &&
    left.filters.length === right.filters.length && left.filters.every((filter, index) => {
      const reviewed = right.filters[index];
      return filter.field === reviewed.field && filter.operator === reviewed.operator && filter.value === reviewed.value;
    });
}

function FilterRow({ filter, index, disabled, change, remove }: {
  filter: SandboxPipelineFilter; index: number; disabled: boolean;
  change: (filter: SandboxPipelineFilter) => void; remove: () => void;
}) {
  const ordinal = index + 1;
  return <li className="flow-sandbox-builder__filter">
    <label>Field<select aria-label={`Filter field ${ordinal}`} value={filter.field} disabled={disabled}
      onChange={event => change({ field: event.target.value === 'id' ? 'id' : 'value', operator: 'eq', value: '' })}>
      <option value="id">id · bigint</option><option value="value">value · text</option>
    </select></label>
    <label>Operator<select aria-label={`Filter operator ${ordinal}`} value={filter.operator} disabled={disabled}
      onChange={event => {
        const operator = event.target.value;
        if (filter.field === 'id' && (operator === 'eq' || operator === 'gte' || operator === 'lte')) change({ ...filter, operator });
        if (filter.field === 'value' && (operator === 'eq' || operator === 'prefix')) change({ ...filter, operator });
      }}>
      {(filter.field === 'id' ? idOperators : textOperators).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
    </select></label>
    <label>Value<Input aria-label={`Filter value ${ordinal}`} value={filter.value} disabled={disabled}
      placeholder={filter.field === 'id' ? 'Signed integer' : 'Case-sensitive text'} maxLength={filter.field === 'id' ? 20 : 256}
      onChange={event => change({ ...filter, value: event.target.value })} /></label>
    <Button size="small" aria-label={`Remove filter ${ordinal}`} disabled={disabled} onClick={remove}>Remove</Button>
    {!validFilter(filter) && <small>{filter.field === 'id' ? 'Enter an exact signed 64-bit integer without leading zeroes.' : 'Use 128 characters or fewer.'}</small>}
  </li>;
}

export default function SandboxPipelineBuilder() {
  const identity = useSandboxIdentity();
  return identity ? <PipelineBuilderWorkspace key={identity} identity={identity} /> : null;
}

function PipelineBuilderWorkspace({ identity }: { identity: string }) {
  const [allowed, setAllowed] = useState<boolean>();
  const [loaded, setLoaded] = useState<{
    available: boolean; versions: SandboxCdcVersion[]; governanceEnabled: boolean; allowedMarkings: string[]; canSave: boolean; matchEnabled: boolean; restoreEnabled: boolean;
  }>();
  const [saved, setSaved] = useState<SandboxPipelineRevision[]>([]);
  const [generation, setGeneration] = useState<number | null>(null);
  const [config, setConfig] = useState<SandboxPipelineConfig>(initialConfig);
  const [preview, setPreview] = useState<SandboxPipelinePreview>();
  const [previewing, setPreviewing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [loadFailed, setLoadFailed] = useState(false);
  const [notice, setNotice] = useState<{ text: string; error?: boolean }>();
  const [refresh, setRefresh] = useState(0);
  const active = useRef(false);
  const previewRequest = useRef<AbortController>();
  const savePending = useRef(false);
  const restoreRequest = useRef<AbortController>();
  const [restoreReview, setRestoreReview] = useState<{ source: SandboxPipelineRevision; head: SandboxPipelineRevision }>();
  const [restoreNeedsRead, setRestoreNeedsRead] = useState(false);
  const isLive = (signal?: AbortSignal) => {
    const session = getSessionState();
    return active.current && !signal?.aborted && session.authenticated && !session.locked && session.identity === identity;
  };

  useEffect(() => { active.current = true; return () => { active.current = false; restoreRequest.current?.abort(); }; }, []);
  useEffect(() => {
    const controller = new AbortController();
    const current = () => !controller.signal.aborted;
    setLoadFailed(false); setLoaded(undefined);
    void (async () => {
      try {
        const context = await fetchCurrentUserContext();
        if (!current()) return;
        setAllowed(context.user.is_superuser);
        if (!context.user.is_superuser) return;
        const status = await fetchSandboxPipelineStatus(controller.signal);
        if (!current()) return;
        setGeneration(status.latestGeneration);
        const governance = { governanceEnabled: status.governanceEnabled === true, allowedMarkings: status.allowedMarkings ?? [],
          canSave: status.governanceEnabled !== true || status.canSave === true, matchEnabled: status.matchEnabled === true, restoreEnabled: status.restoreEnabled === true };
        if (!status.available) { setLoaded({ available: false, versions: [], ...governance }); return; }
        const [versions, revisions] = await Promise.all([
          fetchSandboxCdcVersions(controller.signal), fetchSandboxPipelineRevisions(controller.signal),
        ]);
        if (!current()) return;
        setLoaded({ available: true, versions: versions.available ? versions.versions : [], ...governance });
        setSaved(revisions); setRestoreNeedsRead(false);
      } catch {
        if (current()) setLoadFailed(true);
      }
    })();
    return () => { controller.abort(); previewRequest.current?.abort(); previewRequest.current = undefined; };
  }, [refresh]);

  const clearScope = () => {
    previewRequest.current?.abort(); previewRequest.current = undefined;
    restoreRequest.current?.abort(); setRestoreReview(undefined);
    setPreviewing(false); setPreview(undefined); setNotice(undefined); setConfig(initialConfig);
    setLoaded(undefined); setSaved([]); setGeneration(null);
  };
  const revise = (next: SandboxPipelineConfig) => {
    if (savePending.current) return;
    previewRequest.current?.abort(); previewRequest.current = undefined;
    setPreviewing(false); setPreview(undefined); setNotice(undefined); setConfig(next);
  };
  const isMatched = Boolean(config.matchVersionId);
  const source = loaded?.versions.find(item => item.id === config.versionId);
  const matchingSource = loaded?.versions.find(item => item.id === config.matchVersionId);
  const knownPair = Boolean(source && matchingSource) || saved.some(item =>
    item.config.versionId === config.versionId && item.config.matchVersionId === config.matchVersionId);
  const matchAvailable = !isMatched || Boolean(loaded?.matchEnabled && config.matchVersionId !== config.versionId && knownPair);
  const valid = Boolean(loaded?.available && config.versionId && matchAvailable && config.filters.every(validFilter) && config.projection.length);
  const runPreview = async () => {
    if (!valid || savePending.current || previewRequest.current) return;
    const controller = new AbortController();
    previewRequest.current = controller; setPreviewing(true); setPreview(undefined); setNotice(undefined);
    try {
      const result = await previewSandboxPipeline(config, controller.signal);
      if (!controller.signal.aborted && result.versionId === config.versionId &&
        (result.matchVersionId ?? null) === (config.matchVersionId ?? null)) setPreview(result);
    } catch (failure) {
      if (!controller.signal.aborted) {
        if (loaded?.governanceEnabled && sandboxScopeDenied(failure)) { clearScope(); setLoadFailed(true); }
        else setNotice({ text: 'Preview could not be verified for the selected configuration.', error: true });
      }
    } finally {
      if (previewRequest.current === controller) { previewRequest.current = undefined; setPreviewing(false); }
    }
  };
  const saveRevision = async () => {
    if (!valid || !loaded?.canSave || restoreNeedsRead || savePending.current || previewRequest.current) return;
    savePending.current = true; setSaving(true); setNotice(undefined);
    try {
      const revision = await saveSandboxPipelineRevision(config, generation);
      if (active.current) {
        setGeneration(revision.generation);
        setSaved(items => [revision, ...items.filter(item => item.generation !== revision.generation)].slice(0, 25));
        setNotice({ text: `Configuration revision ${revision.generation} saved. No data was written.` });
      }
    } catch (failure) {
      if (active.current) {
        if (loaded?.governanceEnabled && sandboxScopeDenied(failure)) { clearScope(); setLoadFailed(true); return; }
        const status = (failure as { response?: { status?: number } })?.response?.status;
        setNotice({ text: status === 409 ? 'Another configuration revision was saved. Review history before saving again.'
          : 'Configuration save could not be confirmed. Review history before retrying.', error: true });
        setRefresh(value => value + 1);
      }
    } finally {
      savePending.current = false;
      if (active.current) setSaving(false);
    }
  };
  const head = saved.find(item => item.generation === generation);
  const canRestore = (source: SandboxPipelineRevision) => Boolean(loaded?.restoreEnabled && loaded.canSave && !restoreNeedsRead &&
    head && source.generation < head.generation && (loaded.matchEnabled || (!source.config.matchVersionId && !head.config.matchVersionId)) &&
    /^[0-9a-f]{64}$/.test(source.configHash) && /^[0-9a-f]{64}$/.test(head.configHash) &&
    (head.markings ?? []).every(mark => source.markings?.includes(mark)));
  const restore = async () => {
    if (!restoreReview || !head || !isLive() || savePending.current || previewRequest.current || !canRestore(restoreReview.source)) return;
    const { source, head: expected } = restoreReview;
    if (head.generation !== expected.generation || head.configHash !== expected.configHash ||
        !saved.some(item => item.generation === source.generation && item.configHash === source.configHash)) return;
    const request = new AbortController(); restoreRequest.current = request;
    savePending.current = true; setSaving(true); setRestoreReview(undefined); setNotice(undefined);
    try {
      const revision = await restoreSandboxPipelineRevision({ sourceGeneration: source.generation, expectedSourceConfigHash: source.configHash,
        expectedHeadGeneration: expected.generation, expectedHeadConfigHash: expected.configHash }, request.signal);
      if (!isLive(request.signal)) return;
      if (revision.generation !== expected.generation + 1 || revision.restoredFromGeneration !== source.generation ||
          revision.configHash !== source.configHash || !sameConfig(revision.config, source.config) ||
          JSON.stringify(revision.markings ?? []) !== JSON.stringify(source.markings ?? [])) throw new Error('Unverified restore result');
      setGeneration(revision.generation);
      setSaved(items => [revision, ...items.filter(item => item.generation !== revision.generation)].slice(0, 25));
      setNotice({ text: `Configuration revision ${revision.generation} restored from revision ${source.generation}. The current draft was not changed. No output was written.` });
    } catch (failure) {
      if (!isLive(request.signal)) return;
      const status = (failure as { response?: { status?: number } })?.response?.status;
      if (status === 401 || status === 403) { clearScope(); setLoadFailed(true); return; }
      setRestoreNeedsRead(true);
      setNotice({ text: 'Configuration restore was not confirmed. Refresh configuration history before acting again. The current draft has not changed.', error: true });
    } finally {
      if (restoreRequest.current === request) restoreRequest.current = undefined;
      savePending.current = false;
      if (isLive()) setSaving(false);
    }
  };
  const setFilter = (index: number, filter?: SandboxPipelineFilter) => revise({ ...config, filters: filter
    ? config.filters.map((item, position) => position === index ? filter : item)
    : config.filters.filter((_, position) => position !== index) });
  const moveColumn = (index: number, direction: -1 | 1) => {
    const next = [...config.projection];
    const target = index + direction;
    if (target < 0 || target >= next.length) return;
    [next[index], next[target]] = [next[target], next[index]];
    revise({ ...config, projection: next });
  };
  const reviewed = loaded?.canSave && !restoreNeedsRead && matchAvailable ? saved.find(item => item.generation === generation && sameConfig(config, item.config)) : undefined;
  const scopeKey = loaded?.governanceEnabled ? `governed:${[...loaded.allowedMarkings].sort().join(',')}` : undefined;
  return <section className="flow-sandbox-builder flow-foundry-workbench" aria-label="Sandbox Pipeline Builder">
    <header><h1>Sandbox Pipeline Builder</h1><p>Transform a committed synthetic snapshot. Preview and save configuration only; no destination data is written.</p>
      {loaded?.governanceEnabled && <Button size="small" disabled={saving} onClick={() => { clearScope(); setRefresh(value => value + 1); }}>Refresh authorized scope</Button>}
    </header>
    {loadFailed ? <div role="alert">Builder could not be loaded. <Button size="small" onClick={() => setRefresh(value => value + 1)}>Retry builder</Button></div>
      : allowed === false ? <p>Sandbox Pipeline Builder is available to administrators only.</p>
        : !loaded ? <p role="status">Loading pipeline builder…</p>
          : !loaded.available ? <p>Sandbox Pipeline Builder is disabled in this environment.</p> : <>
    <div className={`flow-sandbox-builder__graph${isMatched ? ' is-matched' : ''}`} role="group" aria-label="Pipeline graph">
      <section aria-label="Source snapshot"><header><span>01 · Input</span><h2>Source snapshot</h2></header>
        <label>{loaded.matchEnabled || isMatched ? 'Left version' : 'Captured source version'}
          <select aria-label={loaded.matchEnabled || isMatched ? 'Left version' : 'Captured source version'} value={config.versionId} disabled={saving || (!loaded.versions.length && !config.versionId)}
            onChange={event => revise({ ...config, versionId: event.target.value })}>
            <option value="">Select a captured version</option>
            {config.versionId && !source && <option value={config.versionId}>Source pinned in a configuration revision</option>}
            {loaded.versions.map(version => <option key={version.id} value={version.id} disabled={version.id === config.matchVersionId}>Checkpoint {version.appliedThrough} · {timeText(version.recordedAt)}</option>)}
          </select>
        </label>
        {config.versionId && <p>{isMatched ? 'Left version receipt' : 'Source receipt'}: <code>{config.versionId}</code></p>}
        <SandboxMarkings markings={source?.markings} label={isMatched ? 'Left source markings' : 'Inherited source markings'} />
        {(loaded.matchEnabled || isMatched) && <div className="flow-sandbox-builder__matching-input">
          <label>Matching version
            <select aria-label="Matching version" value={config.matchVersionId ?? ''} disabled={saving || !loaded.matchEnabled}
              onChange={event => {
                const { matchVersionId: _previous, ...base } = config;
                revise(event.target.value ? { ...base, matchVersionId: event.target.value } : base);
              }}>
              <option value="">No matching version · single input</option>
              {config.matchVersionId && !matchingSource && <option value={config.matchVersionId}>Matching version pinned in a configuration revision</option>}
              {loaded.versions.filter(version => version.id !== config.versionId).map(version =>
                <option key={version.id} value={version.id}>Checkpoint {version.appliedThrough} · {timeText(version.recordedAt)}</option>)}
            </select>
          </label>
          {config.matchVersionId && <p>Matching version receipt: <code>{config.matchVersionId}</code></p>}
          <SandboxMarkings markings={matchingSource?.markings} label="Matching source markings" />
          {loaded.matchEnabled && loaded.versions.length < 2 && <p>Matching requires two different authorized captured versions.</p>}
          {isMatched && !loaded.matchEnabled && <p>Match by ID is unavailable for new runs. These are the saved historical inputs.</p>}
          {isMatched && !loaded.matchEnabled && <Button size="small" disabled={saving} onClick={() => {
            const { matchVersionId: _previous, ...base } = config;
            revise(base);
          }}>Switch to single input</Button>}
        </div>}
        {!loaded.versions.length && <p>No captured source versions. Apply a reviewed sandbox CDC batch with capture enabled first.</p>}
      </section>
      <ArrowRightOutlined className="flow-sandbox-builder__arrow" aria-hidden="true" />
      {isMatched && <>
        <section aria-label="Match by ID"><header><span>02 · Match</span><h2>Match by ID</h2></header>
          <p>Keep only left rows whose ID also appears in the matching version. The right version supplies membership only; values always come from the left.</p>
        </section>
        <ArrowRightOutlined className="flow-sandbox-builder__arrow" aria-hidden="true" />
      </>}
      <section aria-label="Filter and projection"><header><span>{isMatched ? '03' : '02'} · Transform</span><h2>Filter and projection</h2></header>
        <p>All filters must match (AND). Choose up to three.</p>
        {config.filters.length > 0 && <ol aria-label="Pipeline filters">{config.filters.map((filter, index) => <FilterRow key={index}
          filter={filter} index={index} disabled={saving} change={item => setFilter(index, item)} remove={() => setFilter(index)} />)}</ol>}
        <Button size="small" disabled={saving || config.filters.length >= 3}
          onClick={() => revise({ ...config, filters: [...config.filters, { field: 'id', operator: 'eq', value: '' }] })}>Add filter</Button>
        <fieldset disabled={saving}><legend>Projection columns</legend>
          {fields.map(field => <label key={field}><input type="checkbox" checked={config.projection.includes(field)}
            onChange={event => revise({ ...config, projection: event.target.checked ? [...config.projection, field] : config.projection.filter(item => item !== field) })} />{field}</label>)}
        </fieldset>
        <ol aria-label="Projection order">{config.projection.map((item, index) => <li key={item}>
          <code>{item}</code><Button size="small" aria-label={`Move ${item} up`} disabled={saving || index === 0} onClick={() => moveColumn(index, -1)}>↑</Button>
          <Button size="small" aria-label={`Move ${item} down`} disabled={saving || index === config.projection.length - 1} onClick={() => moveColumn(index, 1)}>↓</Button>
        </li>)}</ol>
        {!config.projection.length && <p>Select at least one output column.</p>}
        {loaded.governanceEnabled && <label>Additional marking
          <select aria-label="Additional marking" value={config.additionalMarking ?? ''} disabled={saving}
            onChange={event => {
              const { additionalMarking: _old, ...base } = config;
              revise(event.target.value === 'sandbox-restricted' ? { ...base, additionalMarking: 'sandbox-restricted' } : base);
            }}>
            <option value="">No additional marking · retain inherited</option>
            {loaded.allowedMarkings.includes('sandbox-restricted') && <option value="sandbox-restricted">Add sandbox-restricted</option>}
          </select>
        </label>}
        {loaded.governanceEnabled && <p>{isMatched ? 'Inherited markings from either input cannot be removed.' : 'Inherited source markings cannot be removed.'}</p>}
      </section>
      <ArrowRightOutlined className="flow-sandbox-builder__arrow" aria-hidden="true" />
      <section aria-label="Output preview"><header><span>{isMatched ? '04' : '03'} · Output</span><h2>Output preview</h2></header>
        <p>{isMatched ? 'Read-only preview of two captured inputs. No target is written.' : 'Read-only preview of the reviewed source snapshot. No target is written.'}</p>
        <div className="flow-sandbox-builder__actions">
          <Button size="small" type="primary" disabled={!valid || saving} loading={previewing} onClick={runPreview}>Preview pipeline</Button>
          <Button size="small" disabled={!valid || !loaded.canSave || restoreNeedsRead || previewing} loading={saving} onClick={saveRevision}>Save configuration revision</Button>
        </div>
        {!loaded.canSave && <p>Saving and committing are unavailable under the current authorization.</p>}
        {isMatched && loaded.matchEnabled && !knownPair && <p>Choose both inputs from the authorized versions before previewing or saving.</p>}
        {notice && <p role={notice.error ? 'alert' : 'status'}>{notice.text}</p>}
        {preview && <><dl className="flow-sandbox-builder__counts">
          <div><dt>{preview.matchVersionId ? 'Left input rows' : 'Input rows'}</dt><dd>{preview.inputRows}</dd></div>
          {preview.matchVersionId && preview.rightInputRows != null && <div><dt>Matching input rows</dt><dd>{preview.rightInputRows}</dd></div>}
          <div><dt>Matched rows</dt><dd>{preview.matchedRows}</dd></div>
          <div><dt>Sample rows</dt><dd>{preview.rows.length}</dd></div>
        </dl><p>No data written.</p><SandboxMarkings markings={preview.markings} />
          {preview.matchVersionId && preview.markings?.length ? <p>Effective markings are evaluated across both exact input versions.</p> : null}
          {preview.rows.length ? <div className="flow-sandbox-builder__table"><table aria-label="Pipeline preview rows">
            <thead><tr>{preview.columns.map(column => <th key={column} scope="col">{column}</th>)}</tr></thead>
            <tbody>{preview.rows.map((row, index) => <tr key={index}>{row.map((value, position) =>
              <td key={position}><code>{value === '' ? '""' : value}</code></td>)}</tr>)}</tbody>
          </table></div> : <p>No rows matched this configuration.</p>}
        </>}
      </section>
    </div>
    <section className="flow-sandbox-builder__history" aria-label="Configuration revision history">
      <h2>Configuration revision history</h2><p>Up to 25 saved configurations. These are not committed dataset versions.</p>
      {restoreNeedsRead && <Button size="small" onClick={() => setRefresh(value => value + 1)}>Refresh configuration history</Button>}
      {saved.length ? <ol aria-label="Saved configuration revisions">{saved.map(item => <li key={item.generation}>
        <strong>Revision {item.generation}</strong>
        {item.restoredFromGeneration != null && <span>Restored from revision {item.restoredFromGeneration}</span>}<time dateTime={item.recordedAt}>{timeText(item.recordedAt)}</time>
        <span>{item.config.matchVersionId ? 'Left version receipt' : 'Source receipt'}: <code>{item.config.versionId}</code></span>
        {item.config.matchVersionId && <span>Matching version receipt: <code>{item.config.matchVersionId}</code></span>}
        <SandboxMarkings markings={item.markings} />
        <Button size="small" disabled={saving} onClick={() => revise(item.config)}>{`Load configuration revision ${item.generation}`}</Button>
        {canRestore(item) && head && <Button size="small" disabled={saving || previewing} onClick={() => setRestoreReview({ source: item, head })}>
          Restore configuration revision {item.generation}
        </Button>}
      </li>)}</ol> : <p>No configuration revisions saved.</p>}
    </section>
    <Modal open={Boolean(restoreReview)} destroyOnHidden className="flow-surface-modal" title="Restore saved configuration?"
      okText="Confirm configuration restore" onCancel={() => setRestoreReview(undefined)} onOk={() => void restore()}>
      {restoreReview && <>
        <p>Create one immutable configuration revision from revision <strong>{restoreReview.source.generation}</strong>, with revision
          <strong> {restoreReview.head.generation}</strong> as the expected current head.</p>
        <p>This does not replace your open draft or run, copy, or publish any output.</p>
      </>}
    </Modal>
    <SandboxPipelineOutputs key={scopeKey ?? 'legacy'} scopeKey={scopeKey} reviewed={saving ? undefined : reviewed}
      onScopeDenied={loaded.governanceEnabled ? () => { clearScope(); setLoadFailed(true); } : undefined} />
    </>}
  </section>;
}
