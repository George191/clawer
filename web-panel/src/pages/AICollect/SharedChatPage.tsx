import { useEffect, useState } from 'react';
import { PageLoading } from '@/components/ui';
import { apiUrl } from '@/services/endpoints';
import { getAccessToken, getSessionState } from '@/services/session';
import { ChatTranscript } from './CodexChatDialogs';
import type { ShareSnapshot } from './chatStore';

export default function SharedChatPage() {
  const [snapshot, setSnapshot] = useState<ShareSnapshot>();
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    const id = window.location.pathname.split('/').pop();
    const session = getSessionState();
    const token = session.authenticated && !session.locked ? getAccessToken() : null;
    fetch(apiUrl(`/api/v1/scout/shares/${encodeURIComponent(id ?? '')}`), { signal: controller.signal, headers: token ? { Authorization: `Bearer ${token}` } : {} }).then(async response => {
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail || 'Shared chat could not be loaded');
      setSnapshot(body);
    }).catch(error => { if (!controller.signal.aborted) setError(error.message); });
    return () => controller.abort();
  }, []);
  if (!snapshot && !error) return <PageLoading fullscreen tip="Loading shared chat…" />;
  return <main className="codex-shared-page"><a href="/scout">Scout</a>{error ? <><p role="alert">{error}</p><a href={`/login?next=${encodeURIComponent(window.location.pathname)}`}>Sign in</a></> : snapshot ? <><h1>{snapshot.title}</h1><ChatTranscript snapshot={snapshot} /></> : null}</main>;
}
