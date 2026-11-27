import { useEffect, useRef, useState } from 'react';
import { branchChat, deleteChat, newChat, persistTurns, readChat, saveChat, type SavedChat, type SavedTurn } from './chatStore';

export default function useScoutChat(turns: SavedTurn[], onLoad: (chat: SavedChat) => void, onError: (message: string) => void) {
  const [chat, setChat] = useState(newChat);
  const [loading, setLoading] = useState(Boolean(new URLSearchParams(window.location.search).get('chat')));
  const current = useRef(chat);
  const content = useRef(turns);
  const callbacks = useRef({ onLoad, onError });
  const queue = useRef<Promise<unknown>>(Promise.resolve());
  const revisions = useRef(new Map<string, number>());
  const lastSaved = useRef('');
  current.current = chat; content.current = turns; callbacks.current = { onLoad, onError };
  const serialized = JSON.stringify(turns.map(({ id, prompt, reply, template, adapter, step, done, complete, waiting, vote, attachments }) => ({ id, prompt, reply, template, adapter, step, done, complete, waiting, vote, attachments })));

  function save(changes: Partial<Pick<SavedChat, 'title' | 'pinned' | 'archived'>> = {}) {
    const snapshot = { ...current.current, ...changes, turns: content.current };
    const operation = queue.current.then(async () => {
      const latest = current.current.id === snapshot.id ? current.current : snapshot;
      const target = { ...snapshot, turns: await persistTurns(snapshot.turns), title: latest.title, pinned: latest.pinned, archived: latest.archived, ...changes,
        revision: revisions.current.get(snapshot.id) ?? snapshot.revision };
      const result = await saveChat(target);
      revisions.current.set(result.id, result.revision);
      if (current.current.id === result.id) {
        const updated = { ...current.current, ...changes, revision: result.revision };
        current.current = updated; setChat(updated);
        lastSaved.current = JSON.stringify({ id: target.id, title: target.title, pinned: target.pinned, archived: target.archived, turns: target.turns });
      }
      return result;
    });
    queue.current = operation.catch(() => {});
    return operation;
  }

  function apply(next: SavedChat) {
    revisions.current.set(next.id, next.revision);
    current.current = next; setChat(next); content.current = next.turns;
    lastSaved.current = JSON.stringify({ id: next.id, title: next.title, pinned: next.pinned, archived: next.archived, turns: next.turns });
    callbacks.current.onLoad(next);
    const url = new URL(window.location.href);
    if (next.revision) url.searchParams.set('chat', next.id); else url.searchParams.delete('chat');
    window.history.replaceState(window.history.state, '', url);
  }

  useEffect(() => {
    const id = new URLSearchParams(window.location.search).get('chat');
    if (!id) return;
    let active = true;
    readChat(id).then(next => { if (active) apply(next); }).catch(error => { if (active) callbacks.current.onError(error.message); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (loading || !turns.length) return;
    const snapshot = JSON.stringify({ id: chat.id, title: chat.title, pinned: chat.pinned, archived: chat.archived, turns: JSON.parse(serialized) });
    if (snapshot === lastSaved.current) return;
    const timer = window.setTimeout(() => { void save().catch(error => callbacks.current.onError(`Chat was not saved: ${error.message}`)); }, 1500);
    return () => window.clearTimeout(timer);
  }, [serialized, chat.id, chat.title, chat.pinned, chat.archived, loading]);

  async function open(id: string) {
    if (content.current.length && !loading) await save();
    setLoading(true);
    try { apply(await readChat(id)); } finally { setLoading(false); }
  }
  async function startNew() { if (content.current.length) await save(); apply(newChat()); }
  async function branch() { const saved = await save(); apply(await branchChat(saved.id)); }
  async function remove() { await queue.current; if (current.current.revision) await deleteChat(current.current.id); apply(newChat()); }
  function title(value: string) { const next = { ...current.current, title: value }; current.current = next; setChat(next); }
  return { chat, loading, save, open, startNew, branch, remove, title };
}
