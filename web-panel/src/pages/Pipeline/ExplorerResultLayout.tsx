import { useEffect, useRef, useState } from 'react';
import { Button, Checkbox, Input, Modal, Select } from '@/components/ui';
import { layoutPath, readLayouts, writeLayout, type ExplorerSort, type Layouts, type Presentation, type ResultColumn, type ResultLayout } from '../../services/explorerLayouts';
import './explorer-result-layout.css';

const defaultColumn = (field: string): ResultColumn => ({ field, visible: true, width: 180, presentation: 'value' });
const labels: Record<Presentation, string> = { value: 'Value', timeSeries: 'Time series', geoJson: 'GeoJSON', media: 'Media' };
export function columnPresentations(sqlType = ''): Presentation[] {
  if (['json', 'jsonb'].includes(sqlType)) return ['value', 'timeSeries', 'geoJson', 'media'];
  return /^(text|(?:varchar|character varying)(?:\(\d+\))?)$/.test(sqlType) ? ['value', 'media'] : ['value'];
}

function matchColumns(fields: string[], saved: ResultColumn[] = []) {
  const names = new Set(fields);
  const kept = saved.filter(c => names.has(c.field));
  const known = new Set(kept.map(c => c.field));
  return [...kept, ...fields.filter(f => !known.has(f)).map(defaultColumn)];
}

function errorMessage(error: unknown) {
  const data = (error as { response?: { data?: { detail?: unknown; errors?: unknown } } }).response?.data;
  const detail = data?.errors ?? data?.detail;
  return typeof detail === 'string' ? detail : 'Unable to load or save this result layout.';
}

export function useResultLayout(identity: string, schema: string, table: string, fields: string[], sorts: ExplorerSort[],
  applySorts: (sorts: ExplorerSort[]) => void, applyInitialSorts = true) {
  const path = layoutPath(schema, table);
  const id = identity ? JSON.stringify([identity, path]) : '';
  const active = useRef(id);
  const epoch = useRef(0);
  if (active.current !== id) epoch.current++;
  active.current = id;
  const apply = useRef(applySorts);
  apply.current = applySorts;
  const sortsRef = useRef(sorts);
  sortsRef.current = sorts;
  const [state, setState] = useState<{ id: string; slots: Layouts; draft: ResultLayout | null }>();
  const [revision, reload] = useState(0);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const pending = useRef('');
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    const initialSorts = JSON.stringify(sortsRef.current);
    setState(undefined);
    setError('');
    setSaving(false);
    setOpen(false);
    setSearch('');
    pending.current = '';
    if (id) readLayouts(path, controller.signal).then(slots => {
      if (active.current !== id || controller.signal.aborted) return;
      const draft = slots.personal.layout ?? slots.default.layout;
      setState({ id, slots, draft });
      const nextSorts = draft?.sorts ?? [];
      if (applyInitialSorts && initialSorts === JSON.stringify(sortsRef.current) && JSON.stringify(nextSorts) !== initialSorts) apply.current(nextSorts);
    }).catch(e => {
      if (active.current === id && !controller.signal.aborted) setError(errorMessage(e));
    });
    return () => controller.abort();
  }, [id, path, revision, applyInitialSorts]);

  const current = state?.id === id ? state : undefined;
  const columns = matchColumns(fields, current?.draft?.columns);
  const layout = { columns, sorts, wrap: current?.draft?.wrap ?? false };
  function change(patch: Partial<ResultLayout>) {
    if (!current || saving) return;
    setState({ ...current, draft: { ...layout, ...patch } });
  }
  function column(field: string, props: Partial<ResultColumn>) {
    change({ columns: columns.map(c => c.field === field ? { ...c, ...props } : c) });
  }
  function move(from: string, to: string) {
    const before = columns.findIndex(c => c.field === from), after = columns.findIndex(c => c.field === to);
    if (before < 0 || after < 0 || before === after) return;
    const next = [...columns];
    next.splice(after, 0, next.splice(before, 1)[0]);
    change({ columns: next });
  }
  async function save(scope: keyof Layouts, reset = false) {
    if (!current || pending.current === id) return;
    const operation = epoch.current;
    pending.current = id;
    setSaving(true);
    setError('');
    try {
      const result = await writeLayout(path, scope, { layout: reset ? null : layout, version: current.slots[scope].version });
      if (active.current !== id || epoch.current !== operation) return;
      if (reset) {
        // The table default may have changed in another user's session.
        reload(v => v + 1);
      } else {
        setState({ id, draft: layout, slots: { ...current.slots, [scope]: result } });
      }
      setOpen(false);
    } catch (e) {
      if (active.current === id && epoch.current === operation) setError(errorMessage(e));
    } finally {
      if (active.current === id && epoch.current === operation) { pending.current = ''; setSaving(false); }
    }
  }
  return { columns, layout, ready: !!current, saving, error, open, search, setSearch, setOpen,
    change, column, move, save, reload: () => reload(v => v + 1) };
}
type Controls = ReturnType<typeof useResultLayout>;

function drag(field: string, controls: Controls) {
  return {
    draggable: controls.ready && !controls.saving,
    onDragStart: (e: React.DragEvent) => { e.dataTransfer.setData('application/x-explorer-column', field); e.dataTransfer.effectAllowed = 'move'; },
    onDragOver: (e: React.DragEvent) => { if (e.dataTransfer.types.includes('application/x-explorer-column')) e.preventDefault(); },
    onDrop: (e: React.DragEvent) => { e.preventDefault(); controls.move(e.dataTransfer.getData('application/x-explorer-column'), field); },
  };
}

export function LayoutHeader({ field, width, controls, children }: {
  field: string; width: number; controls: Controls; children: React.ReactNode;
}) {
  const start = useRef<{ x: number; width: number }>();
  const [delta, setDelta] = useState(0);
  return <div className="result-column-header" {...drag(field, controls)}>
    <span className="result-grip" title={'Drag to move ' + field} aria-hidden="true">⠿</span>{children}
    <span role="separator" aria-orientation="vertical" aria-label={'Resize ' + field} aria-valuenow={width} tabIndex={0}
      className="result-resizer" draggable={false} style={{ transform: 'translateX(' + delta + 'px)' }}
      onDragStart={e => { e.stopPropagation(); e.preventDefault(); }}
      onPointerDown={e => {
        if (!controls.ready || controls.saving || e.button !== 0) return;
        e.preventDefault(); e.stopPropagation(); e.currentTarget.setPointerCapture(e.pointerId);
        start.current = { x: e.clientX, width: e.currentTarget.closest('th')?.getBoundingClientRect().width || width };
      }}
      onPointerMove={e => { if (start.current) setDelta(e.clientX - start.current.x); }}
      onPointerCancel={() => { start.current = undefined; setDelta(0); }}
      onPointerUp={e => {
        if (!start.current) return;
        const next = start.current.width + e.clientX - start.current.x;
        start.current = undefined; setDelta(0);
        controls.column(field, { width: Math.max(80, Math.min(1200, Math.round(next))) });
      }}
      onKeyDown={e => {
        if (!['ArrowLeft', 'ArrowRight'].includes(e.key)) return;
        e.preventDefault();
        controls.column(field, { width: Math.min(1200, Math.max(80, width + (e.key === 'ArrowLeft' ? -10 : 10))) });
      }} />
  </div>;
}

export function LayoutToolbar({ controls: c, admin, types }: { controls: Controls; admin: boolean; types?: Record<string, string> }) {
  const columns = c.columns.filter(item => item.field.toLowerCase().includes(c.search.toLowerCase()));
  const error = c.error && <p role="alert">{c.error} <Button disabled={c.saving} onClick={c.reload}>Reload saved layout</Button></p>;
  return <>
    <button className="flow-tool" type="button" aria-label="Configure columns" disabled={!c.ready} onClick={() => c.setOpen(true)}>Custom layout <span aria-hidden="true">▾</span></button>
    {!c.open && error}
    <Modal className="flow-surface-modal result-layout-modal" title="Configure columns" open={c.open} onCancel={() => c.setOpen(false)} footer={null} width={570}>
      <div className="result-column-config">
        <label className="result-column-search">Find columns<Input size="small" type="search" placeholder="Search columns" value={c.search} onChange={e => c.setSearch(e.target.value)} /></label>
        <div className="result-layout-actions">
          <Button size="small" disabled={c.saving} onClick={() => c.change({ columns: c.columns.map(col => ({ ...col, visible: true })) })}>Show all</Button>
          <Button size="small" disabled={c.saving} onClick={() => c.change({ columns: c.columns.map(col => ({ ...col, visible: false })) })}>Hide all</Button>
        </div>
        <ul aria-label="Column order">{columns.map(col => <li key={col.field} {...drag(col.field, c)}>
          <span className="result-grip" aria-hidden="true">⠿</span>
          <Checkbox checked={col.visible} disabled={c.saving} onChange={e => c.column(col.field, { visible: e.target.checked })}>{col.field}</Checkbox>
          <span className="result-column-presentation">{columnPresentations(types?.[col.field]).length > 1 && <Select size="small" popupClassName="flow-select-popup result-layout-popup" aria-label={'Display ' + col.field + ' as'} value={col.presentation}
            disabled={c.saving} onChange={value => c.column(col.field, { presentation: value as Presentation })}
            options={columnPresentations(types?.[col.field]).map(mode => ({ value: mode, label: labels[mode] }))} />}</span>
          <span className="result-column-order"><Button size="small" type="text" aria-label={'Move ' + col.field + ' to top'} disabled={c.saving || c.columns[0] === col} onClick={() => c.move(col.field, c.columns[0].field)}>Top</Button>
          <Button size="small" type="text" aria-label={'Move ' + col.field + ' to bottom'} disabled={c.saving || c.columns[c.columns.length - 1] === col} onClick={() => c.move(col.field, c.columns[c.columns.length - 1].field)}>Bottom</Button></span>
        </li>)}</ul>
        {!columns.length && <p>No matching columns.</p>}
        <Checkbox checked={c.layout.wrap} disabled={c.saving} onChange={e => c.change({ wrap: e.target.checked })}>Wrap text in cells</Checkbox>
        {error}
        <div className="result-layout-actions">
          <Button size="small" disabled={c.saving} onClick={() => c.save('personal', true)}>Restore table default</Button>
          {admin && <Button size="small" disabled={c.saving} onClick={() => c.save('default')}>Save as table default</Button>}
          <Button size="small" type="primary" disabled={c.saving} onClick={() => c.save('personal')}>Save personal layout</Button>
        </div>
      </div>
    </Modal>
  </>;
}
