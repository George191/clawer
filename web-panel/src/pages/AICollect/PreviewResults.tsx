import { useEffect, useRef } from 'react';
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
type PreviewResult = { id: number; prompt: string; step: number; template: string; adapter: string; complete: boolean };

export default function PreviewResults({ turn, step = turn?.step ?? 0, running, onNotice }: { turn?: PreviewResult; step?: number; running: boolean; onNotice: (notice: string) => void }) {
  const phase = step < 2 ? 'source' : step === 2 ? 'template' : 'adapter';
  const code = useRef<HTMLDivElement>(null);
  const follow = useRef(true);
  const text = phase === 'template' ? turn?.template ?? '' : turn?.adapter ?? '';
  const filename = phase === 'template' ? 'template.yaml' : 'adapter.py';
  const writing = running && step === turn?.step && (step === 2 || step === 3);
  useEffect(() => { follow.current = true; }, [turn?.id, phase]);
  useEffect(() => { if (follow.current && writing) code.current?.scrollTo({ top: code.current.scrollHeight }); }, [text, writing]);
  async function copy() {
    try { await navigator.clipboard.writeText(text); onNotice(`${filename} copied`); }
    catch { onNotice('Clipboard is unavailable. Select the file content to copy it.'); }
  }
  function download() {
    const url = URL.createObjectURL(new Blob([text], { type: 'text/plain;charset=utf-8' }));
    const link = document.createElement('a'); link.href = url; link.download = filename; link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return <>
    <header className="scout-results-header"><h2>{phase === 'source' ? 'Webpage analysis' : phase === 'template' ? 'Collection template' : 'Collection adapter'}</h2><span className="scout-preview-badge">Example</span></header>
    <div className="scout-result-content" data-phase={phase}>
      {phase === 'source' ? <div className="scout-source-preview"><div className="scout-source-address">https://example.org/articles <span>Example page</span></div><div className="scout-source-page"><small>THE COLLECTION JOURNAL</small><h3>Ideas worth collecting.</h3><p>A sample article page for the collection design preview.</p>{sampleRows.map((row, index) => <article key={row.url}><span>0{index + 1} · ARTICLE</span><h4>{row.title}</h4><p>Title and article link are the example fields.</p></article>)}</div></div>
        : <><div className="scout-result-toolbar"><span>{filename}</span><span className="scout-result-file-status">Example draft</span><button type="button" aria-label={`Copy ${filename}`} title="Copy file" disabled={!text} onClick={() => void copy()}><CopyOutlined /></button><button type="button" aria-label={`Download ${filename}`} title="Download file" disabled={!text} onClick={download}><svg viewBox="0 0 24 24"><path d="M12 3v12m-4-4 4 4 4-4M4 16v4h16v-4" /></svg></button></div>
          <div ref={code} className={`scout-result-code ${phase === 'template' ? 'scout-template-sheet' : ''}`} onScroll={() => { const node = code.current; if (node) follow.current = node.scrollHeight - node.scrollTop - node.clientHeight < 60; }}>
            {phase === 'template' ? <section className="ai-template-stage-section"><div className="ai-template-stage-head"><span className="ai-template-stage-title">Collection contract</span><small>Source, pagination, and fields</small></div><div className="ai-template-stage-body">{text.split('\n').map((line, index) => {
              if (!line.trim() || line.trim().startsWith('#')) return null;
              const field = line.match(/^(\s*)(- )?([^:]+):\s*(.*)$/);
              return <div key={index} className={`ai-template-field scout-template-row ${field && !field[4] ? 'is-group' : ''}`} style={{ paddingLeft: field ? `${field[1].length * 8}px` : undefined }}><div className="ai-template-field-key"><span>{field ? `${field[2] ?? ''}${field[3]}` : line}</span></div>{field?.[4] && <div className="ai-template-field-value"><pre>{field[4]}</pre></div>}{writing && index === text.trimEnd().split('\n').length - 1 && <i className="scout-result-caret" />}</div>;
            })}</div></section>
              : <pre>{text.split('\n').map((line, index) => <div className="scout-result-code-line ai-session-inspector-editor-line" key={index}><span aria-hidden="true">{index + 1}</span><span className="scout-code-prefix" aria-hidden="true">+</span><code>{line || ' '}{writing && index === text.split('\n').length - 1 && <i className="scout-result-caret" />}</code></div>)}</pre>}
            {phase === 'adapter' && turn?.complete && <details className="scout-result-sample"><summary>Sample data · 2 example records</summary><div className="scout-result-data"><p>Example records · Not collected from a live website</p><table><thead><tr><th>title</th><th>url</th></tr></thead><tbody>{sampleRows.map((row) => <tr key={row.url}><td>{row.title}</td><td>{row.url}</td></tr>)}</tbody></table></div></details>}
          </div>
        </>}
    </div>
  </>;
}
