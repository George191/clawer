import { useState } from 'react';
import { Button, Input, Modal } from '@/components/ui';
import type {
  TableExportCapabilities, TableExportGrant, TableExportGrantChange, TableExportPlan,
} from '../../services/api';

const statusLabel: Record<TableExportGrant['status'], string> = {
  not_granted: 'Not approved', active: 'Approved', stale: 'Approval is stale', revoked: 'Approval revoked',
};

export default function TableExportAuthorization({ grant, plan, scope, busy, saving, dirty, onChange }: {
  grant: TableExportGrant; plan: TableExportPlan | null; scope: TableExportCapabilities;
  busy: boolean; saving: boolean; dirty: boolean;
  onChange: (request: TableExportGrantChange) => Promise<void>;
}) {
  const [decision, setDecision] = useState<TableExportGrantChange>();
  const canApprove = Boolean(grant.available && plan && !dirty && grant.status !== 'active');
  const canRevoke = Boolean(plan && (grant.status === 'active' || grant.status === 'stale'));
  const choose = (approved: boolean) => {
    if (!plan || busy || (approved ? !canApprove : !canRevoke)) return;
    setDecision({
      approved, expectedPlanUpdatedAt: plan.updatedAt, expectedGeneration: grant.generation,
      marking: grant.marking, reason: '',
    });
  };
  return <section className="ontology-table-export__authorization" aria-label="Export authorization">
    <h4>Export authorization</h4>
    <p role="status"><strong>{statusLabel[grant.status]}</strong>
      {grant.status === 'active' && !grant.effective ? ' · Not effective' : ''}</p>
    <p>Saving a draft does not authorize delivery. This approval is limited to the saved revision,
      the listed columns, and the fixed receiver. Data marking: <code>{grant.marking}</code>.</p>
    {grant.approvedAt && <p>Approved at: <time dateTime={grant.approvedAt}>
      {new Date(grant.approvedAt).toLocaleString()}</time></p>}
    {grant.revokedAt && <p>Revoked at: <time dateTime={grant.revokedAt}>
      {new Date(grant.revokedAt).toLocaleString()}</time></p>}
    <div className="ontology-source-sync__actions">
      <Button size="small" disabled={busy || !canApprove} onClick={() => choose(true)}>Approve export</Button>
      {canRevoke && <Button size="small" disabled={busy} onClick={() => choose(false)}>Revoke approval</Button>}
    </div>
    <Modal open={Boolean(decision)} destroyOnHidden className="flow-surface-modal"
      title={decision?.approved ? 'Approve sandbox export?' : 'Revoke export approval?'}
      onCancel={() => { if (!saving) setDecision(undefined); }}
      onOk={async () => {
        if (!decision || !decision.reason.trim() || busy) return;
        await onChange({ ...decision, reason: decision.reason.trim() });
        setDecision(undefined);
      }}
      confirmLoading={saving}
      okButtonProps={{ disabled: !decision?.reason.trim() }}
      okText={decision?.approved ? 'Confirm approval' : 'Confirm revocation'}>
      {decision?.approved ? <>
        <p>Authorize only data marked <code>{grant.marking}</code> from the reviewed sandbox table
          <code> {scope.sourceSchema}.{scope.sourceTable}</code> to the fixed receiver
          <code> {scope.targetSchema}.{scope.targetTable}</code>. This does not send data.</p>
        <p>Approved projection: {scope.mapping.map(field =>
          `${field.sourceColumn} → ${field.targetColumn} (${field.type})`).join(', ')}.</p>
      </> : <p>Revocation can wait for an already admitted delivery to finish. It blocks subsequent
        source and receiver access but does not undo a delivery already committed by the receiver.</p>}
      <label>Reason<Input aria-label="Authorization reason" maxLength={240} value={decision?.reason ?? ''}
        disabled={saving} onChange={event => setDecision(value =>
          value && { ...value, reason: event.target.value })} /></label>
    </Modal>
  </section>;
}
