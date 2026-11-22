import { useContext, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { ExplorerSelectionOverlayContext } from './ExplorerSelectionOverlayContext';
import { Button, message } from '@/components/ui';
import { createExplorerRowLink, fetchExplorerRowLinks, queryExplorerPreview } from '../../services/api';
import { fetchExplorerProperties } from '../../services/explorerProperties';
import { createPublishedRelationship, fetchPublishedRelationships, type ExplorerDetailSource } from '../../services/publishedExplorerDetails';
import { queryPublishedExplorer } from '../../services/publishedExplorer';
import { fetchPublishedOntologyType, ontologyError, type PublishedOntologyType } from '../../services/ontology';
import { getSessionState } from '../../services/session';
import { layoutPath, publishedLayoutPath, readLayouts, type ResultLayout } from '../../services/explorerLayouts';
import { publishedColumnTypes } from '../../services/publishedExplorerPresentation';
import type { LinkSelectionRelation } from '../../services/explorerRelationshipLinks';
import { ExplorerTypeIcon, explorerRowTitle, type ExplorerPreviewRow } from './ExplorerObjectOverview';
import SemanticTypeGlyph from './SemanticTypeGlyph';
import useExplorerErrorToast from './useExplorerErrorToast';
import ExplorerLinkTargetTable from './ExplorerLinkTargetTable';
import { ExplorerResultsFooter } from './ExplorerResultsFooter';
import './explorer-link-object-picker.css';

type Props = { open: boolean; source: ExplorerDetailSource; sourceRow: Pick<ExplorerPreviewRow, 'identity' | 'key' | 'title'>;
  relation: LinkSelectionRelation; onClose: () => void; onCreated: () => void };
type ObjectPage = { choices: ExplorerPreviewRow[]; dataType?: string; definition?: PublishedOntologyType['definition'];
  columns: string[]; names: Record<string, string>; widths: Record<string, number>; wrap: boolean;
  hasMore: boolean; limited: boolean };
function displayedColumns(fields: string[], layout: ResultLayout | null) {
  const configured = layout?.columns ?? [], available = new Set(fields), known = new Set(configured.map(column => column.field));
  return { columns: [...configured.filter(column => column.visible && available.has(column.field)).map(column => column.field), ...fields.filter(field => !known.has(field))],
    widths: Object.fromEntries(configured.filter(column => available.has(column.field)).map(column => [column.field, column.width])), wrap: layout?.wrap ?? false };
}
const exactKey = (key: unknown): key is Record<string, string> => !!key && typeof key === 'object' && !Array.isArray(key)
  && Object.keys(key).length > 0 && Object.values(key).every(value => typeof value === 'string');
const validKey = (source: ExplorerDetailSource, key: unknown) => exactKey(key)
  && (source.kind === 'backed' || Object.keys(key).length === 1 && typeof key.$recordId === 'string' && !!key.$recordId);
function ObjectPicker({ source, sourceRow, relation, onClose, onCreated, host }: Props & { host: HTMLDivElement }) {
  const overlay = useContext(ExplorerSelectionOverlayContext);
  useEffect(() => { overlay?.setActive(true); return () => overlay?.setActive(false); }, [overlay?.setActive]);
  const [query, setQuery] = useState({ search: '', offset: 0 });
  const [pageSize, setPageSize] = useState(10);
  const [revision, refresh] = useState(0);
  const [selected, setSelected] = useState('');
  const [busy, setBusy] = useState(false);
  const [read, setRead] = useState<{ scope: string; page?: ObjectPage; error?: string }>();
  const mounted = useRef(true), writing = useRef<AbortController>();
  const identity = useRef(getSessionState().identity).current;
  const current = () => {
    const session = getSessionState();
    return mounted.current && session.authenticated && !session.locked && session.identity === identity;
  };
  const scope = JSON.stringify([query, pageSize, revision]);
  const latestScope = useRef(scope); latestScope.current = scope;
  const target = relation.target;
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; writing.current?.abort(); }; }, []);
  useEffect(() => {
    const controller = new AbortController();
    setSelected('');
    async function load(): Promise<ObjectPage> {
      if (source.kind !== target.kind || !validKey(source, sourceRow.identity)) throw new Error('The source object must have a stable identity before it can be linked.');
      if (target.kind === 'backed') {
        const [page, presentation, layouts] = await Promise.all([
          queryExplorerPreview(target.schema, target.table, { ...query, limit: pageSize, filters: [], sorts: [], withIdentity: true }, controller.signal),
          fetchExplorerProperties(target.schema, target.table, controller.signal),
          readLayouts(layoutPath(target.schema, target.table), controller.signal),
        ]);
        if (!page.primaryKey?.length || page.rows.some((_, index) => !exactKey(page.rowIdentities?.[index]) || Object.keys(page.rowIdentities![index]).length !== page.primaryKey!.length
          || page.primaryKey!.some(field => typeof page.rowIdentities![index][field] !== 'string'))) {
          throw new Error('Objects must have a stable primary key before they can be linked.');
        }
        return { choices: page.rows.map((row, index) => {
          const identity = page.rowIdentities![index];
          return { record: row, identity, key: JSON.stringify(identity), columnTypes: page.columnTypes ?? {}, title: explorerRowTitle(row, identity, presentation.titleProperty, page.primaryKey) };
        }), dataType: presentation.dataType,
        ...displayedColumns(page.columns, layouts.personal.layout ?? layouts.default.layout),
        names: Object.fromEntries(presentation.properties.filter(property => property.sourceColumn).map(property => [property.sourceColumn!, property.name])),
        hasMore: page.hasMore && page.canPageFurther, limited: page.hasMore && !page.canPageFurther };
      }
      const [page, type, layouts] = await Promise.all([
        queryPublishedExplorer(target.definitionId, { filters: [], search: query.search }, pageSize, query.offset, controller.signal),
        fetchPublishedOntologyType(target.definitionId, controller.signal),
        readLayouts(publishedLayoutPath(target.definitionId), controller.signal),
      ]);
      if (type.definition.source.mode !== 'none') throw new Error('This object type does not support independently managed relationships.');
      if (page.items.some(row => !row.id || !validKey(target, row.identity) || row.identity!.$recordId !== row.id)) {
        throw new Error('Objects must have a stable record identity before they can be linked.');
      }
      const hasMore = page.hasMore && query.offset + pageSize <= 100_000;
      const columnTypes = publishedColumnTypes(type);
      return { choices: page.items.map(row => ({ record: row.properties, identity: row.identity!, key: row.id!, columnTypes, title: String(row.title ?? row.id) })),
        definition: type.definition,
        ...displayedColumns(type.definition.properties.map(property => property.id), layouts.personal.layout ?? layouts.default.layout),
        names: Object.fromEntries(type.definition.properties.map(property => [property.id, property.name])),
        hasMore, limited: page.hasMore && !hasMore };
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
          direction: relation.direction, targetKey: choice.identity as Record<string, string> }, controller.signal);
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
  return createPortal(<section className="explorer-link-object-layer" role="region" aria-label="Link object selection"
    onKeyDown={event => { if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close(); } }}>
    <ExplorerLinkTargetTable rows={page?.choices ?? []} columns={page?.columns ?? []} names={page?.names} widths={page?.widths} wrap={page?.wrap}
      glyph={page?.definition ? <SemanticTypeGlyph definition={page.definition} size="row" /> : <ExplorerTypeIcon compact dataType={page?.dataType} table={target.kind === 'backed' ? target.table : ''} />}
      selectedKey={selected} loading={loading} busy={busy} onSelect={choose} />
    <ExplorerResultsFooter offset={query.offset} rowCount={page?.choices.length ?? 0} pageSize={pageSize} loading={loading} locked={busy}
      canPageFurther={!!page?.hasMore} onPrevious={() => turnPage(query.offset - pageSize)} onNext={() => turnPage(query.offset + pageSize)}
      onPageSize={size => { if (!writing.current && !loading && current()) { setSelected(''); setPageSize(size); setQuery(previous => ({ ...previous, offset: 0 })); } }}
      notices={page?.limited ? 'Object preview limit reached.' : undefined}>
      <Button size="small" disabled={busy} onClick={close}>Cancel</Button>
      <Button size="small" type="primary" loading={busy} disabled={loading || !choice || !relation.canLink} onClick={add}>Add link</Button>
    </ExplorerResultsFooter>
  </section>, host);
}

export default function ExplorerLinkObjectPicker(props: Props) {
  const overlay = useContext(ExplorerSelectionOverlayContext);
  return props.open && overlay?.host ? <ObjectPicker host={overlay.host} key={JSON.stringify([props.source, props.sourceRow.identity, props.relation.id, props.relation.target, getSessionState().identity])} {...props} /> : null;
}
