import { useEffect, useState, type ReactNode } from 'react';
import { Button, Select } from '@/components/ui';
import { ReloadOutlined } from '@/components/ui/icons';
import { fetchDataSources, type DataConnectorEngine } from '../../services/api';
import { projectHref } from '../../services/projectDomains';
import ConnectorMark from './ConnectorMark';

type Choice = { id: string; name: string; engine: DataConnectorEngine };
export const warehouseConnection = 'warehouse';

export default function OntologySourceConnection({ value, warehouseName, disabled, onChange, onAvailabilityChange, children }: {
  value?: string; warehouseName?: string; disabled: boolean; onChange: (id: string) => void;
  onAvailabilityChange: (available: boolean) => void; children: ReactNode;
}) {
  const [connections, setConnections] = useState<Choice[]>([]);
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
  const options: Choice[] = [
    { id: warehouseConnection, name: warehouseName ? 'Workspace warehouse · ' + warehouseName : 'Workspace warehouse', engine: 'postgresql' },
    ...connections,
  ];
  const selected = options.find(option => option.id === value);
  const external = selected && selected.id !== warehouseConnection;
  const available = value === warehouseConnection || (status === 'ready' && Boolean(selected));
  useEffect(() => { onAvailabilityChange(available); }, [available, onAvailabilityChange]);
  return <div className="flow-source-dataset">
    <div className="flow-source-connection-row">
      <div className="flow-create-field"><span>Data connection</span>
        <div title={selected?.name}>
          <Select aria-label={selected ? 'Data connection: ' + selected.name : 'Data connection'}
            className="flow-ontology__select flow-source-connection"
            popupClassName="flow-ontology__select-popup flow-source-connection-menu" showSearch loading={status === 'loading'}
            value={selected?.id} placeholder={status === 'loading' ? 'Loading connections…' : 'Select a connection'} disabled={disabled} onChange={onChange}
            options={options.map(option => ({ value: option.id, label: <span className="flow-source-connection-option" title={option.name}>
              <ConnectorMark engine={option.engine} /><span className="flow-source-connection-name">{option.name}</span>
            </span> }))} />
        </div>
      </div>
      <div className="flow-source-connection-actions">
        <a href={projectHref(external ? '/ontology/connections?sourceId=' + encodeURIComponent(selected.id) : '/ontology/connections')}
          target="_blank" rel="noreferrer">Data Connections ↗</a>
        <Button size="small" type="text" icon={<ReloadOutlined spin={status === 'loading'} />} aria-label="Refresh connections" title="Refresh connections"
          disabled={disabled || status === 'loading'} onClick={() => setRevision(n => n + 1)} />
      </div>
    </div>
    {status === 'error' && <p role="alert">Could not load saved connections. Refresh connections to retry.</p>}
    {value && !selected && status === 'ready' && <p role="alert">This saved connection is no longer available. Select another connection.</p>}
    {selected && <>
      {external && <p className="flow-create-help">Select the destination table used by your sync or ingestion job. Objects read from this warehouse table.</p>}
      {children}
    </>}
  </div>;
}
