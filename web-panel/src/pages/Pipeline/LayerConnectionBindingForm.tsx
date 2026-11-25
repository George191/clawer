import { useEffect, useState } from 'react';
import { Button, Input } from '@/components/ui';
import { bindLayerConnection, fetchLayerBindings, type DataSource, type LayerConnectionBinding } from '../../services/api';

export default function LayerConnectionBindingForm({ source }: { source: DataSource }) {
  const [bindings, setBindings] = useState<LayerConnectionBinding[]>();
  const [name, setName] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setBindings(undefined);
    fetchLayerBindings(controller.signal).then(rows => {
      if (!controller.signal.aborted) setBindings(rows);
    }).catch(() => { if (!controller.signal.aborted) setError('Layer bindings are unavailable.'); });
    return () => controller.abort();
  }, [source.id, revision]);
  const layer = name.trim();
  const current = bindings?.find(item => item.layer === layer);
  const save = async () => {
    if (!bindings || saving || !/^[a-z_][a-z0-9_]{0,62}$/.test(layer)) return;
    setSaving(true); setError('');
    try {
      const saved = await bindLayerConnection(layer, source, current?.connectionId ?? null);
      setBindings(rows => [...(rows ?? []).filter(item => item.layer !== layer), saved]);
    } catch {
      setError('Binding failed. Reload to check the saved connection and current assignment.');
    } finally { setSaving(false); }
  };
  return <section aria-label="Layer bindings">
    <h3>Layer bindings</h3>
    <p>Assigned here: {bindings?.filter(item => item.connectionId === source.id).map(item => item.layer).join(', ') || (bindings ? 'None' : 'Loading…')}</p>
    <label>Logical layer<Input value={name} list="warehouse-layer-options" onChange={event => setName(event.target.value)} /></label>
    <datalist id="warehouse-layer-options">{bindings?.map(row => <option value={row.layer} key={row.layer} />)}</datalist>
    {current?.name && <p>Currently assigned to {current.name}. {({
      postgres_worker: 'PostgreSQL worker enabled.', mongo_source: 'MongoDB source for ODS.',
      catalog_only: 'Catalog access only.',
    })[current.executionMode]}</p>}
    {layer && current?.connectionId !== source.id && <p>Applies to every registered object in this layer. Complete the data and CDC cutover before restarting workers.</p>}
    <Button disabled={!bindings || saving || !/^[a-z_][a-z0-9_]{0,62}$/.test(layer) || current?.connectionId === source.id} onClick={save}>Bind to this connection</Button>
    {error && <p role="alert">{error} <Button onClick={() => { setError(''); setRevision(value => value + 1); }}>Reload</Button></p>}
  </section>;
}
