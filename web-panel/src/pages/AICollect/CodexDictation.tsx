import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react';
import { useThemeStore } from '@/stores/settings';
import { CodexToolbarButton, CodexTooltip } from './CodexToolbar';
import ScoutToolbarIcon from './ScoutToolbarIcon';
import CodexChangeIcon from './CodexChangeIcon';

type Action = 'insert' | 'send' | 'abort';
type Recognition = {
  lang: string; continuous: boolean; interimResults: boolean;
  onstart: (() => void) | null; onend: (() => void) | null;
  onerror: ((event: { error: string }) => void) | null;
  onresult: ((event: { results: ArrayLike<{ 0: { transcript: string } }> }) => void) | null;
  start: () => void; stop: () => void; abort: () => void;
};
type Session = { recognition: Recognition; prefix: string; text: string; action: Action; started: boolean; stream?: MediaStream; audio?: AudioContext; frame?: number };
type Props = { value: string; entered: boolean; onChange: (value: string) => void; onActiveChange: (active: boolean) => void; onSubmit: () => void };
export type DictationHandle = { stop: (action?: Action) => void };

// Port of Codex's dictation states and waveform/cancel/insert/send controls
// (103760, V/Y). Browser speech recognition supplies this app's transcript.
export default forwardRef<DictationHandle, Props>(function CodexDictation(props, ref) {
  const [phase, setPhase] = useState<'idle' | 'starting' | 'recording' | 'transcribing' | 'error'>('idle');
  const [error, setError] = useState('');
  const session = useRef<Session>();
  const canvas = useRef<HTMLCanvasElement>(null);
  const latest = useRef(props); latest.current = props;
  const language = useThemeStore(state => state.language);
  const t = (en: string, zh: string) => language === 'zh-CN' ? zh : en;
  const active = phase === 'starting' || phase === 'recording' || phase === 'transcribing';

  function release(current: Session) {
    if (current.frame !== undefined) cancelAnimationFrame(current.frame);
    current.frame = undefined;
    current.stream?.getTracks().forEach(track => track.stop()); current.stream = undefined;
    const audio = current.audio; current.audio = undefined;
    if (audio && audio.state !== 'closed') void audio.close();
  }
  function finish(current: Session, failure = '') {
    if (session.current !== current) return;
    session.current = undefined;
    release(current);
    latest.current.onActiveChange(false);
    if (failure) { setError(failure); setPhase('error'); return; }
    setPhase('idle');
    if (current.action === 'abort') return;
    if (!current.text.trim()) { setError(t('No speech was detected. Try again.', '未检测到语音，请重试。')); setPhase('error'); return; }
    const value = `${current.prefix}${current.prefix && !/\s$/.test(current.prefix) ? ' ' : ''}${current.text}`;
    latest.current.onChange(value.replace(latest.current.entered ? /\r/g : /[\r\n]+/g, ' '));
    if (current.action === 'send') requestAnimationFrame(() => latest.current.onSubmit());
  }
  function stop(action: Action = 'insert') {
    const current = session.current;
    if (!current) return;
    current.action = action;
    if (action === 'abort' || !current.started) {
      current.recognition.onend = null; current.recognition.onerror = null;
      if (current.started) current.recognition.abort();
      finish(current); return;
    }
    setPhase('transcribing');
    release(current);
    current.recognition.stop();
  }
  useImperativeHandle(ref, () => ({ stop }));

  async function start() {
    if (session.current) { stop(); return; }
    const browser = window as typeof window & { SpeechRecognition?: new () => Recognition; webkitSpeechRecognition?: new () => Recognition };
    const Engine = browser.SpeechRecognition ?? browser.webkitSpeechRecognition;
    if (!window.isSecureContext || !Engine || !navigator.mediaDevices?.getUserMedia) {
      setError(!window.isSecureContext ? t('Dictation requires HTTPS or localhost.', '听写需要 HTTPS 或 localhost。') : t('This browser does not support dictation. Use Chrome or Edge.', '此浏览器不支持听写，请使用 Chrome 或 Edge。'));
      setPhase('error'); return;
    }
    setError(''); setPhase('starting'); latest.current.onActiveChange(true);
    const recognition = new Engine();
    const current: Session = { recognition, prefix: latest.current.value, text: '', action: 'insert', started: false };
    session.current = current;
    recognition.lang = language === 'zh-CN' ? 'zh-CN' : 'en-US';
    recognition.continuous = true; recognition.interimResults = true;
    recognition.onresult = event => { if (session.current === current) current.text = Array.from(event.results, result => result[0].transcript).join(''); };
    recognition.onstart = () => { if (session.current === current) setPhase('recording'); };
    recognition.onend = () => finish(current);
    recognition.onerror = event => {
      const messages: Record<string, string> = {
        'not-allowed': t('Microphone access was denied.', '麦克风访问被拒绝。'),
        'audio-capture': t('No microphone is available.', '没有可用的麦克风。'),
        network: t('Transcription could not connect. Check your connection and retry.', '语音转写连接失败，请检查网络后重试。'),
        'no-speech': t('No speech was detected. Try again.', '未检测到语音，请重试。'),
      };
      recognition.onend = null; recognition.onerror = null; if (current.started) recognition.abort();
      finish(current, messages[event.error] ?? `${t('Dictation failed', '听写失败')}: ${event.error}`);
    };
    try {
      current.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (session.current !== current) { release(current); return; }
      current.audio = new AudioContext();
      const analyser = current.audio.createAnalyser(); analyser.fftSize = 256;
      current.audio.createMediaStreamSource(current.stream).connect(analyser);
      await current.audio.resume();
      if (session.current !== current) { release(current); return; }
      const samples = new Uint8Array(analyser.fftSize), levels: number[] = [];
      const draw = () => {
        if (session.current !== current) return;
        analyser.getByteTimeDomainData(samples);
        const level = Math.sqrt(samples.reduce((sum, sample) => sum + ((sample - 128) / 128) ** 2, 0) / samples.length);
        levels.push(level); if (levels.length > 80) levels.shift();
        const node = canvas.current, context = node?.getContext('2d');
        if (node && context) {
          node.width = Math.max(1, node.clientWidth * devicePixelRatio); node.height = 32 * devicePixelRatio;
          context.scale(devicePixelRatio, devicePixelRatio); context.clearRect(0, 0, node.clientWidth, 32);
          context.fillStyle = '#ededed';
          levels.forEach((amplitude, index) => { const height = Math.max(2, Math.min(30, amplitude * 120)); context.fillRect(node.clientWidth / 2 + (index - levels.length / 2) * 3, (32 - height) / 2, 2, height); });
        }
        current.frame = requestAnimationFrame(draw);
      };
      draw(); current.started = true; recognition.start();
    } catch (failure) { finish(current, failure instanceof Error ? failure.message : t('Could not start dictation.', '无法启动听写。')); }
  }
  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.shiftKey && event.code === 'KeyD' && !event.repeat) { event.preventDefault(); void start(); }
      else if (event.key === 'Escape' && session.current) { event.preventDefault(); event.stopPropagation(); stop('abort'); }
    };
    window.addEventListener('keydown', key, true);
    return () => window.removeEventListener('keydown', key, true);
  });
  useEffect(() => () => {
    const current = session.current; session.current = undefined;
    if (current) { current.recognition.onend = null; current.recognition.onerror = null; if (current.started) current.recognition.abort(); release(current); }
  }, []);

  const label = active ? t('Stop dictation', '停止听写') : phase === 'error' ? t('Retry dictation', '重试听写') : t('Dictate', '听写');
  return <>
    {!active && <CodexTooltip label={label} shortcut="Ctrl+Shift+D"><CodexToolbarButton className="scout-voice" aria-label={label} aria-pressed={active} onClick={() => void start()}>{phase === 'error' ? <CodexChangeIcon name="refresh" /> : <ScoutToolbarIcon name="microphone" />}</CodexToolbarButton></CodexTooltip>}
    {error && <div className="codex-dictation-error" role="alert">{error}</div>}
    {active && <div className="codex-dictation-controls" role="group" aria-label={t('Dictation', '听写')}>
      <CodexTooltip label={t('Cancel dictation', '取消听写')} shortcut="Esc"><CodexToolbarButton aria-label={t('Cancel dictation', '取消听写')} onClick={() => stop('abort')}><ScoutToolbarIcon name="close" /></CodexToolbarButton></CodexTooltip>
      <div className="codex-dictation-waveform">{phase === 'recording' ? <><span className="sr-only" role="status">{t('Dictation is active and in use', '正在听写')}</span><canvas ref={canvas} aria-label={t('Microphone waveform', '麦克风波形')} /></> : <span role="status">{phase === 'starting' ? t('Starting dictation…', '正在启动听写…') : t('Transcribing', '正在转写')}</span>}</div>
      <CodexTooltip label={t('Stop dictation', '停止听写')}><CodexToolbarButton aria-label={t('Finish dictation', '完成听写')} disabled={phase !== 'recording'} onClick={() => stop('insert')}><ScoutToolbarIcon name="dictationStop" /></CodexToolbarButton></CodexTooltip>
      <CodexTooltip label={t('Transcribe and send', '转写并发送')}><button type="button" className="scout-send" aria-label={t('Transcribe and send', '转写并发送')} disabled={phase !== 'recording'} onClick={() => stop('send')}><ScoutToolbarIcon name="send" /></button></CodexTooltip>
    </div>}
  </>;
});
