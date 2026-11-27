import { useEffect, useRef, useState } from 'react';
import type { SelectedLineRange } from '@pierre/diffs';
import { useThemeStore } from '@/stores/settings';
import ScoutToolbarIcon from './ScoutToolbarIcon';

type Recognition = {
  lang: string; continuous: boolean; interimResults: boolean;
  onresult: ((event: { results: ArrayLike<{ 0: { transcript: string } }> }) => void) | null;
  onerror: ((event: { error: string }) => void) | null; onend: (() => void) | null;
  start: () => void; stop: () => void; abort: () => void;
};

// Ported from installed Codex 26.1120: ev / q6KF / en (diff comment editor).
// Desktop editor and dictation bridges are replaced with the web app's contracts.
export default function CodexCommentEditor({ userName, range, body, busy, error, onChange, onCancel, onSubmit }: {
  userName: string; range: SelectedLineRange; body: string; busy: boolean; error: string;
  onChange: (body: string) => void; onCancel: () => void; onSubmit: () => void;
}) {
  const zh = useThemeStore(state => state.language === 'zh-CN');
  const t = (en: string, cn: string) => zh ? cn : en;
  const input = useRef<HTMLTextAreaElement>(null);
  const recognition = useRef<Recognition | null>(null);
  const [listening, setListening] = useState(false);
  const [voiceError, setVoiceError] = useState('');
  const start = Math.min(range.start, range.end), end = Math.max(range.start, range.end);
  const side = range.side === 'deletions' ? 'L' : 'R';
  useEffect(() => { input.current?.focus({ preventScroll: true }); }, [range.start, range.end, range.side]);
  useEffect(() => () => {
    const active = recognition.current;
    recognition.current = null;
    if (active) { active.onresult = null; active.onerror = null; active.onend = null; active.abort(); }
  }, []);
  function toggleVoice() {
    if (recognition.current) { recognition.current.stop(); return; }
    setVoiceError('');
    const browser = window as typeof window & { SpeechRecognition?: new () => Recognition; webkitSpeechRecognition?: new () => Recognition };
    const Voice = browser.SpeechRecognition ?? browser.webkitSpeechRecognition;
    if (!Voice || !window.isSecureContext) {
      setVoiceError(t('Voice input requires HTTPS and a browser with speech recognition.', '语音输入需要 HTTPS 和支持语音识别的浏览器。')); return;
    }
    const active = new Voice(), prefix = body;
    active.lang = zh ? 'zh-CN' : 'en-US'; active.continuous = true; active.interimResults = true;
    active.onresult = event => {
      if (recognition.current !== active) return;
      const text = Array.from(event.results, result => result[0].transcript).join('');
      onChange(`${prefix}${prefix && text && !/\s$/.test(prefix) ? ' ' : ''}${text}`);
    };
    active.onerror = event => { if (recognition.current === active) setVoiceError(`${t('Voice input failed', '语音输入失败')}: ${event.error}`); };
    active.onend = () => { if (recognition.current === active) { recognition.current = null; setListening(false); input.current?.focus(); } };
    recognition.current = active;
    try { active.start(); setListening(true); }
    catch (error) { recognition.current = null; setListening(false); setVoiceError(String(error)); }
  }
  return <div className="codex-comment-slot">
    <form className="codex-comment-editor" aria-label={t('Leave a comment', '添加评论')} onSubmit={event => { event.preventDefault(); if (body.trim() && !busy && !listening) onSubmit(); }} onKeyDown={event => {
      if (event.nativeEvent.isComposing) return;
      if (event.key === 'Escape' && !busy) { event.preventDefault(); event.stopPropagation(); onCancel(); }
      if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) { event.preventDefault(); event.currentTarget.requestSubmit(); }
    }}>
      <header><span className="codex-comment-author"><span className="codex-comment-avatar" aria-hidden="true">{(userName || 'You').split(/\s+/).map(part => part[0]).join('').slice(0, 2).toUpperCase()}</span><span>{t('You', '你')}</span></span><span>{start === end ? t(`Local comment on line ${side}${end}`, `第 ${side}${end} 行的本地评论`) : t(`Local comment on lines ${side}${start} to ${side}${end}`, `第 ${side}${start} 至 ${side}${end} 行的本地评论`)}</span></header>
      <div className="codex-comment-body"><textarea ref={input} aria-label="Comment" placeholder={t('Add a comment…', '添加一条评论…')} value={body} disabled={busy || listening} onChange={event => onChange(event.target.value)} rows={2} /></div>
      {(error || voiceError) && <p role="alert">{error || voiceError}</p>}
      {listening && <p role="status">{t('Listening…', '正在聆听…')}</p>}
      <footer><button type="button" className="codex-comment-microphone" aria-label={listening ? 'Stop voice input' : 'Voice input'} aria-pressed={listening} disabled={busy} onClick={toggleVoice}><ScoutToolbarIcon name="microphone" /></button><div className="codex-comment-actions"><button type="button" disabled={busy} onClick={onCancel}>{t('Cancel', '取消')}</button><button type="submit" className="codex-comment-submit" aria-label="Add comment" disabled={!body.trim() || busy || listening}>{busy ? t('Saving…', '保存中…') : t('Comment', '评论')}</button></div></footer>
    </form>
  </div>;
}
