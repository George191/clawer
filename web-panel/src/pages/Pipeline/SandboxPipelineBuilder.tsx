import { useEffect, useRef, useState } from 'react';
import { Button, Input } from '@/components/ui';
import { ArrowRightOutlined } from '@/components/ui/icons';
import {
  fetchCurrentUserContext, fetchSandboxCdcVersions, fetchSandboxPipelineRevisions, fetchSandboxPipelineStatus,
  previewSandboxPipeline, saveSandboxPipelineRevision,
} from '../../services/api';
import type {
  SandboxCdcVersion, SandboxPipelineColumn, SandboxPipelineConfig, SandboxPipelineFilter, SandboxPipelinePreview,
  SandboxPipelineRevision,
} from '../../services/api';
import SandboxPipelineOutputs from './SandboxPipelineOutputs';
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
  return left.versionId === right.versionId &&
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
  const [allowed, setAllowed] = useState<boolean>();
  const [loaded, setLoaded] = useState<{ available: boolean; versions: SandboxCdcVersion[] }>();
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

  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
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
        if (!status.available) { setLoaded({ available: false, versions: [] }); return; }
        const [versions, revisions] = await Promise.all([
          fetchSandboxCdcVersions(controller.signal), fetchSandboxPipelineRevisions(controller.signal),
        ]);
        if (!current()) return;
        setLoaded({ available: true, versions: versions.available ? versions.versions : [] });
        setSaved(revisions);
      } catch {
        if (current()) setLoadFailed(true);
      }
    })();
    return () => { controller.abort(); previewRequest.current?.abort(); previewRequest.current = undefined; };
  }, [refresh]);

  const revise = (next: SandboxPipelineConfig) => {
    if (savePending.current) return;
    previewRequest.current?.abort(); previewRequest.current = undefined;
    setPreviewing(false); setPreview(undefined); setNotice(undefined); setConfig(next);
  };
  const valid = Boolean(loaded?.available && config.versionId && config.filters.every(validFilter) && config.projection.length);
  const runPreview = async () => {
    if (!valid || savePending.current || previewRequest.current) return;
    const controller = new AbortController();
    previewRequest.current = controller; setPreviewing(true); setPreview(undefined); setNotice(undefined);
    try {
      const result = await previewSandboxPipeline(config, controller.signal);
      if (!controller.signal.aborted && result.versionId === config.versionId) setPreview(result);
    } catch {
      if (!controller.signal.aborted) setNotice({ text: 'Preview could not be verified for the selected configuration.', error: true });
    } finally {
      if (previewRequest.current === controller) { previewRequest.current = undefined; setPreviewing(false); }
    }
  };
  const saveRevision = async () => {
    if (!valid || savePending.current || previewRequest.current) return;
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
  const sourceListed = loaded?.versions.some(item => item.id === config.versionId);
  const reviewed = saved.find(item => item.generation === generation && sameConfig(config, item.config));
  return <section className="flow-sandbox-builder flow-foundry-workbench" aria-label="Sandbox Pipeline Builder">
    <header><h1>Sandbox Pipeline Builder</h1><p>Transform a committed synthetic snapshot. Preview and save configuration only; no destination data is written.</p></header>
    {loadFailed ? <div role="alert">Builder could not be loaded. <Button size="small" onClick={() => setRefresh(value => value + 1)}>Retry builder</Button></div>
      : allowed === false ? <p>Sandbox Pipeline Builder is available to administrators only.</p>
        : !loaded ? <p role="status">Loading pipeline builder…</p>
          : !loaded.available ? <p>Sandbox Pipeline Builder is disabled in this environment.</p> : <>
    <div className="flow-sandbox-builder__graph" role="group" aria-label="Pipeline graph">
      <section aria-label="Source snapshot"><header><span>01 · Input</span><h2>Source snapshot</h2></header>
        <label>Captured source version
          <select aria-label="Captured source version" value={config.versionId} disabled={saving || (!loaded.versions.length && !config.versionId)}
            onChange={event => revise({ ...config, versionId: event.target.value })}>
            <option value="">Select a captured version</option>
            {config.versionId && !sourceListed && <option value={config.versionId}>Source pinned in a configuration revision</option>}
            {loaded.versions.map(version => <option key={version.id} value={version.id}>Checkpoint {version.appliedThrough} · {timeText(version.recordedAt)}</option>)}
          </select>
        </label>
        {config.versionId && <p>Source receipt: <code>{config.versionId}</code></p>}
        {!loaded.versions.length && <p>No captured source versions. Apply a reviewed sandbox CDC batch with capture enabled first.</p>}
      </section>
      <ArrowRightOutlined className="flow-sandbox-builder__arrow" aria-hidden="true" />
      <section aria-label="Filter and projection"><header><span>02 · Transform</span><h2>Filter and projection</h2></header>
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
      </section>
      <ArrowRightOutlined className="flow-sandbox-builder__arrow" aria-hidden="true" />
      <section aria-label="Output preview"><header><span>03 · Output</span><h2>Output preview</h2></header>
        <p>Read-only preview of the reviewed source snapshot. No target is written.</p>
        <div className="flow-sandbox-builder__actions">
          <Button size="small" type="primary" disabled={!valid || saving} loading={previewing} onClick={runPreview}>Preview pipeline</Button>
          <Button size="small" disabled={!valid || previewing} loading={saving} onClick={saveRevision}>Save configuration revision</Button>
        </div>
        {notice && <p role={notice.error ? 'alert' : 'status'}>{notice.text}</p>}
        {preview && <><dl className="flow-sandbox-builder__counts">
          <div><dt>Input rows</dt><dd>{preview.inputRows}</dd></div>
          <div><dt>Matched rows</dt><dd>{preview.matchedRows}</dd></div>
          <div><dt>Sample rows</dt><dd>{preview.rows.length}</dd></div>
        </dl><p>No data written.</p>
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
      {saved.length ? <ol aria-label="Saved configuration revisions">{saved.map(item => <li key={item.generation}>
        <strong>Revision {item.generation}</strong><time dateTime={item.recordedAt}>{timeText(item.recordedAt)}</time>
        <span>Source receipt: <code>{item.config.versionId}</code></span>
        <Button size="small" disabled={saving} onClick={() => revise(item.config)}>{`Load configuration revision ${item.generation}`}</Button>
      </li>)}</ol> : <p>No configuration revisions saved.</p>}
    </section>
    <SandboxPipelineOutputs reviewed={saving ? undefined : reviewed} />
    </>}
  </section>;
}
