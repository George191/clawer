import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import { Modal } from '@/components/ui';
import { DownloadOutlined, EyeOutlined } from '@/components/ui/icons';
import { fetchExplorerFile, fetchExplorerStorage, type ExplorerStorage } from '../../services/api';
import { JsonTreeValue } from './ExplorerJsonValue';
import './explorer-value.css';

const Storage = createContext<{ schema: string; table: string; config?: ExplorerStorage }>({ schema: '', table: '' });
export function ExplorerValueProvider({ schema, table, children }: { schema: string; table: string; children: ReactNode }) {
  const [config, setConfig] = useState<ExplorerStorage>();
  const [error, setError] = useState(false);
  useEffect(() => {
    const request = new AbortController();
    fetchExplorerStorage(request.signal).then(setConfig, () => { if (!request.signal.aborted) setError(true); });
    return () => request.abort();
  }, []);
  return <Storage.Provider value={{ schema, table, config }}>{error && <p role="alert">MinIO file links are unavailable. Reload to retry.</p>}{children}</Storage.Provider>;
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
    if (!value.startsWith('/' + config.bucket + '/')) return;
    path = value.slice(config.bucket.length + 1);
  }
  if (!path || path.endsWith('/')) return;
  let name: string;
  try { name = decodeURIComponent(path.split('/').pop()!); } catch { return; }
  return { name, href: config.baseUrl + '/' + config.bucket + path };
}

export function ExplorerValue({ value, text, identity = {}, field, compact = false }: {
  value: unknown; text?: string; identity?: Record<string, unknown>; field?: string | null; compact?: boolean;
}) {
  const { schema, table, config } = useContext(Storage);
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  const [preview, setPreview] = useState<string>();
  const request = useRef<AbortController>();
  const recordKey = JSON.stringify(identity);
  useEffect(() => {
    setPreview(undefined); setError(''); setBusy(false);
    return () => request.current?.abort();
  }, [value, field, schema, table, recordKey]);
  const file = typeof value === 'string' ? fileUrl(value, config) : undefined;
  const extension = file?.name.split('.').pop()?.toLowerCase();
  const kind = /^(png|jpe?g|gif|webp|avif|bmp|svg|ico)$/.test(extension || '') ? 'image'
    : /^(mp4|webm|ogv|mov|m4v)$/.test(extension || '') ? 'video'
      : /^(mp3|wav|m4a|ogg|flac)$/.test(extension || '') ? 'audio' : extension === 'pdf' ? 'pdf' : undefined;
  async function openFile(download: boolean, inline = false) {
    if (busy || !file || !field) return;
    const tab = download || inline ? null : window.open('about:blank', '_blank');
    if (!download && !inline && !tab) { setError('Allow a new tab to open this file.'); return; }
    if (tab) tab.opener = null;
    const controller = new AbortController();
    request.current = controller;
    setBusy(true); setError('');
    try {
      const result = await fetchExplorerFile(schema, table, identity, field, download, controller.signal);
      if (controller.signal.aborted) { tab?.close(); return; }
      if (inline) setPreview(result.url);
      else if (tab) tab.location.replace(result.url);
      else { const anchor = document.createElement('a'); anchor.href = result.url; anchor.download = file.name; anchor.click(); }
    } catch { tab?.close(); if (!controller.signal.aborted) setError('Unable to open this file. Try again.'); }
    finally { if (!controller.signal.aborted) setBusy(false); }
  }
  if (file) return <span className="explorer-file" onClick={event => event.stopPropagation()}>
    <a href={file.href} target="_blank" rel="noopener noreferrer" title={file.name} onClick={event => {
      if (field && Object.keys(identity).length) { event.preventDefault(); void openFile(false, Boolean(kind)); }
    }}>{file.name}</a>
    <svg className="explorer-s3-icon" role="img" aria-label="S3" viewBox="0 0 24 24"><path d="M4 5h16l-2 15H6L4 5Z" fill="currentColor" opacity=".2"/><ellipse cx="12" cy="5" rx="8" ry="3" fill="none" stroke="currentColor"/><path d="m4 5 2 15c4 2 8 2 12 0l2-15" fill="none" stroke="currentColor"/><text x="6" y="16" fontSize="8" fill="currentColor">S3</text></svg>
    {kind && <button type="button" aria-label={'Preview ' + file.name} title="Preview file" disabled={busy || !field || !Object.keys(identity).length} onClick={() => void openFile(false, true)}><EyeOutlined /></button>}
    <button type="button" aria-label={'Download ' + file.name} title={Object.keys(identity).length ? 'Download file' : 'A primary key is required to download this file'} disabled={busy || !field || !Object.keys(identity).length} onClick={() => void openFile(true)}><DownloadOutlined /></button>{error && <span role="alert">{error}</span>}
    {preview && <Modal open centered width={960} title={file.name} footer={null} onCancel={() => setPreview(undefined)} className="flow-surface-modal explorer-file-preview">
      {kind === 'image' ? <img src={preview} alt={file.name} onError={() => { setPreview(undefined); setError('Image preview failed.'); }} />
        : kind === 'video' ? <video src={preview} controls playsInline preload="metadata" aria-label={file.name} onError={() => { setPreview(undefined); setError('Video preview failed.'); }} />
          : kind === 'audio' ? <audio src={preview} controls preload="metadata" aria-label={file.name} onError={() => { setPreview(undefined); setError('Audio preview failed.'); }} />
            : <iframe src={preview} title={'PDF preview: ' + file.name} referrerPolicy="no-referrer" />}
    </Modal>}
  </span>;
  let parsed: unknown = value;
  if (typeof value === 'string' && /^[\s]*[\[{]/.test(value)) {
    try { parsed = JSON.parse(value); } catch { /* Invalid JSON remains its original text. */ }
  }
  if (parsed !== null && typeof parsed === 'object') return <div className={`explorer-json${compact ? ' is-compact' : ''}`} onClick={event => { if ((event.target as Element).closest('button')) event.stopPropagation(); }}>
    <JsonTreeValue name="" value={parsed} depth={0} comma={false} initiallyOpen={!compact} />
  </div>;
  if (typeof value === 'string' && /<\/?(?:html|body|head|div|p|span|a|img|table|ul|ol|li|section|article|h[1-6]|br|strong|em)\b[^>]*>/i.test(value)) {
    const html = '<meta http-equiv="Content-Security-Policy" content="default-src &#39;none&#39;; style-src &#39;unsafe-inline&#39;; img-src data:"><style>body{margin:4px;font:11px/1.45 system-ui;overflow-wrap:anywhere}img{max-width:100%}</style>' + value;
    return <iframe className={`explorer-html${compact ? ' is-compact' : ''}`} title="HTML content" sandbox="" referrerPolicy="no-referrer" srcDoc={html} />;
  }
  return <span className="flow-explorer-selection__property-value">{text ?? (value == null ? 'null' : String(value))}</span>;
}
