import { useRef, useState } from 'react';
import { Dropdown, Modal } from '@/components/ui';
import { MoreOutlined } from '@/components/ui/icons';
import { changePublishedCatalogState, ontologyError, type PublishedCatalogItem } from '../../services/ontology';

const labels = { hide: 'Hide object type', unhide: 'Unhide object type', delete: 'Delete object type', restore: 'Restore object type' };
const details = {
  hide: 'Hide this type from the default catalog. Project administrators can find it using Show hidden object types.',
  unhide: 'Show this type in the default catalog for its authorized readers.',
  delete: 'Remove access to this type and its records. Records and relationships are retained for restoration.',
  restore: 'Restore access to this type under its existing permissions and visibility settings.',
};

export function PublishedTypeActions({ item, onChanged }: { item: PublishedCatalogItem; onChanged: () => void }) {
  const [operation, setOperation] = useState<keyof typeof labels>();
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const locked = useRef(false);
  const choices: (keyof typeof labels)[] = item.deleted ? ['restore'] : [item.hidden ? 'unhide' : 'hide', 'delete'];
  const apply = async () => {
    if (!operation || locked.current) return;
    locked.current = true; setBusy(true); setError('');
    try {
      await changePublishedCatalogState(item.id, item.revision, operation);
      setOperation(undefined); onChanged();
    } catch (failure) { setError(ontologyError(failure, 'Unable to update the object type.')); }
    finally { locked.current = false; setBusy(false); }
  };
  return <span className="flow-published-type-actions" onClick={event => event.stopPropagation()}>
    <Dropdown trigger={['click']} overlayClassName="flow-menu-popup" menu={{ items: choices.map(key => ({ key, label: labels[key], danger: key === 'delete', onClick: () => { setError(''); setOperation(key); } })) }}>
      <button type="button" aria-label={`Manage ${item.definition.name}`}><MoreOutlined /></button>
    </Dropdown>
    {operation && <Modal open centered className="flow-surface-modal" width={450} title={`${labels[operation]} · ${item.definition.name}`} okText={labels[operation]} confirmLoading={busy} onOk={apply} onCancel={() => { if (!locked.current) setOperation(undefined); }} closable={!busy} maskClosable={!busy} keyboard={!busy}>
      <p>{details[operation]}</p>{error && <p role="alert" className="flow-create-error">{error}</p>}
    </Modal>}
  </span>;
}
