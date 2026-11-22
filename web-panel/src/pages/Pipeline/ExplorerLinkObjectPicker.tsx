import { useEffect, useId, useRef, useState } from 'react';
import { Button, Input, Modal, message } from '@/components/ui';
import { LinkOutlined, SearchOutlined } from '@/components/ui/icons';
import { createExplorerRowLink, fetchExplorerRowLinks, queryExplorerPreview } from '../../services/api';
import { fetchExplorerProperties } from '../../services/explorerProperties';
import { createPublishedRelationship, fetchPublishedRelationships, type ExplorerDetailSource } from '../../services/publishedExplorerDetails';
import { queryPublishedExplorer } from '../../services/publishedExplorer';
import { fetchPublishedOntologyType, ontologyError, type PublishedOntologyType } from '../../services/ontology';
import { getSessionState } from '../../services/session';
import type { LinkSelectionRelation } from '../../services/explorerRelationshipLinks';
import { ExplorerTypeIcon, explorerRowTitle, type ExplorerPreviewRow } from './ExplorerObjectOverview';
import SemanticTypeGlyph from './SemanticTypeGlyph';
import useExplorerErrorToast from './useExplorerErrorToast';
import './explorer-link-object-picker.css';

type Props = { open: boolean; source: ExplorerDetailSource; sourceRow: Pick<ExplorerPreviewRow, 'identity' | 'key' | 'title'>;
  relation: LinkSelectionRelation; onClose: () => void; onCreated: () => void };
type Choice = { identity: Record<string, string>; key: string; title: string };
type ObjectPage = { choices: Choice[]; typeName: string; dataType?: string; definition?: PublishedOntologyType['definition'];
  hasMore: boolean; limited: boolean; total?: number };
const pageSize = 10;
const exactKey = (key: unknown): key is Record<string, string> => !!key && typeof key === 'object' && !Array.isArray(key)
  && Object.keys(key).length > 0 && Object.values(key).every(value => typeof value === 'string');
const validKey = (source: ExplorerDetailSource, key: unknown) => exactKey(key)
  && (source.kind === 'backed' || Object.keys(key).length === 1 && typeof key.$recordId === 'string' && !!key.$recordId);
const keyText = (key: Record<string, unknown>) => Object.entries(key).map(([field, value]) => `${field}: ${value}`).join(' · ');
const sourceText = (source: ExplorerDetailSource) => source.kind === 'backed' ? `${source.schema}.${source.table}` : 'Published object';

function ObjectPicker({ source, sourceRow, relation, onClose, onCreated }: Props) {
  const [text, setText] = useState('');
  const [query, setQuery] = useState({ search: '', offset: 0 });
  const [revision, refresh] = useState(0);
  const [selected, setSelected] = useState('');
  const [busy, setBusy] = useState(false);
  const [read, setRead] = useState<{ scope: string; page?: ObjectPage; error?: string }>();
  const radioGroup = useId();
  const mounted = useRef(true), writing = useRef<AbortController>();
  const identity = useRef(getSessionState().identity).current;
  const current = () => {
    const session = getSessionState();
    return mounted.current && session.authenticated && !session.locked && session.identity === identity;
  };
  const scope = JSON.stringify([query, revision]);
  const latestScope = useRef(scope); latestScope.current = scope;
  const target = relation.target;
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; writing.current?.abort(); }; }, []);
  useEffect(() => {
    const controller = new AbortController();
    setSelected('');
    async function load(): Promise<ObjectPage> {
      if (source.kind !== target.kind || !validKey(source, sourceRow.identity)) throw new Error('The source object must have a stable identity before it can be linked.');
      if (target.kind === 'backed') {
        const [page, presentation] = await Promise.all([
          queryExplorerPreview(target.schema, target.table, { ...query, limit: pageSize, filters: [], sorts: [], withIdentity: true }, controller.signal),
          fetchExplorerProperties(target.schema, target.table, controller.signal),
        ]);
        if (!page.primaryKey?.length || page.rows.some((_, index) => !exactKey(page.rowIdentities?.[index]) || Object.keys(page.rowIdentities![index]).length !== page.primaryKey!.length
          || page.primaryKey!.some(field => typeof page.rowIdentities![index][field] !== 'string'))) {
          throw new Error('Objects must have a stable primary key before they can be linked.');
        }
        return { choices: page.rows.map((row, index) => {
          const identity = page.rowIdentities![index];
          return { identity, key: JSON.stringify(identity), title: explorerRowTitle(row, identity, presentation.titleProperty, page.primaryKey) };
        }), typeName: presentation.displayName || target.table, dataType: presentation.dataType,
        hasMore: page.hasMore && page.canPageFurther, limited: page.hasMore && !page.canPageFurther };
      }
      const [page, type] = await Promise.all([
        queryPublishedExplorer(target.definitionId, { filters: [], search: query.search }, pageSize, query.offset, controller.signal),
        fetchPublishedOntologyType(target.definitionId, controller.signal),
      ]);
      if (type.definition.source.mode !== 'none') throw new Error('This object type does not support independently managed relationships.');
      if (page.items.some(row => !row.id || !validKey(target, row.identity) || row.identity!.$recordId !== row.id)) {
        throw new Error('Objects must have a stable record identity before they can be linked.');
      }
      const hasMore = page.hasMore && query.offset + pageSize <= 100_000;
      return { choices: page.items.map(row => ({ identity: row.identity!, key: row.id!, title: String(row.title ?? row.id) })),
        typeName: type.definition.name, definition: type.definition, hasMore, limited: page.hasMore && !hasMore, total: page.count };
    }
    load().then(page => {
      if (!controller.signal.aborted && current()) setRead({ scope, page });
    }).catch(error => {
      if (!controller.signal.aborted && current() && error?.code !== 'ERR_CANCELED') setRead({ scope, error: ontologyError(error, 'Unable to read available objects.') });
    });
    return () => controller.abort();
  }, [scope]);
  const snapshot = read?.scope === scope ? read : undefined;
  const page = snapshot?.page, loading = !snapshot;
  const choice = page?.choices.find(item => item.key === selected);
  useExplorerErrorToast(snapshot?.error, 'Objects unavailable', () => refresh(value => value + 1));
  const close = () => { if (!writing.current && current()) onClose(); };
  const choose = (key: string) => { if (!writing.current && !loading && current()) setSelected(key); };
  const search = (value: string) => {
    if (writing.current || !current()) return;
    setText(value); setSelected(''); setQuery({ search: value.trim(), offset: 0 });
  };
  const turnPage = (offset: number) => {
    if (writing.current || loading || !current()) return;
    setSelected(''); setQuery(previous => ({ ...previous, offset }));
  };
  async function add() {
    if (!choice || loading || !relation.canLink || writing.current || !current()) return;
    const controller = new AbortController(), reviewedScope = scope;
    writing.current = controller; setBusy(true);
    try {
      if (source.kind === 'backed' && target.kind === 'backed') {
        const available = await fetchExplorerRowLinks(source.schema, source.table, sourceRow.identity, relation.id, 0, controller.signal);
        if (!available.canManage || !available.relations.some(item => item.id === relation.id && item.relationId === relation.relationId
          && item.canLink && item.direction === relation.direction && item.schema === target.schema && item.table === target.table)) {
          throw new Error('This relationship is no longer available for linking.');
        }
        if (controller.signal.aborted || !current() || latestScope.current !== reviewedScope) return;
        await createExplorerRowLink(source.schema, source.table, { key: sourceRow.identity, relationId: relation.relationId,
          direction: relation.direction, targetKey: choice.identity }, controller.signal);
      } else if (source.kind === 'published' && target.kind === 'published') {
        const available = await fetchPublishedRelationships(source.definitionId, String(sourceRow.identity.$recordId), relation.id, 0, controller.signal);
        if (!available.canManage || !available.relations.some(item => item.id === relation.relationId && item.id + ':' + item.direction === relation.id
          && item.direction === relation.direction && item.targetDefinitionId === target.definitionId)) {
          throw new Error('This relationship is no longer available for linking.');
        }
        if (controller.signal.aborted || !current() || latestScope.current !== reviewedScope) return;
        await createPublishedRelationship(relation.relationId, relation.direction, String(sourceRow.identity.$recordId), String(choice.identity.$recordId), controller.signal);
      } else throw new Error('These object types do not support this relationship.');
      if (!controller.signal.aborted && current() && latestScope.current === reviewedScope) {
        message.success({ content: 'Relationship added', className: 'explorer-toast' }); onCreated();
      }
    } catch (error) {
      if (!controller.signal.aborted && current() && latestScope.current === reviewedScope && (error as { code?: string })?.code !== 'ERR_CANCELED') {
        message.error({ content: 'Unable to add relationship', description: ontologyError(error, 'The relationship could not be added.'), className: 'explorer-toast' });
      }
    } finally {
      if (writing.current === controller) writing.current = undefined;
      if (current()) setBusy(false);
    }
  }
  return <Modal open centered width={720} className="flow-surface-modal explorer-link-object-dialog" title="Add link"
    onCancel={close} closable={!busy} keyboard={!busy} maskClosable={!busy} footer={<div className="explorer-link-picker-footer">
      <div className="explorer-link-picker-selected"><span>Target object</span><strong title={choice?.title}>{choice?.title || 'No object selected'}</strong></div>
      <div><Button disabled={busy} onClick={close}>Cancel</Button>
        <Button type="primary" loading={busy} disabled={loading || !choice || !relation.canLink} onClick={add}>Add link</Button></div>
    </div>}>
    <div className="explorer-link-picker">
      <div className="explorer-link-picker-context">
        <div><span>Current object</span><strong>{sourceRow.title}</strong><small title={keyText(sourceRow.identity)}>{sourceText(source)}</small></div>
        <div className="explorer-link-picker-relation"><LinkOutlined /><strong>{relation.name}</strong><small>{relation.direction}</small></div>
        <div><span>Target object type</span><strong>{page?.typeName || (target.kind === 'backed' ? target.table : 'Published objects')}</strong><small>{sourceText(target)}</small></div>
      </div>
      <Input aria-label="Find objects to link" placeholder="Search objects" prefix={<SearchOutlined />} allowClear value={text}
        disabled={busy} onChange={event => search(event.target.value)} />
      <div className="explorer-link-picker-results" role="region" aria-label="Objects to link" aria-busy={loading}>
        <table><thead><tr><th aria-label="Select target" /><th scope="col">Object</th><th scope="col">Identifier</th></tr></thead><tbody>
          {loading ? <tr><td colSpan={3} role="status">Loading objects…</td></tr> : page?.choices.map(item => <tr key={item.key} aria-selected={item.key === selected}
            tabIndex={busy ? -1 : 0} onClick={() => choose(item.key)} onKeyDown={event => {
              if (event.target === event.currentTarget && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); choose(item.key); }
            }}>
            <td><input type="radio" name={radioGroup} aria-label={`Select ${item.title}`} checked={item.key === selected} disabled={busy} onChange={() => choose(item.key)} /></td>
            <td><span className="explorer-link-picker-title">{page.definition ? <SemanticTypeGlyph definition={page.definition} size="row" />
              : <ExplorerTypeIcon compact dataType={page.dataType} table={target.kind === 'backed' ? target.table : page.typeName} />}<strong>{item.title}</strong></span></td>
            <td><code title={keyText(item.identity)}>{keyText(item.identity)}</code></td>
          </tr>)}
          {!loading && !page?.choices.length && <tr><td colSpan={3}>{snapshot?.error ? 'Objects could not be loaded.' : 'No objects match this search.'}</td></tr>}
          {!loading && !page?.choices.length && <tr><td colSpan={3}>{snapshot?.error ? 'Objects could not be loaded.' : 'No objects match this search.'}</td></tr>}
        </tbody></table>
      </div>
      <nav className="explorer-link-picker-pages" aria-label="Available objects pages">
        <span>{page?.choices.length ? `${query.offset + 1}–${query.offset + page.choices.length}${page.total == null ? '' : ` of ${page.total.toLocaleString()}`} objects` : 'Objects'}</span>
        {(query.offset > 0 || page?.hasMore) && <div><Button size="small" disabled={busy || loading || query.offset === 0} onClick={() => turnPage(query.offset - pageSize)}>Previous</Button>
          <Button size="small" disabled={busy || loading || !page?.hasMore} onClick={() => turnPage(query.offset + pageSize)}>Next</Button></div>}
      </nav>
      {page?.limited && <p className="explorer-link-picker-limit">Refine your search to see more objects.</p>}
    </div>
  </Modal>;
}

export default function ExplorerLinkObjectPicker(props: Props) {
  return props.open ? <ObjectPicker key={JSON.stringify([props.source, props.sourceRow.identity, props.relation.id, props.relation.target, getSessionState().identity])} {...props} /> : null;
}
