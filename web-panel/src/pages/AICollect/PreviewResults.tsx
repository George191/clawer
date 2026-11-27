import { parseTemplateEntries } from './templatePreview';
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
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
export type PreviewResult = { id: number; prompt: string; step: number; template: string; adapter: string; complete: boolean };

export function previewFile(turn: PreviewResult | undefined, step: number) {
  if (step === 2) return { name: 'template.yaml', content: turn?.template ?? '' };
  if (step === 3) return { name: 'adapter.py', content: turn?.adapter ?? '' };
  if (step === 5) return { name: 'sample-data.json', content: JSON.stringify(sampleRows, null, 2) };
  if (step === 1) return { name: 'Source.md', content: '# https://example.org/articles\n\nIdeas worth collecting.\n\n' + sampleRows.map(row => `[${row.title}](${row.url})`).join('\n\n') };
  return { name: step === 4 ? 'Review.md' : 'Request.md', content: (turn?.prompt ?? '') + (step === 4 ? '\n\nOutput fields: title, url\nPagination: up to 20 pages\nFiles: template.yaml, adapter.py' : '') };
}

export default function PreviewResults({ turn, step, running, structured = false, wrap = false, lineNumbers = true }: { turn?: PreviewResult; step?: number; running: boolean; structured?: boolean; wrap?: boolean; lineNumbers?: boolean }) {
  const displayStep = step ?? turn?.step ?? 0;
  const phase = step === 0 ? 'brief' : step === 4 ? 'review' : step === 5 ? 'data' : displayStep < 2 ? 'source' : displayStep === 2 ? 'template' : 'adapter';
  const code = useRef<HTMLDivElement>(null);
  const follow = useRef(true);
  const [edges, setEdges] = useState({ top: false, bottom: false });
  const updateEdges = useCallback(() => {
    const node = code.current;
    if (!node) return;
    const top = node.scrollTop > 2;
    const bottom = node.scrollHeight - node.scrollTop - node.clientHeight > 2;
    setEdges((previous) => previous.top === top && previous.bottom === bottom ? previous : { top, bottom });
  }, []);
  const text = phase === 'template' ? turn?.template ?? '' : turn?.adapter ?? '';
  const writing = running && displayStep === turn?.step && (phase === 'template' || phase === 'adapter') && (displayStep === 2 || displayStep === 3);
  useEffect(() => { follow.current = true; }, [turn?.id, phase]);
  useEffect(() => { if (follow.current && writing) code.current?.scrollTo({ top: code.current.scrollHeight }); }, [text, writing]);
  useLayoutEffect(updateEdges, [text, phase, updateEdges]);
  useEffect(() => {
    const node = code.current;
    if (!node) return;
    const observer = new ResizeObserver(updateEdges);
    observer.observe(node);
    if (node.firstElementChild) observer.observe(node.firstElementChild);
    return () => observer.disconnect();
  }, [phase, updateEdges]);
  const browserActive = phase === 'source' && running && turn?.step === 1;
  return <div className="scout-result-viewport" data-browser-active={browserActive || undefined}>
    <div ref={code} className="scout-result-content" data-phase={phase} onScroll={() => { const node = code.current; if (node) follow.current = node.scrollHeight - node.scrollTop - node.clientHeight < 60; updateEdges(); }}>
      {phase === 'brief' || phase === 'review' ? <div className="scout-result-brief"><p>{turn?.prompt || 'Collection request'}</p>{phase === 'review' && <><p>https://example.org/articles</p><dl><dt>Output fields</dt><dd>title, url</dd><dt>Pagination</dt><dd>page · up to 20 pages</dd><dt>Files</dt><dd>template.yaml · adapter.py</dd></dl></>}</div>
        : phase === 'data' ? <div className="scout-result-data"><table><thead><tr><th>title</th><th>url</th></tr></thead><tbody>{sampleRows.map((row) => <tr key={row.url}><td>{row.title}</td><td>{row.url}</td></tr>)}</tbody></table></div> : null}
      {phase === 'source' ? <div className="scout-source-preview"><div className="scout-source-address">https://example.org/articles </div><div className="scout-source-page"><small>THE COLLECTION JOURNAL</small><h3>Ideas worth collecting.</h3><p>Articles, research, and collection workflows.</p>{sampleRows.map((row, index) => <article key={row.url}><span>0{index + 1} · ARTICLE</span><h4>{row.title}</h4><p>Read the article and explore its sources.</p></article>)}</div></div>
        : (phase === 'template' || phase === 'adapter') && <>
          <div className={`scout-result-code ${phase === 'template' && structured ? 'scout-template-sheet' : ''} ${wrap ? 'is-wrapped' : ''} ${lineNumbers ? '' : 'without-line-numbers'}`}>
            {phase === 'template' && structured ? <TemplateSheet text={text} writing={writing} />
              : <pre>{text.split('\n').map((line, index) => <div className="scout-result-code-line" key={index}><span className="scout-line-number" aria-hidden="true">{index + 1}</span><code>{phase === 'adapter' ? renderPythonPreviewContent(line || ' ', `preview-${turn?.id}-${index}`) : renderYamlLine(line)}{writing && index === text.split('\n').length - 1 && <i className="scout-result-caret" />}</code></div>)}</pre>}

          </div>
        </>}
    </div>
    <div className={`scout-result-overflow-hint is-top ${edges.top ? 'is-visible' : ''}`} aria-hidden="true" />
    <div className={`scout-result-overflow-hint is-bottom ${edges.bottom ? 'is-visible' : ''}`} aria-hidden="true" />
    <div className="scout-computer-control" data-active={browserActive || undefined} aria-hidden="true"><span data-edge="top" /><span data-edge="bottom" /><span data-edge="left" /><span data-edge="right" /></div>
  </div>;
}

function renderYamlLine(line: string) {
  if (line.trimStart().startsWith('#')) return <span className="scout-yaml-comment">{line}</span>;
  const match = line.match(/^(\s*(?:-\s+)?)([\w.-]+)(:)(.*)$/);
  return match ? <>{match[1]}<span className="scout-yaml-key">{match[2]}</span>{match[3]}<span className={/^\s*(true|false|null|\d+)\s*$/.test(match[4]) ? 'scout-yaml-literal' : undefined}>{match[4]}</span></> : line || ' ';
}

const sections = ['site', 'request', 'response', 'pagination', 'fields', 'dedup'] as const;
const sectionRoots: Record<string, string> = { list_page: 'request', list_request: 'request', response_type: 'response', list_pagination: 'pagination', list_fields: 'fields', dedup_fields: 'dedup' };
function TemplateSheet({ text, writing }: { text: string; writing: boolean }) {
  const entries = parseTemplateEntries(text, (_key, path) => ({ stage: sectionRoots[path.split(/[.[]/)[0]] ?? 'site' }));
  const last = [...entries].reverse().find((entry) => entry.nodeType === 'value')?.id;
  return <>{sections.map((stage) => {
    // Match renderTemplateStageSection: hide sequence placeholders and flattened roots.
    const flattened = new Set(['list_pagination', 'dedup_fields', 'download']);
    const rows = entries.filter((entry) => entry.stage === stage && !(entry.nodeType === 'group' && (/^(params|dedup_fields|list_fields|detail_fields|download)\[\d+\]$/.test(entry.key) || flattened.has(entry.key))));
    if (!rows.length) return null;
    return <section key={stage} className="ai-template-stage-section" data-template-stage={stage}>
      <div className="ai-template-stage-head"><div className="ai-template-stage-copy"><span className="ai-template-stage-title">{stage}</span></div></div>
      <div className="ai-template-stage-body">{stage === 'request' && <div className="ai-template-fields-subsection" role="heading" aria-level={3}>List</div>}{rows.map((entry, index) => {
        const itemKey = entry.key.match(/^(?:(?:params|dedup_fields|list_fields|detail_fields|download)\[\d+\]|batch_params\.param_name\[\d+\])(?=\.|$)/)?.[0];
        const previousItemKey = rows[index - 1]?.key.match(/^(?:(?:params|dedup_fields|list_fields|detail_fields|download)\[\d+\]|batch_params\.param_name\[\d+\])(?=\.|$)/)?.[0];
        const root = entry.key.split(/[.[]/)[0];
        const isGroup = entry.nodeType === 'group';
        const isRootGroup = isGroup && !entry.key.includes('.') && !/\[\d+\]$/.test(entry.key);
        const label = /^dedup_fields\[\d+\]$/.test(entry.key) ? 'field' : entry.key.split('.').pop() ?? entry.key;
        const pageSection = stage === 'request' && /^(list_page|list_request)(?:\.|$)/.test(entry.key);
        const depth = Math.max(0, entry.depth - (itemKey || flattened.has(root) && entry.key !== root ? 1 : 0) + (pageSection ? 1 : 0));
        return <div key={entry.id} className={`ai-template-field ${isGroup ? 'is-group' : ''} ${isRootGroup ? 'is-root-group' : ''} ${entry.multiline ? 'is-multiline' : ''} ${itemKey ? 'is-yaml-list-item' : ''} ${itemKey && itemKey !== previousItemKey ? 'has-yaml-dash' : ''}`} style={{ '--ai-template-depth': depth } as React.CSSProperties}>
          <div className="ai-template-field-key">{itemKey && <i className="ai-template-field-dash" aria-hidden="true">-</i>}<span>{label}</span></div>
          {!isGroup && <div className={`ai-template-field-value ${entry.multiline || label === 'description' ? 'is-rich' : ''}`}><pre>{entry.multiline ? entry.value : entry.value.replace(/^['"]|['"]$/g, '')}</pre>{writing && entry.id === last && <i className="scout-result-caret" />}</div>}
        </div>;
      })}</div>
    </section>;
  })}</>;
}
