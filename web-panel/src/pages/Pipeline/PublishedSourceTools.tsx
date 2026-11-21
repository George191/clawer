import { useEffect, useState } from 'react';
import { Button, Modal } from '@/components/ui';
import { ReloadOutlined } from '@/components/ui/icons';
import { fetchEtlApis, fetchTableStream, type EtlPublishedApi } from '../../services/api';
import PublishedApiWorkbench from './PublishedApiWorkbench';
import { explorerResourceCapabilities } from '../../services/explorerCapabilities';
import { ontologyError, type PublishedOntologyType } from '../../services/ontology';
import type { EtlStreamState } from '../../services/types';
import type { ResourceView } from './ExplorerWorkbenchHeader';
import ExplorerStreamPositions, { topicRate } from './ExplorerStreamPositions';
import ExplorerTableEvidence from './ExplorerTableEvidence';
import './published-source-tools.css';
import './explorer-resource-tools.css';

function SourceStream({ schema, table }: { schema: string; table: string }) {
  const [result, setResult] = useState<EtlStreamState>();
  const [error, setError] = useState('');
  const [version, refresh] = useState(0);
  useEffect(() => {
    let active = true, timer: number | undefined, previous: EtlStreamState | undefined;
    setResult(undefined); setError('');
    async function read() {
      try {
        const next = await fetchTableStream(schema, table);
        if (active) { setResult({ ...next, throughput: topicRate(previous, next) }); previous = next; setError(''); }
      } catch (failure) { if (active) { setResult(undefined); setError(ontologyError(failure, 'Unable to read the source stream.')); } }
      finally { if (active) timer = window.setTimeout(read, 5000); }
    }
    void read();
    return () => { active = false; window.clearTimeout(timer); };
  }, [schema, table, version]);
  return <section className="flow-schema-details flow-explorer-stream" aria-label="Source stream">
    <header><div><h3>Source stream</h3><p>Kafka topic and consumer positions for the physical source. Shared topics may include other tables.</p></div><Button size="small" icon={<ReloadOutlined />} onClick={() => refresh(value => value + 1)}>Refresh</Button></header>
    {error ? <p role="alert">{error}</p> : !result ? <p role="status">Checking source stream…</p> : !result.available ? <div role="status"><strong>{result.topic ? 'Source stream unavailable' : 'No active source stream'}</strong><p>{result.reason}</p></div> : <><p>Topic <code>{result.topic}</code></p><ExplorerStreamPositions stream={result} /></>}
  </section>;
}

function SourceApis({ schema, table }: { schema: string; table: string }) {
  const [items, setItems] = useState<EtlPublishedApi[]>();
  const [error, setError] = useState('');
  const [version, refresh] = useState(0);
  useEffect(() => {
    let active = true;
    setItems(undefined); setError('');
    fetchEtlApis(schema, table).then(result => { if (active) setItems(result); }, failure => { if (active) setError(ontologyError(failure, 'Unable to read source APIs.')); });
    return () => { active = false; };
  }, [schema, table, version]);
  return <PublishedApiWorkbench title="Source APIs" items={items || []} loading={!items && !error} error={error}
    onReload={() => refresh(value => value + 1)} />;
}

const titles = { preview: '', stream: 'Source stream', evidence: 'Source evidence', schema: 'Source column mapping', api: 'Source APIs' };
export default function PublishedSourceTools({ objectType, resource, onClose, canManage }: {
  objectType: PublishedOntologyType; resource: ResourceView; onClose: () => void; canManage: boolean;
}) {
  const definition = objectType.definition, source = definition.source;
  if (resource === 'preview') return null;
  const allowed = explorerResourceCapabilities(source, canManage).includes(resource);
  return <Modal className="flow-surface-modal published-source-tools explorer-resource-dialog" title={titles[resource]} open onCancel={onClose} width={960} footer={<Button onClick={onClose}>Done</Button>}>
    {!allowed ? <p role="alert">This object type does not provide the requested source tool.</p> : <>
      <div className="published-source-context explorer-resource-context"><strong>{definition.name}</strong><span>Physical source <code>{source.schema}.{source.table}</code></span></div>
      <div className="explorer-resource-dialog-body" role="region" aria-label={titles[resource]} tabIndex={0}>
      {resource === 'stream' && <SourceStream key={objectType.id} schema={source.schema!} table={source.table!} />}
      {resource === 'evidence' && <ExplorerTableEvidence key={objectType.id} schema={source.schema!} table={source.table!} canManage={canManage} />}
      {resource === 'schema' && <section className="flow-schema-details" aria-label="Source column mapping"><header><div><h3>Source column mapping</h3><p>Published property mappings. Edit the object type through Model revisions to change this definition.</p></div></header>
        <div className="explorer-resource-table" role="region" aria-label="Published property source columns" tabIndex={0}><table><thead><tr><th scope="col">Property</th><th scope="col">Property ID</th><th scope="col">Type</th><th scope="col">Source column</th></tr></thead><tbody>{definition.properties.map(property => <tr key={property.id}><td>{property.name}</td><td><code>{property.id}</code></td><td>{property.type}</td><td><code>{property.sourceColumn || '—'}</code></td></tr>)}</tbody></table></div>
      </section>}
      {resource === 'api' && <SourceApis key={objectType.id} schema={source.schema!} table={source.table!} />}
      </div>
    </>}
  </Modal>;
}
