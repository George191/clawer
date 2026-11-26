import { parseTemplateEntries } from './templatePreview';
import { useEffect, useRef } from 'react';
import { renderPythonPreviewContent } from './pythonPreview';

// Product preview fixtures, never fetched, executed, or published.
export const previewTemplate = `name: collection_articles
display_name: Articles
base_url: https://example.org
data_type: other
description: Collect article titles and links
adapter: collection_articles
list_page: /articles?page={page}
list_request:
  method: GET
response_type: html
list_pagination:
  type: page_number
  max_pages: 20
list_fields:
  - name: title
    selector: article h2
    selector_type: css
    field_type: text
    required: true
  - name: url
    selector: article a
    selector_type: css
    field_type: href
    required: true
dedup_fields:
  - url
`;

export const previewAdapter = `"""Normalize article records for the collection adapter."""

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
      {phase === 'brief' || phase === 'review' ? <div className="scout-result-brief"><p>{turn?.prompt || 'Collection request'}</p>{phase === 'review' && <><p>https://example.org/articles</p><dl><dt>Output fields</dt><dd>title, url</dd><dt>Pagination</dt><dd>page · up to 20 pages</dd><dt>Files</dt><dd>template.yaml · adapter.py</dd></dl></>}</div>
        : phase === 'data' ? <div className="scout-result-data"><table><thead><tr><th>title</th><th>url</th></tr></thead><tbody>{sampleRows.map((row) => <tr key={row.url}><td>{row.title}</td><td>{row.url}</td></tr>)}</tbody></table></div> : null}
      {phase === 'source' ? <div className={`scout-source-preview ${running && turn?.step === 1 ? 'is-scanning' : ''}`}><div className="scout-source-address">https://example.org/articles </div><div className="scout-source-page"><small>THE COLLECTION JOURNAL</small><h3>Ideas worth collecting.</h3><p>Articles, research, and collection workflows.</p>{sampleRows.map((row, index) => <article key={row.url}><span>0{index + 1} · ARTICLE</span><h4>{row.title}</h4><p>Read the article and explore its sources.</p></article>)}</div></div>
        : (phase === 'template' || phase === 'adapter') && <>
          <div className={`scout-result-code ${phase === 'template' ? 'scout-template-sheet' : ''}`}>
            {phase === 'template' ? <TemplateSheet text={text} writing={writing} />
              : <pre>{text.split('\n').map((line, index) => <div className="scout-result-code-line ai-session-inspector-editor-line" key={index}><span aria-hidden="true">{index + 1}</span><span className="scout-code-prefix" aria-hidden="true">+</span><code>{renderPythonPreviewContent(line || ' ', `preview-${turn?.id}-${index}`)}{writing && index === text.split('\n').length - 1 && <i className="scout-result-caret" />}</code></div>)}</pre>}

          </div>
        </>}
    </div>
  </>;
}

const sections = ['site', 'request', 'response', 'pagination', 'fields', 'dedup'] as const;
const sectionRoots: Record<string, string> = { list_page: 'request', list_request: 'request', response_type: 'response', list_pagination: 'pagination', list_fields: 'fields', dedup_fields: 'dedup' };
function TemplateSheet({ text, writing }: { text: string; writing: boolean }) {
  const entries = parseTemplateEntries(text, (_key, path) => ({ stage: sectionRoots[path.split(/[.[]/)[0]] ?? 'site' }));
  const last = entries[entries.length - 1]?.id;
  return <>{sections.map((stage) => {
    const rows = entries.filter((entry) => entry.stage === stage);
    if (!rows.length) return null;
    return <section key={stage} className="ai-template-stage-section" data-template-stage={stage}>
      <h3 className="scout-template-section-label">{stage}</h3>
      <div className="ai-template-stage-body">{rows.map((entry) => {
        const isItem = /\[\d+\]$/.test(entry.key);
        const label = entry.key.split('.').pop()?.replace(/\[\d+\]$/, '') ?? entry.key;
        return <div key={entry.id} className={`ai-template-field scout-template-row ${entry.nodeType === 'group' ? 'is-group' : ''} ${isItem ? 'is-item-group' : ''}`} style={{ '--ai-template-depth': Math.max(0, entry.depth - (entry.key.includes('[') ? 1 : 0)) } as React.CSSProperties}>
          <div className="ai-template-field-key"><span>{isItem ? '−' : label}</span></div>
          {entry.nodeType !== 'group' && <div className="ai-template-field-value"><pre>{entry.value.replace(/^['"]|['"]$/g, '')}</pre>{writing && entry.id === last && <i className="scout-result-caret" />}</div>}
        </div>;
      })}</div>
    </section>;
  })}</>;
}
