import { useEffect, useState } from 'react';
import { Modal } from '@/components/ui';
import CodexReplyIcon from './CodexReplyIcon';
import { createShare, listChats, type ChatSummary, type SavedChat, type ShareSnapshot } from './chatStore';
import client, { unwrap } from '@/services/api';
import './codexChatDialogs.css';

// Codex TnP0/qKAH: chat preview, access selection and an outline Copy link action.
export function CodexShareDialog({ snapshot, onSave, onClose }: { snapshot: ShareSnapshot; onSave: () => Promise<SavedChat>; onClose: () => void }) {
  const [audience, setAudience] = useState<'anyone' | 'invited'>('anyone');
  const [emails, setEmails] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [url, setUrl] = useState('');
  const [copied, setCopied] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const recipients = emails.split(/[,;\s]+/).filter(Boolean);
  async function submit() {
    setBusy(true); setError(''); setCopied(false);
    try {
      const link = url || await createShare(await onSave(), snapshot, audience, recipients);
      setUrl(link);
      await navigator.clipboard.writeText(link);
      setCopied(true);
    } catch (error) { setError(error instanceof Error ? error.message : 'Could not create or copy shared chat link.'); }
    finally { setBusy(false); }
  }
  const reset = () => { setUrl(''); setCopied(false); setError(''); };
  return <Modal open title={snapshot.title === 'New chat' ? 'Share chat' : `Share ${snapshot.title}`} onCancel={() => { if (!busy) onClose(); }} footer={null} width={520} className="codex-chat-dialog">
    <form onSubmit={event => { event.preventDefault(); void submit(); }}>
      <div className={`codex-share-preview ${expanded ? 'is-expanded' : ''}`} role="region" aria-label="Preview of the beginning of the shared chat">
        <button type="button" className="codex-preview-expand" aria-label={expanded ? 'Collapse preview' : 'Expand preview'} onClick={() => setExpanded(value => !value)}>{expanded ? '↙' : '↗'}</button>
        <ChatTranscript snapshot={snapshot} />
      </div>
      <p className="codex-share-info">Your name and any messages you add after sharing won’t appear in the shared chat.</p>
      <label className="codex-share-access">Who can access<select aria-label="Who can access" disabled={busy} value={audience} onChange={event => { setAudience(event.target.value as typeof audience); reset(); }}><option value="anyone">Anyone with the link</option><option value="invited">Only people invited</option></select></label>
      {audience === 'invited' && <label className="codex-share-access">Add people by email<input type="text" aria-label="Add people by email" placeholder="Emails separated by commas" value={emails} disabled={busy} onChange={event => { setEmails(event.target.value); reset(); }} /></label>}
      {error && <p role="alert" className="codex-chat-error">{error}</p>}
      {url && <input className="codex-share-link" aria-label="Share link" readOnly value={url} onFocus={event => event.target.select()} />}
      <footer><span>{audience === 'anyone' ? 'Anyone with the link can view this chat' : 'Invited people must sign in to view this chat'}</span><button className="codex-chat-action" type="submit" disabled={busy || (audience === 'invited' && !recipients.length)}><CodexReplyIcon name={copied ? 'copied' : 'copy'} />{busy ? 'Creating link…' : copied ? 'Copied' : 'Copy link'}</button></footer>
    </form>
  </Modal>;
}

export function ChatTranscript({ snapshot }: { snapshot: ShareSnapshot }) {
  return <div className="codex-chat-transcript">{snapshot.turns.map(turn => <article key={turn.id}><p className="codex-shared-prompt">{turn.prompt}</p>{turn.attachments?.map((file, index) => <a key={index} download={file.name} href={`data:application/octet-stream;base64,${file.content}`}>{file.name}</a>)}{turn.reply && <p>{turn.reply}</p>}{turn.template && <details><summary>template.yaml</summary><pre>{turn.template}</pre></details>}{turn.adapter && <details><summary>adapter.py</summary><pre>{turn.adapter}</pre></details>}</article>)}</div>;
}

export function CodexChatLibrary({ onOpen, onNew, onClose }: { onOpen: (chat: ChatSummary) => void; onNew: () => void; onClose: () => void }) {
  const [archived, setArchived] = useState(false);
  const [items, setItems] = useState<ChatSummary[]>([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [more, setMore] = useState(false);
  const [page, setPage] = useState(0);
  useEffect(() => {
    let active = true; setLoading(true); setError('');
    listChats(archived, page * 100).then(rows => { if (active) { setItems(current => page ? [...current, ...rows] : rows); setMore(rows.length === 100); } }).catch(error => { if (active) setError(error.message); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [archived, page]);
  return <Modal open title="Chats" onCancel={onClose} footer={null} width={520} className="codex-chat-dialog"><div className="codex-library-controls"><button className="codex-chat-action" aria-pressed={!archived} onClick={() => { setArchived(false); setPage(0); }}>Recent</button><button className="codex-chat-action" aria-pressed={archived} onClick={() => { setArchived(true); setPage(0); }}>Archived</button><button className="codex-chat-action" onClick={onNew}>New chat</button></div>{error && <p role="alert">{error}</p>}{!loading && !items.length && <p>No chats yet.</p>}<div className="codex-chat-library">{items.map(chat => <button key={chat.id} onClick={() => onOpen(chat)}><span>{chat.pinned ? '● ' : ''}{chat.title}</span><small>{chat.archived ? 'Archived' : 'Open'}</small></button>)}</div>{loading && <p role="status">Loading chats…</p>}{more && <button className="codex-chat-action" disabled={loading} onClick={() => setPage(value => value + 1)}>Load more</button>}</Modal>;
}

type ScheduledAgent = { id: string; name: string; harness: string; enabled: boolean };
type Schedule = { interval_seconds: number; next_run_at: string; last_error?: string; status?: string; result?: unknown; request: { agent_id: string; input: Record<string, string> } };

export function CodexChatSchedule({ onSave, snapshot, onClose }: { onSave: () => Promise<SavedChat>; snapshot: ShareSnapshot; onClose: () => void }) {
  const [agents, setAgents] = useState<ScheduledAgent[]>([]);
  const [agent, setAgent] = useState('');
  const [seconds, setSeconds] = useState(86400);
  const [url, setUrl] = useState('');
  const [task, setTask] = useState(snapshot.turns[snapshot.turns.length - 1]?.prompt ?? '');
  const [schedule, setSchedule] = useState<Schedule>();
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(true);
  const [chatId, setChatId] = useState('');
  const selected = agents.find(item => item.id === agent);
  useEffect(() => {
    let active = true;
    Promise.all([onSave(), client.get('/agents/catalog')]).then(async ([chat, response]) => {
      const rows = unwrap<{ agents: ScheduledAgent[] }>(response.data).agents.filter(item => item.enabled);
      const saved = unwrap<Schedule | null>((await client.get(`/scout/chats/${chat.id}/schedule`)).data);
      if (!active) return;
      setAgents(rows); setChatId(chat.id); setAgent(saved?.request.agent_id ?? rows[0]?.id ?? '');
      if (saved) { setSchedule(saved); setSeconds(saved.interval_seconds); setUrl(saved.request.input.url ?? ''); setTask(saved.request.input.task ?? saved.request.input.goal ?? task); }
    }).catch(error => { if (active) setError(error.message); }).finally(() => { if (active) setBusy(false); });
    return () => { active = false; };
  }, []);
  async function save() {
    setBusy(true); setError('');
    try {
      const input = selected?.harness === 'collection' ? { url, goal: task } : { task, context: snapshot.turns.map(turn => `${turn.prompt}\n${turn.reply}`).join('\n').slice(-40000) };
      setSchedule(unwrap<Schedule>((await client.put(`/scout/chats/${chatId}/schedule`, { interval_seconds: seconds, request: { agent_id: agent, product_domain: 'ai-collect', input } })).data));
    } catch (error) { setError(error instanceof Error ? error.message : 'Could not save scheduled task'); }
    finally { setBusy(false); }
  }
  return <Modal open title={schedule ? 'Edit scheduled task' : 'Add scheduled task'} onCancel={onClose} footer={null} width={520} className="codex-chat-dialog"><form onSubmit={event => { event.preventDefault(); void save(); }}>
    <label className="codex-share-access">Agent<select aria-label="Scheduled agent" value={agent} disabled={busy} onChange={event => setAgent(event.target.value)}>{agents.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
    {!busy && !agents.length && <p>Enable an agent in <a href="/agents">Agents</a> before adding a scheduled task.</p>}
    <label className="codex-share-access">Task<input aria-label="Scheduled task" value={task} maxLength={selected?.harness === 'collection' ? 4000 : 12000} onChange={event => setTask(event.target.value)} /></label>
    {selected?.harness === 'collection' && <label className="codex-share-access">Webpage<input aria-label="Scheduled webpage" type="url" required value={url} onChange={event => setUrl(event.target.value)} /></label>}
    <label className="codex-share-access">Repeat<select aria-label="Repeat" value={seconds} disabled={busy} onChange={event => setSeconds(Number(event.target.value))}><option value={3600}>Every hour</option><option value={86400}>Every day</option><option value={604800}>Every week</option></select></label>
    {schedule && <p>Next run: {new Date(schedule.next_run_at).toLocaleString()}. {schedule.status ?? 'Waiting for first run'}</p>}
    {schedule?.last_error && <p role="alert" className="codex-chat-error">{schedule.last_error}</p>}
    {schedule?.result != null && <details><summary>Last result</summary><pre>{JSON.stringify(schedule.result, null, 2)}</pre></details>}
    {error && <p role="alert" className="codex-chat-error">{error}</p>}
    <div className="codex-library-controls"><button className="codex-chat-action" disabled={busy || !agent || !task.trim()} type="submit">{busy ? 'Saving…' : 'Save task'}</button>{schedule && <button type="button" className="codex-chat-action" disabled={busy} onClick={() => { setBusy(true); void client.delete(`/scout/chats/${chatId}/schedule`).then(() => setSchedule(undefined)).catch(error => setError(error.message)).finally(() => setBusy(false)); }}>Remove task</button>}</div>
  </form></Modal>;
}
