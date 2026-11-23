import { useId, useState } from 'react';
import { Button, Input, Modal } from '@/components/ui';
import './ontology-tool-workbench.css';

export default function StoragePublicationNameDialog({ mode, initialName, busy, onSave, onClose }: {
  mode: 'create' | 'rename';
  initialName: string;
  busy: boolean;
  onSave: (name: string) => void;
  onClose: () => void;
}) {
  const [name, setName] = useState(initialName);
  const formId = useId();
  const trimmed = name.trim();
  const maxLength = 120;
  const valid = trimmed.length > 0 && trimmed.length <= maxLength;
  const creating = mode === 'create';
  const close = () => { if (!busy) onClose(); };

  return <Modal open title={creating ? 'Publish API' : 'Rename publication'} className="flow-surface-modal explorer-resource-dialog" width={640}
    maskClosable={!busy} closable={!busy} keyboard={!busy} onCancel={close}
    footer={<>
      <Button disabled={busy} onClick={close}>Cancel</Button>
      <Button htmlType="submit" form={formId} type="primary" loading={busy} disabled={busy || !valid}>{creating ? 'Publish API' : 'Save name'}</Button>
    </>}>
    <form id={formId} className="ontology-tool-fields" onSubmit={event => {
      event.preventDefault();
      if (!busy && valid) onSave(trimmed);
    }}>
      <label>Publication name<Input autoFocus required maxLength={maxLength} disabled={busy} value={name}
        onChange={event => setName(event.target.value)} /></label>
    </form>
  </Modal>;
}
