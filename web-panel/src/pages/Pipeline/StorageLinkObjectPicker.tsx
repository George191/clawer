import { useEffect, useRef, useState } from 'react';
import { Button, Input, Modal, message } from '@/components/ui';
import { LinkOutlined } from '@/components/ui/icons';
import { createExplorerRowLink, queryExplorerPreview, type ExplorerRowLinkPage } from '../../services/api';
import { fetchExplorerProperties, type ExplorerPropertyMapping } from '../../services/explorerProperties';
import { getSessionState } from '../../services/session';
import { ontologyError } from '../../services/ontology';
import { ExplorerTypeIcon, explorerRowTitle } from './ExplorerObjectOverview';
import useExplorerErrorToast from './useExplorerErrorToast';
import './storage-link-object-picker.css';

type Relation = ExplorerRowLinkPage['relations'][number];
type Props = { open: boolean; schema: string; table: string; sourceKey: Record<string, unknown>; relation: Relation;
  onClose: () => void; onCreated: () => void };
type Choice = { identity: Record<string, string>; key: string; title: string };

function ObjectPicker({ schema, table, sourceKey, relation, onClose, onCreated }: Props) {
  const [text, setText] = useState('');
  const [query, setQuery] = useState({ search: '', offset: 0 });
  const [revision, refresh] = useState(0);
  const [selected, setSelected] = useState('');
  const [busy, setBusy] = useState(false);
  const [read, setRead] = useState<{ scope: string; choices?: Choice[]; presentation?: ExplorerPropertyMapping; hasMore?: boolean; limited?: boolean; error?: string }>();
  const active = useRef(true), writing = useRef<AbortController>();
  const identity = useRef(getSessionState().identity).current;
  const current = () => {
    const session = getSessionState();
    return active.current && session.authenticated && !session.locked && session.identity === identity;
  };
  const scope = JSON.stringify([relation.id, query, revision]);
  useEffect(() => { active.current = true; return () => { active.current = false; writing.current?.abort(); }; }, []);
  useEffect(() => {
    const controller = new AbortController();
    setSelected('');
    Promise.all([
      queryExplorerPreview(relation.schema, relation.table, { ...query, limit: 10, filters: [], sorts: [], withIdentity: true }, controller.signal),
      fetchExplorerProperties(relation.schema, relation.table, controller.signal),
    ]).then(([page, presentation]) => {
      if (controller.signal.aborted || !current()) return;
      if (!page.primaryKey?.length || page.rows.some((_, index) => !page.rowIdentities?.[index] || page.primaryKey!.some(field => typeof page.rowIdentities![index][field] !== 'string'))) {
        throw new Error('Objects must have a stable primary key before they can be linked.');
      }
      const choices = page.rows.map((row, index) => {
        const identity = page.rowIdentities![index];
        return { identity, key: JSON.stringify(identity), title: explorerRowTitle(row, identity, presentation.titleProperty, page.primaryKey) };
      });
      setRead({ scope, choices, presentation, hasMore: page.hasMore && page.canPageFurther, limited: page.hasMore && !page.canPageFurther });
    }).catch(error => {
      if (!controller.signal.aborted && current() && error?.code !== 'ERR_CANCELED') setRead({ scope, error: ontologyError(error, 'Unable to read available objects.') });
    });
    return () => controller.abort();
  }, [scope]);
  const page = read?.scope === scope ? read : undefined;
  const loading = !page, choice = page?.choices?.find(item => item.key === selected);
  useExplorerErrorToast(page?.error, 'Objects unavailable', () => refresh(value => value + 1));
  const close = () => { if (!writing.current && current()) onClose(); };
  async function add() {
    if (!choice || loading || !relation.canLink || writing.current || !current()) return;
    const controller = new AbortController(); writing.current = controller; setBusy(true);
    try {
      await createExplorerRowLink(schema, table, { key: sourceKey, relationId: relation.relationId,
        direction: relation.direction, targetKey: choice.identity }, controller.signal);
      if (!controller.signal.aborted && current()) {
        message.success({ content: 'Relationship added', className: 'explorer-toast' });
        onCreated();
      }
    } catch (error) {
      if (!controller.signal.aborted && current() && (error as { code?: string })?.code !== 'ERR_CANCELED') message.error({ content: 'Unable to add relationship',
        description: ontologyError(error, 'The relationship could not be added.'), className: 'explorer-toast' });
    } finally { if (writing.current === controller) writing.current = undefined; if (current()) setBusy(false); }
  }
  const search = (value: string) => { if (!writing.current) { setSelected(''); setQuery({ search: value.trim(), offset: 0 }); } };
  const turnPage = (offset: number) => { if (!writing.current) { setSelected(''); setQuery(previous => ({ ...previous, offset })); } };
  return <Modal open centered width={620} className="flow-surface-modal storage-link-object-dialog" title="Add link"
    onCancel={close} closable={!busy} keyboard={!busy} maskClosable={!busy} footer={<>
      <Button disabled={busy} onClick={close}>Cancel</Button>
      <Button type="primary" loading={busy} disabled={loading || !choice || !relation.canLink} onClick={add}>Add link</Button>
    </>}>
    <div className="storage-link-object-context"><LinkOutlined /><div><strong>{relation.name} · {relation.direction}</strong><span>Choose an object from {relation.schema}.{relation.table}.</span></div></div>
    <Input.Search aria-label="Find objects to link" placeholder="Search objects" value={text} onChange={event => setText(event.target.value)} onSearch={search} disabled={busy} enterButton="Search" />
    <div className="storage-link-object-choices" aria-label="Objects to link" aria-busy={loading}>
      {loading ? <p role="status">Loading objects…</p> : page?.choices?.map(item => <button type="button" key={item.key} aria-pressed={item.key === selected}
        disabled={busy} onClick={() => setSelected(item.key)}>
        <ExplorerTypeIcon compact dataType={page.presentation?.dataType} table={relation.table} /><span><strong>{item.title}</strong>
          <small>{Object.entries(item.identity).map(([field, value]) => `${field}: ${value}`).join(' · ')}</small></span>
      </button>)}
      {page?.choices?.length === 0 && <p>No objects match this search.</p>}
    </div>
    <nav className="storage-link-object-pages" aria-label="Available objects pages">
      <span>{page?.choices?.length ? `Objects ${query.offset + 1}–${query.offset + page.choices.length}` : 'Objects'}</span>
      <Button size="small" disabled={busy || loading || query.offset === 0} onClick={() => turnPage(query.offset - 10)}>Previous</Button>
      <Button size="small" disabled={busy || loading || !page?.hasMore} onClick={() => turnPage(query.offset + 10)}>Next</Button>
    </nav>
    {page?.limited && <p className="storage-link-object-limit">Narrow your search to see more objects.</p>}
  </Modal>;
}

export default function StorageLinkObjectPicker(props: Props) {
  return props.open ? <ObjectPicker key={JSON.stringify([props.schema, props.table, props.sourceKey, props.relation.id, getSessionState().identity])} {...props} /> : null;
}
