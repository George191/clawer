import { useEffect, useRef, useState } from 'react';
import { Input } from '@/components/ui';
import { ArrowUpOutlined, AudioOutlined, CopyOutlined, GlobalOutlined, StopOutlined } from '@/components/ui/icons';
import { DOT_ACTIVITIES, type DotActivity } from '@/components/NativeDot';
import './chatPreview.css';

const steps: Array<{ activity: DotActivity; title: string; detail: string }> = [
  { activity: 'Thinking', title: 'Understanding the request', detail: 'Organize the goal, scope, and expected result.' },
  { activity: 'Searching', title: 'Planning source discovery', detail: 'Outline where to look and which sources to consider.' },
  { activity: 'Working', title: 'Reviewing the approach', detail: 'Consider fields, page structure, and collection boundaries.' },
  { activity: 'Creating', title: 'Drafting a collection outline', detail: 'Bring the source, fields, and schedule into one plan.' },
  { activity: 'NeedsInput', title: 'Preparing questions', detail: 'Identify the details that need your confirmation.' },
  { activity: 'Ready', title: 'Ready to review', detail: 'Review the outline and describe what to change.' },
];
const labels: Record<DotActivity, string> = { None: 'Idle', Working: 'Working', Searching: 'Searching', Creating: 'Creating', Payment: 'Payment', Thinking: 'Thinking', NeedsInput: 'Needs input', Ready: 'Ready', Paused: 'Paused', Error: 'Error' };
const outlineReply = 'A collection outline is ready to review. Tell me which sources, fields, and schedule you have in mind, and we can shape the next step together.';
type Turn = { id: number; prompt: string; step: number; reply: string; complete: boolean; vote?: 'up' | 'down' };

export default function ChatPreview({ entered, userName, onEnter }: { entered: boolean; userName: string; onEnter: () => void }) {
  const [draft, setDraft] = useState('');
  const [turns, setTurns] = useState<Turn[]>([]);
  const [activity, setActivity] = useState<DotActivity>('Paused');
  const [running, setRunning] = useState(false);
  const [copied, setCopied] = useState<number | null>(null);
  const [speaking, setSpeaking] = useState<number | null>(null);
  const [notice, setNotice] = useState('');
  const nextId = useRef(0);
  const input = useRef<HTMLInputElement>(null);
  const thread = useRef<HTMLDivElement>(null);
  const follow = useRef(true);
  const latest = turns[turns.length - 1];

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
  useEffect(() => () => { window.speechSynthesis?.cancel(); }, []);

  function submit() {
    if (running) { setRunning(false); setActivity('Paused'); return; }
    const prompt = draft;
    setDraft('');
    setTurns((prev) => [...prev, { id: ++nextId.current, prompt, step: 0, reply: '', complete: false }]);
    setActivity('Thinking');
    setRunning(true);
    follow.current = true;
    onEnter();
    input.current?.focus();
  }
  async function copy(turn: Turn) {
    try { await navigator.clipboard.writeText(turn.reply); setCopied(turn.id); setNotice('Copied to clipboard'); }
    catch { setNotice('Clipboard is unavailable. Select the response to copy it.'); }
  }
  function vote(id: number, value: 'up' | 'down') {
    setTurns((prev) => prev.map((turn) => turn.id === id ? { ...turn, vote: turn.vote === value ? undefined : value } : turn));
  }
  function read(turn: Turn) {
    window.speechSynthesis.cancel();
    if (speaking === turn.id) { setSpeaking(null); return; }
    const utterance = new SpeechSynthesisUtterance(turn.reply);
    utterance.lang = 'en-US';
    utterance.onend = () => setSpeaking(null);
    utterance.onerror = () => { setSpeaking(null); setNotice('Read aloud could not start.'); };
    setSpeaking(turn.id);
    window.speechSynthesis.speak(utterance);
  }

  return <section className={`scout-chat-preview ${entered ? 'is-entered' : ''}`} aria-label="Scout conversation">
    <div className={`ai-chat-dot ${!entered ? 'is-landing' : ''}`} data-activity={activity} />
    <h1 className="scout-chat-greeting ai-prompt-title" aria-hidden={entered}>Hi{userName ? <>, <span className="ai-prompt-name">{userName}</span></> : null}, any new ideas?</h1>
    {entered && <details className="scout-activity-menu"><summary aria-label="Dot activity">···</summary><div role="group" aria-label="Dot states">{(Object.keys(DOT_ACTIVITIES) as DotActivity[]).map((value) => <button key={value} type="button" aria-pressed={activity === value} onClick={() => { setRunning(false); setActivity(value); }}>{labels[value]}</button>)}</div></details>}
    <div ref={thread} className="scout-chat-messages" role="log" aria-label="Conversation" aria-live="polite" onScroll={() => { const node = thread.current; if (node) follow.current = node.scrollHeight - node.scrollTop - node.clientHeight < 80; }}>
      {turns.map((turn) => <article className="scout-chat-turn" key={turn.id}>
        <div className="scout-user-message">{turn.prompt}</div>
        <div className="scout-assistant-message">
          <details className="scout-worked" open={!turn.complete}>
            <summary><span className={`scout-step-orb ${running && turn.id === latest?.id ? 'is-active' : ''}`} />{turn.complete ? 'Worked through 6 steps' : turn.step === steps.length - 1 ? 'Writing response' : steps[turn.step].title}<span className="scout-step-chevron">⌄</span></summary>
            <ol>{steps.slice(0, turn.step + 1).map((step, index) => <li key={step.activity}><button type="button" onClick={() => { setRunning(false); setActivity(step.activity); }}><span className="scout-step-mark">{index < turn.step || turn.complete ? '✓' : '·'}</span><span>{step.title}<small>{step.detail}</small></span></button></li>)}</ol>
          </details>
          {turn.reply && <p className="scout-reply">{turn.reply}</p>}
          {turn.complete && <><div className="scout-response-actions" aria-label="Response actions">
            <button type="button" title={copied === turn.id ? 'Copied' : 'Copy'} aria-label="Copy response" onClick={() => void copy(turn)}>{copied === turn.id ? <span>✓</span> : <CopyOutlined />}</button>
            <button type="button" title="Good response" aria-label="Like response" aria-pressed={turn.vote === 'up'} onClick={() => vote(turn.id, 'up')}><FeedbackIcon /></button>
            <button type="button" title="Bad response" aria-label="Dislike response" aria-pressed={turn.vote === 'down'} onClick={() => vote(turn.id, 'down')}><FeedbackIcon down /></button>
            <button type="button" title={'speechSynthesis' in window ? 'Read aloud' : 'Read aloud is unavailable in this browser'} aria-label={speaking === turn.id ? 'Stop reading' : 'Read aloud'} aria-pressed={speaking === turn.id} disabled={!('speechSynthesis' in window)} onClick={() => read(turn)}><svg viewBox="0 0 24 24"><path d="M11 5 6 9H3v6h3l5 4V5Zm4 3a6 6 0 0 1 0 8m3-11a10 10 0 0 1 0 14" /></svg></button>
          </div></>}
        </div>
      </article>)}
      {entered && activity === 'Paused' && latest && !latest.complete && <button type="button" className="scout-resume" onClick={() => { setActivity(steps[latest.step].activity); setRunning(true); }}>Continue</button>}
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
  return <svg viewBox="0 0 24 24" style={down ? { transform: 'rotate(180deg)' } : undefined}><path d="M7 10v10H3V10h4Zm0 0 5-7c2 0 2 2 1 6h6c2 0 2 2 1 5l-2 6H7" /></svg>;
}
