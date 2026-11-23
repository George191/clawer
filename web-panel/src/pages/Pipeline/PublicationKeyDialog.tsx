import { Button, Input, Modal } from '@/components/ui';
import type { EtlPublishedApi } from '../../services/api';

export default function PublicationKeyDialog({ item, issuedKey, busy, onClose, onChange }: {
  item?: Pick<EtlPublishedApi, 'keyStatus'>;
  issuedKey: string;
  busy: boolean;
  onClose: () => void;
  onChange: (revoke: boolean) => void;
}) {
  const active = item?.keyStatus === 'active';
  const close = () => { if (!busy) onClose(); };
  return <Modal open={!!item} title="Publication API key" className="flow-surface-modal explorer-resource-dialog" width={560}
    maskClosable={!busy} closable={!busy} keyboard={!busy} onCancel={close}
    footer={<>
      <Button disabled={busy} onClick={close}>Close</Button>
      <Button disabled={busy || !active} danger onClick={() => onChange(true)}>Revoke key</Button>
      <Button type="primary" disabled={busy} onClick={() => onChange(false)}>{active ? 'Rotate key' : 'Issue key'}</Button>
    </>}>
    <p>Rotation immediately invalidates the previous key. Revocation stops new requests. A new key is shown only here; store it before closing.</p>
    {issuedKey && <label>New API key<Input aria-label="New publication API key" readOnly value={issuedKey} onFocus={event => event.target.select()} /></label>}
    {item && <p>Key status: {item.keyStatus}</p>}
  </Modal>;
}
