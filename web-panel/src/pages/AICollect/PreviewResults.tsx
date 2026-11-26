import { useEffect, useRef } from 'react';
import { renderPythonPreviewContent } from './pythonPreview';

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

export default function PreviewResults({ turn, step, running }: { turn?: PreviewResult; step?: number; running: boolean }) {
  const displayStep = step ?? turn?.step ?? 0;
  const phase = step === 0 ? 'brief' : step === 4 ? 'review' : step === 5 ? 'data' : displayStep < 2 ? 'source' : displayStep === 2 ? 'template' : 'adapter';
  const code = useRef<HTMLDivElement>(null);
  const follow = useRef(true);
  const text = phase === 'template' ? turn?.template ?? '' : turn?.adapter ?? '';
  const writing = running && displayStep === turn?.step && (phase === 'template' || phase === 'adapter') && (displayStep === 2 || displayStep === 3);
  useEffect(() => { follow.current = true; }, [turn?.id, phase]);
  useEffect(() => { if (follow.current && writing) code.current?.scrollTo({ top: code.current.scrollHeight }); }, [text, writing]);
  return <>
    <div ref={code} className="scout-result-content" data-phase={phase} onScroll={() => { const node = code.current; if (node) follow.current = node.scrollHeight - node.scrollTop - node.clientHeight < 60; }}>
      {phase === 'brief' || phase === 'review' ? <div className="scout-result-brief"><p>{turn?.prompt || 'Collection request'}</p>{phase === 'review' && <><p>https://example.org/articles</p><dl><dt>Output fields</dt><dd>title, url</dd><dt>Pagination</dt><dd>page · up to 20 pages</dd><dt>Files</dt><dd>template.yaml · adapter.py</dd></dl></>}<small>Example artifact · product preview</small></div>
        : phase === 'data' ? <div className="scout-result-data"><p>Example records · Not collected from a live website</p><table><thead><tr><th>title</th><th>url</th></tr></thead><tbody>{sampleRows.map((row) => <tr key={row.url}><td>{row.title}</td><td>{row.url}</td></tr>)}</tbody></table></div> : null}
      {phase === 'source' ? <div className={`scout-source-preview ${running && turn?.step === 1 ? 'is-scanning' : ''}`}><div className="scout-source-address">https://example.org/articles <span>Example page</span></div><div className="scout-source-page"><small>THE COLLECTION JOURNAL</small><h3>Ideas worth collecting.</h3><p>A sample article page for the collection design preview.</p>{sampleRows.map((row, index) => <article key={row.url}><span>0{index + 1} · ARTICLE</span><h4>{row.title}</h4><p>Title and article link are the example fields.</p></article>)}</div></div>
        : (phase === 'template' || phase === 'adapter') && <>
          <div className={`scout-result-code ${phase === 'template' ? 'scout-template-sheet' : ''}`}>
            {phase === 'template' ? <section className="ai-template-stage-section"><div className="ai-template-stage-body">{text.split('\n').map((line, index) => {
              if (!line.trim() || line.trim().startsWith('#')) return null;
              const field = line.match(/^(\s*)(- )?([^:]+):\s*(.*)$/);
              return <div key={index} className={`ai-template-field scout-template-row ${field && !field[4] ? 'is-group' : ''}`} style={{ paddingLeft: field ? `${field[1].length * 8}px` : undefined }}><div className="ai-template-field-key"><span>{field ? `${field[2] ?? ''}${field[3]}` : line}</span></div>{field?.[4] && <div className="ai-template-field-value"><pre>{field[4]}</pre></div>}{writing && index === text.trimEnd().split('\n').length - 1 && <i className="scout-result-caret" />}</div>;
            })}</div></section>
              : <pre>{text.split('\n').map((line, index) => <div className="scout-result-code-line ai-session-inspector-editor-line" key={index}><span aria-hidden="true">{index + 1}</span><span className="scout-code-prefix" aria-hidden="true">+</span><code>{renderPythonPreviewContent(line || ' ', `preview-${turn?.id}-${index}`)}{writing && index === text.split('\n').length - 1 && <i className="scout-result-caret" />}</code></div>)}</pre>}

          </div>
        </>}
    </div>
  </>;
}
