import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Modal } from '@/components/ui';
import { ArtifactIcon, type StageArtifact } from './StageArtifacts';
import ScoutToolbarIcon from './ScoutToolbarIcon';

type ContextTurn = { id: number; prompt: string; step: number; done: number; template: string; adapter: string; complete: boolean };
type Step = { title: string; detail: string };

// Mirrors Codex's Changes / Background processes / Sources interactions using collection preview state.
export default function ConversationContext({ open, pinned, canPin, onTogglePinned, turn, steps, running, activity, onClose, onView, activePreview, onPause, sources, onAddFiles, onPreviewFile }: { open: boolean; pinned: boolean; canPin: boolean; onTogglePinned: () => void; turn?: ContextTurn; steps: Step[]; running: boolean; activity: string; onClose: () => void; onView: (artifact: StageArtifact) => void; activePreview?: string; onPause: () => void; sources: File[]; onAddFiles: (files: File[]) => void; onPreviewFile: (file: File) => void }) {
  const [showAll, setShowAll] = useState(false);
  const picker = useRef<HTMLInputElement>(null);
  const files = turn ? [{ name: 'template.yaml', content: turn.template, step: 2 }, { name: 'adapter.py', content: turn.adapter, step: 3 }].filter((file) => file.content) : [];
  function view(step: number) {
    if (turn) onView({ key: `${turn.id}-${step}`, turn: turn.id, step });
  }
  return <>
    <aside id="scout-conversation-context" className={`scout-context-panel ${open ? 'is-open' : ''} ${pinned ? 'is-pinned' : ''}`} aria-label="Conversation context" aria-hidden={!open} onKeyDown={(event) => { if (event.key === 'Escape') { event.stopPropagation(); onClose(); } }}>
      <header className="scout-context-heading"><span>Summary</span><div>{canPin && <button type="button" className="scout-toolbar-button" aria-label={pinned ? 'Unpin summary' : 'Pin summary'} title={pinned ? 'Unpin summary' : 'Pin summary'} aria-pressed={pinned} onClick={onTogglePinned}><ScoutToolbarIcon name="panel" active={pinned} /></button>}<button type="button" className="scout-toolbar-button" aria-label="Close context panel" title="Close summary" onClick={onClose}><ScoutToolbarIcon name="close" /></button></div></header>
      <ContextSection title="Changes">
        {files.length ? files.map((file) => <button type="button" key={file.name} className="scout-context-row" aria-label={`Open ${file.name}`} aria-pressed={activePreview === `${turn!.id}-${file.step}`} aria-controls="scout-collection-results" onClick={() => view(file.step)}><ArtifactIcon step={file.step} /><span>{file.name}<small>{turn!.done > file.step ? 'Generated · example' : 'Writing · example'}</small></span><b className="scout-context-added" aria-label={`${file.content.trimEnd().split('\n').length} generated lines`}>+{file.content.trimEnd().split('\n').length}</b></button>) : <p className="scout-context-empty">Generated files appear here.</p>}
      </ContextSection>
      <ContextSection title="Background processes">
        {turn && steps.slice(0, turn.step + 1).map((step, index) => {
          const done = index < turn.done;
          const active = !done && index === turn.step;
          return <div key={step.title} className="scout-context-process"><div className={`scout-context-process-row ${active ? 'is-active' : ''}`}><button type="button" className="scout-context-row" aria-label={`Inspect ${step.title}`} aria-pressed={activePreview === `${turn!.id}-${index}`} aria-controls="scout-collection-results" onClick={() => view(index)}><svg viewBox="0 0 20 20" aria-hidden="true"><rect x="2" y="3" width="16" height="14" rx="3" /><path d="m6 7 3 3-3 3m5 0h3" /></svg><span>{step.title}</span><small>{done ? 'Done' : active ? running ? 'Running' : activity : 'Queued'}</small></button>{active && running && <button type="button" className="scout-context-stop" aria-label={`Pause ${step.title}`} title="Pause process" onClick={onPause}><span /></button>}</div></div>;
        })}
      </ContextSection>
      <ContextSection title="Sources" action={<button type="button" className="scout-context-add" aria-label="Add source" title="Add source" onClick={() => picker.current?.click()}>+</button>}>
        <input ref={picker} type="file" aria-label="Source files" accept=".txt,.md,.yaml,.yml,.json,.py,image/*" multiple hidden onChange={(event) => { const files = Array.from(event.target.files ?? []); onAddFiles(files); event.target.value = ''; }} />
        <button type="button" className="scout-context-row" aria-label="Open webpage source" aria-pressed={activePreview === `${turn?.id}-1`} aria-controls="scout-collection-results" onClick={() => view(1)}><ArtifactIcon step={1} /><span>example.org/articles<small>Example webpage</small></span></button>
        <button type="button" className="scout-context-row" aria-label="Open request source" aria-pressed={activePreview === `${turn?.id}-0`} aria-controls="scout-collection-results" onClick={() => view(0)}><ArtifactIcon step={0} /><span>{turn?.prompt || 'Collection request'}</span></button>
        {(showAll ? sources : sources.slice(0, 3)).map((file, index) => <button type="button" key={`${file.name}-${index}`} className="scout-context-row" title={file.name} aria-label={`Preview source ${file.name}`} onClick={() => onPreviewFile(file)}><ArtifactIcon step={file.name.endsWith('.py') ? 3 : /\.ya?ml$/.test(file.name) ? 2 : 0} /><span>{file.name}<small>{Math.ceil(file.size / 1024)} KB · Local source</small></span></button>)}
        <button type="button" className="scout-context-view-all" aria-label={showAll ? 'Show less' : 'View all'} aria-expanded={showAll} onClick={() => setShowAll((prev) => !prev)}>{showAll ? 'Show less' : 'View all'}<small>{sources.length + 2}</small></button>
      </ContextSection>
    </aside>

  </>;
}

function ContextSection({ title, action, children }: { title: string; action?: ReactNode; children: ReactNode }) {
  const [open, setOpen] = useState(true);
  return <section className="scout-context-section"><div className="scout-context-section-head"><button type="button" aria-expanded={open} onClick={() => setOpen((prev) => !prev)}>{title}</button>{action}</div><div className={`scout-context-section-content ${open ? 'is-expanded' : ''}`} aria-hidden={!open}><div>{children}</div></div></section>;
}

export function SourcePreview({ file, onClose }: { file: File; onClose: () => void }) {
  const [preview, setPreview] = useState({ text: '', url: '', error: '' });
  useEffect(() => {
    if (!file) return;
    let active = true;
    setPreview({ text: '', url: '', error: '' });
    if (file.type.startsWith('image/')) {
      const url = URL.createObjectURL(file);
      setPreview({ text: '', url, error: '' });
      return () => URL.revokeObjectURL(url);
    }
    file.text().then((text) => { if (active) setPreview({ text, url: '', error: '' }); })
      .catch(() => { if (active) setPreview({ text: '', url: '', error: 'Unable to read this source file.' }); });
    return () => { active = false; };
  }, [file]);
  return <Modal open={Boolean(file)} title={file?.name} footer={null} onCancel={onClose} width={640} className="scout-source-dialog">{preview.error ? <p role="alert">{preview.error}</p> : preview.url ? <img src={preview.url} alt={file?.name} /> : <pre>{preview.text}</pre>}</Modal>;
}
