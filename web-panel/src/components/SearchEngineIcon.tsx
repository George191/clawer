import React, { useEffect, useState } from 'react';
import { SearchOutlined } from '@ant-design/icons';
import { fetchWorkspaceTemplates, type SearchEngine } from '@/services/aiApi';

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

type SearchEngineIcons = Partial<Record<SearchEngine, string>>;
let iconRequest: Promise<SearchEngineIcons> | undefined;

const loadSearchEngineIcons = () => {
  if (!iconRequest) {
    iconRequest = fetchWorkspaceTemplates(false).then((templates) => {
      const icons: SearchEngineIcons = {};
      for (const engine of Object.keys(searchEngineLabels) as SearchEngine[]) {
        const template = templates.find((item) => item.name === `${engine}_search` && item.status === 'active' && item.favicon_url);
        if (template?.favicon_url) icons[engine] = template.favicon_url;
      }
      return icons;
    }).catch((error) => {
      iconRequest = undefined;
      throw error;
    });
  }
  return iconRequest;
};

const SearchEngineIcon: React.FC<{
  engine: SearchEngine;
  style?: React.CSSProperties;
}> = ({ engine, style }) => {
  const [icons, setIcons] = useState<SearchEngineIcons>({});
  useEffect(() => {
    let active = true;
    void loadSearchEngineIcons().then((value) => {
      if (active) setIcons(value);
    }).catch(() => {});
    return () => { active = false; };
  }, []);

  return icons[engine] ? (
    <img
      src={icons[engine]}
      alt=""
      aria-hidden="true"
      style={{ width: 14, height: 14, objectFit: 'contain', verticalAlign: '-0.125em', ...style }}
    />
  ) : <SearchOutlined aria-hidden="true" style={{ fontSize: 14, ...style }} />;
};

export default SearchEngineIcon;
