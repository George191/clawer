import { forwardRef, useEffect, useImperativeHandle, useLayoutEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Dropdown, Input } from '@/components/ui';
import { ArrowUpOutlined, AudioOutlined, StopOutlined, DownOutlined, EditOutlined, GlobalOutlined } from '@/components/ui/icons';
import { ArtifactIcon } from './StageArtifacts';

export type ComposerField = HTMLInputElement | HTMLTextAreaElement;
export type QueuedMessage = { id: number; prompt: string; files: File[] };
type Props = {
  entered: boolean; value: string; running: boolean; files: File[]; queued: QueuedMessage[];
  onChange: (value: string) => void; onSubmit: () => void; onPause: () => void;
  onAddFiles: (files: File[]) => void; onRemoveFile: (file: File) => void; onPreviewFile: (file: File) => void;
  onViewSources: () => void; onHeightChange: (height: number) => void;
  onEditQueued: (item: QueuedMessage) => void; onDeleteQueued: (id: number) => void; onSendQueued: (item: QueuedMessage) => void;
};

export default forwardRef<ComposerField, Props>(function ChatComposer(props, ref) {
  const { entered, value, running, files, queued, onChange, onSubmit, onPause, onAddFiles, onRemoveFile, onPreviewFile, onViewSources, onHeightChange, onEditQueued, onDeleteQueued, onSendQueued } = props;
  const form = useRef<HTMLFormElement>(null);
  const field = useRef<ComposerField | null>(null);
  const picker = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [reasoning, setReasoning] = useState('High');
  const navigate = useNavigate();
  useImperativeHandle(ref, () => field.current!, [entered]);
  useEffect(() => { if (entered) field.current?.focus(); }, [entered]);
  useLayoutEffect(() => {
    if (!(field.current instanceof HTMLTextAreaElement)) return;
    field.current.style.height = '0px';
    field.current.style.height = `${Math.min(180, field.current.scrollHeight)}px`;
  }, [value, entered]);
  useLayoutEffect(() => {
    let previousWidth = -1;
    const observer = new ResizeObserver(() => {
      if (!form.current) return;
      const width = form.current.getBoundingClientRect().width;
      if (Math.abs(width - previousWidth) > .5 && field.current instanceof HTMLTextAreaElement) {
        previousWidth = width;
        field.current.style.height = '0px';
        field.current.style.height = `${Math.min(180, field.current.scrollHeight)}px`;
      }
      onHeightChange(Math.ceil(form.current.getBoundingClientRect().height));
    });
    observer.observe(form.current!);
    return () => observer.disconnect();
  }, [onHeightChange]);
  const onKeyDown = (event: React.KeyboardEvent<ComposerField>) => {
    if (event.key !== 'Enter') return;
    if (event.nativeEvent.isComposing) { event.preventDefault(); return; }
    if (entered && event.shiftKey) return;
    event.preventDefault(); onSubmit();
  };
  return <form ref={form} className={`scout-persistent-composer ${entered ? 'is-chat' : ''} ${dragging ? 'is-dragging' : ''}`} onSubmit={(event) => { event.preventDefault(); onSubmit(); }} onDragOver={(event) => { if (event.dataTransfer.types.includes('Files')) { event.preventDefault(); setDragging(true); } }} onDragLeave={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node)) setDragging(false); }} onDrop={(event) => { setDragging(false); if (event.dataTransfer.files.length) { event.preventDefault(); onAddFiles(Array.from(event.dataTransfer.files)); } }}>
      <input ref={picker} type="file" aria-label="Composer files" accept=".txt,.md,.yaml,.yml,.json,.py,image/*" multiple hidden onChange={(event) => { onAddFiles(Array.from(event.target.files ?? [])); event.target.value = ''; }} />
    {queued.length > 0 && <div className="scout-queued-messages" aria-label="Queued messages">{queued.map((item) => <div key={item.id} className="scout-queued-message"><span title={item.prompt}>Queued · {item.prompt || `${item.files.length} files`}</span><button type="button" aria-label="Edit queued message" title="Edit" onClick={() => onEditQueued(item)}><EditOutlined /></button><button type="button" aria-label="Send queued message now" title="Send now" onClick={() => onSendQueued(item)}><ArrowUpOutlined /></button><button type="button" aria-label="Delete queued message" title="Delete" onClick={() => onDeleteQueued(item.id)}>×</button></div>)}</div>}
    {files.length > 0 && <div className="scout-composer-attachments" aria-label="Message attachments">{files.map((file, index) => <div className="scout-composer-attachment" key={`${file.name}-${index}`}><button type="button" aria-label={`Preview attachment ${file.name}`} onClick={() => onPreviewFile(file)}><FileThumbnail file={file} /><span>{file.name}</span></button><button type="button" aria-label={`Remove attachment ${file.name}`} onClick={() => onRemoveFile(file)}>×</button></div>)}</div>}
    <div className="scout-composer-body" onPaste={(event) => { const pasted = Array.from(event.clipboardData.files); if (pasted.length) { event.preventDefault(); onAddFiles(pasted); } }}>
      {!entered && <span className="scout-composer-leading" aria-hidden="true"><GlobalOutlined /></span>}
      {entered ? <textarea ref={(node) => { field.current = node; }} className="scout-message-textarea" aria-label="Message Scout" placeholder="Ask for changes or describe a collection" rows={1} value={value} onChange={(event) => onChange(event.target.value)} onKeyDown={onKeyDown} /> : <Input ref={(node) => { field.current = node; }} className="scout-message-input" aria-label="Message Scout" placeholder="Ask anything or describe what you want to collect" value={value} onChange={(event) => onChange(event.target.value.replace(/[\r\n]+/g, ''))} onKeyDown={onKeyDown} />}
      {!entered && <><button type="button" className="scout-voice" aria-label="Voice input" title="Voice input is unavailable in this preview" disabled><AudioOutlined /></button><button type="submit" className="scout-send" aria-label="Send message"><ArrowUpOutlined /></button></>}
    </div>
    {entered && <div className="scout-composer-footer">
      <Dropdown trigger={['click']} placement="topLeft" menu={{ items: [{ key: 'files', label: 'Add files and photos' }, { key: 'sources', label: 'View sources' }], onClick: ({ key }) => key === 'files' ? picker.current?.click() : onViewSources() }}><button type="button" className="scout-composer-add" aria-label="Add files and more" title="Add files and more">+</button></Dropdown>
      <Dropdown trigger={['click']} placement="topLeft" menu={{ selectedKeys: ['model'], items: [{ key: 'model', label: 'GPT-6 Sol' }, { key: 'settings', label: 'Model settings' }], onClick: ({ key }) => { if (key === 'settings') navigate('/settings'); } }}><button type="button" className="scout-composer-model" aria-label="Preview model" title="Preview model">GPT-6 Sol <DownOutlined /></button></Dropdown>
      <Dropdown trigger={['click']} placement="topLeft" menu={{ selectedKeys: [reasoning], items: ['Low', 'Medium', 'High', 'XHigh', 'Max'].map((value) => ({ key: value, label: value })), onClick: ({ key }) => setReasoning(key) }}><button type="button" className="scout-composer-reasoning" aria-label="Preview reasoning effort" title="Preview reasoning effort">{reasoning}<DownOutlined /></button></Dropdown>
      <span className="scout-composer-spacer" />
      <button type="button" className="scout-voice" aria-label="Voice input" title="Voice input is unavailable in this preview" disabled><AudioOutlined /></button>
      {running ? <button type="button" className="scout-send is-stop" aria-label="Stop response" title="Stop response" onClick={onPause}><StopOutlined /></button> : <button type="submit" className="scout-send" aria-label="Send message" title={entered ? 'Send · Enter' : 'Send message'}><ArrowUpOutlined /></button>}
    </div>}
  </form>;
});

function FileThumbnail({ file }: { file: File }) {
  const [url, setUrl] = useState('');
  useEffect(() => { if (!file.type.startsWith('image/')) return; const value = URL.createObjectURL(file); setUrl(value); return () => URL.revokeObjectURL(value); }, [file]);
  return url ? <img src={url} alt="" /> : <ArtifactIcon step={/\.py$/i.test(file.name) ? 3 : /\.ya?ml$/i.test(file.name) ? 2 : 0} />;
}
