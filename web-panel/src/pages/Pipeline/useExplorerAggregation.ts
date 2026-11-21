import { useEffect, useRef, useState } from 'react';
import { ontologyError } from '../../services/ontology';
import { getSessionState } from '../../services/session';
import type { ExplorerExploreAggregation } from './ExplorerExploreView';

export type ExplorerAggregationLoader = (field: string, offset: number, signal: AbortSignal) => Promise<{
  count: number; groups: { value: unknown; count: number; sum?: number | string | null }[]; groupsHasMore: boolean;
}>;
type Read = { key: string; count?: number; groups: Awaited<ReturnType<ExplorerAggregationLoader>>['groups']; hasMore: boolean; loading: boolean; error?: string; limitReached?: boolean };

export default function useExplorerAggregation(loader: ExplorerAggregationLoader | undefined, requestKey: string, field: string, enabled: boolean): ExplorerExploreAggregation | undefined {
  const load = useRef(loader); load.current = loader;
  const identity = getSessionState().identity, key = JSON.stringify([requestKey, field, identity]);
  const [cursor, setCursor] = useState({ key, offset: 0, retry: 0 });
  const { offset, retry } = cursor.key === key ? cursor : { offset: 0, retry: 0 };
  const [read, setRead] = useState<Read>();
  const pending = useRef(false), provided = !!loader;
  const current = read?.key === key ? read : undefined;
  useEffect(() => {
    setCursor(previous => previous.key === key ? previous : { key, offset: 0, retry: 0 });
    if (!provided || !enabled || !field) {
      setRead(undefined);
      setCursor(previous => previous.offset ? { ...previous, offset: 0 } : previous);
      return;
    }
    const controller = new AbortController();
    const active = () => {
      const session = getSessionState();
      return !controller.signal.aborted && session.authenticated && !session.locked && session.identity === identity;
    };
    pending.current = true;
    setRead(previous => ({ key, groups: offset && previous?.key === key ? previous.groups : [],
      count: offset && previous?.key === key ? previous.count : undefined,
      hasMore: offset && previous?.key === key ? previous.hasMore : false, loading: true }));
    load.current!(field, offset, controller.signal).then(result => {
      if (!active()) return;
      setRead(previous => ({ key, count: result.count,
        groups: [...(offset && previous?.key === key ? previous.groups : []), ...result.groups],
        hasMore: result.groupsHasMore, loading: false }));
    }, error => {
      if (active()) setRead(previous => ({ key, groups: previous?.key === key ? previous.groups : [],
        count: previous?.key === key ? previous.count : undefined, hasMore: previous?.key === key && previous.hasMore || false,
        loading: false, error: ontologyError(error, 'Unable to analyze this property.') }));
    }).finally(() => { if (active()) pending.current = false; });
    return () => { controller.abort(); pending.current = false; };
  }, [key, field, identity, offset, retry, enabled, provided]);
  if (!provided) return undefined;
  return { count: enabled && field ? current?.count : undefined, groups: enabled && field ? current?.groups ?? [] : [],
    hasMore: enabled && field ? current?.hasMore ?? false : false, loading: !!(enabled && field && (!current || current.loading)),
    error: enabled && field ? current?.error : undefined,
    limitReached: enabled && field ? current?.limitReached : undefined,
    onLoadMore: () => {
      if (!enabled || !field || !current?.hasMore || current.loading || pending.current || current.error) return;
      if (current.groups.length > 100_000) {
        setRead({ ...current, limitReached: true, error: 'The inspection limit of 100,000 value offsets has been reached. Narrow the object set to continue.' });
        return;
      }
      pending.current = true;
      setCursor({ key, offset: current.groups.length, retry });
    },
    onRetry: () => {
      if (!enabled || !field || pending.current) return;
      pending.current = true;
      setRead({ key, groups: [], hasMore: false, loading: true });
      setCursor({ key, offset: 0, retry: retry + 1 });
    },
  };
}
