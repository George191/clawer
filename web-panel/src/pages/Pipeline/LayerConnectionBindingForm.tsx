import { useEffect, useState } from 'react';
import { Button, NativeSelect, message } from '@/components/ui';
import { bindLayerConnection, fetchLayerBindings, type DataSource, type LayerConnectionBinding } from '../../services/api';

export default function LayerConnectionBindingForm({ source }: { source: DataSource }) {
  const [bindings, setBindings] = useState<LayerConnectionBinding[]>();
  const [name, setName] = useState('');
  const [saving, setSaving] = useState(false);
  const [failed, setFailed] = useState(false);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setBindings(undefined); setName(''); setFailed(false);
    fetchLayerBindings(controller.signal).then(rows => {
      if (!controller.signal.aborted) setBindings(rows);
    }).catch(() => { if (!controller.signal.aborted) { setFailed(true); message.error({ content: 'Layer bindings are unavailable. Reload to retry.', className: 'explorer-toast' }); } });
    return () => controller.abort();
  }, [source.id, revision]);
  const layer = name.trim();
  const current = bindings?.find(item => item.layer === layer);
  const selectLayer = (value: string) => {
    if (saving) return;
    const selected = bindings?.find(item => item.layer === value);
    if (!value) { setName(''); return; }
    if (!selected || !/^[a-z_][a-z0-9_]{0,62}$/.test(value)) {
      setName(''); message.warning({ content: 'This logical layer is unavailable for binding.', className: 'explorer-toast' }); return;
    }
    if (selected.connectionId === source.id) {
      setName(''); message.info({ content: `${value} is already bound to this connection.`, className: 'explorer-toast' }); return;
    }
    setName(value);
    message.warning({ content: `${selected.name ? `Currently assigned to ${selected.name}. ` : ''}Binding ${value} applies to every registered object in this layer. Complete the data and CDC cutover before restarting workers.`, className: 'explorer-toast' });
  };
  const save = async () => {
    if (!current || current.connectionId === source.id || saving || !/^[a-z_][a-z0-9_]{0,62}$/.test(layer)) return;
    setSaving(true); setFailed(false);
    try {
      const saved = await bindLayerConnection(layer, source, current?.connectionId ?? null);
      setBindings(rows => [...(rows ?? []).filter(item => item.layer !== layer), saved]);
      setName(''); message.success({ content: `${layer} is bound to this connection.`, className: 'explorer-toast' });
    } catch {
      setName(''); setFailed(true); message.error({ content: 'Binding failed. Reload to check the saved connection and current assignment.', className: 'explorer-toast' });
    } finally { setSaving(false); }
  };
  return <section className="source-layer-bindings" aria-label="Layer bindings" aria-busy={saving}>
    <div className="source-layer-bindings__heading"><h3>Layer bindings</h3>
    <p>Assigned here: {bindings?.filter(item => item.connectionId === source.id).map(item => item.layer).join(', ') || (bindings ? 'None' : 'Loading…')}</p>
    </div>
    <div className="source-layer-bindings__controls"><label>Logical layer<NativeSelect aria-label="Logical layer" value={name} disabled={!bindings?.length || saving || failed} onChange={event => selectLayer(event.target.value)}>
      <option value="">{failed ? 'Layers unavailable' : !bindings ? 'Loading layers…' : bindings.length ? 'Select a layer' : 'No logical layers'}</option>
      {bindings?.map(row => <option value={row.layer} key={row.layer}>{row.layer}</option>)}
    </NativeSelect></label>
    {failed ? <Button onClick={() => setRevision(value => value + 1)}>Reload layers</Button> : <Button disabled={!current || saving || !/^[a-z_][a-z0-9_]{0,62}$/.test(layer) || current?.connectionId === source.id} onClick={save}>Bind to this connection</Button>}
    </div>
  </section>;
}
