import type { DataSource, DataSourceCapabilityKey } from '../../services/api';

const items: { key: DataSourceCapabilityKey; label: string }[] = [
  { key: 'connectionTest', label: 'Connection test' },
  { key: 'catalog', label: 'Catalog' },
  { key: 'snapshot', label: 'Snapshot sync' },
  { key: 'committedBatch', label: 'Committed batch sync' },
  { key: 'reviewedRange', label: 'Reviewed range snapshot' },
];
const reasons: Partial<Record<string, string>> = {
  source_paused: 'The saved connection is paused.',
  driver_missing: 'The required connector driver is unavailable.',
  unsupported_engine: 'This capability is not supported for this connector.',
  explicit_local_required: 'This capability is limited to an explicitly enabled local environment.',
  mode_disabled: 'This capability is disabled in this environment.',
  numeric_loopback_required: 'This capability requires a numeric loopback PostgreSQL address.',
  source_identity_not_configured: 'Source identity verification is not configured.',
  source_identity_unverified: 'Verify the saved source identity first.',
  source_identity_stale: 'The saved source identity requires review again.',
  source_identity_policy_disabled: 'The source identity policy is disabled.',
  source_identity_registry_unavailable: 'The source identity status could not be verified.',
};

export default function SavedConnectionCapabilities({ capabilities }: { capabilities: DataSource['capabilities'] }) {
  if (!capabilities) return null;
  return <section className="ontology-source-catalog ontology-connection-capabilities" aria-label="Saved connection capabilities">
    <header><div><h3>Saved connection capabilities</h3><p>“Available to try” does not prove connectivity, permissions, or an approved run.</p></div></header>
    <dl className="ontology-connection-capabilities__list">
      {items.map(item => {
        const capability = capabilities[item.key];
        const label = capability?.status === 'available_to_try' ? 'Available to try'
          : capability?.status === 'unavailable' ? 'Unavailable' : 'Not reported';
        return <div key={item.key}><dt>{item.label}</dt><dd><strong>{label}</strong>
          {capability?.status === 'unavailable' && capability.reason &&
            <span>{Object.prototype.hasOwnProperty.call(reasons, capability.reason) ? reasons[capability.reason] : 'Reason not reported.'}</span>}
        </dd></div>;
      })}
    </dl>
  </section>;
}
