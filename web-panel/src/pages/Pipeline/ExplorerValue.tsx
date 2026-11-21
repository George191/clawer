import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import { theme } from '@/components/ui';
import { fetchExplorerHtml, fetchExplorerFile, fetchExplorerStorage, type ExplorerStorage } from '../../services/api';
import { apiUrl } from '../../services/endpoints';
import { JsonTreeValue } from './ExplorerJsonValue';
import './explorer-value.css';
import scrollbarStyles from '../../styles/scrollbars.css?raw';
import { useThemeStore } from '../../stores/settings';
import { fetchPublishedFile, fetchPublishedHtml, fetchPublishedStorage, type ExplorerDetailSource } from '../../services/publishedExplorerDetails';
import { getSessionState } from '../../services/session';
import useExplorerErrorToast from './useExplorerErrorToast';

const live = (signal: AbortSignal, identity: string) => {
  const session = getSessionState();
  return !signal.aborted && session.authenticated && !session.locked && session.identity === identity;
};
const Storage = createContext<{ source: ExplorerDetailSource; config?: ExplorerStorage; failed?: boolean }>({ source: { kind: 'backed', schema: '', table: '' } });
export function ExplorerValueProvider({ schema = '', table = '', source, children }: { schema?: string; table?: string; source?: ExplorerDetailSource; children: ReactNode }) {
  const target = source ?? { kind: 'backed' as const, schema, table };
  const targetKey = JSON.stringify(target);
  const [config, setConfig] = useState<ExplorerStorage>();
  const [error, setError] = useState(false);
  const [retry, reload] = useState(0);
  const identity = getSessionState().identity;
  useEffect(() => {
    const request = new AbortController();
    setConfig(undefined); setError(false);
    const load = target.kind === 'published' ? fetchPublishedStorage(target.definitionId, request.signal) : fetchExplorerStorage(request.signal);
    load.then(value => { if (live(request.signal, identity)) setConfig(value); }, error => { if (live(request.signal, identity) && error?.code !== 'ERR_CANCELED') setError(true); });
    return () => request.abort();
  }, [targetKey, identity, retry]);
  useExplorerErrorToast(error ? 'MinIO file links are unavailable.' : undefined, 'File links unavailable', () => reload(value => value + 1));
  return <Storage.Provider value={{ source: target, config, failed: error }}>{children}</Storage.Provider>;
}

function fileUrl(value: string, config?: ExplorerStorage) {
  if (!config?.bucket) return;
  let path: string;
  try {
    const url = new URL(value);
    if (['s3:', 'minio:'].includes(url.protocol) && url.host === config.bucket) path = url.pathname;
    else if (['http:', 'https:'].includes(url.protocol) && !url.username && !url.password &&
      config.origins.some(origin => new URL(origin).host === url.host) && url.pathname.startsWith('/' + config.bucket + '/')) {
      path = url.pathname.slice(config.bucket.length + 1);
    } else return;
  } catch {
    if (/^[a-z][a-z0-9_]*\/[a-z][a-z0-9_]*\/[^?#]+$/.test(value)) path = '/' + value;
    else if (value.startsWith('/' + config.bucket + '/')) path = value.slice(config.bucket.length + 1);
    else return;
  }
  if (!path || path.endsWith('/')) return;
  let name: string;
  try { name = decodeURIComponent(path.split('/').pop()!); } catch { return; }
  return { name, href: config.baseUrl + '/' + config.bucket + path };
}

function HtmlPreview({ value, identity, field }: { value: string; identity: Record<string, unknown>; field?: string | null }) {
  const { source } = useContext(Storage);
  const sourceKey = JSON.stringify(source);
  const { token } = theme.useToken();
  const dark = useThemeStore(state => state.mode === 'dark');
  const [html, setHtml] = useState<string>(), [error, setError] = useState(false);
  const [retry, reload] = useState(0);
  const sessionIdentity = getSessionState().identity;
  const key = JSON.stringify(identity);
  const invalid = !field || !Object.keys(identity).length;
  useEffect(() => {
    const controller = new AbortController();
    setHtml(undefined); setError(false);
    if (invalid) { setError(true); return; }
    const read = source.kind === 'published' ? fetchPublishedHtml(source.definitionId, identity, field, controller.signal) : fetchExplorerHtml(source.schema, source.table, identity, field, controller.signal);
    read.then(result => {
      if (live(controller.signal, sessionIdentity)) setHtml(result.html);
    }, error => { if (live(controller.signal, sessionIdentity) && error?.code !== 'ERR_CANCELED') setError(true); });
    return () => controller.abort();
  }, [sourceKey, key, field, value, sessionIdentity, retry]);
  useExplorerErrorToast(error && !invalid ? 'HTML preview failed.' : undefined, 'HTML preview unavailable', () => reload(value => value + 1));
  if (error) return invalid ? <div className="explorer-html explorer-html--status" role="alert">HTML preview failed. Reload to retry.</div> : null;
  if (html === undefined) return <div className="explorer-html explorer-html--status" role="status">Loading HTML preview…</div>;
  const scrollbarTheme = `:root{--ui-muted:${token.colorTextSecondary};--ui-panel:${token.colorBgContainer};color-scheme:${dark ? 'dark' : 'light'}}`;
  const style = `body{margin:16px;font:13px/1.65 system-ui;overflow-wrap:anywhere;color:${dark ? '#d8dee9' : '#263244'};background:${dark ? '#242936' : '#fff'}}img,video{display:block;max-width:100%;height:auto;margin:12px auto}audio{width:100%}a{color:${dark ? '#80bfff' : '#176bb5'}}table{border-collapse:collapse;max-width:100%}td,th{border:1px solid #687080;padding:6px}pre{white-space:pre-wrap}blockquote{margin:12px 0;padding-left:16px;border-left:3px solid #687080}`;
  const doc = '<!doctype html><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src &#39;none&#39;; style-src &#39;unsafe-inline&#39;; img-src https: http: data:; media-src https: http:; base-uri &#39;none&#39;; form-action &#39;none&#39;"><style>' + style + scrollbarTheme + scrollbarStyles + '</style>' + html;
  return <iframe className="explorer-html" title="HTML content" sandbox="allow-popups allow-popups-to-escape-sandbox allow-downloads" referrerPolicy="no-referrer" srcDoc={doc} />;
}

export function ExplorerValue({ value, text, identity = {}, field, presentation = 'value', path = [] }: {
  value: unknown; text?: string; identity?: Record<string, unknown>; field?: string | null;
  presentation?: 'value' | 'media'; path?: (string | number)[];
}) {
  const { source: target, config, failed } = useContext(Storage);
  const sourceKey = JSON.stringify(target);
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  const [requestError, setRequestError] = useState(''), [retry, reload] = useState(0);
  const sessionIdentity = getSessionState().identity;
  const [preview, setPreview] = useState<string>();
  const request = useRef<AbortController>();
  const recordKey = JSON.stringify(identity);
  let parsed: unknown = value;
  if (typeof value === 'string' && /^[\s]*[\[{]/.test(value)) {
    try { parsed = JSON.parse(value); } catch { /* Invalid JSON remains its original text. */ }
  }
  const media = presentation === 'media';
  const ref = media && parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed as Record<string, unknown> : undefined;
  const source = ref ? ref.url : value;
  const valuePath = ref ? [...path, 'url'] : path;
  const pathKey = JSON.stringify(valuePath);
  const local = typeof source === 'string' ? fileUrl(source, config) : undefined;
  let external: { href: string; name: string } | undefined;
  if (media && !local && typeof source === 'string') {
    try {
      if (!/^(https?:\/\/|\/(?!\/))/.test(source)) throw new Error();
      const url = new URL(apiUrl(source));
      if (url.username || url.password || url.protocol !== 'https:' && url.origin !== location.origin) throw new Error();
      external = { href: url.href, name: decodeURIComponent(url.pathname.split('/').pop() || 'Media resource') };
    } catch { /* Unrecognized sources are reported below. */ }
  }
  const file = local || external;
  const extension = file?.name.split('.').pop()?.toLowerCase();
  const mime = (typeof ref?.mimeType === 'string' ? ref.mimeType : typeof ref?.mime_type === 'string' ? ref.mime_type : '').toLowerCase();
  const kind = !media ? undefined : mime.startsWith('image/') || /^(png|jpe?g|gif|webp|avif|bmp|svg|ico)$/.test(extension || '') ? 'image'
    : mime.startsWith('video/') || /^(mp4|webm|ogv|mov|m4v)$/.test(extension || '') ? 'video'
      : mime.startsWith('audio/') || /^(mp3|wav|m4a|ogg|flac)$/.test(extension || '') ? 'audio' : mime === 'application/pdf' || extension === 'pdf' ? 'pdf'
        : mime === 'text/html' || /^(html?|xhtml)$/.test(extension || '') ? 'html' : undefined;
  useEffect(() => {
    setPreview(undefined); setError(''); setRequestError(''); setBusy(false);
    if (kind && config) {
      if (external) setPreview(external.href);
      else if (!field || !Object.keys(identity).length) setError('A primary key is required to preview this file.');
      else {
        const controller = new AbortController();
        request.current = controller; setBusy(true);
        const read = target.kind === 'published' ? fetchPublishedFile(target.definitionId, identity, field, false, controller.signal, valuePath) : fetchExplorerFile(target.schema, target.table, identity, field, false, controller.signal, valuePath);
        read.then(result => {
          if (live(controller.signal, sessionIdentity)) setPreview(result.url);
        }, error => { if (live(controller.signal, sessionIdentity) && error?.code !== 'ERR_CANCELED') setRequestError('Unable to preview this file.'); })
          .finally(() => { if (live(controller.signal, sessionIdentity)) setBusy(false); });
      }
    }
    return () => request.current?.abort();
  }, [value, field, sourceKey, recordKey, kind, file?.href, pathKey, media, !!config, !!local, sessionIdentity, retry]);
  useExplorerErrorToast(requestError, 'File preview unavailable', kind ? () => reload(value => value + 1) : undefined);
  async function openFile() {
    if (busy || !file || !field) return;
    const tab = window.open('about:blank', '_blank');
    if (!tab) { setError('Allow a new tab to open this file.'); return; }
    tab.opener = null;
    const controller = new AbortController();
    request.current = controller;
    setBusy(true); setError(''); setRequestError('');
    try {
      const result = target.kind === 'published' ? await fetchPublishedFile(target.definitionId, identity, field, false, controller.signal, valuePath) : await fetchExplorerFile(target.schema, target.table, identity, field, false, controller.signal, valuePath);
      if (!live(controller.signal, sessionIdentity)) { tab.close(); return; }
      tab.location.replace(result.url);
    } catch (error) { tab.close(); if (live(controller.signal, sessionIdentity) && (error as { code?: string })?.code !== 'ERR_CANCELED') setRequestError('Unable to open this file. Try again.'); }
    finally { if (live(controller.signal, sessionIdentity)) setBusy(false); }
  }
  if (media && !config) return failed ? null : <span role="status">Loading media configuration…</span>;
  if (media && Array.isArray(parsed)) return <div className="explorer-media-list">{parsed.length ? parsed.map((item, index) =>
    <ExplorerValue key={index} value={item} identity={identity} field={field} presentation="media" path={[...path, index]} />) : <span>No media resources.</span>}</div>;
  if (media && typeof value === 'string' && (field?.endsWith('_html') || /<\/?(?:html|body|head|div|p|span|a|img|table|ul|ol|li|section|article|h[1-6]|br|strong|em)\b[^>]*>/i.test(value))) {
    return path.length ? <span role="alert">HTML preview requires a scalar property.</span> : <HtmlPreview value={value} identity={identity} field={field} />;
  }
  if (file) return <span className={`explorer-file${kind ? ' explorer-file--media' : ''}`} onClick={event => event.stopPropagation()}>
    <a href={preview || file.href} target="_blank" rel="noopener noreferrer" title={file.name} onClick={event => {
      if (kind) { if (!preview) event.preventDefault(); }
      else if (local && field && Object.keys(identity).length) { event.preventDefault(); void openFile(); }
    }}>{file.name}</a>
    {local && <img className="explorer-minio-icon" src="/minio-logo.svg" alt="MinIO" title="MinIO" />}
    {media && !kind && <span role="status">This file type has no inline preview.</span>}
    {!kind && error && <span role="alert">{error}</span>}
    {kind && <span className={`explorer-file-media explorer-file-media--${kind}`} onContextMenu={event => event.stopPropagation()}>
      {busy ? <span role="status">Loading file preview…</span> : error ? <span role="alert">{error}</span> : requestError || !preview ? null
        : kind === 'image' ? <img src={preview} alt={file.name} loading="lazy" referrerPolicy="no-referrer" onError={() => { setPreview(undefined); setRequestError('Image preview failed.'); }} />
        : kind === 'video' ? <video src={preview} controls playsInline preload="metadata" aria-label={file.name} onError={() => { setPreview(undefined); setRequestError('Video preview failed.'); }} />
          : kind === 'audio' ? <audio src={preview} controls preload="metadata" aria-label={file.name} onError={() => { setPreview(undefined); setRequestError('Audio preview failed.'); }} />
            : <iframe src={preview} title={(kind === 'pdf' ? 'PDF' : 'HTML file') + ' preview: ' + file.name} sandbox={kind === 'html' ? 'allow-popups allow-downloads' : undefined} referrerPolicy="no-referrer" />}
    </span>}
  </span>;
  if (media) return <span role="alert">Expected a media URL, a resource object with a URL, or a resource list.</span>;
  if (parsed !== null && typeof parsed === 'object') return <div className="explorer-json" onClick={event => { if ((event.target as Element).closest('button')) event.stopPropagation(); }}>
    <JsonTreeValue name="" value={parsed} depth={0} comma={false} initiallyOpen />
  </div>;
  return <span className="flow-explorer-selection__property-value">{text ?? (value == null ? 'null' : String(value))}</span>;
}
