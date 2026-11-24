import { useEffect, useId, useState, type ReactNode } from 'react';
import { Button, Select } from '@/components/ui';
import { ReloadOutlined } from '@/components/ui/icons';
import { fetchDataSources } from '../../services/api';
import { dataConnectionHref } from '../../services/explorerNavigation';
import type { OntologyConnection } from '../../services/ontology';
import { projectHref } from '../../services/projectDomains';
import ConnectorMark from './ConnectorMark';

export const warehouseConnection = 'warehouse';

export default function OntologySourceConnection({ value, warehouseName, disabled, onChange, onAvailabilityChange, children }: {
  value?: string; warehouseName?: string; disabled: boolean; onChange: (id: string) => void;
  onAvailabilityChange: (available: boolean) => void; children: ReactNode;
}) {
  const connectionId = useId();
  const [connections, setConnections] = useState<OntologyConnection[]>([]);
  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading');
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setStatus('loading');
    fetchDataSources(controller.signal).then(rows => {
      if (!controller.signal.aborted) {
        setConnections(rows.map(({ id, name, engine }) => ({ id, name, engine })));
        setStatus('ready');
      }
    }, () => { if (!controller.signal.aborted) setStatus('error'); });
    return () => controller.abort();
  }, [revision]);
  const options: OntologyConnection[] = [
    { id: warehouseConnection, name: warehouseName ? 'Workspace warehouse · ' + warehouseName : 'Workspace warehouse', engine: 'postgresql' },
    ...connections,
  ];
  const selected = options.find(option => option.id === value);
  const external = selected && selected.id !== warehouseConnection;
  const available = value === warehouseConnection || (status === 'ready' && Boolean(selected));
  useEffect(() => { onAvailabilityChange(available); }, [available, onAvailabilityChange]);
  return <div className="flow-source-dataset">
    <div className="flow-source-connection-row">
      <label className="flow-source-connection-label" htmlFor={connectionId}>Upstream connection</label>
      <div className="flow-source-connection-field" title={selected?.name}>
        <Select id={connectionId} aria-label={selected ? 'Upstream connection: ' + selected.name : 'Upstream connection'}
          className="flow-ontology__select flow-source-connection"
          popupClassName="flow-ontology__select-popup flow-source-connection-menu" showSearch optionFilterProp="title" loading={status === 'loading'}
          value={selected?.id} placeholder={status === 'loading' ? 'Loading connections…' : 'Select a connection'} disabled={disabled} onChange={onChange}
          options={options.map(option => ({ value: option.id, title: option.name, label: <span className="flow-source-connection-option" title={option.name}>
            <ConnectorMark engine={option.engine} size="small" /><span className="flow-source-connection-name">{option.name}</span>
          </span> }))} />
      </div>
      <div className="flow-source-connection-actions">
        <a href={projectHref(external ? dataConnectionHref(selected.id) : '/ontology/connections')}
          target="_blank" rel="noreferrer">Data Connections ↗</a>
        <Button size="small" type="text" icon={<ReloadOutlined spin={status === 'loading'} />} aria-label="Refresh connections" title="Refresh connections"
          disabled={disabled || status === 'loading'} onClick={() => setRevision(n => n + 1)} />
      </div>
    </div>
    {status === 'error' && <p role="alert">Could not load saved connections. Refresh connections to retry.</p>}
    {value && !selected && status === 'ready' && <p role="alert">This saved connection is no longer available. Select another connection.</p>}
    {selected && <>
      {external && <p className="flow-create-help">Select the warehouse table that your sync or ingestion job writes to. The object type reads from this table.</p>}
      {children}
    </>}
  </div>;
}
