import { useEffect, useState } from 'react';
import { Button } from '@/components/ui';
import { ReloadOutlined } from '@/components/ui/icons';
import { fetchEtlApis, fetchTableStream, type EtlPublishedApi } from '../../services/api';
import PublishedApiWorkbench from './PublishedApiWorkbench';
import { explorerResourceCapabilities } from '../../services/explorerCapabilities';
import { ontologyError, type PublishedOntologyType } from '../../services/ontology';
import type { EtlStreamState } from '../../services/types';
import type { ResourceView } from './ExplorerWorkbenchHeader';
import ExplorerStreamPositions, { topicRate } from './ExplorerStreamPositions';
import ExplorerToolDialog from './ExplorerToolDialog';
import ExplorerTableEvidence from './ExplorerTableEvidence';
import useExplorerErrorToast from './useExplorerErrorToast';
import './published-source-tools.css';

function SourceStream({ schema, table }: { schema: string; table: string }) {
  const [result, setResult] = useState<EtlStreamState>();
  const [error, setError] = useState('');
  const [version, refresh] = useState(0);
  useExplorerErrorToast(error, 'Unable to read the source stream', () => refresh(value => value + 1));
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
    {error ? null : !result ? <p role="status">Checking source stream…</p> : !result.available ? <div role="status"><strong>{result.topic ? 'Source stream unavailable' : 'No active source stream'}</strong><p>{result.reason}</p></div> : <><p>Topic <code>{result.topic}</code></p><ExplorerStreamPositions stream={result} /></>}
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
  const allowed = resource !== 'preview' && explorerResourceCapabilities(source, canManage).includes(resource);
  return <ExplorerToolDialog key={objectType.id} title={titles[resource]} onClose={onClose} bodyClassName="published-source-tools"
    context={allowed && <><strong>{definition.name}</strong><span>Physical source <code>{source.schema}.{source.table}</code></span></>}>
    {resource === 'preview' ? null : !allowed ? <p role="alert">This object type does not provide the requested source tool.</p> : <>
      {resource === 'stream' && <SourceStream key={objectType.id} schema={source.schema!} table={source.table!} />}
      {resource === 'evidence' && <ExplorerTableEvidence key={objectType.id} schema={source.schema!} table={source.table!} canManage={canManage} />}
      {resource === 'schema' && <section className="flow-schema-details" aria-label="Source column mapping"><header><div><h3>Source column mapping</h3><p>Published property mappings. Edit the object type through Model revisions to change this definition.</p></div></header>
        <div className="explorer-resource-table" role="region" aria-label="Published property source columns" tabIndex={0}><table><thead><tr><th scope="col">Property</th><th scope="col">Property ID</th><th scope="col">Type</th><th scope="col">Source column</th></tr></thead><tbody>{definition.properties.map(property => <tr key={property.id}><td>{property.name}</td><td><code>{property.id}</code></td><td>{property.type}</td><td><code>{property.sourceColumn || '—'}</code></td></tr>)}</tbody></table></div>
      </section>}
      {resource === 'api' && <SourceApis key={objectType.id} schema={source.schema!} table={source.table!} />}
    </>}
  </ExplorerToolDialog>;
}
