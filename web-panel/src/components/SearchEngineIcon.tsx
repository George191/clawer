import React from 'react';
import type { SearchEngine } from '@/services/aiApi';

export const searchEngineLabels: Record<SearchEngine, string> = {
  duckduckgo: 'DuckDuckGo',
  google: 'Google',
  bing: 'Bing',
  brave: 'Brave',
};

export const inferSearchEngine = (source: string): SearchEngine | null => {
  const normalized = source.replace(/@.*$/, '').trim().toLowerCase();
  if (/(^|_)duckduckgo_search(?:_|$)/.test(normalized)) return 'duckduckgo';
  if (/(^|_)google_search(?:_|$)/.test(normalized)) return 'google';
  if (/(^|_)bing_search(?:_|$)/.test(normalized)) return 'bing';
  if (/(^|_)brave_search(?:_|$)/.test(normalized)) return 'brave';
  return null;
};

const SearchEngineIcon: React.FC<{
  engine: SearchEngine;
  style?: React.CSSProperties;
}> = ({ engine, style }) => (
  <img
    src={`/search/${engine}.svg`}
    alt=""
    aria-hidden="true"
    style={{ width: 14, height: 14, objectFit: 'contain', verticalAlign: '-0.125em', ...style }}
  />
);

export default SearchEngineIcon;
