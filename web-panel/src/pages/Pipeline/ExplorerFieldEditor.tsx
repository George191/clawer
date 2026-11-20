import type { KeyboardEventHandler } from 'react';
import { explorerPropertyType } from '../../services/explorerProperties';
import './explorer-field-editor.css';

export default function ExplorerFieldEditor({ name, type = 'text', value, onChange, disabled, onKeyDown }: {
  name: string; type?: string; value: string | null; onChange: (value: string | null) => void;
  disabled?: boolean; onKeyDown?: KeyboardEventHandler<HTMLDivElement>;
}) {
  const kind = explorerPropertyType(type);
  const structured = kind === 'json';
  const number = kind === 'integer' || kind === 'number';
  const change = (next: string) => onChange(next === '' && (structured || number || kind === 'date' || kind === 'datetime') ? null : next);
  const props = { 'aria-label': 'Edit ' + name, disabled, value: value ?? '', onChange: (event: { target: { value: string } }) => change(event.target.value) };
  return <div className="explorer-field-editor" onKeyDown={onKeyDown}>
    {kind === 'boolean' ? <select {...props} onChange={event => onChange(event.target.value || null)}>
      <option value="">No value</option><option value="true">True</option><option value="false">False</option>
    </select> : structured ? <textarea {...props} rows={5} spellCheck={false} placeholder="No value · enter JSON" />
      : <input {...props} type={number ? 'number' : kind === 'date' ? 'date' : 'text'} step={kind === 'integer' ? '1' : 'any'}
          inputMode={number ? 'decimal' : undefined} placeholder={kind === 'datetime' ? 'YYYY-MM-DD HH:mm:ss[.fraction][±HH:mm]' : 'No value'} />}
    {kind === 'datetime' && <small>ISO 8601 · preserves timezone and fractional seconds</small>}
  </div>;
}
