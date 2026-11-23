import type { ReactNode } from 'react';
import { Button, Modal } from '@/components/ui';
import './explorer-resource-tools.css';

type Props = {
  title: string;
  context: ReactNode;
  onClose: () => void;
  bodyClassName: string;
  children: ReactNode;
};

export default function ExplorerToolDialog({ title, context, onClose, bodyClassName, children }: Props) {
  return <Modal open centered width="var(--explorer-tool-width, 1040px)" title={title} onCancel={onClose}
    className="flow-surface-modal flow-published-dialog explorer-resource-dialog explorer-tool-workbench-dialog"
    footer={<Button onClick={onClose}>Close</Button>}>
    {context && <div className="explorer-resource-context">{context}</div>}
    <div className={`flow-ontology-tool-content explorer-resource-dialog-body ${bodyClassName}`} role="region" aria-label={title} tabIndex={0}>{children}</div>
  </Modal>;
}
