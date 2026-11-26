import { useEffect, useRef, useState } from 'react';
import { Dropdown } from '@/components/ui';
import { DOT_ACTIVITIES, type DotActivity } from '@/components/NativeDot';
import { useThemeStore } from '@/stores/settings';
import PreviewResults, { previewAdapter, previewTemplate } from './PreviewResults';
import { ArtifactIcon } from './StageArtifacts';
import ConversationContext, { SourcePreview } from './ConversationContext';
import ChatComposer, { type ComposerField, type QueuedMessage } from './ChatComposer';
import CodexReplyIcon from './CodexReplyIcon';
import './chatPreview.css';

const steps: Array<{ activity: DotActivity; title: string; detail: string }> = [
  { activity: 'Thinking', title: 'Understanding the request', detail: 'Organize the goal, scope, and expected result.' },
  { activity: 'Searching', title: 'Analyzing the webpage', detail: 'Inspect page structure, pagination, and candidate data sources.' },
  { activity: 'Working', title: 'Generating the template', detail: 'Map source fields and extraction rules into template.yaml.' },
  { activity: 'Working', title: 'Writing the adapter', detail: 'Draft adapter.py with extraction, pagination, and normalization.' },
  { activity: 'Working', title: 'Reviewing the collection plan', detail: 'Review the confirmed template, adapter, and sample output together.' },
  { activity: 'Ready', title: 'Validating the adapter', detail: 'Review sample output, required fields, and collection readiness.' },
];
const labels: Record<DotActivity, string> = { None: 'Idle', Working: 'Working', Searching: 'Searching', Creating: 'Creating', Payment: 'Payment', Thinking: 'Thinking', NeedsInput: 'Needs input', Ready: 'Ready', Paused: 'Paused', Error: 'Error' };
const outlineReply = 'The example template and adapter are ready in the results panel. Review template.yaml, adapter.py, and the sample data, or describe what you would like to change.';
type Turn = { id: number; prompt: string; files: File[]; step: number; reply: string; template: string; adapter: string; done: number; waiting?: 'template' | 'adapter'; complete: boolean; vote?: 'up' | 'down' };

export default function ChatPreview({ entered, userName, userLoading, onEnter }: { entered: boolean; userName: string; userLoading: boolean; onEnter: () => void }) {
  const [draft, setDraft] = useState('');
  const [turns, setTurns] = useState<Turn[]>([]);
  const [activity, setActivity] = useState<DotActivity>('Paused');
  const [running, setRunning] = useState(false);
  const [pendingFiles, setPendingFiles] = useState<File[]>([]);
  const [sourceFiles, setSourceFiles] = useState<File[]>([]);
  const [selectedSource, setSelectedSource] = useState<File | null>(null);
  const [queued, setQueued] = useState<QueuedMessage[]>([]);
  const [composerHeight, setComposerHeight] = useState(108);
  const [contextOpen, setContextOpen] = useState(false);
  const [resultTurnId, setResultTurnId] = useState<number | null>(null);
  const [resultStep, setResultStep] = useState<number | undefined>();
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
  const input = useRef<ComposerField>(null);
  const nextQueuedId = useRef(0);
  const thread = useRef<HTMLDivElement>(null);
  const follow = useRef(true);
  const latest = turns[turns.length - 1];
  const resultTurn = turns.find((turn) => turn.id === resultTurnId) ?? latest;
  const dotActivity = running ? activity : speaking !== null ? 'Working' : speechError ? 'Error' : activity;
  const history = turns.flatMap((turn) => [
    { key: `turn-${turn.id}`, turn: turn.id, target: `scout-turn-${turn.id}`, step: 0, title: turn.prompt || 'Message' },
    ...steps.slice(0, turn.step + 1).map((step, index) => ({ key: `step-${turn.id}-${index}`, turn: turn.id, target: `scout-step-${turn.id}-${index}`, step: index, title: step.title })),
  ]);
  const currentHistory = selectedHistory ?? history[history.length - 1]?.key;

  useEffect(() => { setResultStep(undefined); setResultTurnId(null); }, [latest?.id, latest?.step]);
  useEffect(() => {
    const wide = window.matchMedia('(min-width: 1280px)');
    const onChange = () => { if (!wide.matches) setContextOpen(false); };
    wide.addEventListener('change', onChange);
    return () => wide.removeEventListener('change', onChange);
  }, []);

  useEffect(() => {
    if (!running || !latest) return;
    const writing = latest.step === steps.length - 1;
    const artifact = latest.step === 2 ? 'template' : latest.step === 3 ? 'adapter' : null;
    const timer = window.setTimeout(() => {
      if (artifact) {
        const source = artifact === 'template' ? previewTemplate : previewAdapter;
        // The historical editor pumps a character queue, rather than replacing complete lines.
        const content = source.slice(0, latest[artifact].length + 4);
        const complete = content.length === source.length;
        setTurns((prev) => prev.map((turn) => turn.id === latest.id ? { ...turn, [artifact]: content, done: complete ? latest.step + 1 : turn.done, waiting: complete ? artifact : undefined } : turn));
        if (complete) { setActivity('NeedsInput'); setRunning(false); }
      } else if (writing) {
        const reply = outlineReply.slice(0, latest.reply.length + 4);
        const complete = reply.length === outlineReply.length;
        setTurns((prev) => prev.map((turn) => turn.id === latest.id ? { ...turn, reply, complete, done: complete ? steps.length : turn.done } : turn));
        if (complete) { setRunning(false); setActivity('Ready'); }
      } else {
        const step = latest.step + 1;
        setTurns((prev) => prev.map((turn) => turn.id === latest.id ? { ...turn, step, done: step } : turn));
        setActivity(step === steps.length - 1 ? 'Working' : steps[step].activity);
      }
    }, artifact ? 48 : writing ? 44 : latest.step === 0 ? 1800 : latest.step === 1 ? 4200 : 2600);
    return () => window.clearTimeout(timer);
  }, [running, latest?.id, latest?.step, latest?.reply, latest?.template, latest?.adapter]);
  useEffect(() => {
    if (running || !latest?.complete || !queued.length) return;
    const next = queued[0];
    setQueued((prev) => prev.filter((item) => item.id !== next.id));
    startTurn(next.prompt, next.files, false);
  }, [running, latest?.id, latest?.complete, queued]);
  useEffect(() => {
    if (follow.current) thread.current?.scrollTo({ top: thread.current.scrollHeight, behavior: 'smooth' });
  }, [turns, entered, composerHeight, contextOpen]);
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
  function startTurn(prompt: string, files: File[] = pendingFiles, clearComposer = true) {
    stopReading();
    if (clearComposer) { setDraft(''); setPendingFiles([]); }
    const id = ++nextId.current;
    setTurns((prev) => [...prev, { id, prompt, files, step: 0, reply: '', template: '', adapter: '', done: 0, complete: false }]);
    setActivity('Thinking');
    setRunning(true);
    if (!entered) setContextOpen(window.innerWidth >= 1280);
    setResultTurnId(null);
    setResultStep(undefined);
    setSelectedHistory(null);
    follow.current = true;
    onEnter();
    if (clearComposer) input.current?.focus();
  }
  function selectActivity(value: DotActivity) {
    stopReading();
    setResultStep(undefined);
    setActivity(value);
    const index = steps.findIndex((step) => step.activity === value);
    const executing = ['Thinking', 'Searching', 'Working', 'Creating'].includes(value);
    setRunning(executing && Boolean(latest));
    if (!latest) return;
    setTurns((prev) => prev.map((turn) => turn.id !== latest.id ? turn : {
      ...turn, step: value === 'Ready' ? 5 : value === 'NeedsInput' ? 2 : index >= 0 ? index : turn.step,
      done: value === 'Ready' ? 6 : value === 'NeedsInput' ? 3 : index >= 0 ? index : turn.done,
      waiting: value === 'NeedsInput' ? 'template' : undefined,
      reply: value === 'Ready' ? outlineReply : '',
      template: value === 'NeedsInput' ? previewTemplate : index < 0 ? turn.template : index > 2 ? previewTemplate : '',
      adapter: index < 0 ? turn.adapter : index > 3 ? previewAdapter : '',
      complete: value === 'Ready', vote: undefined,
    }));
  }
  function resume() {
    if (!latest) return;
    stopReading();
    const step = latest.waiting ? latest.step + 1 : latest.step;
    setTurns((prev) => prev.map((turn) => turn.id === latest.id ? { ...turn, step, waiting: undefined } : turn));
    setActivity(step === 5 ? 'Working' : steps[step].activity);
    setRunning(true);
  }
  function jumpHistory(entry: typeof history[number]) {
    setSelectedHistory(entry.key);
    setResultTurnId(entry.turn);
    setResultStep(entry.step);
    setReveal((prev) => ({ turn: entry.turn, sequence: prev.sequence + 1 }));
    follow.current = false;
    window.clearTimeout(historyTimer.current);
    historyTimer.current = window.setTimeout(() => {
      const node = document.getElementById(entry.target);
      const scroll = thread.current;
      if (node && scroll) scroll.scrollTo({ top: scroll.scrollTop + node.getBoundingClientRect().top - scroll.getBoundingClientRect().top - 16, behavior: 'smooth' });
    }, window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 260);
  }

  function pauseResponse() { stopReading(); setRunning(false); setActivity('Paused'); }
  function addSourceFiles(files: File[]) {
    setSourceFiles((prev) => [...prev, ...files]);
    setPendingFiles((prev) => [...prev, ...files]);
  }
  function removePendingFile(file: File) {
    setPendingFiles((prev) => prev.filter((item) => item !== file));
    if (!turns.some((turn) => turn.files.includes(file)) && !queued.some((item) => item.files.includes(file))) setSourceFiles((prev) => prev.filter((item) => item !== file));
  }
  function submit() {
    if (running) {
      const id = ++nextQueuedId.current;
      setQueued((prev) => [...prev, { id, prompt: draft, files: pendingFiles }]);
      setDraft(''); setPendingFiles([]); input.current?.focus();
      return;
    }
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

  return <section className={`scout-chat-preview ${entered ? 'is-entered' : ''} ${entered ? 'has-results' : ''} ${entered && contextOpen ? 'has-context' : ''}`} aria-label="Scout conversation" style={{ '--scout-composer-height': `${composerHeight}px` } as React.CSSProperties}>
    <div className={`ai-chat-dot ${!entered ? 'is-landing' : ''}`} data-activity={dotActivity} />
    {entered && <div className="scout-chat-dot-frost" aria-hidden="true" />}
    <h1 className="scout-chat-greeting ai-prompt-title" aria-hidden={entered}>Hi{userLoading || userName ? <>, <span className="scout-greeting-name">{userLoading ? <span className="native-dot-loading scout-profile-loading" role="status" aria-label="Loading your profile"><i /><i /><i /></span> : <span key={userName} className="ai-prompt-name scout-loaded-name">{userName}</span>}</span></> : null}, any new ideas?</h1>
    <aside id="scout-collection-results" className="scout-collection-results" aria-label="Collection results" aria-hidden={!entered}>
      <PreviewResults turn={resultTurn} step={resultStep} running={running && resultTurn?.id === latest?.id} />
    </aside>
    {entered && <div className="scout-activity-menu"><Dropdown trigger={['click']} placement="bottomRight" overlayClassName="scout-dot-state-menu" menu={{ selectedKeys: [activity], items: (Object.keys(DOT_ACTIVITIES) as DotActivity[]).map((value) => ({ key: value, label: labels[value] })), onClick: ({ key }) => selectActivity(key as DotActivity) }}><button type="button" aria-label="Dot activity"><CodexReplyIcon name="more" /></button></Dropdown></div>}
    {entered && <aside className="scout-history-rail" aria-label="Conversation history" onMouseLeave={() => setHoveredHistory(null)} onBlur={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node)) setHoveredHistory(null); }}>
      <div className="ai-session-stage-bars">{history.map((entry, index) => {
        const distance = hoveredHistory === null ? -1 : Math.abs(index - hoveredHistory);
        const width = distance === 0 ? 30 : distance === 1 ? 18 : distance === 2 ? 11 : 6;
        return <button key={entry.key} type="button" className={`ai-session-stage-bar ${currentHistory === entry.key ? 'is-active' : ''}`} aria-label={entry.title} title={entry.title} aria-current={currentHistory === entry.key ? 'step' : undefined} onMouseEnter={() => setHoveredHistory(index)} onFocus={() => setHoveredHistory(index)} onClick={() => jumpHistory(entry)} style={{ '--ai-stage-bar-width': `${width}px`, '--ai-stage-bar-height': distance === 0 ? '1.8px' : '1.4px', '--ai-stage-bar-color': distance === 0 ? '#fff' : currentHistory === entry.key ? '#D9E0EA' : 'rgba(204,211,221,.38)' } as React.CSSProperties}><span /></button>;
      })}</div>
    </aside>}
    <div ref={thread} className="scout-chat-messages" role="log" aria-label="Conversation" aria-live="polite" onScroll={() => { const node = thread.current; if (node) follow.current = node.scrollHeight - node.scrollTop - node.clientHeight < 80; }}>
      {turns.map((turn) => <article id={`scout-turn-${turn.id}`} className="scout-chat-turn" key={turn.id}>
        <div className="scout-user-message">{turn.prompt}{turn.files.length > 0 && <div className="scout-user-files">{turn.files.map((file, index) => <button type="button" key={`${file.name}-${index}`} aria-label={`Preview sent attachment ${file.name}`} onClick={() => setSelectedSource(file)}><ArtifactIcon step={/\.py$/i.test(file.name) ? 3 : /\.ya?ml$/i.test(file.name) ? 2 : 0} /><span>{file.name}</span></button>)}</div>}</div>
        <div className="scout-assistant-message">
          <StepProgress turn={turn} running={running && turn.id === latest?.id} current={turn.id === latest?.id} activity={activity} expandSignal={reveal.turn === turn.id ? reveal.sequence : 0} />
          {turn.reply && <p className="scout-reply">{turn.reply}</p>}
          {turn.complete && <ReleasePreview />}
          {turn.complete && <><div className="scout-response-actions" aria-label="Response actions">
            <button type="button" title={copied === turn.id ? 'Copied' : 'Copy'} aria-label="Copy response" onClick={() => void copy(turn)}><CodexReplyIcon name={copied === turn.id ? 'copied' : 'copy'} /></button>
            <Dropdown trigger={['click']} placement="bottomLeft" overlayClassName="scout-feedback-menu" menu={{ selectedKeys: turn.vote ? [turn.vote] : [], items: [{ key: 'up', label: goodLabel, icon: <FeedbackIcon /> }, { key: 'down', label: badLabel, icon: <FeedbackIcon down /> }], onClick: ({ key }) => vote(turn.id, key as 'up' | 'down') }}>
              <button type="button" title={turn.vote === 'up' ? goodLabel : turn.vote === 'down' ? badLabel : 'Rate response'} aria-label="Rate response" aria-pressed={Boolean(turn.vote)}><CodexReplyIcon name={turn.vote === 'up' ? 'upSelected' : turn.vote === 'down' ? 'downSelected' : 'feedback'} /></button>
            </Dropdown>
            <button type="button" title={'speechSynthesis' in window ? 'Read aloud' : 'Read aloud is unavailable in this browser'} aria-label={speaking === turn.id ? 'Stop reading' : 'Read aloud'} aria-pressed={speaking === turn.id} disabled={!('speechSynthesis' in window)} onClick={() => read(turn)}><CodexReplyIcon name="speaker" /></button>
            <Dropdown trigger={['click']} placement="bottomLeft" overlayClassName="scout-response-more-menu" menu={{ items: [{ key: 'steps', label: 'Show steps' }, { key: 'replay', label: 'Replay response' }], onClick: ({ key }) => { if (key === 'steps') jumpHistory(history.find((entry) => entry.key === `step-${turn.id}-0`)!); else startTurn(turn.prompt, turn.files, false); } }}><button type="button" aria-label="More response actions" title="More"><CodexReplyIcon name="more" /></button></Dropdown>
          </div></>}
        </div>
      </article>)}
      {entered && !running && latest && !latest.complete && <div className="scout-interruption" role="status">
        <span>{activity === 'Error' ? 'Response interrupted.' : latest.waiting === 'template' ? 'Review the template before starting adapter generation.' : latest.waiting === 'adapter' ? 'Review the adapter before preparing the collection plan.' : activity === 'NeedsInput' ? 'Waiting for your confirmation.' : activity === 'Payment' ? 'Waiting for confirmation.' : activity === 'None' ? 'Ready when you are.' : 'Response paused.'}</span>
        <button type="button" className="scout-resume" onClick={resume}>{activity === 'Error' ? 'Retry' : latest.waiting === 'template' ? 'Confirm Template' : latest.waiting === 'adapter' ? 'Confirm Adapter' : 'Continue'}</button>
      </div>}
    </div>
    {entered && <><button type="button" className="scout-context-toggle" aria-label="Conversation context" title="Conversation context" aria-expanded={contextOpen} aria-controls="scout-conversation-context" onClick={() => setContextOpen((prev) => !prev)}><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="3" /><path d="M15 4v16" /></svg></button><ConversationContext open={contextOpen} turn={resultTurn} steps={steps} running={running && resultTurn?.id === latest?.id} activity={labels[activity]} onClose={() => { setContextOpen(false); document.querySelector<HTMLButtonElement>('.scout-context-toggle')?.focus(); }} onView={(artifact) => { setResultTurnId(artifact.turn); setResultStep(artifact.step); }} onPause={pauseResponse} sources={sourceFiles} onAddFiles={addSourceFiles} onPreviewFile={setSelectedSource} /></>}
    <ChatComposer ref={input} entered={entered} value={draft} running={running} files={pendingFiles} queued={queued} onChange={setDraft} onSubmit={submit} onPause={pauseResponse} onAddFiles={addSourceFiles} onRemoveFile={removePendingFile} onPreviewFile={setSelectedSource} onViewSources={() => { if (!entered) onEnter(); setContextOpen(true); }} onHeightChange={setComposerHeight} onEditQueued={(item) => { setQueued((prev) => prev.filter((value) => value.id !== item.id)); setDraft(item.prompt); setPendingFiles(item.files); input.current?.focus(); }} onDeleteQueued={(id) => setQueued((prev) => prev.filter((item) => item.id !== id))} onSendQueued={(item) => { setQueued((prev) => prev.filter((value) => value.id !== item.id)); startTurn(item.prompt, item.files, false); }} />
    {selectedSource && <SourcePreview file={selectedSource} onClose={() => setSelectedSource(null)} />}
    <span className="scout-action-notice" role="status">{notice}</span>
  </section>;
}

function FeedbackIcon({ down = false }: { down?: boolean }) {
  return <CodexReplyIcon name={down ? 'down' : 'up'} />;
}
function StepProgress({ turn, running, current, activity, expandSignal }: { turn: Turn; running: boolean; current: boolean; activity: DotActivity; expandSignal: number }) {
  const [open, setOpen] = useState(!turn.complete);
  useEffect(() => { setOpen(!turn.complete); }, [turn.complete]);
  useEffect(() => { if (expandSignal) setOpen(true); }, [expandSignal]);
  const title = turn.complete ? 'Worked through 6 steps' : !current ? 'Response stopped' : !running ? labels[activity] : turn.step === steps.length - 1 ? 'Writing response' : steps[turn.step].title;
  return <section className={`scout-worked ${open ? 'is-open' : ''}`}>
    <button type="button" className="scout-worked-toggle" aria-expanded={open} aria-controls={`scout-steps-${turn.id}`} onClick={() => setOpen((value) => !value)}><span className={`scout-step-orb ${running ? 'is-active' : ''}`} /><span>{title}</span><span className="scout-step-chevron"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="m4 6 4 4 4-4" /></svg></span></button>
    <div id={`scout-steps-${turn.id}`} className="scout-step-content" aria-hidden={!open}><div className="scout-step-content-inner"><ol>{steps.slice(0, turn.step + 1).map((step, index) => <li id={`scout-step-${turn.id}-${index}`} key={step.title}><span className="scout-step-mark">{index < turn.done ? '✓' : '·'}</span><span>{step.title}<small>{step.detail}</small>{index === 3 && <AdapterBuildProgress code={turn.adapter} complete={turn.done > 3} />}</span></li>)}</ol></div></div>
  </section>;
}

function AdapterBuildProgress({ code, complete }: { code: string; complete: boolean }) {
  const fraction = code.length / previewAdapter.length;
  const current = complete ? 3 : fraction < .25 ? 0 : fraction < .8 ? 1 : 2;
  const [expanded, setExpanded] = useState<number | null>(current);
  useEffect(() => { setExpanded(current < 3 ? current : null); }, [current]);
  const plan = [
    { title: 'GET request contract', detail: 'Bind the confirmed source, request inputs, and page contract.' },
    { title: '2 field mapping', detail: 'Normalize title and URL, check required values, and deduplicate records.' },
    { title: 'Finalize adapter.py', detail: 'Complete the parser entry and review the generated file.' },
  ];
  return <div className="scout-adapter-milestones">{plan.map((item, index) => <section key={item.title} className={expanded === index ? 'is-expanded' : ''}><button type="button" aria-expanded={expanded === index} onClick={() => setExpanded((prev) => prev === index ? null : index)}><span>{index < current ? '✓' : index === current ? '·' : '○'}</span><span>{item.title}</span><small>{index < current ? 'Completed' : index === current ? 'Writing' : 'Queued'}</small></button><div className="scout-build-details"><div><p>{item.detail}</p></div></div></section>)}</div>;
}

function ReleasePreview() {
  const [action, setAction] = useState('publish');
  const [createTask, setCreateTask] = useState(true);
  const [prepared, setPrepared] = useState(false);
  return <section className="scout-release-preview" aria-label="Release options"><div role="group" aria-label="Template action">{['draft', 'archive', 'publish'].map((item) => <button key={item} type="button" aria-pressed={action === item} onClick={() => { setAction(item); setPrepared(false); }}>{item === 'draft' ? 'Draft' : item === 'archive' ? 'Archive' : 'Publish'}</button>)}</div>{action === 'publish' && <label><input type="checkbox" checked={createTask} onChange={(event) => { setCreateTask(event.target.checked); setPrepared(false); }} />Create collection task</label>}<button type="button" className="scout-resume" onClick={() => setPrepared(true)}>{action === 'draft' ? 'Save Draft' : action === 'archive' ? 'Archive Template' : createTask ? 'Publish & Create Task' : 'Publish Template'}</button><small role="status">{prepared ? 'Example release prepared. No template was published and no task was created.' : 'Product preview · release actions are simulated.'}</small></section>;
}
