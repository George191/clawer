import { useEffect, useRef, useState } from 'react';
import { Modal } from '@/components/ui';
import { useThemeStore } from '@/stores/settings';
import { CodexSummarySection } from './CodexToolbar';
import { ArtifactIcon, type StageArtifact } from './StageArtifacts';
import ScoutToolbarIcon from './ScoutToolbarIcon';

type ContextTurn = { id: number; prompt: string; done: number; template: string; adapter: string };

export default function ConversationContext({ open, turn, onClose, onView, activePreview, sources, onAddFiles, onPreviewFile }: { open: boolean; turn?: ContextTurn; onClose: () => void; onView: (artifact: StageArtifact) => void; activePreview?: string; sources: File[]; onAddFiles: (files: File[]) => void; onPreviewFile: (file: File) => void }) {
  const [showAll, setShowAll] = useState(false);
  const picker = useRef<HTMLInputElement>(null);
  const zh = useThemeStore(state => state.language === 'zh-CN');
  const files = turn ? [{ name: 'template.yaml', content: turn.template, step: 2 }, { name: 'adapter.py', content: turn.adapter, step: 3 }].filter(file => file.content) : [];
  const urls = [...new Set(turn?.prompt.match(/https?:\/\/[^\s<>()]+/g) ?? [])];
  return open ? <aside id="scout-conversation-context" className={`scout-context-panel ${open ? 'is-open' : ''}`} aria-label="Conversation context" onKeyDown={event => { if (event.key === 'Escape') { event.stopPropagation(); onClose(); } }}>
    {files.length > 0 && <CodexSummarySection title={zh ? '更改' : 'Changes'}>
      {files.map(file => <button type="button" key={file.name} className="scout-context-row" aria-label={`Open ${file.name}`} aria-pressed={activePreview === `${turn!.id}-${file.step}`} aria-controls="scout-collection-results" onClick={() => onView({ key: `${turn!.id}-${file.step}`, turn: turn!.id, step: file.step })}><ArtifactIcon step={file.step} /><span>{file.name}</span><b className="scout-context-added">+{file.content.trimEnd().split('\n').length}</b></button>)}
    </CodexSummarySection>}
    <CodexSummarySection title={zh ? '来源' : 'Sources'} action={<button type="button" className="scout-context-add" aria-label="Add source" title={zh ? '添加来源' : 'Add source'} onClick={() => picker.current?.click()}>+</button>}>
      <input ref={picker} type="file" aria-label="Source files" accept=".txt,.md,.yaml,.yml,.json,.py,image/*" multiple hidden onChange={event => { onAddFiles(Array.from(event.target.files ?? [])); event.target.value = ''; }} />
      {urls.map(url => <a key={url} className="scout-context-row" href={url} target="_blank" rel="noopener noreferrer"><ArtifactIcon step={1} /><span>{url}</span></a>)}
      {(showAll ? sources : sources.slice(0, 3)).map((file, index) => <button type="button" key={`${file.name}-${index}`} className="scout-context-row" title={file.name} aria-label={`Preview source ${file.name}`} onClick={() => onPreviewFile(file)}><SourceThumbnail file={file} /><span>{file.name}</span></button>)}
      {sources.length > 0 && <button type="button" className="scout-context-view-all" aria-label={showAll ? 'Show less' : 'View all'} aria-expanded={showAll} onClick={() => setShowAll(value => !value)}><ScoutToolbarIcon name="link" /><span>{showAll ? zh ? '收起' : 'Show less' : zh ? '查看全部' : 'View all'}</span></button>}
    </CodexSummarySection>
  </aside> : null;
}

function SourceThumbnail({ file }: { file: File }) {
  const [url, setUrl] = useState('');
  useEffect(() => {
    if (!file.type.startsWith('image/')) return;
    const value = URL.createObjectURL(file); setUrl(value);
    return () => URL.revokeObjectURL(value);
  }, [file]);
  return url ? <img src={url} alt="" /> : <ArtifactIcon step={file.name.endsWith('.py') ? 3 : /\.ya?ml$/.test(file.name) ? 2 : 0} />;
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
