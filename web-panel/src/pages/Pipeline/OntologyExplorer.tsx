import PropertyTypeIcon from './PropertyTypeIcon';
import { explorerPropertyType } from '../../services/explorerProperties';
import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Checkbox, Drawer, Select, Tooltip } from '@/components/ui';
import {
  ApartmentOutlined, AppstoreOutlined, ArrowRightOutlined, BarsOutlined,
  CloseOutlined, DatabaseOutlined, EyeOutlined, InfoCircleOutlined,
  PlusOutlined, ReloadOutlined, RightOutlined, SearchOutlined, StarFilled, StarOutlined,
} from '@/components/ui/icons';
import { fetchCurrentUserContext, fetchEtlObjectFavorites, fetchEtlObjectPreview, fetchEtlObjectTypes, fetchLayers, importEtlObjectFavorites, updateEtlObjectFavorite, updateEtlObjectVisibility } from '../../services/api';
import { getSessionState } from '../../services/session';
import type { EtlObjectCatalog, EtlObjectPreview, EtlObjectType, EtlObjectVisibility, LayerNode } from '../../services/types';
import { accentStyle, schemaColor } from '../../theme/tokens';
import { inferSiteKind, TemplateGlyph } from '../AICollect/WorkspaceDock';
import SemanticObjectWizard from './SemanticObjectWizard';
import PublishedOntology, { type PublishedCatalogPage } from './PublishedOntology';
import OntologyCatalogSkeleton from './OntologyCatalogSkeleton';
import { listOntologyProjects } from '../../services/ontology';
import PostgresGlyph from './PostgresGlyph';
import './ontology-explorer.css';
import './ontology-create.css';

type Group = 'all' | 'favorites' | 'ingestion' | 'processing' | 'serving' | 'other';
const groups: Array<{ key: Group; label: string }> = [
  { key: 'all', label: 'All object types' }, { key: 'favorites', label: 'Favorites' },
  { key: 'ingestion', label: 'Ingestion' }, { key: 'processing', label: 'Processing' },
  { key: 'serving', label: 'Serving' }, { key: 'other', label: 'Other object types' },
];
const favoriteStorageKey = 'flow-favorite-object-types';
const favoriteImportOwnerKey = 'flow-favorite-object-types-import-owner';
const catalogBatchSize = 500;
const emptyCatalog: EtlObjectCatalog = { items: [], total: 0, limit: catalogBatchSize, offset: 0, schemaCounts: {}, groupCounts: {}, groupSchemaCounts: {} };
const number = (value: number) => value.toLocaleString('en-US');
const visibilityLabels: Record<EtlObjectVisibility, string> = { normal: 'Normal', prominent: 'Prominent', hidden: 'Hidden' };
const visibilityDescriptions: Record<EtlObjectVisibility, string> = {
  normal: 'Shown in the object type catalog.',
  prominent: 'Shown first within its resource group and schema.',
  hidden: 'Hidden from the catalog by default. Administrators can show hidden object types.',
};
type CardFrame = { node: HTMLElement; rect: DOMRect; opacity: number };
type CardMotion = { animation: Animation; x: number; y: number; from: number; to: number };
function useCatalogMotion(root: React.RefObject<HTMLDivElement>, scope: string, revision: unknown, kind: 'cards' | 'schemas' = 'cards') {
  const previous = useRef<{ scope: string; cards: Map<string, CardFrame> }>({ scope: '', cards: new Map() });
  const motions = useRef(new Map<HTMLElement, CardMotion>());
  const ghosts = useRef(new Map<string, CardFrame>());
  const reduced = useRef(window.matchMedia('(prefers-reduced-motion: reduce)'));
  const cancel = () => {
    for (const [node, motion] of motions.current) { motion.animation.cancel(); node.style.removeProperty('will-change'); }
    motions.current.clear();
    for (const frame of ghosts.current.values()) frame.node.remove();
    ghosts.current.clear();
  };
  const selector = kind === 'cards' ? '[data-testid="ontology-object-card"]' : '.flow-ontology__sidebar [data-group], .flow-ontology__schema-collapse.is-expanded [data-schema-motion-id]';
  const snapshot = () => new Map(Array.from(root.current?.querySelectorAll<HTMLElement>(selector) || [], (node) => [node.dataset.objectId || node.dataset.schemaMotionId || `group:${node.dataset.group}`, { node, rect: node.getBoundingClientRect(), opacity: 1 }]));
  useLayoutEffect(() => {
    const container = root.current;
    if (!container) return;
    const viewport = container.getBoundingClientRect();
    const visible = (rect: DOMRect) => rect.bottom > viewport.top && rect.top < viewport.bottom && rect.right > viewport.left && rect.left < viewport.right;
    const visual = (frame: CardFrame): CardFrame => {
      const motion = motions.current.get(frame.node), progress = motion?.animation.effect?.getComputedTiming().progress;
      if (!motion || progress == null) return frame;
      return { ...frame, rect: new DOMRect(frame.rect.x + motion.x * (1 - progress), frame.rect.y + motion.y * (1 - progress), frame.rect.width, frame.rect.height), opacity: motion.from + (motion.to - motion.from) * progress };
    };
    const before = new Map(Array.from(previous.current.cards, ([id, frame]) => [id, visual(frame)]));
    const exiting = new Map(Array.from(ghosts.current, ([id, frame]) => [id, visual(frame)]));
    const sameScope = previous.current.scope === scope;
    const resizingSchemas = kind === 'schemas' && Array.from(container.querySelectorAll('.flow-ontology__schema-collapse')).some((node) => node.getAnimations().some((animation) => animation instanceof CSSTransition && animation.transitionProperty === 'grid-template-rows' && animation.playState === 'running'));
    cancel();
    const after = snapshot();
    const play = (node: HTMLElement, x: number, y: number, from: number, to: number, duration: number, remove = false) => {
      node.style.willChange = 'transform, opacity';
      const animation = node.animate([{ transform: `translate(${x}px, ${y}px)`, opacity: from }, { transform: 'translate(0, 0)', opacity: to }], { duration, easing: 'cubic-bezier(.2, .8, .2, 1)' });
      motions.current.set(node, { animation, x, y, from, to });
      const finish = () => { if (motions.current.get(node)?.animation === animation) motions.current.delete(node); node.style.removeProperty('will-change'); if (remove) { node.remove(); for (const [id, frame] of ghosts.current) if (frame.node === node) ghosts.current.delete(id); } if (kind === 'schemas' && !motions.current.size) previous.current.cards = snapshot(); };
      animation.onfinish = finish; animation.oncancel = finish;
    };
    if (!reduced.current.matches) {
      if (sameScope) for (const [id, frame] of before) {
        if (after.has(id) || !visible(frame.rect)) continue;
        if (kind === 'schemas' && (frame.node.isConnected || resizingSchemas)) continue;
        const node = frame.node.cloneNode(true) as HTMLElement;
        for (const attribute of ['data-testid', 'data-object-id', 'data-schema', 'data-schema-filter', 'data-schema-motion-id', 'data-group', 'id']) node.removeAttribute(attribute);
        node.querySelectorAll('[id]').forEach((element) => element.removeAttribute('id'));
        node.classList.add(kind === 'cards' ? 'flow-ontology__asset-ghost' : 'flow-ontology__schema-ghost'); node.setAttribute('aria-hidden', 'true'); node.inert = true;
        Object.assign(node.style, { position: 'absolute', left: `${frame.rect.left - viewport.left}px`, top: `${frame.rect.top - viewport.top}px`, width: `${frame.rect.width}px`, height: `${frame.rect.height}px`, margin: '0' });
        container.append(node); ghosts.current.set(id, { ...frame, node });
        play(node, 0, 0, frame.opacity, 0, 140, true);
      }
      for (const [id, frame] of after) {
        if (!visible(frame.rect)) continue;
        const old = sameScope || id.startsWith('group:') ? before.get(id) || exiting.get(id) : undefined;
        const x = old && !resizingSchemas ? old.rect.left - frame.rect.left : 0, y = old && !resizingSchemas ? old.rect.top - frame.rect.top : 0;
        const opacity = old?.opacity ?? 0;
        if (Math.abs(x) > .5 || Math.abs(y) > .5 || opacity < 1) play(frame.node, x, y, opacity, 1, old ? 220 : 160);
      }
    }
    previous.current = { scope, cards: after };
  }, [scope, revision]);
  useEffect(() => {
    const body = root.current?.querySelector('.flow-ontology__body');
    const reset = () => { cancel(); previous.current.cards = snapshot(); };
    const settled = (event: Event) => { if ((event as TransitionEvent).propertyName === 'grid-template-rows' && !motions.current.size) reset(); };
    body?.addEventListener('scroll', reset, { passive: true, capture: true }); window.addEventListener('resize', reset); reduced.current.addEventListener('change', reset);
    if (kind === 'schemas') body?.addEventListener('transitionend', settled);
    return () => { body?.removeEventListener('scroll', reset, true); body?.removeEventListener('transitionend', settled); window.removeEventListener('resize', reset); reduced.current.removeEventListener('change', reset); cancel(); };
  }, []);
}

export { schemaColor };

export default function OntologyExplorer({ refreshToken = 0 }: { refreshToken?: number }) {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const publishedCatalog = params.get('catalog') === 'published';
  const publishedId = publishedCatalog ? params.get('objectType') || '' : '';
  const keyword = params.get('q') || '';
  const group: Group = groups.some((item) => item.key === params.get('group')) ? params.get('group') as Group : 'all';
  const view = params.get('view') === 'list' ? 'list' : 'grid';
  const allProperties = params.get('tab') === 'properties';
  const selectedSchema = params.get('schema') || '';
  const selectedTable = params.get('table') || '';
  const selectedId = selectedSchema && selectedTable ? `${selectedSchema}.${selectedTable}` : '';
  const source = params.get('source') || (!selectedTable ? selectedSchema : '');
  const rootRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const inspectorRef = useRef<HTMLElement>(null);
  const cardRefs = useRef<Record<string, HTMLButtonElement | null>>({});
  const [mobilePreview, setMobilePreview] = useState(() => window.matchMedia('(max-width: 1150px)').matches);
  const [inspectedObject, setInspectedObject] = useState({ schema: selectedSchema, table: selectedTable, allProperties });
  const inspectorSchema = selectedId ? selectedSchema : inspectedObject.schema;
  const inspectorTable = selectedId ? selectedTable : inspectedObject.table;
  const inspectorId = inspectorSchema && inspectorTable ? `${inspectorSchema}.${inspectorTable}` : '';
  const inspectorOpen = !publishedCatalog && !!inspectorId;
  const inspectorClosing = inspectorOpen && !selectedId;
  const inspectorAllProperties = selectedId ? allProperties : inspectedObject.allProperties;
  const [catalogState, setCatalogState] = useState<{ key: string; scope: string; userId: string; group: Group; data: EtlObjectCatalog }>({ key: '', scope: '', userId: '', group: 'all', data: emptyCatalog });
  const [publishedPage, setPublishedPage] = useState<PublishedCatalogPage>();
  const [menuFacetState, setMenuFacetState] = useState<{ key: string; userId: string; counts: EtlObjectCatalog['groupSchemaCounts'] }>({ key: '', userId: '', counts: {} });
  const [favoriteSchemaCounts, setFavoriteSchemaCounts] = useState<Record<string, number>>({});
  const [favoriteSchemaKey, setFavoriteSchemaKey] = useState('');
  const [favoriteVisibleCount, setFavoriteVisibleCount] = useState<number | null>(null);
  const [favoriteSummaryError, setFavoriteSummaryError] = useState('');
  const [expandedGroup, setExpandedGroup] = useState<Group | null>(null);
  const [catalogLoading, setCatalogLoading] = useState(true);
  const [catalogError, setCatalogError] = useState({ key: '', message: '' });
  const [version, setVersion] = useState(0);
  const [creationSchemas, setCreationSchemas] = useState<LayerNode[]>([]);
  const [canManage, setCanManage] = useState(false);
  const [currentUserId, setCurrentUserId] = useState('');
  const [showHidden, setShowHidden] = useState(false);
  const includeHidden = canManage && showHidden;
  const [createOpen, setCreateOpen] = useState(false);
  const [managedProjects, setManagedProjects] = useState<string[]>([]);
  const semanticProjectAccess = managedProjects.length > 0;
  const publishedHidden = semanticProjectAccess && params.get('publishedHidden') === 'true';
  const publishedDeleted = semanticProjectAccess && params.get('publishedDeleted') === 'true';
  const [createSchema, setCreateSchema] = useState<string>();
  const [favoriteState, setFavoriteState] = useState<{ userId: string; ids: string[] }>({ userId: '', ids: [] });
  const favorites = favoriteState.userId === currentUserId ? favoriteState.ids : [];
  const favoriteReady = !!currentUserId && favoriteState.userId === currentUserId;
  const [favoriteLoading, setFavoriteLoading] = useState(true);
  const [favoriteError, setFavoriteError] = useState('');
  const [favoritePending, setFavoritePending] = useState<string[]>([]);
  const favoritePendingRef = useRef(new Set<string>());
  const favoriteMutationRef = useRef(0);
  const sessionIdentity = useRef(getSessionState().identity).current;
  const userIdRef = useRef(currentUserId);
  userIdRef.current = currentUserId;
  const [preview, setPreview] = useState<{ id: string; data?: EtlObjectPreview; loading: boolean; error: string }>({ id: '', loading: false, error: '' });
  const linkedPreview = useRef<{ id: string; data: EtlObjectPreview }>();
  const previewEpoch = useRef(0);
  const [linkedLoading, setLinkedLoading] = useState('');
  const [linkedError, setLinkedError] = useState('');
  const [previewVersion, setPreviewVersion] = useState(0);
  const [visibilityDraft, setVisibilityDraft] = useState<EtlObjectVisibility>('normal');
  const [visibilitySaving, setVisibilitySaving] = useState('');
  const [visibilityError, setVisibilityError] = useState('');
  const [visibilityNotice, setVisibilityNotice] = useState('');
  const activeRef = useRef(true);
  const selectionRef = useRef(selectedId);
  selectionRef.current = selectedId;
  const favoriteIds = favorites.join(',');
  const favoriteQuery = group === 'favorites' ? favorites.join(',') : '';
  const menuFacetKey = JSON.stringify([keyword.trim(), includeHidden]);
  const menuFacetsReady = !!menuFacetState.key && menuFacetState.userId === currentUserId;
  const menuGroupSchemaCounts = menuFacetsReady ? menuFacetState.counts : {};
  const favoriteSummaryKey = JSON.stringify([currentUserId, favoriteIds, includeHidden]);
  const favoriteSummaryReady = favoriteReady && !!favoriteSchemaKey && JSON.parse(favoriteSchemaKey)[0] === currentUserId;
  const catalogFavoritesReady = group !== 'favorites' || favoriteReady;
  const catalogScope = JSON.stringify([currentUserId, keyword]);
  const catalogKey = JSON.stringify([source, keyword, group, favoriteQuery, catalogFavoritesReady, includeHidden, currentUserId]);
  const catalog = catalogState.key === catalogKey ? catalogState.data : emptyCatalog;
  const error = catalogError.key === catalogKey ? catalogError.message : '';
  const loading = catalogLoading || (catalogState.key !== catalogKey && !error);
  const confirmedTotal = catalogState.key && catalogState.userId === currentUserId ? catalogState.data.total : null;
  const hasPresentedCatalog = catalogState.scope === catalogScope;
  const presentedGroup = hasPresentedCatalog ? catalogState.group : group;
  const presentedItems = useMemo(() => {
    if (catalogState.key === catalogKey) return catalogState.data.items;
    if (!hasPresentedCatalog) return [];
    if (catalogState.group === 'favorites' && group === 'favorites') {
      return catalogState.data.items.filter((object) => favorites.includes(object.id));
    }
    return catalogState.data.items;
  }, [catalogState, catalogKey, hasPresentedCatalog, group, favoriteIds]);
  useCatalogMotion(rootRef, `${catalogScope}:${view}`, presentedItems);

  useEffect(() => { activeRef.current = true; return () => { activeRef.current = false; }; }, []);

  useEffect(() => {
    rootRef.current?.querySelector<HTMLElement>('.flow-ontology__body')?.scrollTo({ top: 0 });
  }, [keyword, view, publishedCatalog, publishedId]);

  useEffect(() => {
    let active = true;
    Promise.allSettled([fetchLayers(), fetchCurrentUserContext()]).then(([layers, context]) => {
      if (!active || getSessionState().identity !== sessionIdentity) return;
      if (layers.status === 'fulfilled') setCreationSchemas(layers.value.filter((item) => item.status === 'running').map((item) => ({ ...item, key: item.schemaName || (item.key.startsWith('ts_') ? item.key : `ts_${item.key}`) })).filter((item) => !['ts_meta', 'ts_meta_hist'].includes(item.key)));
      if (context.status === 'fulfilled') { setCanManage(!!context.value.user.is_superuser); setCurrentUserId(context.value.user.id); }
      else setFavoriteError('Unable to load your account. Retry to load saved favorites.');
    });
    return () => { active = false; };
  }, [version, refreshToken, sessionIdentity]);
  useEffect(() => {
    if (!currentUserId) return;
    let alive = true;
    listOntologyProjects().then(items => {
      if (alive && getSessionState().identity === sessionIdentity) setManagedProjects(items.map(item => item.id));
    }).catch(() => {
      if (alive && getSessionState().identity === sessionIdentity) setManagedProjects([]);
    });
    return () => { alive = false; };
  }, [currentUserId, sessionIdentity, refreshToken]);
  useEffect(() => {
    if (publishedCatalog || !currentUserId) return;
    let active = true;
    const owner = currentUserId;
    const mutation = favoriteMutationRef.current;
    const current = () => active && getSessionState().identity === sessionIdentity && userIdRef.current === owner;
    setFavoriteLoading(true); setFavoriteError('');
    const load = async () => {
      let loaded = false;
      try {
        if (!current()) return;
        const saved = await fetchEtlObjectFavorites();
        if (!current()) return;
        if (mutation !== favoriteMutationRef.current) { setVersion((value) => value + 1); return; }
        setFavoriteState({ userId: owner, ids: saved.ids });
        loaded = true;
        const legacy = localStorage.getItem(favoriteStorageKey);
        if (legacy === null) return;
        const importOwner = localStorage.getItem(favoriteImportOwnerKey);
        if (importOwner && importOwner !== owner) return;
        if (!importOwner) {
          localStorage.setItem(favoriteImportOwnerKey, owner);
          if (localStorage.getItem(favoriteImportOwnerKey) !== owner) throw new Error('Unable to claim legacy favorites');
        }
        const stored: unknown = JSON.parse(legacy);
        if (!Array.isArray(stored)) throw new Error('Invalid legacy favorites');
        const ids = [...new Set(stored.filter((id): id is string => typeof id === 'string' && /^ts_[a-z0-9_]+\.[a-z_][a-z0-9_]*$/.test(id) && !['ts_meta', 'ts_meta_hist'].includes(id.split('.')[0])))];
        for (let start = 0; start < ids.length; start += 200) {
          const imported = await importEtlObjectFavorites(ids.slice(start, start + 200));
          if (!current()) return;
          setFavoriteState({ userId: owner, ids: imported.ids });
        }
        if (localStorage.getItem(favoriteStorageKey) !== legacy || localStorage.getItem(favoriteImportOwnerKey) !== owner) throw new Error('Legacy favorites changed while importing');
        localStorage.removeItem(favoriteStorageKey);
        localStorage.removeItem(favoriteImportOwnerKey);
      } catch { if (current()) setFavoriteError(loaded ? 'Unable to import legacy browser favorites. Your saved favorites are retained. Retry to finish the import.' : 'Unable to load saved favorites. Retry to load them.'); }
      finally { if (current()) setFavoriteLoading(false); }
    };
    void load();
    return () => { active = false; };
  }, [currentUserId, sessionIdentity, version, refreshToken, publishedCatalog]);
  useEffect(() => {
    let active = true;
    setFavoriteSummaryError('');
    if (publishedCatalog || !favoriteReady) return;
    const load = async () => {
      try {
        const ids = favoriteIds ? favoriteIds.split(',') : [];
        let total = 0;
        const counts: Record<string, number> = {};
        for (let start = 0; start < ids.length; start += 40) {
          const part = await fetchEtlObjectTypes({ ids: ids.slice(start, start + 40).join(','), limit: 1, includeHidden });
          if (!active) return;
          total += part.total;
          Object.entries(part.schemaCounts).forEach(([schema, count]) => { counts[schema] = (counts[schema] || 0) + count; });
        }
        if (active) { setFavoriteVisibleCount(total); setFavoriteSchemaCounts(counts); setFavoriteSchemaKey(favoriteSummaryKey); }
      } catch { if (active) setFavoriteSummaryError('Unable to load favorite object types. Retry to update the count.'); }
    };
    void load();
    return () => { active = false; };
  }, [favoriteIds, favoriteReady, includeHidden, version, refreshToken, favoriteSummaryKey, publishedCatalog]);
  useEffect(() => {
    if (publishedCatalog) return;
    if (!currentUserId || !catalogFavoritesReady) { setCatalogLoading(true); return; }
    let active = true;
    setCatalogLoading(true); setCatalogError({ key: catalogKey, message: '' });
    const timer = window.setTimeout(async () => {
      try {
        const query = { schema: source || undefined, search: keyword.trim() || undefined, includeHidden };
        let result: EtlObjectCatalog;
        let menuCounts: EtlObjectCatalog['groupSchemaCounts'];
        if (group === 'favorites') {
          const directory = await fetchEtlObjectTypes({ search: query.search, limit: 1, includeHidden });
          if (!active) return;
          menuCounts = directory.groupSchemaCounts;
          const ids = favoriteQuery ? favoriteQuery.split(',') : [];
          const items: EtlObjectType[] = [];
          const schemaCounts: Record<string, number> = {};
          const groupCounts: Record<string, number> = {};
          const groupSchemaCounts: Record<string, Record<string, number>> = {};
          for (let start = 0; start < ids.length; start += 40) {
            const part = await fetchEtlObjectTypes({ ...query, ids: ids.slice(start, start + 40).join(','), limit: 40 });
            if (!active) return;
            items.push(...part.items);
            Object.entries(part.schemaCounts).forEach(([key, count]) => { schemaCounts[key] = (schemaCounts[key] || 0) + count; });
            Object.entries(part.groupCounts).forEach(([key, count]) => { groupCounts[key] = (groupCounts[key] || 0) + count; });
            Object.entries(part.groupSchemaCounts).forEach(([key, schemas]) => {
              const counts = groupSchemaCounts[key] ||= {};
              Object.entries(schemas).forEach(([schema, count]) => { counts[schema] = (counts[schema] || 0) + count; });
            });
          }
          result = { items, total: items.length, limit: items.length, offset: 0, schemaCounts, groupCounts, groupSchemaCounts };
        } else {
          const filters = { ...query, group: group === 'all' ? undefined : group, limit: catalogBatchSize };
          result = await fetchEtlObjectTypes({ ...filters, offset: 0 });
          if (!active) return;
          menuCounts = result.groupSchemaCounts;
          const items = [...result.items];
          while (items.length < result.total) {
            const part = await fetchEtlObjectTypes({ ...filters, offset: items.length });
            if (!active) return;
            if (!part.items.length) throw new Error('Object catalog changed while loading');
            items.push(...part.items);
          }
          result = { ...result, items, limit: items.length, offset: 0 };
        }
        if (!active) return;
        setCatalogState({ key: catalogKey, scope: catalogScope, userId: currentUserId, group, data: result });
        setMenuFacetState({ key: menuFacetKey, userId: currentUserId, counts: menuCounts });
      } catch { if (active) setCatalogError({ key: catalogKey, message: 'Unable to load object types. The last loaded results are retained. Retry to update them.' }); }
      finally { if (active) setCatalogLoading(false); }
    }, keyword ? 200 : 0);
    return () => { active = false; window.clearTimeout(timer); };
  }, [source, keyword, group, favoriteQuery, catalogFavoritesReady, includeHidden, version, refreshToken, catalogKey, catalogScope, menuFacetKey, publishedCatalog]);
  useEffect(() => {
    if (selectedId) { setInspectedObject({ schema: selectedSchema, table: selectedTable, allProperties }); return; }
    if (!inspectorId) return;
    const timer = window.setTimeout(() => setInspectedObject({ schema: '', table: '', allProperties: false }), window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 220);
    return () => window.clearTimeout(timer);
  }, [selectedId, selectedSchema, selectedTable, allProperties, inspectorId]);
  useEffect(() => {
    previewEpoch.current += 1;
    setLinkedLoading(''); setLinkedError('');
    const prepared = linkedPreview.current;
    linkedPreview.current = undefined;
    if (publishedCatalog) return;
    if (!inspectorId) { setPreview({ id: '', loading: false, error: '' }); return; }
    if (inspectorClosing) return;
    if (prepared?.id === inspectorId) {
      setPreview({ id: inspectorId, data: prepared.data, loading: false, error: '' }); return;
    }
    let active = true;
    setPreview({ id: inspectorId, loading: true, error: '' });
    fetchEtlObjectPreview(inspectorSchema, inspectorTable, includeHidden).then((data) => {
      if (active) setPreview({ id: inspectorId, data, loading: false, error: '' });
    }).catch(() => { if (active) setPreview({ id: inspectorId, loading: false, error: 'Unable to load this object type. Try again.' }); });
    return () => { active = false; };
  }, [inspectorId, inspectorSchema, inspectorTable, inspectorClosing, includeHidden, previewVersion, version, refreshToken, publishedCatalog]);
  useEffect(() => {
    setVisibilityError(''); setVisibilityNotice('');
  }, [inspectorId]);
  useEffect(() => {
    if (preview.id === inspectorId && preview.data) setVisibilityDraft(preview.data.visibility);
  }, [inspectorId, preview.id, preview.data?.visibility]);

  const saveVisibility = async () => {
    if (!canManage || visibilitySaving || !preview.data || preview.id !== inspectorId || inspectorClosing || getSessionState().identity !== sessionIdentity) return;
    const id = inspectorId;
    setVisibilitySaving(id); setVisibilityError(''); setVisibilityNotice('');
    try {
      const saved = await updateEtlObjectVisibility(inspectorSchema, inspectorTable, visibilityDraft);
      if (!activeRef.current || getSessionState().identity !== sessionIdentity) return;
      if (selectionRef.current === id) {
        if (saved.visibility === 'hidden') setShowHidden(true);
        setPreview((current) => current.id === id && current.data ? { ...current, data: { ...current.data, visibility: saved.visibility } } : current);
        setVisibilityDraft(saved.visibility);
        setVisibilityNotice(saved.visibility === 'hidden' ? 'Visibility saved. Hidden object types are shown so you can manage this object.' : 'Visibility saved.');
        inspectorRef.current?.querySelector<HTMLButtonElement>('button')?.focus();
      }
      setVersion((value) => value + 1);
    } catch (error) {
      if (activeRef.current && getSessionState().identity === sessionIdentity && selectionRef.current === id) setVisibilityError((error as { response?: { status?: number } }).response?.status === 403 ? 'You do not have permission to change visibility.' : 'Unable to save visibility. Try again.');
    } finally { if (activeRef.current && getSessionState().identity === sessionIdentity) setVisibilitySaving(''); }
  };

  const toggleFavorite = async (object: EtlObjectType) => {
    if (!favoriteReady || favoriteLoading || getSessionState().identity !== sessionIdentity) return;
    const owner = currentUserId;
    const key = `${sessionIdentity}:${object.id}`;
    if (favoritePendingRef.current.has(key)) return;
    favoritePendingRef.current.add(key);
    favoriteMutationRef.current += 1;
    setFavoritePending((current) => [...current, key]); setFavoriteError('');
    const current = () => activeRef.current && getSessionState().identity === sessionIdentity && userIdRef.current === owner;
    try {
      const saved = await updateEtlObjectFavorite(object.schema, object.table, !favorites.includes(object.id));
      if (!current()) return;
      setFavoriteState((state) => state.userId !== owner ? state : { ...state, ids: saved.favorite ? [...new Set([...state.ids, saved.id])] : state.ids.filter((id) => id !== saved.id) });
    } catch { if (current()) setFavoriteError('Unable to save this favorite. Try the star again, or retry to reload saved favorites.'); }
    finally {
      favoritePendingRef.current.delete(key);
      if (current()) { favoriteMutationRef.current += 1; setFavoritePending((pending) => pending.filter((id) => id !== key)); }
    }
  };

  const updateQuery = (changes: Record<string, string | null>, replace = false) => setParams((current) => {
    const next = new URLSearchParams(current);
    Object.entries(changes).forEach(([key, value]) => value ? next.set(key, value) : next.delete(key));
    return next;
  }, { replace });
  const catalogSearch = publishedCatalog ? params.get('publishedQ') || '' : keyword;
  const searchLabel = publishedCatalog ? 'Search published types' : 'Search object types';
  const changeCatalogSearch = (value: string) => updateQuery(
    publishedCatalog ? { publishedQ: value || null, publishedOffset: null } : { q: value || null }, true,
  );
  const selectPublished = (id = '') => updateQuery({ catalog: 'published', objectType: id || null, schema: null, table: null, tab: null });
  const selectObject = (object: { schema: string; table: string }) => updateQuery({ schema: object.schema, table: object.table, tab: null });
  const selectLinkedObject = async (object: { id: string; schema: string; table: string }) => {
    if (preview.loading || visibilitySaving || preview.id !== inspectorId || inspectorClosing || object.id === inspectorId) return;
    const operation = ++previewEpoch.current;
    const origin = inspectorId;
    const current = () => activeRef.current && previewEpoch.current === operation && selectionRef.current === origin
      && getSessionState().identity === sessionIdentity;
    setLinkedLoading(object.id); setLinkedError('');
    try {
      const data = await fetchEtlObjectPreview(object.schema, object.table, includeHidden);
      if (!current()) return;
      linkedPreview.current = { id: object.id, data };
      setPreview({ id: object.id, data, loading: false, error: '' });
      selectObject(object);
    } catch {
      if (current()) setLinkedError('Unable to load the linked object type. Select it again to retry.');
    } finally {
      if (current()) setLinkedLoading('');
    }
  };
  const closeInspector = () => {
    updateQuery({ schema: null, table: null, tab: null }, true);
  };
  const openWorkspace = () => {
    if (selectedId) navigate(`/ontology/explorer?${new URLSearchParams({ schema: selectedSchema, table: selectedTable, returnTo: '/ontology?' + params.toString() })}`);
  };
  const startNewObject = () => {
    const destination = source || selectedSchema;
    setCreateSchema(creationSchemas.some((item) => item.key === destination) ? destination : undefined);
    setCreateOpen(true);
  };
  useEffect(() => {
    const query = window.matchMedia('(max-width: 1150px)');
    const resize = () => setMobilePreview(query.matches);
    query.addEventListener('change', resize);
    return () => query.removeEventListener('change', resize);
  }, []);
  useEffect(() => {
    if (inspectorId) inspectorRef.current?.querySelector<HTMLButtonElement>('button')?.focus();
  }, [inspectorId]);
  useEffect(() => {
    if (!inspectorOpen) return;
    const background = document.getElementById('root');
    const before = background?.inert;
    if (background) background.inert = true;
    return () => {
      if (background) background.inert = !!before;
      requestAnimationFrame(() => {
        const target = cardRefs.current[inspectorId] || searchRef.current;
        if (target?.isConnected) target.focus();
      });
    };
  }, [inspectorOpen]);

  const sections = useMemo(() => {
    const resourceGroups = presentedGroup === 'all' ? groups.filter((item) => item.key !== 'all' && item.key !== 'favorites') : groups.filter((item) => item.key === presentedGroup);
    return resourceGroups.map((item) => {
      const items = presentedGroup === 'all' ? presentedItems.filter((object) => object.resourceGroup === item.key) : presentedItems;
      return { ...item, schemas: [...new Set(items.map((object) => object.schema))].sort().map((schema) => ({ schema, items: items.filter((object) => object.schema === schema).sort((left, right) => Number(right.visibility === 'prominent') - Number(left.visibility === 'prominent')) })) };
    }).filter((section) => section.schemas.length);
  }, [presentedItems, presentedGroup]);
  const renderedObjects = sections.flatMap((section) => section.schemas.flatMap((schema) => schema.items));
  const favoriteFacetsReady = favoriteReady && (!favoriteIds || (group !== 'favorites' || !keyword.trim()
    ? favoriteSummaryReady : catalogState.group === 'favorites' && catalogState.userId === currentUserId && !!catalogState.key));
  const displayedFavoriteSchemaCounts = useMemo(() => {
    if (!favoriteFacetsReady || !favoriteIds) return {};
    if (group !== 'favorites' || !keyword.trim()) return favoriteSchemaCounts;
    const counts: Record<string, number> = {};
    Object.values(catalogState.data.groupSchemaCounts).forEach((schemas) => Object.entries(schemas).forEach(([schema, count]) => { counts[schema] = (counts[schema] || 0) + count; }));
    return counts;
  }, [group, keyword, catalogState.data.groupSchemaCounts, favoriteSchemaCounts, favoriteFacetsReady, favoriteIds]);
  const schemaCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    Object.values(menuGroupSchemaCounts).forEach((schemas) => Object.entries(schemas).forEach(([schema, count]) => { counts[schema] = (counts[schema] || 0) + count; }));
    if (menuFacetsReady && source && !(source in counts)) counts[source] = 0;
    return counts;
  }, [menuGroupSchemaCounts, source, menuFacetsReady]);
  const facetGroupCounts = Object.fromEntries(Object.entries(menuGroupSchemaCounts).map(([key, schemas]) => [key, Object.values(schemas).reduce((sum, count) => sum + count, 0)]));
  useCatalogMotion(rootRef, currentUserId, JSON.stringify([expandedGroup, menuGroupSchemaCounts, displayedFavoriteSchemaCounts]), 'schemas');
  const moveCardFocus = (event: React.KeyboardEvent<HTMLButtonElement>, index: number) => {
    if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    if (event.key === 'ArrowUp' || event.key === 'ArrowDown') {
      const current = event.currentTarget.getBoundingClientRect();
      const candidates = renderedObjects.map((object) => cardRefs.current[object.id]).filter((button): button is HTMLButtonElement => !!button).map((button) => ({ button, rect: button.getBoundingClientRect() }))
        .filter(({ rect }) => event.key === 'ArrowDown' ? rect.top > current.top + 1 : rect.top < current.top - 1)
        .sort((a, b) => Math.abs(a.rect.top - current.top) - Math.abs(b.rect.top - current.top) || Math.abs(a.rect.left - current.left) - Math.abs(b.rect.left - current.left));
      candidates[0]?.button.focus(); return;
    }
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? renderedObjects.length - 1 : Math.max(0, Math.min(renderedObjects.length - 1, index + (event.key === 'ArrowLeft' ? -1 : 1)));
    cardRefs.current[renderedObjects[next]?.id]?.focus();
  };
  const data = preview.id === inspectorId ? preview.data : undefined;
  const visibleProperties = data ? inspectorAllProperties ? data.properties : data.properties.slice(0, 6) : [];
  const identity = data || catalog.items.find((object) => object.id === inspectorId);
  const selectedColor = identity ? accentStyle(identity.dataType) : undefined;
  const matchingPublishedCount = publishedPage?.identity === sessionIdentity ? publishedPage.total : null;
  const displayedTotal = publishedCatalog ? matchingPublishedCount : confirmedTotal;
  const lineageLink = <button type="button" className="flow-ontology__sidebar-link" onClick={() => navigate('/ontology/lineage')}><ApartmentOutlined /><span>Lineage &amp; impact</span><ArrowRightOutlined /></button>;
  const groupTotal = Object.values(facetGroupCounts).reduce((sum, count) => sum + count, 0);

  return <div ref={rootRef} className="flow-ontology" data-testid="ontology-explorer">
    <div className="flow-ontology__workspace">
      <header className="flow-ontology__hero" aria-label="Object type catalog">
        <div className="flow-ontology__catalog-bar">
          <nav className="flow-ontology__catalog-navigation" aria-label="Catalog views">
            <button type="button" aria-pressed={!publishedCatalog} onClick={() => { if (publishedCatalog) updateQuery({ catalog: null, objectType: null }); }}><DatabaseOutlined />Storage-backed</button>
            <button type="button" aria-pressed={publishedCatalog} onClick={() => { if (!publishedCatalog) selectPublished(); }}><AppstoreOutlined />Published</button>
          </nav>
          {!publishedId && <div className="flow-ontology__topbar">
            <div className="flow-ontology__search" role="search">
              <SearchOutlined /><input ref={searchRef} data-testid="ontology-search" aria-label={searchLabel} placeholder={searchLabel + '…'}
                value={catalogSearch} maxLength={publishedCatalog ? 200 : undefined} onChange={event => changeCatalogSearch(event.target.value)}
                onKeyDown={event => {
                  if (event.key === 'ArrowDown') {
                    const first = publishedCatalog ? rootRef.current?.querySelector<HTMLButtonElement>('[data-published-type-link]') : cardRefs.current[renderedObjects[0]?.id];
                    if (first) { event.preventDefault(); first.focus(); }
                  }
                }} />
              {catalogSearch && <button type="button" aria-label={publishedCatalog ? 'Clear published search' : 'Clear search'}
                onClick={() => { changeCatalogSearch(''); searchRef.current?.focus(); }}><CloseOutlined /></button>}
            </div>
            <div className="flow-ontology__view-switch" role="group" aria-label="Catalog view"><button type="button" aria-label="Grid view" aria-pressed={view === 'grid'} className={view === 'grid' ? 'is-active' : ''} onClick={() => updateQuery({ view: null })}><AppstoreOutlined /><span>Grid</span></button><button type="button" aria-label="List view" aria-pressed={view === 'list'} className={view === 'list' ? 'is-active' : ''} onClick={() => updateQuery({ view: 'list' })}><BarsOutlined /><span>List</span></button></div>
            {(publishedCatalog ? semanticProjectAccess : canManage) && <Checkbox className="flow-ontology__show-hidden" checked={publishedCatalog ? publishedHidden : includeHidden} onChange={event => publishedCatalog ? updateQuery({ publishedHidden: event.target.checked ? 'true' : null, publishedOffset: null }) : setShowHidden(event.target.checked)}>Show hidden object types</Checkbox>}
            {publishedCatalog && semanticProjectAccess && <Checkbox className="flow-ontology__show-hidden" checked={publishedDeleted} onChange={event => updateQuery({ publishedDeleted: event.target.checked ? 'true' : null, publishedOffset: null })}>Show deleted object types</Checkbox>}
            <span className="flow-ontology__summary" aria-busy={publishedCatalog ? displayedTotal == null : loading}><span className="flow-ontology__summary-value">{displayedTotal == null ? '…' : number(displayedTotal)}</span><span>object types</span></span>
            <button type="button" className="flow-ontology__button" onClick={() => navigate('/ontology/runtime')}>Runtime</button>
            <button type="button" className="flow-ontology__button" onClick={() => navigate('/ontology/connections')}>Data Connections</button>
          </div>}
          {(canManage || semanticProjectAccess) && <button type="button" className="flow-ontology__button flow-ontology__button--primary flow-ontology__create-action" onClick={startNewObject}><PlusOutlined />New object type</button>}
        </div>
      </header>
      <div className="flow-ontology__body">
        {publishedCatalog ? <PublishedOntology key={publishedId} selection={publishedId} view={view} managedProjects={managedProjects} includeHidden={publishedHidden} includeDeleted={publishedDeleted} catalogPage={publishedPage?.identity === getSessionState().identity ? publishedPage : undefined} onCatalogPage={setPublishedPage} onSelect={selectPublished} onClearSearch={() => changeCatalogSearch('')} /> : <>
        <aside className="flow-ontology__sidebar" aria-label="Object type groups"><nav aria-busy={loading}>
          {groups.map((item) => {
            const expanded = expandedGroup === item.key;
            const active = group === item.key && !source;
            const counts = item.key === 'favorites' ? { ...displayedFavoriteSchemaCounts } : item.key === 'all' ? schemaCounts : { ...menuGroupSchemaCounts[item.key] };
            const facetsReady = item.key === 'favorites' ? favoriteFacetsReady : menuFacetsReady;
            if (facetsReady && group === item.key && source && !(source in counts)) counts[source] = 0;
            const schemas = Object.keys(counts).sort();
            return <React.Fragment key={item.key}>{item.key === 'ingestion' && <div className="flow-ontology__sidebar-title">Object type groups</div>}
              <div className="flow-ontology__group">
                <button type="button" data-group={item.key} aria-pressed={active} aria-expanded={expanded} aria-controls={`ontology-schema-group-${item.key}`} className={active ? 'is-active' : ''} onClick={() => {
                  setExpandedGroup(expanded ? null : item.key);
                  if (!active) updateQuery({ group: item.key === 'all' ? null : item.key, source: null, ...(!selectedTable ? { schema: null } : {}) });
                }}><RightOutlined rotate={expanded ? 90 : 0} /><span>{item.label}</span><small>{item.key === 'favorites' ? !favoriteSummaryReady || favoriteVisibleCount === null ? '…' : number(favoriteVisibleCount) : menuFacetsReady ? number(item.key === 'all' ? groupTotal : facetGroupCounts[item.key] || 0) : '…'}</small></button>
                <div id={`ontology-schema-group-${item.key}`} className={`flow-ontology__schema-collapse${expanded ? ' is-expanded' : ''}`} aria-hidden={!expanded} ref={(element) => { if (element) element.inert = !expanded; }}>
                  <div className="flow-ontology__schema-collapse-content">
                    <div className="flow-ontology__schema-nav" role="group" aria-label={`${item.label} schemas`}>
                      {schemas.length ? schemas.map((schema) => <button type="button" key={schema} data-schema-filter={schema} data-schema-motion-id={`${item.key}:${schema}`} aria-pressed={group === item.key && source === schema} className={group === item.key && source === schema ? 'is-active' : ''} onClick={() => updateQuery({ group: item.key === 'all' ? null : item.key, source: schema, ...(!selectedTable ? { schema: null } : {}) })}><span className="flow-ontology__schema-label"><DatabaseOutlined /><span title={schema}>{schema}</span></span><small>{number(counts[schema])}</small></button>) : <span className={`flow-ontology__schema-empty${facetsReady ? '' : ' is-pending'}`} aria-busy={!facetsReady} aria-label={facetsReady ? undefined : 'Schema groups not loaded'}>{facetsReady ? 'No schemas' : <span className="flow-ontology__schema-skeleton" aria-hidden="true" />}</span>}
                    </div>
                  </div>
                </div>
              </div>
            </React.Fragment>;
          })}
        </nav>
        {lineageLink}
      </aside>
        <main className="flow-ontology__main" aria-label="Object types" aria-busy={loading}>
          {error && <div className="flow-ontology__notice" role="alert"><span>{error}</span><button type="button" onClick={() => setVersion((value) => value + 1)}>Retry</button></div>}
          {favoriteSummaryError && <div className="flow-ontology__notice" role="alert"><span>{favoriteSummaryError}</span><button type="button" onClick={() => setVersion((value) => value + 1)}>Retry</button></div>}
          {favoriteError && <div className="flow-ontology__notice" role="alert"><span>{favoriteError}</span><button type="button" aria-label="Retry favorites" onClick={() => setVersion((value) => value + 1)}>Retry</button></div>}
          {error && !presentedItems.length ? <div className="flow-ontology__empty"><DatabaseOutlined /><strong>Catalog unavailable</strong><span>Retry to load object types.</span></div> : loading && !hasPresentedCatalog ? <OntologyCatalogSkeleton view={view} label="Loading object types" /> : !presentedItems.length ? <div className="flow-ontology__empty"><DatabaseOutlined /><strong>{presentedGroup === 'favorites' ? 'No favorite object types' : 'No object types found'}</strong><span>{presentedGroup === 'favorites' ? 'Star an object type to keep it here.' : 'Try a different resource group, schema, or search.'}</span></div> : <div className={`flow-ontology__collections is-${view}`} data-testid="ontology-assets">
            {sections.map((section) => <section key={section.key} className="flow-ontology__collection"><header className="flow-ontology__collection-heading"><h2>{section.label}</h2></header>{section.schemas.map(({ schema, items }) => <section key={schema} className="flow-ontology__schema-section" data-schema-group={schema}><h3 className="flow-ontology__schema-heading"><PostgresGlyph className="flow-ontology__database-glyph" /><span className="flow-ontology__schema-tag"><DatabaseOutlined /><span title={schema}>{schema}</span></span></h3><div className={`flow-ontology__assets is-${view}`}>
              {items.map((object) => <article key={object.id} data-testid="ontology-object-card" data-object-id={object.id} data-schema={object.schema} className={`flow-ontology__asset${selectedId === object.id ? ' is-selected' : ''}`} style={accentStyle(object.dataType)}>
                <button ref={(element) => { cardRefs.current[object.id] = element; }} type="button" className="flow-ontology__asset-main" aria-label={`Preview ${object.id}`} aria-pressed={selectedId === object.id} onKeyDown={(event) => moveCardFocus(event, renderedObjects.findIndex((item) => item.id === object.id))} onClick={() => selectObject(object)}>
                  <span className="flow-ontology__asset-icon"><TemplateGlyph kind={inferSiteKind(object.dataType, object.table)} source={object.table} /></span><span className="flow-ontology__asset-name"><span className="flow-ontology__asset-heading"><strong title={object.displayName}>{object.displayName}</strong>{object.visibility !== 'normal' && <small className={`flow-ontology__asset-visibility is-${object.visibility}`} title={visibilityDescriptions[object.visibility]}>{visibilityLabels[object.visibility]}</small>}<small className="flow-ontology__table-count" title={`${object.objectCountEstimated ? 'Estimated object count' : 'Object count'}: ${object.objectCountEstimated ? '~' : ''}${number(object.objectCount)}`}>{object.objectCountEstimated ? '~' : ''}{number(object.objectCount)}</small></span><span className="flow-ontology__asset-description" title={object.description?.trim() || 'No description provided.'}>{object.description?.trim() || 'No description provided.'}</span></span><span className="flow-ontology__preview-action" aria-hidden="true"><InfoCircleOutlined /></span>
                </button><button type="button" className={`flow-ontology__favorite${favorites.includes(object.id) ? ' is-favorite' : ''}`} aria-label={`${favorites.includes(object.id) ? 'Unstar' : 'Star'} ${object.id}`} aria-pressed={favorites.includes(object.id)} aria-busy={favoritePending.includes(`${sessionIdentity}:${object.id}`)} disabled={!favoriteReady || favoriteLoading || favoritePending.includes(`${sessionIdentity}:${object.id}`)} onClick={() => void toggleFavorite(object)}>{favoritePending.includes(`${sessionIdentity}:${object.id}`) ? <ReloadOutlined spin /> : favorites.includes(object.id) ? <StarFilled /> : <StarOutlined />}</button>
              </article>)}
            </div></section>)}</section>)}
          </div>}
          <footer className="flow-ontology__catalog-footer"><span>{loading ? 'Updating catalog…' : error ? 'Catalog unavailable' : `${number(catalog.total)} object types`}</span></footer>
        </main>
        </>}
      </div>
      {publishedCatalog && !publishedId && <footer className="flow-published-nav-footer">{lineageLink}</footer>}
    </div>
    <Drawer open={inspectorOpen} onClose={closeInspector} width="100%" title={`${inspectorId} Object type preview`} closable={false} destroyOnHidden rootClassName={`flow-ontology flow-ontology__drawer${inspectorClosing ? ' is-closing' : ''}`} styles={{ header: { display: 'none' }, body: { display: 'flex', padding: 0, overflow: 'hidden' } }}>
    <aside ref={inspectorRef} className="flow-ontology__inspector" data-testid="ontology-inspector" data-object-id={inspectorId} style={selectedColor}>
      <header className="flow-ontology__inspector-toolbar"><span className="flow-ontology__asset-icon"><TemplateGlyph kind={inferSiteKind(identity?.dataType || '', inspectorTable)} source={inspectorTable} /></span><div className="flow-ontology__preview-identity"><h2 title={inspectorId}>{identity?.displayName || inspectorTable}</h2><span>Object Type</span></div><button type="button" className="flow-ontology__icon-button" aria-label="Close asset preview" onClick={closeInspector}><CloseOutlined /></button></header>
      <div className="flow-ontology__inspector-content" aria-busy={preview.loading}>
        {preview.loading || preview.id !== inspectorId ? <div className="flow-ontology__empty is-compact" role="status"><ReloadOutlined spin /><span>Loading object type</span></div> : preview.error ? <div className="flow-ontology__notice" role="alert"><span>{preview.error}</span><button type="button" onClick={() => setPreviewVersion((value) => value + 1)}>Retry</button>{canManage && !includeHidden && <button type="button" onClick={() => setShowHidden(true)}>Show hidden object types</button>}</div> : data && <>
          <section className="flow-ontology__preview-section"><h3>Description</h3><p className="flow-ontology__muted">{data.description || 'No description provided.'}</p></section>
          <section className="flow-ontology__preview-section"><div className="flow-ontology__visibility"><h3>Visibility</h3><span className={`flow-ontology__visibility-badge is-${data.visibility}`} title={visibilityDescriptions[data.visibility]}><EyeOutlined />{visibilityLabels[data.visibility]}</span></div>
            {canManage && <div className="flow-ontology__visibility-editor"><Select className="flow-ontology__select" popupClassName="flow-ontology__select-popup" aria-label="Object type visibility" value={visibilityDraft} disabled={!!visibilitySaving || !!linkedLoading || inspectorClosing} onChange={(value: EtlObjectVisibility) => { setVisibilityDraft(value); setVisibilityError(''); setVisibilityNotice(''); }} options={(Object.keys(visibilityLabels) as EtlObjectVisibility[]).map((value) => ({ value, label: visibilityLabels[value] }))} /><button type="button" className="flow-ontology__button" aria-label="Save visibility" disabled={!!visibilitySaving || !!linkedLoading || visibilityDraft === data.visibility || inspectorClosing} aria-busy={visibilitySaving === inspectorId} onClick={() => void saveVisibility()}>{visibilitySaving === inspectorId ? <><ReloadOutlined spin />Saving…</> : 'Save'}</button></div>}
            <p className="flow-ontology__visibility-help">{visibilityDescriptions[canManage ? visibilityDraft : data.visibility]} {canManage && visibilityDraft !== data.visibility ? 'Changes take effect after saving. ' : ''}Access permissions are managed separately.</p>
            {visibilityError && <p className="flow-ontology__visibility-feedback is-error" role="alert">{visibilityError}</p>}
            {visibilityNotice && <p className="flow-ontology__visibility-feedback" role="status">{visibilityNotice}</p>}
          </section>
          <section className="flow-ontology__preview-section"><header><h3>Properties <small>({number(data.properties.length)})</small></h3>{data.properties.length > 6 && <button type="button" className="flow-ontology__properties-toggle" aria-expanded={inspectorAllProperties} aria-controls="ontology-properties" onClick={() => updateQuery({ tab: inspectorAllProperties ? null : 'properties' })}>{inspectorAllProperties ? <><RightOutlined rotate={180} /><span>Back</span></> : <><span>View</span><RightOutlined /></>}</button>}</header>
            <div id="ontology-properties" className="flow-ontology__property-list" role="region" aria-label="Properties" tabIndex={inspectorAllProperties && data.properties.length > 6 ? 0 : undefined}>{visibleProperties.map((property) => <div className="flow-ontology__property-row" key={property.name} title={`${property.type}${property.nullable ? ' · Nullable' : ' · Required'}${property.comment ? ` · ${property.comment}` : ''}`}><PropertyTypeIcon type={explorerPropertyType(property.type)} /><span className="flow-ontology__property-name">{property.displayName}</span>{data.titleProperty === property.name && <span className="flow-ontology__property-badge is-title">Title</span>}{property.primaryKey && <span className="flow-ontology__property-badge is-primary">Primary key</span>}</div>)}</div>
            {!data.properties.length && <p className="flow-ontology__muted">No properties defined.</p>}
          </section>
          <section className="flow-ontology__preview-section"><header><h3>Linked object types <small>({number(data.linkedObjectTypes.length)})</small></h3></header><div className="flow-ontology__linked-list">{data.linkedObjectTypes.map((linked) => <Tooltip key={linked.id} placement={mobilePreview ? 'top' : 'left'} overlayClassName="flow-ontology__relation-tooltip" title={`${linked.id} · ${linked.direction} · ${visibilityLabels[linked.visibility]}\n${linked.relationships.map((relation) => `${relation.sourceKind === 'logical' ? 'Logical relationship' : 'Foreign key'}: ${relation.sourceSchema}.${relation.sourceTable} (${relation.columns.join(', ')}) → ${relation.targetSchema}.${relation.targetTable} (${relation.referencedColumns.join(', ')})${relation.cardinality ? ` · ${relation.cardinality.replace(/_/g, ' ')}` : ''}${relation.description ? ` · ${relation.description}` : ''}`).join('\n')}`}><button type="button" className="flow-ontology__linked-row" style={accentStyle(linked.dataType)} aria-busy={linkedLoading === linked.id} disabled={!!visibilitySaving || linkedLoading === linked.id} onClick={() => void selectLinkedObject(linked)}><span className="flow-ontology__linked-icon">{linkedLoading === linked.id ? <ReloadOutlined spin /> : <TemplateGlyph kind={inferSiteKind(linked.dataType, linked.table)} source={linked.table} />}</span><span>{linked.displayName}</span></button></Tooltip>)}</div>{!data.linkedObjectTypes.length && <p className="flow-ontology__muted">No relationships defined.</p>}
            {linkedError && <p className="flow-ontology__visibility-feedback is-error" role="alert">{linkedError}</p>}
          </section>
        </>}
      </div>
      <footer className="flow-ontology__inspector-footer"><button type="button" className="flow-ontology__button flow-ontology__button--primary" disabled={!data || preview.loading || !!linkedLoading} onClick={openWorkspace}>Start exploring <ArrowRightOutlined /></button></footer>
    </aside></Drawer>
    {createOpen && (canManage || semanticProjectAccess) && <SemanticObjectWizard allowedSource={canManage} schemas={creationSchemas} initialSchema={createSchema} onCancel={() => setCreateOpen(false)} onPublished={(object) => { setCreateOpen(false); selectPublished(object.id); }} />}
  </div>;
}
