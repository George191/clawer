import type { ReactNode } from 'react';
import { Button, Checkbox, Modal, type MenuItem } from '@/components/ui';
import { DeleteOutlined, EditOutlined } from '@/components/ui/icons';
import ExplorerFieldEditor from './ExplorerFieldEditor';
import { PropertyLabel } from './PropertyTypeIcon';
import './explorer-record-mutation.css';

type Field = {
  key: string; label: string; type: string; value: string | null;
  onChange: (value: string | null) => void;
  nullControl?: { checked: boolean; onChange: (checked: boolean) => void };
};

export function recordMutationActions({ edit, remove, disabled = false }: {
  edit?: () => void; remove?: () => void; disabled?: boolean;
}): MenuItem[] {
  return [
    ...(edit ? [{ key: 'edit', label: 'Edit', icon: <EditOutlined />, disabled, onClick: edit }] : []),
    ...(remove ? [{ key: 'delete', label: 'Delete', icon: <DeleteOutlined />, danger: true, disabled, onClick: remove }] : []),
  ];
}

export default function ExplorerRecordMutation({ mode, title, identity, fields = [], error, busy = false, disabled = false, onSubmit, onCancel }: {
  mode: 'edit' | 'delete'; title: ReactNode; identity: Record<string, unknown>; fields?: Field[];
  error?: string; busy?: boolean; disabled?: boolean;
  onSubmit: () => void | Promise<void>; onCancel: () => void;
}) {
  if (mode === 'delete') return <Modal open centered width={440} className="flow-surface-modal explorer-delete-object-dialog"
    title="Delete object?" okText="Delete object" cancelText="Cancel" confirmLoading={busy}
    closable={!busy} maskClosable={!busy} keyboard={!busy} okButtonProps={{ danger: true, disabled }} cancelButtonProps={{ disabled: busy }}
    onOk={() => { if (!busy && !disabled) return onSubmit(); }} onCancel={() => { if (!busy) onCancel(); }}>
    <p>Delete <strong>{title}</strong>?</p>
    <code className="flow-explorer-row-key">{JSON.stringify(identity)}</code>
  </Modal>;
  return <section className="flow-explorer-row-mutation" aria-label="Edit object">
    {error && <p role="alert" className="flow-explorer-row-error">{error}</p>}
    <form onSubmit={event => { event.preventDefault(); if (!busy && !disabled) void onSubmit(); }}>
      <div className="flow-explorer-row-fields">{fields.map(field => <div key={field.key} className="flow-explorer-row-field">
        <PropertyLabel type={field.type}>{field.label}</PropertyLabel>
        <div className="flow-explorer-row-control"><ExplorerFieldEditor name={field.label} type={field.type} value={field.value}
          disabled={busy || disabled || field.nullControl?.checked} onChange={field.onChange} />
          {field.nullControl && <Checkbox aria-label={`Set ${field.label} to null`} checked={field.nullControl.checked} disabled={busy || disabled}
            onChange={event => field.nullControl!.onChange(event.target.checked)}>Set to null</Checkbox>}
        </div>
      </div>)}</div>
      <footer className="flow-explorer-row-tools"><Button disabled={busy} onClick={onCancel}>Cancel</Button>
        <Button htmlType="submit" type="primary" loading={busy} disabled={disabled}>Save changes</Button></footer>
    </form>
  </section>;
}
