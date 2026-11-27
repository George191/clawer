import { LoadingOrb } from '@/components/ui/loading';
import { useEffect, useRef, useState } from 'react';
import ScoutToolbarIcon from './ScoutToolbarIcon';
import { Dropdown, Modal } from '@/components/ui';
import useScoutChat from './useScoutChat';
import { CodexChatLibrary, CodexChatSchedule, CodexShareDialog } from './CodexChatDialogs';
import { chatUrl, markdown, restoreFiles, type SavedChat, type SavedTurn } from './chatStore';
import { DOT_ACTIVITIES, type DotActivity } from '@/components/NativeDot';
import { useThemeStore } from '@/stores/settings';
import { previewAdapter, previewTemplate } from './PreviewResults';
import PreviewWorkspace from './PreviewWorkspace';
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
type Turn = SavedTurn & { files: File[] };

export default function ChatPreview({ entered, userName, userLoading, onEnter }: { entered: boolean; userName: string; userLoading: boolean; onEnter: () => void }) {
  const [draft, setDraft] = useState('');
  const [turns, setTurns] = useState<Turn[]>([]);
  const [activity, setActivity] = useState<DotActivity>('Paused');
  const [landingActivity, setLandingActivity] = useState<DotActivity>('Paused');
  useEffect(() => {
    setLandingActivity('Paused');
    if (entered) return;
    let started = false;
    const timers: ReturnType<typeof setTimeout>[] = [];
    const start = () => {
      if (started) return;
      started = true;
      timers.push(setTimeout(() => setLandingActivity('None'), 900));
      timers.push(setTimeout(() => setLandingActivity('Working'), 1700));
    };
    const ready = (event: MessageEvent) => {
      if (event.origin === location.origin && event.source === document.querySelector<HTMLIFrameElement>('.scout-dot-host iframe')?.contentWindow && event.data?.type === 'helio-dot-status' && event.data.state === 'ready') start();
    };
    window.addEventListener('message', ready);
    if (document.querySelector('.scout-dot-host .native-dot-view[data-state="ready"]')) start();
    return () => { timers.forEach(clearTimeout); window.removeEventListener('message', ready); };
  }, [entered]);
  const [running, setRunning] = useState(false);
  const [pendingFiles, setPendingFiles] = useState<File[]>([]);
  const [sourceFiles, setSourceFiles] = useState<File[]>([]);
  const [selectedSource, setSelectedSource] = useState<File | null>(null);
  const [queued, setQueued] = useState<QueuedMessage[]>([]);
  const [composerHeight, setComposerHeight] = useState(108);
  const [contextOpen, setContextOpen] = useState(false);
  const [resultsOpen, setResultsOpen] = useState(true);
  const [selectionRequest, setSelectionRequest] = useState(0);
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
  const [libraryOpen, setLibraryOpen] = useState(false);
  const [shareOpen, setShareOpen] = useState(false);
  const [scheduleOpen, setScheduleOpen] = useState(false);
  const [renameOpen, setRenameOpen] = useState(false);
  const [chatName, setChatName] = useState('');
  const [confirmChat, setConfirmChat] = useState<'archive' | 'delete'>();
  const goodLabel = language === 'zh-CN' ? '回复优秀' : 'Good response';
  const localized = (en: string, zh: string) => language === 'zh-CN' ? zh : en;
  const badLabel = language === 'zh-CN' ? '回复不佳' : 'Bad response';
  const utteranceRef = useRef<SpeechSynthesisUtterance | null>(null);
  const historyTimer = useRef<number>();
  const nextId = useRef(0);
  const input = useRef<ComposerField>(null);
  const nextQueuedId = useRef(0);
  const thread = useRef<HTMLDivElement>(null);
  const follow = useRef(true);
  function loadChat(saved: SavedChat, refreshing: boolean) {
    if (refreshing) {
      setTurns(saved.turns.map(turn => ({ ...turn, files: restoreFiles(turn) })));
      return;
    }
    setRunning(false); stopReading(); setQueued([]); setDraft(''); setPendingFiles([]);
    const restored = saved.turns.map(turn => ({ ...turn, files: restoreFiles(turn) }));
    setTurns(restored); setSourceFiles(restored.flatMap(turn => turn.files));
    setSelectedSource(null); setSelectedHistory(null); setHoveredHistory(null); follow.current = true;
    nextId.current = Math.max(0, ...saved.turns.map(turn => turn.id));
    setActivity(saved.turns[saved.turns.length - 1]?.complete ? 'Ready' : 'Paused');
    setResultTurnId(null); setResultStep(undefined); setResultsOpen(true); setContextOpen(false);
    if (saved.turns.length) onEnter();
  }
  const chatStore = useScoutChat(turns, loadChat, setNotice);
  const chatSnapshot = { title: chatStore.chat.title, turns };
  async function chatAction(action: () => Promise<unknown>) {
    try { await action(); } catch (error) { setNotice(error instanceof Error ? error.message : 'Chat action failed'); }
  }
  async function selectChatAction(key: string) {
    if (key === 'rename') { setChatName(chatStore.chat.title); setRenameOpen(true); }
    else if (key === 'pin') await chatAction(() => chatStore.save({ pinned: !chatStore.chat.pinned }));
    else if (key === 'copy') { try { await navigator.clipboard.writeText(markdown(chatSnapshot)); setNotice('Copied conversation as Markdown'); } catch { setNotice('Failed to copy conversation as Markdown'); } }
    else if (key === 'link') { try { await navigator.clipboard.writeText(chatUrl((await chatStore.save()).id)); setNotice(localized('Chat link copied', '聊天链接已复制')); } catch (error) { setNotice(error instanceof Error ? error.message : 'Could not copy chat link'); } }
    else if (key === 'window' || key === 'tab') {
      const target = window.open('', '_blank', key === 'window' ? 'popup,width=1100,height=800' : undefined);
      if (!target) { setNotice('Allow pop-ups to open this chat in a new window.'); return; }
      target.opener = null;
      try { target.location.href = chatUrl((await chatStore.save()).id); } catch (error) { target.close(); setNotice(error instanceof Error ? error.message : 'Could not open chat'); }
    }
    else if (key === 'new') await chatAction(() => chatStore.startNew());
    else if (key === 'branch') await chatAction(() => chatStore.branch());
    else if (key === 'schedule') setScheduleOpen(true);
    else if (key === 'archive' || key === 'delete') setConfirmChat(key);
  }
  const latest = turns[turns.length - 1];
  const resultTurn = turns.find((turn) => turn.id === resultTurnId) ?? latest;
  const previewStep = resultStep ?? (resultTurn && resultTurn.step < 2 ? 1 : Math.min(resultTurn?.step ?? 0, 3));
  const activePreview = resultsOpen && resultTurn ? `${resultTurn.id}-${previewStep}` : undefined;
  const dotActivity = running ? activity : speaking !== null ? 'Working' : speechError ? 'Error' : activity;
  const history = turns.flatMap((turn) => [
    { key: `turn-${turn.id}`, turn: turn.id, target: `scout-turn-${turn.id}`, step: 0, title: turn.prompt || 'Message' },
    ...steps.slice(0, turn.step + 1).map((step, index) => ({ key: `step-${turn.id}-${index}`, turn: turn.id, target: `scout-step-${turn.id}-${index}`, step: index, title: step.title })),
  ]);
  const currentHistory = selectedHistory ?? history[history.length - 1]?.key;

  useEffect(() => { setResultStep(undefined); setResultTurnId(null); }, [latest?.id]);


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
  }, [turns, entered, composerHeight, contextOpen, resultsOpen]);
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
    if (chatStore.chat.title === 'New chat') chatStore.title(prompt.trim().slice(0, 60) || 'New chat');
    stopReading();
    if (clearComposer) { setDraft(''); setPendingFiles([]); }
    const id = ++nextId.current;
    setTurns((prev) => [...prev, { id, prompt, files, createdAt: new Date().toISOString(), step: 0, reply: '', template: '', adapter: '', done: 0, complete: false }]);
    setActivity('Thinking');
    setRunning(true);
    if (!entered) setContextOpen(false);
    setResultTurnId(null);
    setResultStep(undefined);
    setResultsOpen(true);
    setSelectedHistory(null);
    follow.current = true;
    onEnter();
    if (clearComposer) input.current?.focus();
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
    setResultsOpen(true);
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
    if (chatStore.loading || chatStore.chat.archived) return;
    if (!draft.trim() && !pendingFiles.length) return;
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

  return <section className={`scout-chat-preview ${entered ? 'is-entered' : ''} ${entered && resultsOpen ? 'has-results' : ''} ${entered && contextOpen ? 'has-context' : ''} ${chatStore.chat.archived ? 'is-archived' : ''}`} aria-label="Scout conversation" style={{ '--scout-composer-height': `${composerHeight}px` } as React.CSSProperties}>
    <div className={`ai-chat-dot ${!entered ? 'is-landing' : ''}`} data-activity={entered ? dotActivity : landingActivity} />
    {entered && <div className="scout-chat-dot-frost" aria-hidden="true" />}
    <button type="button" className="codex-chat-title" title="Open chats" onClick={() => setLibraryOpen(true)}>{entered ? `${chatStore.chat.pinned ? '● ' : ''}${chatStore.chat.title}` : 'Chats'}</button>
    <PreviewWorkspace userName={userName} chatId={chatStore.chat.id} visible={entered} snapshot={chatSnapshot} onSave={() => chatStore.save()} onRefresh={chatStore.refresh} conversationMenu={{ items: [
      { key: 'rename', label: localized('Rename', '重命名'), icon: <ScoutToolbarIcon name="rename" />, disabled: chatStore.loading },
      { key: 'pin', label: chatStore.chat.pinned ? localized('Unpin chat', '取消置顶聊天') : localized('Pin chat', '置顶聊天'), icon: <ScoutToolbarIcon name="pin" />, disabled: !turns.length || chatStore.loading },
      { key: 'branch', label: localized('Branch chat', '创建聊天分支'), icon: <ScoutToolbarIcon name="branch" />, disabled: !turns.length || chatStore.loading },
      { key: 'copy-menu', label: localized('Copy', '复制'), icon: <ScoutToolbarIcon name="copy" />, children: [
        { key: 'link', label: localized('Chat link', '聊天链接'), disabled: !turns.length },
        { key: 'copy', label: localized('Copy as Markdown', '复制为 Markdown'), disabled: !turns.length },
      ] },
      { key: 'open-menu', label: localized('Open in', '打开方式'), icon: <ScoutToolbarIcon name="open" />, children: [
        { key: 'window', label: localized('New window', '新窗口'), disabled: !turns.length || chatStore.loading },
        { key: 'tab', label: localized('New tab', '新标签页'), disabled: !turns.length || chatStore.loading },
      ] },
      { key: 'archive', label: chatStore.chat.archived ? localized('Unarchive', '取消归档') : localized('Archive', '归档'), icon: <ScoutToolbarIcon name="archive" />, disabled: !turns.length || chatStore.loading },
      { key: 'delete', label: localized('Delete', '删除'), icon: <ScoutToolbarIcon name="trash" />, disabled: !turns.length || chatStore.loading },
    ], onClick: ({ key }) => { if (key === 'archive' && chatStore.chat.archived) void chatAction(() => chatStore.save({ archived: false })); else void selectChatAction(key); } }} open={entered && resultsOpen} turn={resultTurn} step={previewStep} running={running} selectionRequest={selectionRequest} onReviewChange={async updated => { const next = turns.map(turn => turn.id === updated.id ? { ...turn, ...updated } : turn); await chatStore.save({}, next); setTurns(current => current.map(turn => turn.id === updated.id ? { ...turn, template: updated.template, adapter: updated.adapter, review: updated.review } : turn)); }} onRequestChanges={text => { setDraft(value => value ? `${value}\n\n${text}` : text); input.current?.focus(); }} onClose={() => setResultsOpen(false)} summaryOpen={contextOpen} onToggleSummary={() => setContextOpen(value => !value)} onTogglePreview={() => setResultsOpen(value => !value)} onNotice={setNotice} />
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
            <Dropdown trigger={['click']} placement="bottomLeft" overlayClassName="scout-response-more-menu" menu={{ items: [{ key: 'share', label: localized('Share', '分享'), icon: <ScoutToolbarIcon name="share" /> }], onClick: () => setShareOpen(true) }}><button type="button" aria-label="More response actions" title="More"><CodexReplyIcon name="more" /></button></Dropdown>
          </div></>}
        </div>
      </article>)}
      {entered && !running && latest && !latest.complete && <div className="scout-interruption" role="status">
        <span>{activity === 'Error' ? 'Response interrupted.' : latest.waiting === 'template' ? 'Review the template before starting adapter generation.' : latest.waiting === 'adapter' ? 'Review the adapter before preparing the collection plan.' : activity === 'NeedsInput' ? 'Waiting for your confirmation.' : activity === 'Payment' ? 'Waiting for confirmation.' : activity === 'None' ? 'Ready when you are.' : 'Response paused.'}</span>
        <button type="button" className="scout-resume" onClick={resume}>{activity === 'Error' ? 'Retry' : latest.waiting === 'template' ? 'Confirm Template' : latest.waiting === 'adapter' ? 'Confirm Adapter' : 'Continue'}</button>
      </div>}
    </div>
    {entered && <ConversationContext open={contextOpen} turn={resultTurn} turns={turns} onClose={() => { setContextOpen(false); document.querySelector<HTMLButtonElement>('.scout-context-toggle')?.focus(); }} activePreview={activePreview} onView={(artifact) => { setSelectionRequest(value => value + 1); setResultsOpen(true); setResultTurnId(artifact.turn); setResultStep(artifact.step); }} sources={sourceFiles} onAddFiles={addSourceFiles} onPreviewFile={setSelectedSource} />}
    <ChatComposer ref={input} userName={userName} placeholderReady={!userLoading && landingActivity === 'Working'} entered={entered} value={draft} running={running} files={pendingFiles} queued={queued} onChange={setDraft} onSubmit={submit} onPause={pauseResponse} onAddFiles={addSourceFiles} onRemoveFile={removePendingFile} onPreviewFile={setSelectedSource} onViewSources={() => { if (!entered) onEnter(); setContextOpen(true); }} onHeightChange={setComposerHeight} onEditQueued={(item) => { setQueued((prev) => prev.filter((value) => value.id !== item.id)); setDraft(item.prompt); setPendingFiles(item.files); input.current?.focus(); }} onDeleteQueued={(id) => setQueued((prev) => prev.filter((item) => item.id !== id))} onSendQueued={(item) => { setQueued((prev) => prev.filter((value) => value.id !== item.id)); startTurn(item.prompt, item.files, false); }} />
    {selectedSource && <SourcePreview file={selectedSource} onClose={() => setSelectedSource(null)} />}
    {chatStore.loading && <div className="codex-chat-archived" role="status">Loading chat…</div>}
    {chatStore.chat.archived && <div className="codex-chat-archived">This chat is archived. <button className="codex-chat-action" onClick={() => void chatAction(() => chatStore.save({ archived: false }))}>Unarchive and open</button></div>}
    {libraryOpen && <CodexChatLibrary onClose={() => setLibraryOpen(false)} onNew={() => { setLibraryOpen(false); void chatAction(() => chatStore.startNew()); }} onSchedule={turns.length && !chatStore.chat.archived ? () => { setLibraryOpen(false); setScheduleOpen(true); } : undefined} onOpen={chat => { setLibraryOpen(false); void chatAction(() => chatStore.open(chat.id)); }} />}
    {scheduleOpen && <CodexChatSchedule onSave={() => chatStore.save()} snapshot={chatSnapshot} onClose={() => setScheduleOpen(false)} />}
    {shareOpen && <CodexShareDialog onSave={() => chatStore.save()} snapshot={chatSnapshot} onClose={() => setShareOpen(false)} />}
    <Modal open={renameOpen} title="Rename chat" onCancel={() => setRenameOpen(false)} footer={null} width={400} className="codex-chat-dialog"><form onSubmit={event => { event.preventDefault(); if (chatName.trim()) void chatAction(async () => { await chatStore.save({ title: chatName.trim() }); setRenameOpen(false); }); }}><label className="codex-share-access">Chat name<input aria-label="Chat name" autoFocus value={chatName} maxLength={200} onChange={event => setChatName(event.target.value)} /></label><button className="codex-chat-action" type="submit" disabled={!chatName.trim()}>Save</button></form></Modal>
    <Modal open={Boolean(confirmChat)} title={confirmChat === 'delete' ? 'Delete chat?' : running ? 'Stop and archive this chat?' : 'Archive chat?'} onCancel={() => setConfirmChat(undefined)} footer={null} width={440} className="codex-chat-dialog"><p>{confirmChat === 'delete' ? 'This permanently deletes the chat and its shared links.' : 'Archiving stops this chat and removes its scheduled task. You can restore the chat later.'}</p><div className="codex-library-controls"><button className="codex-chat-action" onClick={() => setConfirmChat(undefined)}>Cancel</button><button className="codex-chat-action" onClick={() => { setRunning(false); stopReading(); setQueued([]); void chatAction(async () => { if (confirmChat === 'delete') await chatStore.remove(); else await chatStore.save({ archived: true }); setConfirmChat(undefined); }); }}>{confirmChat === 'delete' ? 'Delete' : running ? 'Stop and archive' : 'Archive'}</button></div></Modal>
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
    <button type="button" className="scout-worked-toggle" aria-expanded={open} aria-controls={`scout-steps-${turn.id}`} onClick={() => setOpen((value) => !value)}>{running ? <LoadingOrb state={activity === 'Thinking' ? 'thinking' : 'solving'} /> : <span className="scout-step-orb" />}<span>{title}</span><span className="scout-step-chevron"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="m4 6 4 4 4-4" /></svg></span></button>
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
