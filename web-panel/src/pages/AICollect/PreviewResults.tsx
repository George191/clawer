import { useEffect, useRef, useState } from 'react';
import { CopyOutlined } from '@/components/ui/icons';

// Product preview fixtures, never fetched, executed, or published.
export const previewTemplate = `# Example template · product preview
name: example_articles
version: "1.0"
description: Collect article titles and links
source:
  url: https://example.org/articles
  method: GET
pagination:
  parameter: page
  start: 1
  max_pages: 20
fields:
  - name: title
    type: string
    required: true
    selector: article h2
  - name: url
    type: string
    required: true
    selector: article a
    attribute: href
adapter: adapter.py
`;

export const previewAdapter = `"""Example adapter · product preview; not executed."""

from urllib.parse import urljoin


def normalize_article(item, base_url):
    title = (item.get("title") or "").strip()
    href = (item.get("href") or "").strip()
    if not title or not href:
        raise ValueError("Article title and link are required")
    return {"title": title, "url": urljoin(base_url, href)}


def adapt_page(items, base_url):
    seen = set()
    for item in items:
        record = normalize_article(item, base_url)
        if record["url"] in seen:
            continue
        seen.add(record["url"])
        yield record
`;

const sampleRows = [
  { title: 'Designing reliable collection workflows', url: 'https://example.org/articles/workflows' },
  { title: 'A guide to structured article data', url: 'https://example.org/articles/structured-data' },
];
type ResultTab = 'source' | 'template' | 'adapter' | 'data';
type PreviewResult = { id: number; prompt: string; step: number; template: string; adapter: string; complete: boolean };

export default function PreviewResults({ turn, running, onNotice }: { turn?: PreviewResult; running: boolean; onNotice: (notice: string) => void }) {
  const [tab, setTab] = useState<ResultTab>('source');
  const code = useRef<HTMLDivElement>(null);
  const follow = useRef(true);
  useEffect(() => { setTab(!turn || turn.step < 2 ? 'source' : turn.step === 2 ? 'template' : 'adapter'); follow.current = true; }, [turn?.id, turn?.step]);
  const text = tab === 'template' ? turn?.template ?? '' : tab === 'adapter' ? turn?.adapter ?? '' : JSON.stringify(sampleRows, null, 2);
  const filename = tab === 'template' ? 'template.yaml' : tab === 'adapter' ? 'adapter.py' : 'sample.json';
  const writing = running && ((tab === 'template' && turn?.step === 2) || (tab === 'adapter' && turn?.step === 3));
  useEffect(() => { if (follow.current && writing) code.current?.scrollTo({ top: code.current.scrollHeight }); }, [text, writing]);
  const available = tab === 'source' || Boolean(turn && (tab === 'data' ? turn.complete : text));
  async function copy() {
    try { await navigator.clipboard.writeText(text); onNotice(`${filename} copied`); }
    catch { onNotice('Clipboard is unavailable. Select the file content to copy it.'); }
  }
  function download() {
    const url = URL.createObjectURL(new Blob([text], { type: 'text/plain;charset=utf-8' }));
    const link = document.createElement('a'); link.href = url; link.download = filename; link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  const tabs: Array<{ key: ResultTab; label: string }> = [{ key: 'source', label: 'Webpage' }, { key: 'template', label: 'template.yaml' }, { key: 'adapter', label: 'adapter.py' }, { key: 'data', label: 'Sample data' }];
  return <>
    <header className="scout-results-header"><h2>Collection results</h2><span className="scout-preview-badge">Example</span></header>
    <div className="scout-result-tabs" role="tablist" aria-label="Collection artifacts">{tabs.map((item) => <button key={item.key} id={`scout-result-tab-${item.key}`} type="button" role="tab" aria-selected={tab === item.key} aria-controls="scout-result-content" tabIndex={tab === item.key ? 0 : -1} onClick={() => { setTab(item.key); follow.current = true; }} onKeyDown={(event) => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      const index = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (tabs.findIndex((item) => item.key === tab) + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
      setTab(tabs[index].key); document.getElementById(`scout-result-tab-${tabs[index].key}`)?.focus();
    }}>{item.label}</button>)}</div>
    <div className="scout-result-content" id="scout-result-content" role="tabpanel" aria-labelledby={`scout-result-tab-${tab}`}>
      {!available ? <div className="scout-result-empty"><strong>{tab === 'data' ? 'Sample output will appear here' : `${filename} will appear here`}</strong><p>Follow the steps in the conversation as the example takes shape.</p></div>
        : tab === 'source' ? <div className="scout-source-preview"><div className="scout-source-address">https://example.org/articles <span>Example page</span></div><div className="scout-source-page"><small>THE COLLECTION JOURNAL</small><h3>Ideas worth collecting.</h3><p>A sample article page for the collection design preview.</p>{sampleRows.map((row, index) => <article key={row.url}><span>0{index + 1} · ARTICLE</span><h4>{row.title}</h4><p>Title and article link are the example fields.</p></article>)}</div></div>
          : <><div className="scout-result-toolbar"><span>{filename}</span><span className="scout-result-file-status">{writing ? 'Writing…' : 'Example draft'}</span><button type="button" aria-label={`Copy ${filename}`} title="Copy file" onClick={() => void copy()}><CopyOutlined /></button><button type="button" aria-label={`Download ${filename}`} title="Download file" onClick={download}><svg viewBox="0 0 24 24"><path d="M12 3v12m-4-4 4 4 4-4M4 16v4h16v-4" /></svg></button></div>
            {tab === 'data' ? <div className="scout-result-data"><p>Example records · Not collected from a live website</p><table><thead><tr><th>title</th><th>url</th></tr></thead><tbody>{sampleRows.map((row) => <tr key={row.url}><td>{row.title}</td><td>{row.url}</td></tr>)}</tbody></table><pre>{text}</pre></div>
              : <div ref={code} className="scout-result-code" onScroll={() => { const node = code.current; if (node) follow.current = node.scrollHeight - node.scrollTop - node.clientHeight < 60; }}><pre>{text.split('\n').map((line, index) => <div className="scout-result-code-line" key={index}><span aria-hidden="true">{index + 1}</span><code>{line || ' '}{writing && index === text.split('\n').length - 1 && <i className="scout-result-caret" />}</code></div>)}</pre></div>}
          </>}
    </div>
  </>;
}
