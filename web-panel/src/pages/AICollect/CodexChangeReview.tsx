import { useEffect, useMemo, useRef, useState } from 'react';
import { File, FileDiff } from '@pierre/diffs/react';
import { registerCustomTheme, type SelectedLineRange } from '@pierre/diffs';
import { structuredPatch } from 'diff';
import { isAxiosError } from 'axios';
import { Modal, type MenuProps } from '@/components/ui';
import { getFriendlyErrorMessage } from '@/services/api';
import { useThemeStore } from '@/stores/settings';
import { CodexToolbarButton, CodexToolbarMenu } from './CodexToolbar';
import CodexChangeIcon from './CodexChangeIcon';
import CodexCommentEditor from './CodexCommentEditor';
import ScoutToolbarIcon from './ScoutToolbarIcon';
import { ArtifactIcon } from './StageArtifacts';
import type { SavedTurn } from './chatStore';
import { changeHunk, emptyFiles, emptyReview, filePatch, recordCorrection, reviewDiff, reviewFiles, reviewHistory, type ReviewComment, type ReviewFileKey, type ReviewFiles, type ReviewState } from './changeReview';
import codexDarkTheme from './codexDarkTheme';
import { parseTemplateEntries } from './templatePreview';
import './codexChangeReview.css';

registerCustomTheme('codex-native-dark', async () => ({ ...codexDarkTheme, name: 'codex-native-dark', type: 'dark' }));

type Props = {
  chatId: string; turns: SavedTurn[]; activeTurn?: number; activeStep: number; running: boolean;
  selectionRequest: number; userName: string;
  onChange: (turn: SavedTurn) => Promise<void>; onRefresh: () => Promise<unknown>;
  onNotice: (text: string) => void; onRequestChanges: (text: string) => void;
};
type Scope = 'last-turn' | 'all' | 'uncommitted' | 'unstaged' | 'staged';
type View = 'unified' | 'split' | 'auto';
const nativeDiffCSS = `:host{--diffs-font-family:'SFMono-Regular',Consolas,'Liberation Mono',monospace;--diffs-font-size:13px;--diffs-line-height:26px;--diffs-addition-base:#40c977;--diffs-deletion-base:#fa423e;--diffs-bg:#181818;--diffs-bg-buffer:#181818;--diffs-header-bg:#181818;--diffs-fg-number:#a0a0a0;--diffs-gap-block:0;--diffs-gap-inline:0;--diffs-gap-fallback:0}[data-diffs]{background:#181818}[data-separator]{background:#303030;color:#a0a0a0}[data-unmodified-lines]{text-align:left}[data-utility-button]{background:#fff;color:#181818;border-radius:4px}pre{padding-bottom:0!important}`;

function hideImportRows(node: HTMLElement, enabled: boolean) {
  const shadow = node.shadowRoot;
  if (!shadow) return;
  shadow.querySelectorAll<HTMLElement>('[data-review-import]').forEach(row => { row.style.removeProperty('display'); row.removeAttribute('data-review-import'); });
  if (!enabled) return;
  const isImport = (line: Element) => /^\s*(?:import\s|from\s.+\simport\s|(?:const|let|var)\s.*require\()/.test(line.textContent ?? '');
  for (const line of shadow.querySelectorAll<HTMLElement>('[data-line]')) {
    if (!isImport(line)) continue;
    const rows = [...shadow.querySelectorAll<HTMLElement>(`[data-line-index="${line.dataset.lineIndex}"]`)];
    if (rows.filter(row => row.hasAttribute('data-line')).every(isImport)) rows.forEach(row => { row.dataset.reviewImport = ''; row.style.display = 'none'; });
  }
}

export default function CodexChangeReview({ userName, chatId, turns, activeTurn, activeStep, selectionRequest, running, onChange, onRefresh, onNotice, onRequestChanges }: Props) {
  const zh = useThemeStore(state => state.language === 'zh-CN');
  const t = (en: string, cn: string) => zh ? cn : en;
  const history = useMemo(() => reviewHistory(turns), [turns]);
  const latest = history[history.length - 1];
  const [revisionId, setRevisionId] = useState<string>();
  const revisionIndex = revisionId ? history.findIndex(item => item.id === revisionId) : history.length - 1;
  const revision = history[revisionIndex] ?? latest;
  const previous = history[revisionIndex - 1] ?? emptyFiles;
  const owner = turns.find(turn => turn.id === latest?.turnId) ?? turns[turns.length - 1];
  const workingBase = history[history.findIndex(item => item.turnId === owner?.id) - 1] ?? emptyFiles;
  const review = useMemo(() => owner?.review ?? emptyReview(), [owner?.review]);
  const historical = Boolean(revision && latest && revision.id !== latest.id);
  const [scope, setScope] = useState<Scope>('last-turn');
  const [repository, setRepository] = useState('all');
  const [view, setView] = useState<View>('unified');
  const [wrap, setWrap] = useState(false);
  const [wordDiffs, setWordDiffs] = useState(true);
  const [whitespace, setWhitespace] = useState(false);
  const [imports, setImports] = useState(false);
  const [full, setFull] = useState(false);
  const [rich, setRich] = useState(false);
  const [numbers, setNumbers] = useState(true);
  const [openFiles, setOpenFiles] = useState<ReviewFileKey[]>([]);
  const [previewFile, setPreviewFile] = useState<ReviewFileKey>();
  const displayedFiles = previewFile && !openFiles.includes(previewFile) ? [...openFiles, previewFile] : openFiles;
  const [activeFile, setActiveFile] = useState<ReviewFileKey>();
  const [tree, setTree] = useState(false);
  const [timeline, setTimeline] = useState(false);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [query, setQuery] = useState('');
  const [jump, setJump] = useState(false);
  const [find, setFind] = useState(false);
  const [match, setMatch] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [showResolved, setShowResolved] = useState(false);
  const [editor, setEditor] = useState<{ key: ReviewFileKey; text: string; editing: boolean }>();
  const [draft, setDraft] = useState<{ key: ReviewFileKey; range: SelectedLineRange; body: string }>();
  const [revert, setRevert] = useState<{ files: ReviewFiles; message: string }>();
  const root = useRef<HTMLDivElement>(null);
  const scroll = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  const writable = Boolean(owner && latest && !historical && !running && !busy);
  const current = latest ?? emptyFiles;
  useEffect(() => { setDraft(undefined); }, [revision?.id, scope, activeFile, running, historical]);

  useEffect(() => { setOpenFiles([]); setPreviewFile(undefined); setActiveFile(undefined); setRevisionId(undefined); setScope('last-turn'); setCollapsed(new Set()); setEditor(undefined); setDraft(undefined); setError(''); }, [chatId]);
  useEffect(() => {
    const node = root.current;
    if (!node) return;
    const observer = new ResizeObserver(entries => setWidth(entries[0].contentRect.width));
    observer.observe(node); return () => observer.disconnect();
  }, []);
  useEffect(() => {
    if (activeTurn !== undefined) {
      const selected = [...history].reverse().find(item => item.turnId === activeTurn);
      if (selected) { setRevisionId(selected.id === latest?.id ? undefined : selected.id); if (selected.id !== latest?.id || selectionRequest > 0) setScope('last-turn'); }
    }
    setActiveFile(undefined);
    const file = reviewFiles.find(file => file.step === activeStep);
    if (file) requestAnimationFrame(() => root.current?.querySelector(`#codex-change-${file.key}`)?.scrollIntoView({ block: 'nearest' }));
  }, [activeTurn, activeStep, selectionRequest]);

  const files = useMemo(() => reviewFiles.map(file => {
    let before = scope === 'all' ? '' : scope === 'last-turn' || historical ? previous[file.key] : workingBase[file.key];
    let after = revision?.[file.key] ?? '';
    if (!historical && (scope === 'unstaged' || scope === 'uncommitted')) before = scope === 'unstaged' ? review.staged[file.key] ?? before : before;
    if (!historical && scope === 'staged') after = review.staged[file.key] ?? before;
    const diff = reviewDiff(file.path, before, after, whitespace);
    const additions = diff.hunks.reduce((sum, hunk) => sum + hunk.additionLines, 0);
    const deletions = diff.hunks.reduce((sum, hunk) => sum + hunk.deletionLines, 0);
    return { ...file, before, after, diff, additions, deletions };
  }).filter(file => file.additions || file.deletions), [revision, previous, workingBase, scope, historical, review.staged, imports, whitespace]);
  const totals = files.reduce((sum, file) => ({ additions: sum.additions + file.additions, deletions: sum.deletions + file.deletions }), { additions: 0, deletions: 0 });
  const mode = view === 'auto' ? width >= 900 ? 'split' : 'unified' : view;
  const sourceLabel = historical ? `${t('Revision', '修正')} ${revisionIndex + 1}` : ({ 'last-turn': t('Last turn', '上一轮'), all: t('All changes', '全部变更'), uncommitted: t('Uncommitted', '未提交'), unstaged: t('Unstaged', '未暂存'), staged: t('Staged', '已暂存') })[scope];
  const patch = files.map(file => filePatch(file.path, file.before, file.after)).join('\n');

  async function perform(action: () => Promise<unknown>) {
    setBusy(true); setError('');
    try { await action(); } catch (error) { setError(isAxiosError(error) ? getFriendlyErrorMessage(error) : error instanceof Error ? error.message : String(error)); }
    finally { setBusy(false); }
  }
  function saveReview(update: (review: ReviewState) => ReviewState) {
    if (!owner) return Promise.reject(new Error('There is no response to review.'));
    return onChange({ ...owner, review: update(owner.review ?? emptyReview()) });
  }
  function correct(files: ReviewFiles, message: string) {
    if (!owner) return Promise.reject(new Error('There is no response to correct.'));
    return onChange(recordCorrection(owner, current, files, message));
  }
  async function copy(text: string) { await navigator.clipboard.writeText(text); onNotice(t('Copied', '已复制')); }
  function download(name: string, text: string) {
    const url = URL.createObjectURL(new Blob([text], { type: 'text/plain;charset=utf-8' }));
    const anchor = document.createElement('a'); anchor.href = url; anchor.download = name; anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  function toggle(key: string) { setCollapsed(old => { const next = new Set(old); if (!next.delete(key)) next.add(key); return next; }); }
  function selectFile(key: ReviewFileKey) { setCollapsed(old => new Set([...old].filter(item => item !== key))); requestAnimationFrame(() => root.current?.querySelector(`#codex-change-${key}`)?.scrollIntoView({ block: 'start', behavior: 'smooth' })); setJump(false); }
  function stage(key?: ReviewFileKey, unstage = false) {
    void perform(() => saveReview(value => {
      const staged = { ...value.staged };
      for (const file of files.filter(file => !key || file.key === key)) { if (unstage) delete staged[file.key]; else staged[file.key] = file.after; }
      return { ...value, staged };
    }));
  }
  function hunkAction(file: typeof files[number], index: number, action: 'stage' | 'unstage' | 'revert') {
    void perform(async () => {
      if (action === 'revert') await correct({ ...current, [file.key]: changeHunk(file.path, file.before, file.after, index, current[file.key], true) }, `Revert ${file.path} hunk ${index + 1}`);
      else await saveReview(value => ({ ...value, staged: { ...value.staged, [file.key]: changeHunk(file.path, file.before, file.after, index, value.staged[file.key] ?? workingBase[file.key], action === 'unstage') } }));
    });
  }
  function addComment() {
    if (!draft?.body.trim() || !revision) return;
    const comment: ReviewComment = { id: crypto.randomUUID(), revisionId: revision.id, path: draft.key, side: draft.range.side ?? 'additions', start: Math.min(draft.range.start, draft.range.end), end: Math.max(draft.range.start, draft.range.end), body: draft.body.trim(), resolved: false };
    void perform(async () => { await saveReview(value => ({ ...value, comments: [...value.comments, comment] })); setDraft(undefined); });
  }
  function updateComment(id: string, remove = false) { void perform(() => saveReview(value => ({ ...value, comments: remove ? value.comments.filter(comment => comment.id !== id) : value.comments.map(comment => comment.id === id ? { ...comment, resolved: !comment.resolved } : comment) }))); }
  function annotations(key: ReviewFileKey, fullFile = false) {
    const comments = review.comments.filter(comment => comment.revisionId === revision?.id && comment.path === key && (showResolved || !comment.resolved) && (!fullFile || comment.side === 'additions'));
    return [
      ...comments.map(comment => ({ side: comment.side, lineNumber: comment.end, metadata: comment as ReviewComment | null })),
      ...(draft?.key === key && (!fullFile || draft.range.side !== 'deletions') ? [{ side: draft.range.side ?? 'additions' as const, lineNumber: Math.max(draft.range.start, draft.range.end), metadata: null }] : []),
    ];
  }
  function renderAnnotation(comment: ReviewComment | null) {
    return comment ? renderComment(comment) : draft && <CodexCommentEditor userName={userName} range={draft.range} body={draft.body} busy={busy} error={error} onChange={body => setDraft(value => value ? { ...value, body } : value)} onCancel={() => setDraft(undefined)} onSubmit={addComment} />;
  }
  function renderComment(comment: ReviewComment) { return <div className="codex-inline-comment"><p>{comment.body}</p><small>{comment.start === comment.end ? `L${comment.end}` : `L${comment.start}–${comment.end}`}</small><button type="button" disabled={busy} onClick={() => updateComment(comment.id)}>{comment.resolved ? t('Reopen', '重新打开') : t('Resolve', '解决')}</button><button type="button" disabled={busy} onClick={() => updateComment(comment.id, true)}>{t('Delete', '删除')}</button><button type="button" onClick={() => onRequestChanges(`${reviewFiles.find(file => file.key === comment.path)!.path}:${comment.start}-${comment.end}\n${comment.body}`)}>{t('Add to chat', '添加到聊天')}</button></div>; }
  function matches() {
    if (!query.trim()) return [];
    return [...(root.current?.querySelectorAll('diffs-container') ?? [])].flatMap(node => [...(node.shadowRoot?.querySelectorAll<HTMLElement>('[data-line]') ?? [])]).filter(line => line.textContent?.toLocaleLowerCase().includes(query.toLocaleLowerCase()));
  }
  function nextMatch(direction: number) {
    const found = matches(); if (!found.length) { onNotice(t('No matches', '无匹配结果')); return; }
    const index = (match + direction + found.length) % found.length; setMatch(index); found[index].scrollIntoView({ block: 'center' }); onNotice(`${index + 1} / ${found.length}`);
  }
  const optionsMenu = { selectedKeys: [wordDiffs ? 'words' : '', whitespace ? 'whitespace' : '', imports ? 'imports' : '', full ? 'full' : '', rich ? 'rich' : '', numbers ? 'numbers' : '', showResolved ? 'resolved' : ''], items: [
    { key: 'full', label: t('Load full files', '加载完整文件') }, { key: 'rich', label: t('Rich preview', '富文本预览') }, { key: 'words', label: t('Word diffs', '单词差异') }, { key: 'whitespace', label: t('Hide white space', '隐藏空白') }, { key: 'imports', label: t('Hide imports', '隐藏导入') }, { key: 'numbers', label: t('Line numbers', '行号') },
    { type: 'divider' as const }, { key: 'apply', label: t('Copy git apply command', '复制 git apply 命令'), disabled: !patch }, { key: 'patch', label: t('Download patch', '下载补丁'), disabled: !patch }, { key: 'find', label: t('Find in diff', '在差异中查找') }, { key: 'history', label: t('Correction history', '修正记录') }, { key: 'resolved', label: t('Show resolved comments', '显示已解决的批注') },
    { type: 'divider' as const }, { key: 'stage', label: t('Stage all', '全部暂存'), disabled: !writable || !files.length || scope === 'staged' || files.every(file => review.staged[file.key] === file.after) }, { key: 'unstage', label: t('Unstage all', '取消全部暂存'), disabled: !writable || !Object.keys(review.staged).length }, { key: 'revert', label: t('Revert all', '撤销全部变更'), disabled: !writable || !files.length || scope === 'staged' },
  ], onClick: ({ key }: { key: string }) => {
    if (key === 'full') setFull(value => !value); else if (key === 'rich') setRich(value => !value); else if (key === 'words') setWordDiffs(value => !value); else if (key === 'whitespace') setWhitespace(value => !value); else if (key === 'imports') setImports(value => !value); else if (key === 'numbers') setNumbers(value => !value); else if (key === 'resolved') setShowResolved(value => !value);
    else if (key === 'apply') void perform(() => copy(`git apply <<'SCOUT_PATCH'\n${patch}\nSCOUT_PATCH`)); else if (key === 'patch') download('changes.patch', patch); else if (key === 'find') { setFind(value => !value); setQuery(''); } else if (key === 'history') setTimeline(value => !value); else if (key === 'stage') stage(); else if (key === 'unstage') void perform(() => saveReview(value => ({ ...value, staged: {} }))); else if (key === 'revert') setRevert({ files: { ...current, ...Object.fromEntries(files.map(file => [file.key, file.before])) }, message: 'Revert all changes' });
  } };

  return <div ref={root} className="codex-change-review" aria-label="Changes review" aria-busy={busy} onKeyDown={event => { if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'f') { event.preventDefault(); setFind(true); } }}>
    <div className="codex-review-tabs" role="tablist" aria-label={t('Preview tabs', '预览标签页')} onKeyDown={event => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key) || !(event.target instanceof HTMLElement) || event.target.getAttribute('role') !== 'tab') return;
      const tabs = [...event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"]')];
      const index = tabs.indexOf(event.target as HTMLButtonElement);
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
      event.preventDefault(); tabs[next]?.click(); tabs[next]?.focus();
    }}>
      <button type="button" id="codex-changes-tab" role="tab" tabIndex={activeFile ? -1 : 0} aria-selected={!activeFile} aria-controls="codex-changes-panel" onClick={() => setActiveFile(undefined)}><CodexChangeIcon name="unified" />{t('Changes', '变更')}</button>
      {displayedFiles.map(key => { const file = reviewFiles.find(file => file.key === key)!; return <div className={`codex-review-file-tab ${activeFile === key ? 'is-active' : ''} ${!openFiles.includes(key) ? 'is-preview' : ''}`} key={key}><button type="button" id={`codex-file-tab-${key}`} role="tab" tabIndex={activeFile === key ? 0 : -1} aria-selected={activeFile === key} aria-controls="codex-file-panel" onClick={() => setActiveFile(key)} onDoubleClick={() => { setOpenFiles(value => value.includes(key) ? value : [...value, key]); setPreviewFile(undefined); }}><ArtifactIcon step={file.step} />{file.path}</button><button type="button" aria-label={`Close ${file.path} tab`} onClick={() => { const remaining = displayedFiles.filter(item => item !== key); setOpenFiles(value => value.filter(item => item !== key)); if (previewFile === key) setPreviewFile(undefined); if (activeFile === key) setActiveFile(remaining[Math.min(displayedFiles.indexOf(key), remaining.length - 1)]); }}><ScoutToolbarIcon name="close" /></button></div>; })}
    </div>
    {activeFile && <div className="codex-review-file-panel" id="codex-file-panel" role="tabpanel" aria-labelledby={`codex-file-tab-${activeFile}`}><div className="codex-review-file-tools"><span>{reviewFiles.find(file => file.key === activeFile)!.path}</span><button type="button" disabled={!writable} onClick={() => setEditor({ key: activeFile, text: revision?.[activeFile] ?? '', editing: true })}>{t('Edit', '编辑')}</button></div><File selectedLines={draft?.key === activeFile ? draft.range : null} lineAnnotations={annotations(activeFile, true)} renderAnnotation={({ metadata }) => renderAnnotation(metadata)} file={{ name: reviewFiles.find(file => file.key === activeFile)!.path, contents: revision?.[activeFile] ?? '' }} options={{ theme: 'codex-native-dark', themeType: 'dark', disableFileHeader: true, overflow: wrap ? 'wrap' : 'scroll', disableLineNumbers: !numbers, unsafeCSS: nativeDiffCSS, enableLineSelection: writable, enableGutterUtility: writable, onGutterUtilityClick: range => setDraft({ key: activeFile, range: { ...range, side: 'additions' }, body: '' }), onLineSelected: range => { if (range) setDraft({ key: activeFile, range: { ...range, side: 'additions' }, body: '' }); } }} /></div>}
    <div className="codex-changes-panel" id="codex-changes-panel" role="tabpanel" aria-labelledby="codex-changes-tab" hidden={Boolean(activeFile)}>
    <header className="codex-changes-header">
      <div className="codex-changes-sources">
        <CodexToolbarMenu menu={{ selectedKeys: [repository], items: [{ key: 'all', label: t('All repos', '所有仓库') }, { key: 'scout', label: 'Scout' }], onClick: ({ key }) => setRepository(key) }}><button type="button" className="codex-review-source">{repository === 'all' ? t('All repos', '所有仓库') : 'Scout'}<ScoutToolbarIcon name="chevron" /></button></CodexToolbarMenu>
        <CodexToolbarMenu menu={{ selectedKeys: [historical ? revision!.id : scope], items: [
          ...(['last-turn', 'all', 'uncommitted', 'unstaged', 'staged'] as const).map(key => ({ key, label: ({ 'last-turn': t('Last turn', '上一轮'), all: t('All changes', '全部变更'), uncommitted: t('Uncommitted', '未提交'), unstaged: t('Unstaged', '未暂存'), staged: t('Staged', '已暂存') })[key] })),
          { type: 'divider' as const }, { key: 'versions', label: t('Corrections', '修正记录'), children: history.map((item, index) => ({ key: item.id, label: `${index + 1} · ${item.message.slice(0, 50)}` })) },
        ], onClick: ({ key }) => { if (history.some(item => item.id === key)) { setRevisionId(key); setScope('last-turn'); } else { setRevisionId(undefined); setScope(key as Scope); } setCollapsed(new Set()); } }}><button type="button" className="codex-review-source">{sourceLabel}<ScoutToolbarIcon name="chevron" /></button></CodexToolbarMenu>
        <span className="codex-change-stats" aria-label={`Added ${totals.additions}, removed ${totals.deletions}`}><b>+{totals.additions}</b><i>-{totals.deletions}</i></span>
      </div>
      <div className="codex-changes-controls" role="toolbar" aria-label="Changes controls">
        <CodexToolbarMenu menu={optionsMenu}><CodexToolbarButton aria-label="Changes options" title={t('Changes options', '变更选项')}><ScoutToolbarIcon name="more" /></CodexToolbarButton></CodexToolbarMenu>
        <CodexToolbarButton aria-label="Jump to file" title={t('Jump to file', '跳转到文件')} aria-pressed={jump} onClick={() => { setJump(value => !value); setQuery(''); }}><CodexChangeIcon name="search" /></CodexToolbarButton>
        <CodexToolbarButton aria-label="Refresh changes" title={t('Refresh', '刷新')} disabled={busy || running} onClick={() => void perform(onRefresh)}><CodexChangeIcon name="refresh" /></CodexToolbarButton>
        <CodexToolbarButton aria-label="Word wrap" title={t('Word wrap', '自动换行')} aria-pressed={wrap} onClick={() => setWrap(value => !value)}><CodexChangeIcon name="wrap" /></CodexToolbarButton>
        <CodexToolbarButton aria-label={files.length && collapsed.size >= files.length ? 'Expand all diffs' : 'Collapse all diffs'} title={t('Collapse / expand all diffs', '折叠 / 展开全部差异')} onClick={() => setCollapsed(collapsed.size >= files.length ? new Set() : new Set(files.map(file => file.key)))}><CodexChangeIcon name={files.length && collapsed.size >= files.length ? 'expand' : 'collapse'} /></CodexToolbarButton>
        <CodexToolbarButton aria-label={view === 'unified' ? 'Switch to split diff' : view === 'split' ? 'Switch to Auto diff' : 'Auto diff: switch to unified diff'} title={view} data-diff-mode={view} onClick={() => setView(view === 'unified' ? 'split' : view === 'split' ? 'auto' : 'unified')}><CodexChangeIcon name={mode} /></CodexToolbarButton>
      </div>
      <CodexToolbarButton className="codex-tree-toggle" aria-label={tree ? 'Hide file tree' : 'Show file tree'} title={t('File tree', '文件树')} aria-pressed={tree} onClick={() => setTree(value => !value)}><ScoutToolbarIcon name="panelHidden" /></CodexToolbarButton>
    </header>
    {error && <div className="codex-review-error" role="alert">{error}<button type="button" onClick={() => setError('')}>{t('Dismiss', '关闭')}</button></div>}
    {(jump || find) && <div className="codex-review-find"><input autoFocus aria-label={jump ? 'Jump to file' : 'Find in diff'} placeholder={jump ? t('Search files', '搜索文件') : t('Find in diff…', '在差异中查找…')} value={query} onChange={event => { setQuery(event.target.value); setMatch(-1); }} onKeyDown={event => { if (event.key === 'Escape') { setFind(false); setJump(false); } if (event.key === 'Enter') nextMatch(event.shiftKey ? -1 : 1); }} />{find && <><button type="button" aria-label="Previous result" onClick={() => nextMatch(-1)}>↑</button><button type="button" aria-label="Next result" onClick={() => nextMatch(1)}>↓</button></>}<button type="button" aria-label="Close find" onClick={() => { setJump(false); setFind(false); setQuery(''); }}><ScoutToolbarIcon name="close" /></button>{jump && <div className="codex-jump-files">{files.filter(file => file.path.toLowerCase().includes(query.toLowerCase())).map(file => <button key={file.key} type="button" onClick={() => selectFile(file.key)}><ArtifactIcon step={file.step} />{file.path}</button>)}</div>}</div>}
    <div className="codex-review-workspace">
      {(tree || timeline) && <nav className="codex-review-tree" aria-label={timeline ? 'Correction history' : 'Changed files'}>{timeline ? <><div className="codex-review-tree-title">{t('Correction history', '修正记录')}</div>{[...history].reverse().map(item => <button type="button" key={item.id} aria-current={revision?.id === item.id} onClick={() => { setRevisionId(item.id); setScope('last-turn'); }}><strong>{item.message}</strong><small>{item.createdAt ? new Date(item.createdAt).toLocaleString() : `${t('Response', '回复')} ${item.turnId}`}</small></button>)}</> : <><div className="codex-review-tree-title">{t('Changed files', '变更文件')}</div>{files.map(file => <button type="button" key={file.key} onClick={() => selectFile(file.key)}><ArtifactIcon step={file.step} /><span>{file.path}</span><b>{file.additions || -file.deletions}</b></button>)}</>}</nav>}
      <div ref={scroll} className="codex-review-scroll" data-view={mode}>
        {historical && <div className="codex-revision-banner"><span>{revision?.message}</span><button type="button" disabled={busy || running} onClick={() => revision && void perform(async () => { await correct({ template: revision.template, adapter: revision.adapter }, `Restore revision ${revisionIndex + 1}`); setRevisionId(undefined); })}>{t('Restore this revision', '恢复此版本')}</button><button type="button" onClick={() => setRevisionId(undefined)}>{t('Back to latest', '返回最新')}</button></div>}
        {!files.length && <div className="codex-review-empty" role="status">{running ? t('Waiting for file changes…', '等待文件变更…') : t('No changes', '暂无变更')}</div>}
        {files.map(file => {
          const fileAnnotations = annotations(file.key);
          const hunkPatch = structuredPatch(file.path, file.path, file.before, file.after, '', '', { context: 3 });
          const fileMenu: MenuProps = { items: [{ key: 'path', label: t('Copy path', '复制路径') }, { key: 'open', label: t('Open file', '打开文件') }, { key: 'copy', label: t('Copy file contents', '复制文件内容') }, { key: 'patch', label: t('Copy patch', '复制补丁') }, { key: 'download', label: t('Download file', '下载文件') }, { type: 'divider' as const }, { key: scope === 'staged' ? 'unstage' : 'stage', label: scope === 'staged' ? t('Unstage file', '取消暂存文件') : t('Stage file', '暂存文件'), disabled: !writable || scope !== 'staged' && review.staged[file.key] === file.after }, { key: 'revert', label: file.before ? t('Revert file', '撤销文件变更') : t('Delete file', '删除文件'), disabled: !writable || scope === 'staged' }, { key: 'collapse', label: collapsed.has(file.key) ? t('Expand file', '展开文件') : t('Collapse file', '折叠文件') }], onClick: ({ key }) => { if (key === 'path') void perform(() => copy(file.path)); else if (key === 'copy') void perform(() => copy(file.after)); else if (key === 'patch') void perform(() => copy(filePatch(file.path, file.before, file.after))); else if (key === 'download') download(file.path, file.after); else if (key === 'open') setEditor({ key: file.key, text: file.after, editing: false }); else if (key === 'stage' || key === 'unstage') stage(file.key, key === 'unstage'); else if (key === 'revert') setRevert({ files: { ...current, [file.key]: file.before }, message: `Revert ${file.path}` }); else toggle(file.key); } };
          return <section key={file.key} id={`codex-change-${file.key}`} className="codex-change-file" aria-label={`Changes to ${file.path}`}>
            <header className="codex-change-file-header"><CodexToolbarMenu contextMenu menu={fileMenu}><button type="button" className="codex-change-file-toggle" aria-label={`${collapsed.has(file.key) ? 'Expand' : 'Collapse'} ${file.path}`} aria-expanded={!collapsed.has(file.key)} onClick={() => toggle(file.key)}><ArtifactIcon step={file.step} /><span>{file.path}</span></button></CodexToolbarMenu><span className="codex-change-stats"><b>+{file.additions}</b><i>-{file.deletions}</i></span><CodexToolbarButton aria-label={`Preview ${file.path}`} title={t('Preview file', '预览文件')} onClick={() => { setPreviewFile(file.key); setActiveFile(file.key); }}><CodexChangeIcon name="preview" /></CodexToolbarButton><CodexToolbarButton aria-label={`Open ${file.path} in tab`} title={t('Open in new tab', '在新标签页打开')} onClick={() => { setOpenFiles(value => value.includes(file.key) ? value : [...value, file.key]); setPreviewFile(undefined); setActiveFile(file.key); }}><CodexChangeIcon name="openTab" /></CodexToolbarButton>

            </header>
            {!collapsed.has(file.key) && <>
              {writable && !imports && !whitespace && <div className="codex-hunk-actions">{hunkPatch.hunks.map((hunk, index) => <CodexToolbarMenu key={index} menu={{ items: [{ key: scope === 'staged' ? 'unstage' : 'stage', label: scope === 'staged' ? t('Unstage', '取消暂存') : t('Stage', '暂存') }, { key: 'revert', label: t('Revert', '撤销'), disabled: scope === 'staged' }], onClick: ({ key }) => hunkAction(file, index, key as 'stage' | 'unstage' | 'revert') }}><button type="button" aria-label={`Actions for ${file.path} hunk ${index + 1}`}>@@ -{hunk.oldStart},{hunk.oldLines} +{hunk.newStart},{hunk.newLines}</button></CodexToolbarMenu>)}</div>}
              {rich && file.key === 'template' ? <dl className="codex-rich-preview">{parseTemplateEntries(file.after, () => ({})).filter(item => item.nodeType === 'value').map(item => <div key={item.id}><dt>{item.key}</dt><dd>{item.value}</dd></div>)}</dl> : <FileDiff fileDiff={file.diff} selectedLines={draft?.key === file.key ? draft.range : null} lineAnnotations={fileAnnotations} options={{ theme: 'codex-native-dark', themeType: 'dark', diffStyle: mode, diffIndicators: 'bars', disableFileHeader: true, overflow: wrap ? 'wrap' : 'scroll', lineDiffType: wordDiffs ? 'word' : 'none', expandUnchanged: full, disableLineNumbers: !numbers, hunkSeparators: 'line-info', unsafeCSS: nativeDiffCSS, onPostRender: (node, _instance, phase) => { if (phase !== 'unmount') hideImportRows(node, imports); }, enableLineSelection: writable, enableGutterUtility: writable, onGutterUtilityClick: range => setDraft({ key: file.key, range, body: '' }), onLineSelected: range => { if (range) setDraft({ key: file.key, range, body: '' }); } }} renderAnnotation={({ metadata }) => renderAnnotation(metadata)} />}
            </>}
          </section>;
        })}
      </div>
    </div>
    </div>
    <Modal open={Boolean(editor)} title={reviewFiles.find(file => file.key === editor?.key)?.path} onCancel={() => setEditor(undefined)} footer={null} width={800} className="codex-chat-dialog codex-file-dialog">{error && <p role="alert" className="codex-chat-error">{error}</p>}{editor && <><div className="codex-file-editor">{editor.editing ? <textarea aria-label="Edit file contents" value={editor.text} onChange={event => setEditor({ ...editor, text: event.target.value })} /> : <File file={{ name: reviewFiles.find(file => file.key === editor.key)!.path, contents: editor.text }} options={{ theme: 'codex-native-dark', themeType: 'dark', disableFileHeader: true, overflow: 'wrap', unsafeCSS: nativeDiffCSS }} />}</div><footer><span>{t('Saving creates a new correction record.', '保存会新增一条修正记录。')}</span>{editor.editing ? <button type="button" className="codex-chat-action" disabled={busy || !writable} onClick={() => void perform(async () => { await correct({ ...current, [editor.key]: editor.text }, `Edit ${reviewFiles.find(file => file.key === editor.key)!.path}`); setEditor(undefined); })}>{t('Save revision', '保存修正')}</button> : <button type="button" className="codex-chat-action" disabled={!writable} onClick={() => setEditor({ ...editor, editing: true })}>{t('Edit', '编辑')}</button>}</footer></>}</Modal>

    <Modal open={Boolean(revert)} title={t('Revert changes?', '撤销变更？')} onCancel={() => setRevert(undefined)} footer={null} width={440} className="codex-chat-dialog">{error && <p role="alert" className="codex-chat-error">{error}</p>}<p>{t('This creates a new correction record. Earlier versions remain available in correction history.', '此操作会新增修正记录，原版本仍可在修正记录中查看。')}</p><footer><button type="button" className="codex-chat-action" onClick={() => setRevert(undefined)}>{t('Cancel', '取消')}</button><button type="button" className="codex-chat-action" disabled={busy || !writable} onClick={() => revert && void perform(async () => { await correct(revert.files, revert.message); setRevert(undefined); })}>{t('Revert', '撤销')}</button></footer></Modal>
  </div>;
}
