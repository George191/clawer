import { LoadingIndicator } from '@/components/ui/loading';
import { useEffect, useState } from 'react';
import { Button, NativeSelect } from '@/components/ui';
import {
  createTeamKey, deleteTeamKey, fetchCurrentUserContext, issuePublicationKey,
  publicationKeys, revokePublicationKey, rotateTeamKey, teamKeys,
} from '../../services/api';
import './api-keys.css';

function failure(error: unknown) {
  const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  return typeof detail === 'string' ? detail : 'Unable to complete the API key request.';
}

// Associate each response with its query so a team change cannot show the previous team's keys.
function useList<T>(scope: string, fetch: () => Promise<T>) {
  const [revision, setRevision] = useState(0);
  const [result, setResult] = useState<{ request: string; data?: T; error?: string }>();
  const request = scope + ':' + revision;
  useEffect(() => {
    if (!scope) return;
    let active = true;
    fetch().then(
      data => { if (active) setResult({ request, data }); },
      error => { if (active) setResult({ request, error: failure(error) }); },
    );
    return () => { active = false; };
  }, [request]);
  const current = result?.request === request ? result : undefined;
  return { ...current, refresh: () => setRevision(n => n + 1) };
}

function ListState({ error, loading, retry }: { error?: string; loading: boolean; retry: () => void }) {
  if (error) return <div className="key-alert" role="alert">{error} <Button onClick={retry}>Retry</Button></div>;
  if (loading) return <p role="status"><LoadingIndicator size={20}>Loading…</LoadingIndicator></p>;
  return null;
}

export default function ApiKeys() {
  const context = useList('user', fetchCurrentUserContext);
  const user = context.data;
  const [selectedTeam, setTeam] = useState('');
  const team = selectedTeam || user?.teams[0]?.id || '';
  const [teamPage, setTeamPage] = useState(1);
  const teams = useList(team && team + ':' + teamPage, () => teamKeys(team, teamPage));
  const [name, setName] = useState('');
  const initial = new URLSearchParams(window.location.search).get('publication') || '';
  const [search, setSearch] = useState(initial);
  const [query, setQuery] = useState(initial);
  const [offset, setOffset] = useState(0);
  const admin = user?.user.is_superuser;
  const publications = useList(admin ? JSON.stringify([query, offset]) : '', () => publicationKeys(query, offset));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [key, reveal] = useState('');

  async function act(action: () => Promise<{ key: string } | void>, refresh: () => void, confirmation?: string) {
    if (busy || (confirmation && !window.confirm(confirmation))) return;
    setBusy(true);
    setError('');
    reveal('');
    try {
      const result = await action();
      if (result) reveal(result.key);
      refresh();
    } catch (e) {
      setError(failure(e));
    } finally {
      setBusy(false);
    }
  }

  function createKey() {
    return act(async () => {
      const issued = await createTeamKey(team, name.trim());
      setName('');
      setTeamPage(1);
      return issued;
    }, teams.refresh);
  }

  return (
    <section className="acc-card acc-keys" aria-label="API key management">
      <h3>API keys</h3>
      <ListState error={context.error} loading={!user} retry={context.refresh} />
      {error && <p className="key-alert" role="alert">{error}</p>}
      {key && (
        <div className="key-secret">
          <label>New API key<input readOnly autoComplete="off" spellCheck={false} value={key} onFocus={e => e.currentTarget.select()} /></label>
          <div className="key-actions"><span>Save this key now. It is shown once.</span><Button onClick={() => reveal('')}>Dismiss key</Button></div>
        </div>
      )}
      {admin && (
        <section aria-label="Published API keys">
          <h4>Published API keys</h4>
          <form onSubmit={e => { e.preventDefault(); setOffset(0); setQuery(search.trim()); reveal(''); }}>
            <label>Find a publication<input value={search} maxLength={200} placeholder="Schema, table or publication ID" onChange={e => setSearch(e.target.value)} /></label>
            <Button htmlType="submit" disabled={busy}>Search publications</Button>
          </form>
          <ListState error={publications.error} loading={!publications.data} retry={publications.refresh} />
          {publications.data?.items.length === 0 && <p>No publications on this page.</p>}
          {publications.data?.items.map(row => {
            const active = row.keyStatus === 'active';
            const rotate = () => act(() => issuePublicationKey(row.id), publications.refresh,
              active ? 'Rotate this published API key? The existing credential will stop working.' : undefined);
            const revoke = () => act(async () => { await revokePublicationKey(row.id); }, publications.refresh, 'Revoke this published API key?');
            return (
              <div key={row.id} className="key-row">
                <div className="key-metadata">
                  <strong>{row.schema}.{row.table}</strong>
                  <code>{row.url}</code>
                  <small>{new Date(row.validFrom).toLocaleString()} – {new Date(row.validUntil).toLocaleString()}</small>
                </div>
                <span className="key-status">{row.keyStatus}</span>
                <div className="key-actions">
                  <Button disabled={busy} onClick={rotate}>{active ? 'Rotate key' : 'Issue key'}</Button>
                  <Button danger disabled={busy || !active} onClick={revoke}>Revoke key</Button>
                </div>
              </div>
            );
          })}
          {publications.data && (
            <div className="key-pager">
              <Button disabled={!offset || busy} onClick={() => { reveal(''); setOffset(offset - 25); }}>Previous publications</Button>
              <Button disabled={!publications.data.hasMore || busy} onClick={() => { reveal(''); setOffset(offset + 25); }}>Next publications</Button>
            </div>
          )}
        </section>
      )}
      {user && (
        <section aria-label="Team API keys">
          <h4>Team keys</h4>
          {!user.teams.length ? <p>No accessible teams.</p> : (
            <>
              <label>Team
                <NativeSelect value={team} disabled={busy} onChange={e => { setTeam(e.target.value); setTeamPage(1); reveal(''); }}>
                  {user.teams.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}
                </NativeSelect>
              </label>
              <form onSubmit={e => { e.preventDefault(); void createKey(); }}>
                <label>Key description<input value={name} maxLength={160} onChange={e => setName(e.target.value)} /></label>
                <Button htmlType="submit" disabled={busy || !name.trim()}>Create team key</Button>
              </form>
              <ListState error={teams.error} loading={!teams.data} retry={teams.refresh} />
              {teams.data?.items.length === 0 && <p>No team keys on this page.</p>}
              {teams.data?.items.map(row => (
                <div key={row.id} className="key-row">
                  <div className="key-metadata"><strong>{row.description}</strong><code>{row.short_key}</code></div>
                  <div className="key-actions">
                    <Button disabled={busy} onClick={() => act(() => rotateTeamKey(team, row.id), teams.refresh, 'Rotate this team key?')}>Rotate key</Button>
                    <Button danger disabled={busy} onClick={() => act(async () => { await deleteTeamKey(team, row.id); }, teams.refresh, 'Revoke this team key?')}>Revoke key</Button>
                  </div>
                </div>
              ))}
              {teams.data && (
                <div className="key-pager">
                  <Button disabled={busy || teamPage <= 1} onClick={() => { reveal(''); setTeamPage(teamPage - 1); }}>Previous team keys</Button>
                  <Button disabled={busy || teamPage >= teams.data.pages} onClick={() => { reveal(''); setTeamPage(teamPage + 1); }}>Next team keys</Button>
                </div>
              )}
            </>
          )}
        </section>
      )}
    </section>
  );
}
