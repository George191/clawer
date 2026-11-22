import { useEffect, useRef, useState } from 'react';
import { Button, Select, message } from '@/components/ui';
import { LinkOutlined } from '@/components/ui/icons';
import { createExplorerRowLink, fetchExplorerRowLinks, type ExplorerRowLinkPage } from '../../services/api';
import { createPublishedRelationship, fetchPublishedRelationships, type ExplorerDetailSource, type PublishedRelationship } from '../../services/publishedExplorerDetails';
import { backedExplorerHref } from '../../services/explorerNavigation';
import { publishedExplorerHref } from '../../services/publishedExplorerHandoff';
import { getSessionState } from '../../services/session';
import { ontologyError } from '../../services/ontology';
import type { ExplorerPreviewRow } from './ExplorerObjectOverview';
import useExplorerErrorToast from './useExplorerErrorToast';
import './explorer-link-selection.css';

export type LinkSelectionRelation = {
  id: string; relationId: string; name: string; direction: 'incoming' | 'outgoing';
  target: ExplorerDetailSource; canLink: boolean;
};
export type LinkSelectionContext = {
  source: ExplorerDetailSource;
  sourceRow: Pick<ExplorerPreviewRow, 'identity' | 'key' | 'title'>;
  relation: LinkSelectionRelation;
  sessionIdentity: string;
};

export const sameLinkSource = (a: ExplorerDetailSource, b: ExplorerDetailSource) => a.kind === 'backed'
  ? b.kind === 'backed' && a.schema === b.schema && a.table === b.table
  : b.kind === 'published' && a.definitionId === b.definitionId;
export const storageLinkRelation = (relation: ExplorerRowLinkPage['relations'][number]): LinkSelectionRelation => ({
  id: relation.id, relationId: relation.relationId, name: relation.name, direction: relation.direction,
  target: { kind: 'backed', schema: relation.schema, table: relation.table }, canLink: relation.canLink,
});
export const publishedLinkRelation = (relation: PublishedRelationship, canManage: boolean): LinkSelectionRelation => ({
  id: relation.id + ':' + relation.direction, relationId: relation.id, name: relation.name, direction: relation.direction,
  target: { kind: 'published', definitionId: relation.targetDefinitionId }, canLink: canManage,
});
export const createLinkSelection = (source: ExplorerDetailSource, sourceRow: LinkSelectionContext['sourceRow'], relation: LinkSelectionRelation): LinkSelectionContext => ({
  source, sourceRow: { identity: { ...sourceRow.identity }, key: sourceRow.key, title: sourceRow.title }, relation,
  sessionIdentity: getSessionState().identity,
});
export const linkSelectionHref = (source: ExplorerDetailSource) => source.kind === 'backed'
  ? backedExplorerHref(source.schema, source.table) : publishedExplorerHref(source.definitionId);
export function beginLinkSelection(navigate: (to: string, options: { state: { linkSelection: LinkSelectionContext } }) => void, context: LinkSelectionContext) {
  navigate(linkSelectionHref(context.relation.target), { state: { linkSelection: context } });
}

const exactKey = (key: unknown): key is Record<string, string> => !!key && typeof key === 'object' && !Array.isArray(key)
  && Object.keys(key).length > 0 && Object.values(key).every(value => typeof value === 'string');
const identityKey = (key: Record<string, unknown>) => JSON.stringify(Object.entries(key).sort(([a], [b]) => a.localeCompare(b)));
const validSource = (source: ExplorerDetailSource) => source && (source.kind === 'backed'
  ? typeof source.schema === 'string' && !!source.schema && typeof source.table === 'string' && !!source.table
  : source.kind === 'published' && typeof source.definitionId === 'string' && !!source.definitionId);
const validRow = (source: ExplorerDetailSource, row: LinkSelectionContext['sourceRow']) => exactKey(row.identity)
  && (source.kind === 'backed' || typeof row.identity.$recordId === 'string' && !!row.identity.$recordId);

export function readLinkSelection(state: unknown, target: ExplorerDetailSource): LinkSelectionContext | undefined {
  const context = (state as { linkSelection?: LinkSelectionContext } | null)?.linkSelection;
  const session = getSessionState();
  if (!context || !session.authenticated || session.locked || context.sessionIdentity !== session.identity
    || !validSource(context.source) || !validSource(context.relation?.target) || context.source.kind !== context.relation.target.kind
    || !context.sourceRow || !context.sourceRow.identity || typeof context.sourceRow.key !== 'string' || typeof context.sourceRow.title !== 'string'
    || !validRow(context.source, context.sourceRow) || typeof context.relation.id !== 'string' || typeof context.relation.relationId !== 'string'
    || typeof context.relation.name !== 'string' || context.relation.canLink !== true || !['incoming', 'outgoing'].includes(context.relation.direction)
    || !sameLinkSource(context.relation.target, target)) return;
  return context;
}

type Props = {
  source: ExplorerDetailSource; choices: ExplorerPreviewRow[]; active: ExplorerPreviewRow; context?: LinkSelectionContext;
  disabled?: boolean;
  initialRelationId?: string;
  onChooseTarget: (context: LinkSelectionContext) => void; onCreated: () => void; onCancel: () => void; onBusyChange?: (busy: boolean) => void;
};

export function ExplorerLinkForm({ source, choices, active, context, initialRelationId, disabled = false, onChooseTarget, onCreated, onCancel, onBusyChange }: Props) {
  const [anchorKey, setAnchor] = useState(active.key);
  const [relationId, setRelation] = useState(initialRelationId ?? '');
  const [targetKey, setTarget] = useState('');
  const [retry, refresh] = useState(0);
  const [read, setRead] = useState<{ scope: string; relations?: LinkSelectionRelation[]; error?: string }>();
  const [busy, setBusy] = useState(false);
  const anchor = context?.sourceRow ?? choices.find(row => row.key === anchorKey) ?? active;
  const origin = context?.source ?? source;
  const identity = useRef(getSessionState().identity).current;
  const mounted = useRef(true), writing = useRef<AbortController>(), busyCallback = useRef(onBusyChange);
  busyCallback.current = onBusyChange;
  const scope = JSON.stringify([origin, anchor.identity, context?.relation.id, retry]);
  const latestScope = useRef(scope); latestScope.current = scope;
  const current = () => {
    const session = getSessionState();
    return mounted.current && session.authenticated && !session.locked && session.identity === identity;
  };
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; writing.current?.abort(); if (writing.current) busyCallback.current?.(false); };
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    if (!validRow(origin, anchor)) { setRead({ scope, relations: [] }); return () => controller.abort(); }
    const request = origin.kind === 'backed'
      ? fetchExplorerRowLinks(origin.schema, origin.table, anchor.identity, null, 0, controller.signal)
        .then(page => page.relations.map(relation => ({ ...storageLinkRelation(relation), canLink: page.canManage && relation.canLink })))
      : fetchPublishedRelationships(origin.definitionId, String(anchor.identity.$recordId), undefined, 0, controller.signal)
        .then(page => page.relations.map(relation => publishedLinkRelation(relation, page.canManage)));
    request.then(relations => {
      if (!controller.signal.aborted && current()) setRead({ scope, relations });
    }, error => {
      if (!controller.signal.aborted && current() && error?.code !== 'ERR_CANCELED') setRead({ scope, error: ontologyError(error, 'Unable to read relationship types.') });
    });
    return () => controller.abort();
  }, [scope]);
  const page = read?.scope === scope ? read : undefined;
  const relations = page?.relations ?? [];
  const relation = context ? relations.find(item => item.id === context.relation.id && sameLinkSource(item.target, source))
    : relations.find(item => item.id === relationId) ?? relations[0];
  const inCurrentType = relation && sameLinkSource(relation.target, source);
  const targets = inCurrentType ? choices.filter(row => !sameLinkSource(origin, source) || identityKey(row.identity) !== identityKey(anchor.identity)) : [];
  const target = targets.find(row => row.key === targetKey) ?? targets[0];
  const ready = !disabled && !!relation?.canLink && validRow(origin, anchor) && !!target && validRow(source, target);
  const handoffReady = !disabled && !context && !!relation?.canLink && validRow(origin, anchor);
  useExplorerErrorToast(page?.error, 'Relationship types unavailable', () => refresh(value => value + 1));
  async function submit() {
    if (!ready || !relation || !target || writing.current || !current()) return;
    const controller = new AbortController(); writing.current = controller;
    const reviewedScope = scope;
    setBusy(true); busyCallback.current?.(true);
    try {
      if (origin.kind === 'backed') await createExplorerRowLink(origin.schema, origin.table, {
        key: anchor.identity, relationId: relation.relationId, direction: relation.direction, targetKey: target.identity as Record<string, string>,
      }, controller.signal);
      else await createPublishedRelationship(relation.relationId, relation.direction, String(anchor.identity.$recordId), String(target.identity.$recordId), controller.signal);
      if (!controller.signal.aborted && current() && latestScope.current === reviewedScope) {
        message.success({ content: 'Relationship added', className: 'explorer-toast' }); onCreated();
      }
    } catch (error) {
      if (!controller.signal.aborted && current() && latestScope.current === reviewedScope && (error as { code?: string })?.code !== 'ERR_CANCELED') message.error({
        content: 'Unable to add relationship', description: ontologyError(error, 'The relationship could not be added.'), className: 'explorer-toast',
      });
    } finally {
      if (writing.current === controller) { writing.current = undefined; busyCallback.current?.(false); }
      if (current()) setBusy(false);
    }
  }
  return <section className="explorer-link-selection" aria-label="Add relationship" aria-busy={!page || busy}>
    <header><LinkOutlined /><div><h4>Add relationship</h4><p>Use selected objects to create one relationship.</p></div></header>
    {context ? <div className="explorer-link-selection__source"><span>From</span><strong>{anchor.title}</strong><small>{origin.kind === 'backed' ? `${origin.schema}.${origin.table}` : 'Published object'}</small></div>
      : <label><span>From selected object</span><Select size="small" popupClassName="flow-select-popup" aria-label="Relationship source" disabled={disabled || busy} value={anchor.key}
        onChange={key => { if (!disabled && !writing.current) { setAnchor(key); setRelation(''); setTarget(''); } }} options={choices.map(row => ({ value: row.key, label: row.title }))} /></label>}
    <label><span>Relationship and direction</span><Select size="small" popupClassName="flow-select-popup" aria-label="Relationship and direction" disabled={disabled || busy || !page || !!context}
      value={relation?.id} placeholder="Select relationship" onChange={id => { if (!disabled && !writing.current) { setRelation(id); setTarget(''); } }}
      options={relations.map(item => ({ value: item.id, label: `${item.name} · ${item.direction}`, disabled: !item.canLink }))} /></label>
    {!!targets.length && <label><span>To selected object</span><Select size="small" popupClassName="flow-select-popup" aria-label="Relationship target" disabled={disabled || busy} value={target?.key}
      onChange={key => { if (!disabled && !writing.current) setTarget(key); }} options={targets.map(row => ({ value: row.key, label: row.title }))} /></label>}
    {page && !relations.length && <p>No relationship types are available for this object.</p>}
    {page && context && !relation && relations.length > 0 && <p>This relationship type is no longer available.</p>}
    {!context && relation && !targets.length && <p>Select a target in {relation.target.kind === 'backed' ? `${relation.target.schema}.${relation.target.table}` : 'the linked object type'} using the Explorer table.</p>}
    <footer><Button size="small" disabled={disabled || busy} onClick={() => { if (!disabled && !writing.current) onCancel(); }}>Cancel</Button>
      {context || targets.length ? <Button size="small" type="primary" loading={busy} disabled={!ready || busy} onClick={submit}>Add link</Button>
        : <Button size="small" type="primary" disabled={!handoffReady || busy} onClick={() => {
          if (handoffReady && relation && !writing.current && current()) onChooseTarget(createLinkSelection(origin, anchor, relation));
        }}>Choose target objects</Button>}
    </footer>
  </section>;
}
