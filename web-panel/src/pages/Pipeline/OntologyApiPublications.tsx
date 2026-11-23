import { useEffect, useRef, useState } from 'react';
import { Button, Input, Modal, Select, message } from '@/components/ui';
import { getSessionState } from '../../services/session';
import { ontologyError, type PublishedOntologyType } from '../../services/ontology';
import type { PublishedExplorerQuery } from '../../services/publishedExplorerHandoff';
import { changeOntologyPublicationKey, deleteOntologyPublication, listOntologyPublications, saveOntologyPublication,
  type OntologyPublication, type PublicationInput } from '../../services/ontologyPublications';
import PublishedApiWorkbench from './PublishedApiWorkbench';
import OntologySdkDownload from './OntologySdkDownload';
import { publicationDateInput, publicationDateValue } from './publishedApiPresentation';

type Form = PublicationInput & {id?: string};
export default function OntologyApiPublications({objectType, query = {filters: []}}: {
  objectType: PublishedOntologyType; query?: PublishedExplorerQuery;
}) {
  const [page, setPage] = useState<{items: OntologyPublication[]; canManage: boolean}>();
  const [version, refresh] = useState(0), [error, setError] = useState('');
  const [busy, setBusy] = useState(false), [loading, setLoading] = useState(true);
  const [form, setForm] = useState<Form>();
  const [keyItem, setKeyItem] = useState<OntologyPublication>();
  const [issuedKey, setIssuedKey] = useState('');
  const [deleting, setDeleting] = useState<OntologyPublication>();
  const identity = useRef(getSessionState().identity).current;
  const alive = useRef(true), writing = useRef<AbortController>();
  const session = getSessionState();
  const current = () => alive.current && getSessionState().authenticated && !getSessionState().locked && getSessionState().identity === identity;
  useEffect(() => { alive.current = true; return () => {alive.current = false; writing.current?.abort();}; }, []);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError(''); setIssuedKey('');
    if (!current()) {setPage(undefined); return () => controller.abort();}
    listOntologyPublications(objectType.id, controller.signal).then(result => {
      if (!controller.signal.aborted && current()) {setPage(result); setLoading(false);}
    }, failure => {if (!controller.signal.aborted && current()) {setError(ontologyError(failure, 'Unable to read API publications.')); setLoading(false);}});
    return () => controller.abort();
  }, [objectType.id, version, session.identity, session.locked]);
  const run = async (task: (signal: AbortSignal) => Promise<void>) => {
    if (!current() || writing.current || !page?.canManage) return;
    const controller = new AbortController(); writing.current = controller; setBusy(true); setError('');
    try {await task(controller.signal);}
    catch (failure) {if (current() && !controller.signal.aborted) setError(ontologyError(failure, 'Publication request failed.'));}
    finally {writing.current = undefined; if (current()) setBusy(false);}
  };
  const begin = () => {
    const {groupBy: _group, sumField: _sum, ...range} = query;
    setForm({modelRevision: objectType.revision, name: objectType.definition.name + ' API', fields: objectType.definition.properties.map(property => property.id),
      query: range, validFrom: new Date(Date.now() - 60000).toISOString(), validUntil: new Date(Date.now() + 30 * 86400000).toISOString()});
  };
  const save = () => void run(async signal => {
    if (!form) return;
    const {id, ...payload} = form;
    const saved = await saveOntologyPublication(objectType.id, payload, id, signal);
    if (!current() || signal.aborted) return;
    setPage(previous => previous && {...previous, items: [...previous.items.filter(item => item.id !== saved.id), saved]});
    setForm(undefined); message.success({content: 'API publication saved', className: 'explorer-toast'});
  });
  const key = (revoke: boolean) => void run(async signal => {
    if (!keyItem) return;
    const result = await changeOntologyPublicationKey(objectType.id, keyItem.id, revoke, signal);
    if (!current() || signal.aborted) return;
    setIssuedKey(result.key || ''); setKeyItem(previous => previous && {...previous, keyStatus: result.keyStatus});
    // Reload the version changed by key rotation before permitting a later scope edit.
    const next = await listOntologyPublications(objectType.id, signal);
    if (current() && !signal.aborted) setPage(next);
  });
  if (!current()) return null;
  const entries = (page?.items || []).map(item => ({...item, filterType: [], filterSource: []}));
  return <>
    <PublishedApiWorkbench title="Published APIs" scopeKind="ontology" items={entries} loading={loading} busy={busy} error={error}
      onReload={() => refresh(value => value + 1)} onAdd={page?.canManage ? begin : undefined}
      userTokenSdk={<OntologySdkDownload key={objectType.id} definitionId={objectType.id} />}
      onEdit={page?.canManage ? item => {const publication = page.items.find(value => value.id === item.id); if (publication) {const {modelChanged: _changed, revision: _revision, url: _url, keyStatus: _status, ...values} = publication; setForm({...values, modelRevision: objectType.revision});}} : undefined}
      onManageKey={page?.canManage ? item => {setKeyItem(page.items.find(value => value.id === item.id)); setIssuedKey('');} : undefined}
      onDelete={page?.canManage ? item => setDeleting(page.items.find(value => value.id === item.id)) : undefined} />
    {page && !page.canManage && <p className="api-range-note">A project manager can publish and manage an external read API.</p>}
    <Modal open={!!form} title={form?.id ? 'Edit API publication' : 'Publish API'} className="flow-surface-modal explorer-resource-dialog" width={640}
      maskClosable={!busy} closable={!busy} keyboard={!busy} onCancel={() => {if (!busy) setForm(undefined);}}
      footer={<><Button disabled={busy} onClick={() => setForm(undefined)}>Cancel</Button><Button type="primary" loading={busy}
        disabled={!form?.name.trim() || !form.fields.length || !form.validFrom || !form.validUntil || Date.parse(form.validUntil) <= Date.parse(form.validFrom)} onClick={save}>Save publication</Button></>}>
      {form && <div className="ontology-tool-fields">
        <label>Publication name<Input value={form.name} maxLength={120} disabled={busy} onChange={event => setForm({...form, name: event.target.value})} /></label>
        <label>Published properties<Select aria-label="Published properties" mode="multiple" value={form.fields} disabled={busy}
          options={objectType.definition.properties.map(property => ({value: property.id, label: property.name}))} onChange={(fields: string[]) => setForm({...form, fields})} /></label>
        <div className="api-validity-fields"><label>Valid from<Input type="datetime-local" disabled={busy} value={publicationDateInput(form.validFrom)} onChange={event => setForm({...form, validFrom: publicationDateValue(event.target.value)})} /></label>
          <label>Expires at<Input type="datetime-local" disabled={busy} value={publicationDateInput(form.validUntil)} onChange={event => setForm({...form, validUntil: publicationDateValue(event.target.value)})} /></label></div>
        <p className="api-range-note">Read-only · Published model revision {objectType.revision}. This saves the {form.id ? 'existing publication' : 'current Explorer'} range and selected properties. To publish a different range, set the Explorer filters and create another publication.</p>
        <details><summary>Review saved range</summary><pre>{JSON.stringify(form.query, null, 2)}</pre></details>
      </div>}
    </Modal>
    <Modal open={!!keyItem} title="Publication API key" className="flow-surface-modal explorer-resource-dialog" width={560}
      maskClosable={!busy} closable={!busy} keyboard={!busy} onCancel={() => {if (!busy) {setKeyItem(undefined); setIssuedKey('');}}}
      footer={<><Button disabled={busy} onClick={() => {setKeyItem(undefined); setIssuedKey('');}}>Close</Button><Button disabled={busy || keyItem?.keyStatus !== 'active'} danger onClick={() => key(true)}>Revoke key</Button>
        <Button type="primary" disabled={busy} onClick={() => key(false)}>{keyItem?.keyStatus === 'active' ? 'Rotate key' : 'Issue key'}</Button></>}>
      <p>Rotation immediately invalidates the previous key. Revocation stops new requests. A new key is shown only here; store it before closing.</p>
      {issuedKey && <label>New API key<Input aria-label="New publication API key" readOnly value={issuedKey} onFocus={event => event.target.select()} /></label>}
      {keyItem && <p>Key status: {keyItem.keyStatus}</p>}
    </Modal>
    <Modal open={!!deleting} title="Delete API publication?" className="flow-surface-modal explorer-resource-dialog"
      confirmLoading={busy} okText="Delete publication" onCancel={() => {if (!busy) setDeleting(undefined);}}
      onOk={() => void run(async signal => {
        if (!deleting) return;
        await deleteOntologyPublication(objectType.id, deleting.id, signal);
        if (current() && !signal.aborted) {setDeleting(undefined); refresh(value => value + 1);}
      })}><p>This removes the published endpoint and invalidates its key. Source objects are retained.</p></Modal>
  </>;
}
