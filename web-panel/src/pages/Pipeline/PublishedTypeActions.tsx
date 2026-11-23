import { useRef, useState } from 'react';
import { Dropdown, message, Modal } from '@/components/ui';
import { MoreOutlined } from '@/components/ui/icons';
import { changePublishedCatalogState, ontologyError, type PublishedCatalogItem } from '../../services/ontology';

const labels = { hide: 'Hide object type', unhide: 'Unhide object type', delete: 'Delete object type', restore: 'Restore object type' };
const doneLabels = { hide: 'Object type hidden', unhide: 'Object type is now visible', delete: 'Object type deleted', restore: 'Object type restored' };
const details = {
  hide: 'Hide this type from the default catalog. Project administrators can find it using Show hidden object types.',
  unhide: 'Show this type in the default catalog for its authorized readers.',
  delete: 'Delete this type and move all object-managed records to Deleted. Records and relationships are retained. Source table data is not modified.',
  restore: 'Restore this type and move all object-managed records, including individually deleted records, to Active under the existing permissions and visibility settings.',
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
      message.success({ content: doneLabels[operation], description: item.definition.name, className: 'explorer-toast' });
    } catch (failure) {
      setError(ontologyError(failure, 'Unable to update the object type.'));
      message.error({ content: labels[operation] + ' failed', description: ontologyError(failure, 'Unable to update the object type.'), className: 'explorer-toast' });
    } finally { locked.current = false; setBusy(false); }
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
