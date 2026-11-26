import { useEffect, useRef, useState } from 'react';
import { Dropdown, Input } from '@/components/ui';
import { ArrowUpOutlined, AudioOutlined, CopyOutlined, GlobalOutlined, StopOutlined } from '@/components/ui/icons';
import { DOT_ACTIVITIES, type DotActivity } from '@/components/NativeDot';
import { useThemeStore } from '@/stores/settings';
import './chatPreview.css';

const steps: Array<{ activity: DotActivity; title: string; detail: string }> = [
  { activity: 'Thinking', title: 'Understanding the request', detail: 'Organize the goal, scope, and expected result.' },
  { activity: 'Searching', title: 'Analyzing the webpage', detail: 'Inspect page structure, pagination, and candidate data sources.' },
  { activity: 'Working', title: 'Mapping collection fields', detail: 'Map source fields, extraction rules, and the output schema.' },
  { activity: 'Creating', title: 'Writing the adapter', detail: 'Draft adapter.py with extraction, pagination, and normalization.' },
  { activity: 'NeedsInput', title: 'Reviewing the collection plan', detail: 'Confirm the source, fields, and adapter approach before validation.' },
  { activity: 'Ready', title: 'Validating the adapter', detail: 'Review sample output, required fields, and collection readiness.' },
];
const labels: Record<DotActivity, string> = { None: 'Idle', Working: 'Working', Searching: 'Searching', Creating: 'Creating', Payment: 'Payment', Thinking: 'Thinking', NeedsInput: 'Needs input', Ready: 'Ready', Paused: 'Paused', Error: 'Error' };
const feedbackPath = 'M7 10H4a1 1 0 0 0-1 1v8a1 1 0 0 0 1 1h3m0-10 4-7a2.4 2.4 0 0 1 2.2 2.8L12.5 10h6a1.7 1.7 0 0 1 1.6 2.2l-2 6.5a1.8 1.8 0 0 1-1.7 1.3H7V10Z';
const outlineReply = 'A collection outline is ready to review. Tell me which sources, fields, and schedule you have in mind, and we can shape the next step together.';
type Turn = { id: number; prompt: string; step: number; reply: string; complete: boolean; vote?: 'up' | 'down' };

export default function ChatPreview({ entered, userName, onEnter }: { entered: boolean; userName: string; onEnter: () => void }) {
  const [draft, setDraft] = useState('');
  const [turns, setTurns] = useState<Turn[]>([]);
  const [activity, setActivity] = useState<DotActivity>('Paused');
  const [running, setRunning] = useState(false);
  const [progressOpen, setProgressOpen] = useState(false);
  const [copied, setCopied] = useState<number | null>(null);
  const [speaking, setSpeaking] = useState<number | null>(null);
  const [notice, setNotice] = useState('');
  const [speechError, setSpeechError] = useState(false);
  const [hoveredHistory, setHoveredHistory] = useState<number | null>(null);
  const [selectedHistory, setSelectedHistory] = useState<string | null>(null);
  const [reveal, setReveal] = useState({ turn: 0, sequence: 0 });
  const language = useThemeStore((state) => state.language);
  const goodLabel = language === 'zh-CN' ? '回复优秀' : 'Good response';
  const badLabel = language === 'zh-CN' ? '回复不佳' : 'Bad response';
  const utteranceRef = useRef<SpeechSynthesisUtterance | null>(null);
  const historyTimer = useRef<number>();
  const nextId = useRef(0);
  const input = useRef<HTMLInputElement>(null);
  const thread = useRef<HTMLDivElement>(null);
  const follow = useRef(true);
  const latest = turns[turns.length - 1];
  const dotActivity = running ? activity : speaking !== null ? 'Working' : speechError ? 'Error' : activity;
  const history = turns.flatMap((turn) => [
    { key: `turn-${turn.id}`, turn: turn.id, target: `scout-turn-${turn.id}`, title: turn.prompt || 'Message' },
    ...steps.slice(0, turn.step + 1).map((step, index) => ({ key: `step-${turn.id}-${index}`, turn: turn.id, target: `scout-step-${turn.id}-${index}`, title: step.title })),
  ]);
  const currentHistory = selectedHistory ?? history[history.length - 1]?.key;

  useEffect(() => {
    if (!running || !latest) return;
    const writing = latest.step === steps.length - 1;
    const timer = window.setTimeout(() => {
      if (writing) {
        const reply = outlineReply.slice(0, latest.reply.length + 4);
        const complete = reply.length === outlineReply.length;
        setTurns((prev) => prev.map((turn) => turn.id === latest.id ? { ...turn, reply, complete } : turn));
        if (complete) { setRunning(false); setActivity('Ready'); }
      } else {
        const step = latest.step + 1;
        setTurns((prev) => prev.map((turn) => turn.id === latest.id ? { ...turn, step } : turn));
        setActivity(step === steps.length - 1 ? 'Creating' : steps[step].activity);
        if (step === 4) setRunning(false);
      }
    }, writing ? 28 : 1100);
    return () => window.clearTimeout(timer);
  }, [running, latest?.id, latest?.step, latest?.reply]);
  useEffect(() => {
    if (follow.current) thread.current?.scrollTo({ top: thread.current.scrollHeight, behavior: 'smooth' });
  }, [turns, entered]);
  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => { setNotice(''); setCopied(null); }, 1800);
    return () => window.clearTimeout(timer);
  }, [notice]);
  useEffect(() => () => {
    window.clearTimeout(historyTimer.current);
    utteranceRef.current = null;
    window.speechSynthesis?.cancel();
  }, []);

  function stopReading() {
    utteranceRef.current = null;
    window.speechSynthesis?.cancel();
    setSpeaking(null);
    setSpeechError(false);
  }
  function startTurn(prompt: string) {
    stopReading();
    setDraft('');
    const id = ++nextId.current;
    setTurns((prev) => [...prev, { id, prompt, step: 0, reply: '', complete: false }]);
    setActivity('Thinking');
    setRunning(true);
    setProgressOpen(true);
    setSelectedHistory(null);
    follow.current = true;
    onEnter();
    input.current?.focus();
  }
  function selectActivity(value: DotActivity) {
    stopReading();
    setActivity(value);
    const index = steps.findIndex((step) => step.activity === value);
    const executing = ['Thinking', 'Searching', 'Working', 'Creating'].includes(value);
    setRunning(executing && Boolean(latest));
    if (!latest) return;
    setTurns((prev) => prev.map((turn) => turn.id !== latest.id ? turn : {
      ...turn, step: value === 'Ready' ? 5 : index >= 0 ? index : turn.step,
      reply: value === 'Ready' ? outlineReply : '', complete: value === 'Ready', vote: undefined,
    }));
  }
  function resume() {
    if (!latest) return;
    stopReading();
    const step = latest.step === 4 ? 5 : latest.step;
    setTurns((prev) => prev.map((turn) => turn.id === latest.id ? { ...turn, step } : turn));
    setActivity(step === 5 ? 'Creating' : steps[step].activity);
    setRunning(true);
  }
  function jumpHistory(entry: typeof history[number]) {
    setSelectedHistory(entry.key);
    setReveal((prev) => ({ turn: entry.turn, sequence: prev.sequence + 1 }));
    follow.current = false;
    window.clearTimeout(historyTimer.current);
    historyTimer.current = window.setTimeout(() => {
      const node = document.getElementById(entry.target);
      const scroll = thread.current;
      if (node && scroll) scroll.scrollTo({ top: scroll.scrollTop + node.getBoundingClientRect().top - scroll.getBoundingClientRect().top - 16, behavior: 'smooth' });
    }, window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 260);
  }

  function submit() {
    if (running) { stopReading(); setRunning(false); setActivity('Paused'); return; }
    startTurn(draft);
  }
  async function copy(turn: Turn) {
    try { await navigator.clipboard.writeText(turn.reply); setCopied(turn.id); setNotice('Copied to clipboard'); }
    catch { setCopied(null); setNotice('Clipboard is unavailable. Select the response to copy it.'); }
  }
  function vote(id: number, value: 'up' | 'down') {
    setTurns((prev) => prev.map((turn) => turn.id === id ? { ...turn, vote: turn.vote === value ? undefined : value } : turn));
  }
  function read(turn: Turn) {
    const stopping = speaking === turn.id;
    stopReading();
    if (stopping) return;
    const utterance = new SpeechSynthesisUtterance(turn.reply);
    utterance.lang = 'en-US';
    utteranceRef.current = utterance;
    utterance.onend = () => { if (utteranceRef.current === utterance) { utteranceRef.current = null; setSpeaking(null); } };
    utterance.onerror = (event) => {
      if (utteranceRef.current !== utterance) return;
      utteranceRef.current = null;
      setSpeaking(null);
      if (!['canceled', 'interrupted'].includes(event.error)) { setSpeechError(true); setNotice('Read aloud could not start.'); }
    };
    setSpeaking(turn.id);
    try { window.speechSynthesis.speak(utterance); }
    catch { utteranceRef.current = null; setSpeaking(null); setSpeechError(true); setNotice('Read aloud could not start.'); }
  }

  return <section className={`scout-chat-preview ${entered ? 'is-entered' : ''} ${entered && progressOpen ? 'has-progress' : ''}`} aria-label="Scout conversation">
    <div className={`ai-chat-dot ${!entered ? 'is-landing' : ''}`} data-activity={dotActivity} />
    <h1 className="scout-chat-greeting ai-prompt-title" aria-hidden={entered}>Hi{userName ? <>, <span className="ai-prompt-name">{userName}</span></> : null}, any new ideas?</h1>
    {entered && <button type="button" className="scout-progress-toggle" aria-label={progressOpen ? 'Hide live progress' : 'Show live progress'} aria-expanded={progressOpen} aria-controls="scout-live-progress" onClick={() => setProgressOpen((value) => !value)}><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="3" /><path d="M9 4v16m4-11h4m-4 6h4" /></svg></button>}
    <aside id="scout-live-progress" className="scout-live-progress" aria-label="Live collection progress" aria-hidden={!entered || !progressOpen}>
      <header><h2>Live progress</h2><span className="scout-preview-badge">Preview</span></header>
      <div className="scout-progress-body">
        {latest && <><p className="scout-progress-request">{latest.prompt || 'Collection request'}</p><div className="scout-progress-summary" role="status"><span className={`scout-step-orb ${running ? 'is-active' : ''}`} /><span>{latest.complete ? 'Ready to review' : labels[activity]}</span><span>{latest.complete ? steps.length : latest.step} / {steps.length}</span></div>
          <ol className="scout-progress-timeline">{steps.map((step, index) => {
            const done = latest.complete || index < latest.step;
            const active = !latest.complete && index === latest.step;
            return <li key={step.title} className={done ? 'is-done' : active ? 'is-current' : 'is-pending'} aria-current={active ? 'step' : undefined}><span className="scout-progress-mark">{done ? '✓' : index + 1}</span><div><h3>{step.title}</h3><p>{step.detail}</p><small>{done ? 'Completed' : active ? running ? 'In progress' : labels[activity] : 'Queued'}</small></div></li>;
          })}</ol>
          <p className="scout-progress-preview-note">Workflow preview · No webpages are fetched or adapter files written.</p>
        </>}
      </div>
    </aside>
    {entered && <div className="scout-activity-menu"><Dropdown trigger={['click']} placement="bottomRight" overlayClassName="scout-dot-state-menu" menu={{ selectedKeys: [activity], items: (Object.keys(DOT_ACTIVITIES) as DotActivity[]).map((value) => ({ key: value, label: labels[value] })), onClick: ({ key }) => selectActivity(key as DotActivity) }}><button type="button" aria-label="Dot activity"><MoreIcon /></button></Dropdown></div>}
    {entered && <aside className="scout-history-rail" aria-label="Conversation history" onMouseLeave={() => setHoveredHistory(null)} onBlur={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node)) setHoveredHistory(null); }}>
      <div className="ai-session-stage-bars">{history.map((entry, index) => {
        const distance = hoveredHistory === null ? -1 : Math.abs(index - hoveredHistory);
        const width = distance === 0 ? 30 : distance === 1 ? 18 : distance === 2 ? 11 : 6;
        return <button key={entry.key} type="button" className={`ai-session-stage-bar ${currentHistory === entry.key ? 'is-active' : ''}`} aria-label={entry.title} title={entry.title} aria-current={currentHistory === entry.key ? 'step' : undefined} onMouseEnter={() => setHoveredHistory(index)} onFocus={() => setHoveredHistory(index)} onClick={() => jumpHistory(entry)} style={{ '--ai-stage-bar-width': `${width}px`, '--ai-stage-bar-height': distance === 0 ? '1.8px' : '1.4px', '--ai-stage-bar-color': distance === 0 ? '#fff' : currentHistory === entry.key ? '#D9E0EA' : 'rgba(204,211,221,.38)' } as React.CSSProperties}><span /></button>;
      })}</div>
    </aside>}
    <div ref={thread} className="scout-chat-messages" role="log" aria-label="Conversation" aria-live="polite" onScroll={() => { const node = thread.current; if (node) follow.current = node.scrollHeight - node.scrollTop - node.clientHeight < 80; }}>
      {turns.map((turn) => <article id={`scout-turn-${turn.id}`} className="scout-chat-turn" key={turn.id}>
        <div className="scout-user-message">{turn.prompt}</div>
        <div className="scout-assistant-message">
          <StepProgress turn={turn} running={running && turn.id === latest?.id} current={turn.id === latest?.id} activity={activity} expandSignal={reveal.turn === turn.id ? reveal.sequence : 0} />
          {turn.reply && <p className="scout-reply">{turn.reply}</p>}
          {turn.complete && <><div className="scout-response-actions" aria-label="Response actions">
            <button type="button" title={copied === turn.id ? 'Copied' : 'Copy'} aria-label="Copy response" onClick={() => void copy(turn)}>{copied === turn.id ? <span>✓</span> : <CopyOutlined />}</button>
            <Dropdown trigger={['click']} placement="bottomLeft" overlayClassName="scout-feedback-menu" menu={{ selectedKeys: turn.vote ? [turn.vote] : [], items: [{ key: 'up', label: goodLabel, icon: <FeedbackIcon /> }, { key: 'down', label: badLabel, icon: <FeedbackIcon down /> }], onClick: ({ key }) => vote(turn.id, key as 'up' | 'down') }}>
              <button type="button" title={turn.vote === 'up' ? goodLabel : turn.vote === 'down' ? badLabel : 'Rate response'} aria-label="Rate response" aria-pressed={Boolean(turn.vote)}>{turn.vote ? <FeedbackIcon down={turn.vote === 'down'} /> : <FeedbackControlIcon />}</button>
            </Dropdown>
            <button type="button" title={'speechSynthesis' in window ? 'Read aloud' : 'Read aloud is unavailable in this browser'} aria-label={speaking === turn.id ? 'Stop reading' : 'Read aloud'} aria-pressed={speaking === turn.id} disabled={!('speechSynthesis' in window)} onClick={() => read(turn)}><svg viewBox="0 0 24 24"><path d="M11 5 6 9H3v6h3l5 4V5Zm4 3a6 6 0 0 1 0 8m3-11a10 10 0 0 1 0 14" /></svg></button>
            <Dropdown trigger={['click']} placement="bottomLeft" overlayClassName="scout-response-more-menu" menu={{ items: [{ key: 'steps', label: 'Show steps' }, { key: 'replay', label: 'Replay response' }], onClick: ({ key }) => { if (key === 'steps') jumpHistory(history.find((entry) => entry.key === `step-${turn.id}-0`)!); else startTurn(turn.prompt); } }}><button type="button" aria-label="More response actions" title="More"><MoreIcon /></button></Dropdown>
          </div></>}
        </div>
      </article>)}
      {entered && !running && latest && !latest.complete && <div className="scout-interruption" role="status">
        <span>{activity === 'Error' ? 'Response interrupted.' : activity === 'NeedsInput' ? 'Confirm the outline to continue.' : activity === 'Payment' ? 'Waiting for confirmation.' : activity === 'None' ? 'Ready when you are.' : 'Response paused.'}</span>
        <button type="button" className="scout-resume" onClick={resume}>{activity === 'Error' ? 'Retry' : activity === 'NeedsInput' ? 'Confirm and continue' : 'Continue'}</button>
      </div>}
    </div>
    <form className="scout-persistent-composer" onSubmit={(event) => { event.preventDefault(); submit(); }}>
      <span className="scout-composer-leading" aria-hidden="true"><GlobalOutlined /></span>
      <Input ref={input} className="scout-message-input" aria-label="Message Scout" placeholder={entered ? 'Message Scout…' : 'Ask anything or describe what you want to collect'} value={draft} onChange={(event) => setDraft(event.target.value.replace(/[\r\n]+/g, ''))} onKeyDown={(event) => { if (event.key === 'Enter' && event.nativeEvent.isComposing) event.preventDefault(); }} />
      <button type="button" className="scout-voice" aria-label="Voice input" title="Voice input" disabled><AudioOutlined /></button>
      <button type="submit" className={`scout-send ${running ? 'is-stop' : ''}`} aria-label={running ? 'Stop response' : 'Send message'}>{running ? <StopOutlined /> : <ArrowUpOutlined />}</button>
    </form>
    <span className="scout-action-notice" role="status">{notice}</span>
  </section>;
}

function FeedbackIcon({ down = false }: { down?: boolean }) {
  return <svg viewBox="0 0 24 24" style={down ? { transform: 'rotate(180deg)' } : undefined}><path d={feedbackPath} /></svg>;
}

function MoreIcon() {
  return <svg viewBox="0 0 24 24" className="scout-more-icon" aria-hidden="true"><circle cx="5" cy="12" r="1.3" /><circle cx="12" cy="12" r="1.3" /><circle cx="19" cy="12" r="1.3" /></svg>;
}
function FeedbackControlIcon() {
  return <svg viewBox="0 0 28 24" className="scout-feedback-control" aria-hidden="true"><path d={feedbackPath} transform="translate(0 0) scale(.68)" vectorEffect="non-scaling-stroke" /><path d={feedbackPath} transform="translate(28 24) rotate(180) scale(.68)" vectorEffect="non-scaling-stroke" /></svg>;
}
function StepProgress({ turn, running, current, activity, expandSignal }: { turn: Turn; running: boolean; current: boolean; activity: DotActivity; expandSignal: number }) {
  const [open, setOpen] = useState(!turn.complete);
  useEffect(() => { setOpen(!turn.complete); }, [turn.complete]);
  useEffect(() => { if (expandSignal) setOpen(true); }, [expandSignal]);
  const title = turn.complete ? 'Worked through 6 steps' : !current ? 'Response stopped' : !running ? labels[activity] : turn.step === steps.length - 1 ? 'Writing response' : steps[turn.step].title;
  return <section className={`scout-worked ${open ? 'is-open' : ''}`}>
    <button type="button" className="scout-worked-toggle" aria-expanded={open} aria-controls={`scout-steps-${turn.id}`} onClick={() => setOpen((value) => !value)}><span className={`scout-step-orb ${running ? 'is-active' : ''}`} /><span>{title}</span><span className="scout-step-chevron"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="m4 6 4 4 4-4" /></svg></span></button>
    <div id={`scout-steps-${turn.id}`} className="scout-step-content" aria-hidden={!open}><div className="scout-step-content-inner"><ol>{steps.slice(0, turn.step + 1).map((step, index) => <li id={`scout-step-${turn.id}-${index}`} key={step.activity}><span className="scout-step-mark">{index < turn.step || turn.complete ? '✓' : '·'}</span><span>{step.title}<small>{step.detail}</small></span></li>)}</ol></div></div>
  </section>;
}
