import { forwardRef, useEffect, useImperativeHandle, useLayoutEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Input } from '@/components/ui';
import { ArrowUpOutlined, EditOutlined } from '@/components/ui/icons';
import { useThemeStore } from '@/stores/settings';
import { ArtifactIcon } from './StageArtifacts';
import ScoutToolbarIcon from './ScoutToolbarIcon';
import { CodexToolbarButton, CodexToolbarMenu } from './CodexToolbar';

export type ComposerField = HTMLInputElement | HTMLTextAreaElement;
export type QueuedMessage = { id: number; prompt: string; files: File[] };

const landingPrompts = [
  'any new ideas?',
  'what shall we collect?',
  'which site should we explore?',
  'what data do you need?',
  'have a link to explore?',
  'what should we discover?',
];
type VoiceRecognition = {
  lang: string; continuous: boolean; interimResults: boolean;
  onresult: ((event: { results: ArrayLike<{ 0: { transcript: string } }> }) => void) | null;
  onerror: ((event: { error: string }) => void) | null; onend: (() => void) | null;
  start: () => void; stop: () => void; abort: () => void;
};
type Props = {
  userName: string; placeholderReady: boolean;
  entered: boolean; value: string; running: boolean; files: File[]; queued: QueuedMessage[];
  onChange: (value: string) => void; onSubmit: () => void; onPause: () => void;
  onAddFiles: (files: File[]) => void; onRemoveFile: (file: File) => void; onPreviewFile: (file: File) => void;
  onViewSources: () => void; onHeightChange: (height: number) => void;
  onEditQueued: (item: QueuedMessage) => void; onDeleteQueued: (id: number) => void; onSendQueued: (item: QueuedMessage) => void;
};

export default forwardRef<ComposerField, Props>(function ChatComposer(props, ref) {
  const { userName, placeholderReady, entered, value, running, files, queued, onChange, onSubmit, onPause, onAddFiles, onRemoveFile, onPreviewFile, onViewSources, onHeightChange, onEditQueued, onDeleteQueued, onSendQueued } = props;
  const form = useRef<HTMLFormElement>(null);
  const field = useRef<ComposerField | null>(null);
  const picker = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [placeholderLength, setPlaceholderLength] = useState(0);
  const [placeholderPrompt, setPlaceholderPrompt] = useState(landingPrompts[0]);
  useEffect(() => {
    if (!entered) setPlaceholderPrompt(landingPrompts[Math.floor(Math.random() * landingPrompts.length)]);
  }, [entered]);
  const placeholderPrefix = userName ? 'Hi, ' : 'Hi';
  const placeholderText = `${placeholderPrefix}${userName}, ${placeholderPrompt}`;
  useEffect(() => {
    setPlaceholderLength(0);
    if (entered || !placeholderReady) return;
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches || document.documentElement.dataset.reducedMotion === 'true') {
      setPlaceholderLength(placeholderText.length);
      return;
    }
    const timer = setInterval(() => setPlaceholderLength(length => {
      if (length + 1 >= placeholderText.length) clearInterval(timer);
      return Math.min(length + 1, placeholderText.length);
    }), 65);
    return () => clearInterval(timer);
  }, [entered, placeholderReady, placeholderText]);
  const [listening, setListening] = useState(false);
  const [voiceError, setVoiceError] = useState('');
  const recognition = useRef<VoiceRecognition | null>(null);
  const language = useThemeStore(state => state.language);
  useEffect(() => () => {
    const active = recognition.current;
    recognition.current = null;
    if (active) { active.onresult = null; active.onerror = null; active.onend = null; active.abort(); }
  }, []);
  const toggleVoice = () => {
    if (recognition.current) { recognition.current.stop(); return; }
    setVoiceError('');
    const browser = window as typeof window & { SpeechRecognition?: new () => VoiceRecognition; webkitSpeechRecognition?: new () => VoiceRecognition };
    const Recognition = browser.SpeechRecognition ?? browser.webkitSpeechRecognition;
    if (!Recognition || !window.isSecureContext) {
      setVoiceError(!window.isSecureContext ? 'Voice input requires HTTPS or localhost.' : 'This browser does not support voice input. Use Chrome or Edge.');
      return;
    }
    const active = new Recognition();
    const prefix = value;
    active.lang = language === 'zh-CN' ? 'zh-CN' : 'en-US';
    active.continuous = true;
    active.interimResults = true;
    active.onresult = event => {
      if (recognition.current !== active) return;
      const text = Array.from(event.results, result => result[0].transcript).join('');
      onChange(`${prefix}${prefix && text && !/\s$/.test(prefix) ? ' ' : ''}${text}`.replace(entered ? /\r/g : /[\r\n]+/g, ' '));
    };
    active.onerror = event => {
      if (recognition.current !== active) return;
      const messages: Record<string, string> = { 'not-allowed': 'Microphone access was denied. Allow microphone access and try again.', 'audio-capture': 'No microphone is available.', network: 'Voice transcription could not connect. Check your connection and try again.', 'no-speech': 'No speech was detected. Try again.' };
      setVoiceError(messages[event.error] ?? `Voice input failed: ${event.error}`);
    };
    active.onend = () => {
      if (recognition.current !== active) return;
      recognition.current = null; setListening(false); field.current?.focus();
    };
    recognition.current = active;
    try { active.start(); setListening(true); }
    catch (error) { recognition.current = null; setListening(false); setVoiceError(error instanceof Error ? error.message : 'Voice input could not start.'); }
  };
  const submit = () => { if (recognition.current) { recognition.current.stop(); return; } onSubmit(); };
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
    event.preventDefault(); submit();
  };
  const addControl = <CodexToolbarMenu menu={{ items: [{ key: 'files', label: 'Add files and photos' }, { key: 'sources', label: 'View sources' }, { key: 'agents', label: 'Manage agents' }], onClick: ({ key }) => { if (key === 'files') picker.current?.click(); else if (key === 'agents') navigate('/agents'); else onViewSources(); } }}><CodexToolbarButton className="scout-composer-add" aria-label="Add files and more" title="Add files and more"><ScoutToolbarIcon name="plus" /></CodexToolbarButton></CodexToolbarMenu>;
  const voiceControl = <CodexToolbarButton className="scout-voice" aria-label={listening ? 'Stop voice input' : 'Voice input'} title={listening ? 'Stop voice input' : 'Voice input'} aria-pressed={listening} onClick={toggleVoice}><ScoutToolbarIcon name="microphone" /></CodexToolbarButton>;
  return <form ref={form} className={`scout-persistent-composer ${entered ? 'is-chat' : ''} ${dragging ? 'is-dragging' : ''}`} onSubmit={(event) => { event.preventDefault(); submit(); }} onDragOver={(event) => { if (event.dataTransfer.types.includes('Files')) { event.preventDefault(); setDragging(true); } }} onDragLeave={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node)) setDragging(false); }} onDrop={(event) => { setDragging(false); if (event.dataTransfer.files.length) { event.preventDefault(); onAddFiles(Array.from(event.dataTransfer.files)); } }}>
    {voiceError && <div className="scout-voice-status" role="alert">{voiceError}</div>}
    {listening && <div className="scout-voice-status" role="status">Listening… Click the microphone to stop.</div>}
      <input ref={picker} type="file" aria-label="Composer files" accept=".txt,.md,.yaml,.yml,.json,.py,image/*" multiple hidden onChange={(event) => { onAddFiles(Array.from(event.target.files ?? [])); event.target.value = ''; }} />
    {queued.length > 0 && <div className="scout-queued-messages" aria-label="Queued messages">{queued.map((item) => <div key={item.id} className="scout-queued-message"><span title={item.prompt}>Queued · {item.prompt || `${item.files.length} files`}</span><button type="button" aria-label="Edit queued message" title="Edit" onClick={() => onEditQueued(item)}><EditOutlined /></button><button type="button" aria-label="Send queued message now" title="Send now" onClick={() => onSendQueued(item)}><ArrowUpOutlined /></button><button type="button" aria-label="Delete queued message" title="Delete" onClick={() => onDeleteQueued(item.id)}>×</button></div>)}</div>}
    {files.length > 0 && <div className="scout-composer-attachments" aria-label="Message attachments">{files.map((file, index) => <div className="scout-composer-attachment" key={`${file.name}-${index}`}><button type="button" aria-label={`Preview attachment ${file.name}`} onClick={() => onPreviewFile(file)}><FileThumbnail file={file} /><span>{file.name}</span></button><button type="button" aria-label={`Remove attachment ${file.name}`} onClick={() => onRemoveFile(file)}>×</button></div>)}</div>}
    <div className="scout-composer-body" onPaste={(event) => { const pasted = Array.from(event.clipboardData.files); if (pasted.length) { event.preventDefault(); onAddFiles(pasted); } }}>
      {!entered && addControl}
      {entered ? <textarea ref={(node) => { field.current = node; }} className="scout-message-textarea" aria-label="Message Scout" placeholder="Ask for changes or describe a collection" rows={1} value={value} readOnly={listening} onChange={(event) => onChange(event.target.value)} onKeyDown={onKeyDown} /> : <div className="scout-landing-field"><Input ref={(node) => { field.current = node; }} className="scout-message-input" aria-label="Message Scout" aria-description={placeholderText} placeholder="" value={value} readOnly={listening} onChange={(event) => onChange(event.target.value.replace(/[\r\n]+/g, ''))} onKeyDown={onKeyDown} />{!value && <span className="scout-landing-placeholder" aria-hidden="true">{placeholderPrefix.slice(0, placeholderLength)}<span className="ai-prompt-name">{userName.slice(0, Math.max(0, placeholderLength - placeholderPrefix.length))}</span>{placeholderText.slice(placeholderPrefix.length + userName.length, placeholderLength)}{placeholderReady && placeholderLength < placeholderText.length && <span className="scout-placeholder-caret" />}</span>}</div>}
      {!entered && <>{voiceControl}<button type="submit" className="scout-send" aria-label="Send message" disabled={!value.trim() && !files.length}><ScoutToolbarIcon name="send" /></button></>}
    </div>
    {entered && <div className="scout-composer-footer">
      {addControl}
      <span className="scout-composer-spacer" />
      {voiceControl}
      {running ? <button type="button" className="scout-send is-stop" aria-label="Stop response" title="Stop response" onClick={onPause}><ScoutToolbarIcon name="stop" /></button> : <button type="submit" className="scout-send" aria-label="Send message" title="Send · Enter" disabled={!value.trim() && !files.length}><ScoutToolbarIcon name="send" /></button>}
    </div>}
  </form>;
});

function FileThumbnail({ file }: { file: File }) {
  const [url, setUrl] = useState('');
  useEffect(() => { if (!file.type.startsWith('image/')) return; const value = URL.createObjectURL(file); setUrl(value); return () => URL.revokeObjectURL(value); }, [file]);
  return url ? <img src={url} alt="" /> : <ArtifactIcon step={/\.py$/i.test(file.name) ? 3 : /\.ya?ml$/i.test(file.name) ? 2 : 0} />;
}
