import type { KeyboardEventHandler } from 'react';
import { NativeSelect } from '@/components/ui';
import { explorerPropertyType } from '../../services/explorerProperties';
import './explorer-field-editor.css';

export default function ExplorerFieldEditor({ name, type = 'text', value, onChange, disabled, onKeyDown, nullControl }: {
  name: string; type?: string; value: string | null; onChange: (value: string | null) => void;
  disabled?: boolean; onKeyDown?: KeyboardEventHandler<HTMLDivElement>;
  nullControl?: { checked: boolean; onChange: (checked: boolean) => void };
}) {
  const kind = explorerPropertyType(type);
  const structured = kind === 'json';
  const number = kind === 'integer' || kind === 'number';
  const fieldDisabled = disabled || nullControl?.checked;
  const change = (next: string) => { if (!fieldDisabled) onChange(next === '' && (structured || number || kind === 'date' || kind === 'datetime') ? null : next); };
  const props = { 'aria-label': 'Edit ' + name, disabled: fieldDisabled, value: nullControl?.checked ? '' : value ?? '', onChange: (event: { target: { value: string } }) => change(event.target.value) };
  return <div className="explorer-field-editor" onKeyDown={onKeyDown}>
    <div className={`explorer-field-editor__control${nullControl ? ' has-null-control' : ''}${kind === 'boolean' ? ' is-select' : ''}`}>
      {kind === 'boolean' ? <NativeSelect {...props} onChange={event => { if (!fieldDisabled) onChange(event.target.value || null); }}>
        <option value="">No value</option><option value="true">True</option><option value="false">False</option>
      </NativeSelect> : structured ? <textarea {...props} rows={5} spellCheck={false} placeholder={nullControl?.checked ? 'Null' : 'No value · enter JSON'} />
        : <input {...props} type={number ? 'number' : kind === 'date' ? 'date' : 'text'} step={kind === 'integer' ? '1' : 'any'}
            inputMode={number ? 'decimal' : undefined} placeholder={nullControl?.checked ? 'Null' : kind === 'datetime' ? 'YYYY-MM-DD HH:mm:ss[.fraction][±HH:mm]' : 'No value'} />}
      {nullControl && <button type="button" className="explorer-field-editor__null" aria-label={`Set ${name} to null`} aria-pressed={nullControl.checked}
        title={nullControl.checked ? 'Restore previous value' : 'Set to null'} disabled={disabled}
        onClick={() => { if (!disabled) nullControl.onChange(!nullControl.checked); }}>NULL</button>}
    </div>
    {kind === 'datetime' && <small>ISO 8601 · preserves timezone and fractional seconds</small>}
  </div>;
}
