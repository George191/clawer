import { useEffect, useRef, useState } from 'react';
import { Button, Checkbox, Input, message } from '@/components/ui';
import { CloseOutlined, FileSearchOutlined, HistoryOutlined, PlusOutlined, ReloadOutlined, TeamOutlined } from '@/components/ui/icons';
import { ontologyError, type OntologyPrincipal, type SemanticDefinition } from '../../services/ontology';
import {
  mergeRevisionProposal, readRevision, readRevisionHistory, readRevisionPrincipals, readRevisionProposal,
  readRevisionProposals, readRevisionRoles, readRevisionWorkspace, reviewRevisionProposal, setRevisionRoles,
  submitRestoreProposal, submitRevisionProposal,
  type DefinitionChange, type ProposalDetails, type RevisionDetails, type RevisionPage,
  type RevisionProposal, type RevisionRole, type RevisionSummary, type RevisionWorkspace,
} from '../../services/ontology_revisions';
import OntologyRevisionsEditor from './OntologyRevisionsEditor';
import './ontology-create.css';
import './OntologyRevisions.css';

type Tab = 'proposals' | 'history' | 'roles';
type Listing =
  | { tab: 'proposals'; page: RevisionPage<RevisionProposal> }
  | { tab: 'history'; page: RevisionPage<RevisionSummary> }
  | { tab: 'roles'; page: RevisionPage<RevisionRole> };
type Composer = { revision: number; summary: string } & (
  { kind: 'revision'; definition: SemanticDefinition } | { kind: 'restore'; target: number }
);
type Props = { definitionId: string; onMerged: () => void; showTitle?: boolean };
const pageSize = 25;

const describe = (error: unknown) => ontologyError(error, 'The revision request failed. Retry to continue.');
function label(proposal: RevisionProposal) {
  if (proposal.stale) return 'Stale';
  return { pending: 'Pending review', approved: 'Approved', rejected: 'Rejected', merged: 'Merged' }[proposal.status];
}
const pretty = (value: unknown) => value == null ? '—' : typeof value === 'string' ? value : JSON.stringify(value, null, 2);
const when = (time: string) => new Date(time).toLocaleString();

function Changes({ changes }: { changes: DefinitionChange[] }) {
  return <div className="flow-ontology-revision-diff" role="region" aria-label="Definition changes" tabIndex={0}>
    {changes.length ? <table className="flow-ontology-revision-table">
      <thead><tr><th scope="col">Field</th><th scope="col">Before</th><th scope="col">After</th></tr></thead>
      <tbody>{changes.map(item => <tr key={item.path}>
        <th scope="row">{item.path}</th><td><pre>{pretty(item.before)}</pre></td><td><pre>{pretty(item.after)}</pre></td>
      </tr>)}</tbody>
    </table> : <p>No definition changes.</p>}
  </div>;
}

export default function OntologyRevisions({ definitionId, onMerged, showTitle = true }: Props) {
  const [workspace, setWorkspace] = useState<RevisionWorkspace>();
  const [listing, setListing] = useState<Listing>();
  const [tab, setTab] = useState<Tab>('proposals');
  const [offset, setOffset] = useState(0);
  const [version, setVersion] = useState(0);
  const [selectedProposal, setSelectedProposal] = useState('');
  const [selectedRevision, setSelectedRevision] = useState<number>();
  const [proposal, setProposal] = useState<ProposalDetails>();
  const [revision, setRevision] = useState<RevisionDetails>();
  const [detailLoading, setDetailLoading] = useState(false);
  const [composer, setComposer] = useState<Composer>();
  const [principals, setPrincipals] = useState<OntologyPrincipal[]>([]);
  const [principalQuery, setPrincipalQuery] = useState('');
  const [reviewNote, setReviewNote] = useState('');
  const [role, setRole] = useState<RevisionRole>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const locked = useRef(false);
  const active = useRef(true);
  const refresh = () => setVersion(value => value + 1);

  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  useEffect(() => {
    const controller = new AbortController();
    void readRevisionWorkspace(definitionId, controller.signal).then(
      data => { if (!controller.signal.aborted) setWorkspace(data); },
      failure => { if (!controller.signal.aborted) setError(describe(failure)); },
    );
    return () => controller.abort();
  }, [definitionId, version]);
  useEffect(() => {
    if (!workspace) return;
    const controller = new AbortController();
    const signal = controller.signal;
    setListing(undefined);
    const load = async (): Promise<Listing> => {
      if (tab === 'history') return { tab, page: await readRevisionHistory(definitionId, pageSize, offset, signal) };
      if (tab === 'roles') return { tab, page: await readRevisionRoles(definitionId, pageSize, offset, signal) };
      return { tab, page: await readRevisionProposals(definitionId, pageSize, offset, signal) };
    };
    void load().then(
      value => { if (!signal.aborted) setListing(value); },
      failure => { if (!signal.aborted) setError(describe(failure)); },
    );
    return () => controller.abort();
  }, [definitionId, offset, tab, workspace]);
  useEffect(() => {
    const controller = new AbortController();
    const signal = controller.signal;
    setProposal(undefined);
    setRevision(undefined);
    if (!selectedProposal && selectedRevision == null) { setDetailLoading(false); return; }
    setDetailLoading(true);
    const settled = (failure: unknown) => { if (!signal.aborted) setError(describe(failure)); };
    const request = selectedProposal
      ? readRevisionProposal(definitionId, selectedProposal, signal).then(data => { if (!signal.aborted) setProposal(data); }, settled)
      : readRevision(definitionId, selectedRevision!, signal).then(data => { if (!signal.aborted) setRevision(data); }, settled);
    void request.finally(() => { if (!signal.aborted) setDetailLoading(false); });
    return () => controller.abort();
  }, [definitionId, selectedProposal, selectedRevision, version]);
  const editingDefinition = composer?.kind === 'revision';
  useEffect(() => {
    if (!editingDefinition) return;
    const controller = new AbortController();
    const signal = controller.signal;
    const timer = window.setTimeout(() => {
      readRevisionPrincipals(definitionId, principalQuery, signal).then(
        options => { if (!signal.aborted) setPrincipals(previous => [...new Map([...options, ...previous].map(item => [item.kind + ':' + item.id, item])).values()]); },
        failure => { if (!signal.aborted) setError(describe(failure)); },
      );
    }, principalQuery ? 200 : 0);
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [definitionId, editingDefinition, principalQuery, version]);

  const switchTab = (next: Tab) => {
    setTab(next); setOffset(0); setSelectedProposal(''); setSelectedRevision(undefined); setComposer(undefined);
    setRole(undefined); setError('');
  };
  const run = async (work: () => Promise<void>) => {
    if (locked.current) return;
    locked.current = true; setBusy(true); setError('');
    try { await work(); }
    catch (failure) { if (active.current) { const detail = describe(failure); setError(detail); message.error({ content: 'Revision action failed', description: detail, className: 'explorer-toast' }); } }
    finally { locked.current = false; if (active.current) setBusy(false); }
  };
  const submit = () => {
    if (!composer || !composer.summary.trim()) return;
    void run(async () => {
      const result = composer.kind === 'revision'
        ? await submitRevisionProposal(definitionId, composer.revision, composer.definition, composer.summary.trim())
        : await submitRestoreProposal(definitionId, composer.revision, composer.target, composer.summary.trim());
      if (!active.current) return;
      switchTab('proposals');
      setSelectedProposal(result.id); message.success({ content: 'Proposal submitted for independent review.', className: 'explorer-toast' }); refresh();
    });
  };
  const review = (approve: boolean) => {
    if (!proposal) return;
    void run(async () => {
      await reviewRevisionProposal(definitionId, proposal.id, approve, reviewNote.trim());
      if (!active.current) return;
      setReviewNote(''); message.success({ content: approve ? 'Proposal approved.' : 'Proposal rejected.', className: 'explorer-toast' }); refresh();
    });
  };
  const merge = () => {
    if (!proposal) return;
    void run(async () => {
      const result = await mergeRevisionProposal(definitionId, proposal.id, proposal.baseRevision);
      if (!active.current) return;
      message.success({ content: 'Published revision ' + result.mergedRevision + '.', className: 'explorer-toast' });
      refresh(); onMerged();
    });
  };
  const saveRole = () => {
    if (!role) return;
    void run(async () => {
      await setRevisionRoles(definitionId, role);
      if (!active.current) return;
      setRole(undefined); message.success({ content: 'Revision roles saved.', className: 'explorer-toast' }); refresh();
    });
  };

  return <section className="ontology-revisions" aria-label="Model revisions">
    <header className="ontology-revisions__header">
      <div>{showTitle && <h3>Model revisions</h3>}{workspace && <span className="ontology-revisions__publication"><span aria-hidden="true" />Published <strong>Revision {workspace.revision}</strong></span>}</div>
      {workspace && <div className="ontology-revisions__tools">
        <Button size="small" icon={<ReloadOutlined />} disabled={busy || !!composer} onClick={() => { setError(''); refresh(); }}>Refresh</Button>
        {workspace.canEdit && <Button size="small" type="primary" icon={<PlusOutlined />} disabled={busy || !!composer} onClick={() => {
          setError(''); setPrincipalQuery('');
          setComposer({ kind: 'revision', revision: workspace.revision, definition: workspace.definition, summary: '' });
        }}>Propose change</Button>}
      </div>}
    </header>
    {workspace && <nav className="ontology-revisions__tabs" aria-label="Revision views">
      <button type="button" aria-pressed={tab === 'proposals'} disabled={busy} onClick={() => switchTab('proposals')}>Proposals</button>
      <button type="button" aria-pressed={tab === 'history'} disabled={busy} onClick={() => switchTab('history')}>Revision history</button>
      {workspace.canManageRoles && <button type="button" aria-pressed={tab === 'roles'} disabled={busy} onClick={() => switchTab('roles')}>Revision roles</button>}
    </nav>}
    <div className={`ontology-revisions__body${composer ? ' is-composing' : ''}`} role="region" aria-label="Revision workspace" tabIndex={0}>
      {error && <div className="ontology-revisions__error" role="alert"><span>{error}</span><Button size="small" onClick={() => { setError(''); refresh(); }}>Refresh</Button></div>}
      {!workspace && !error && <p className="ontology-revisions__loading" role="status"><ReloadOutlined spin />Loading revision workspace…</p>}
      {workspace && <>
        {composer ? <section className="ontology-revisions__composer" aria-label={composer.kind === 'restore' ? 'Restore proposal' : 'Change proposal'}>
          <div className="ontology-revisions__form" role="region" aria-label="Proposal form" tabIndex={0}>
            <header className="ontology-revisions__panel-heading"><div><h4>{composer.kind === 'restore' ? 'Restore revision ' + composer.target : 'Propose a change'}</h4><p>Based on published revision {composer.revision}. Changes require independent review before publication.</p></div></header>
            <label className="flow-create-field"><span>Proposal summary</span><Input aria-label="Proposal summary" value={composer.summary} maxLength={500}
              disabled={busy} placeholder="Describe the purpose of this change" onChange={event => setComposer({ ...composer, summary: event.target.value })} /></label>
            {composer.kind === 'revision' && <OntologyRevisionsEditor initial={workspace.definition} definition={composer.definition} principals={principals}
              disabled={busy} onPrincipalSearch={setPrincipalQuery} onChange={definition => setComposer({ ...composer, definition })} />}
          </div>
          <footer className="ontology-revisions__footer">
            <Button size="small" onClick={() => setComposer(undefined)} disabled={busy}>Cancel proposal</Button>
            <Button size="small" type="primary" onClick={submit} loading={busy} disabled={!composer.summary.trim()}>Submit proposal</Button>
          </footer>
        </section> : <>
          {!listing && !error && <p className="ontology-revisions__loading" role="status"><ReloadOutlined spin />Loading revisions…</p>}
          {listing?.tab === 'proposals' && <section className="ontology-revisions__listing" aria-label="Revision proposals">
            {!listing.page.items.length ? <div className="ontology-revisions__empty" role="status"><FileSearchOutlined /><strong>No revision proposals</strong><p>Submitted changes will appear here for review.</p></div> : <div className="ontology-revisions__table-wrap"><table className="flow-ontology-revision-table">
              <thead><tr><th scope="col">Proposal</th><th scope="col">Base revision</th><th scope="col">Status</th></tr></thead>
              <tbody>{listing.page.items.map(item => <tr key={item.id} className={selectedProposal === item.id ? 'is-selected' : undefined}>
                <th scope="row"><button type="button" className="ontology-revisions__row-link" disabled={busy} onClick={() => { setSelectedProposal(item.id); setReviewNote(''); setError(''); }}>{item.summary}</button></th>
                <td><code>{item.baseRevision}</code></td><td><span className={`ontology-revisions__status is-${item.stale ? 'stale' : item.status}`}>{label(item)}</span></td>
              </tr>)}</tbody>
            </table></div>}
          </section>}
          {listing?.tab === 'history' && <section className="ontology-revisions__listing" aria-label="Published revisions">
            {!listing.page.items.length ? <div className="ontology-revisions__empty" role="status"><HistoryOutlined /><strong>No published revisions</strong><p>Published model changes will appear here.</p></div> : <div className="ontology-revisions__table-wrap"><table className="flow-ontology-revision-table">
              <thead><tr><th scope="col">Revision</th><th scope="col">Change</th><th scope="col">Published</th></tr></thead>
              <tbody>{listing.page.items.map(item => <tr key={item.revision} className={selectedRevision === item.revision ? 'is-selected' : undefined}>
                <th scope="row"><button type="button" className="ontology-revisions__row-link" disabled={busy} onClick={() => { setSelectedRevision(item.revision); setError(''); }}>{'Revision ' + item.revision}</button>{item.revision === workspace.revision && <span className="ontology-revisions__current">Current</span>}</th>
                <td>{item.restoreRevision != null ? 'Restored revision ' + item.restoreRevision : item.proposalId ? 'Merged proposal' : 'Initial publication'}</td>
                <td><time dateTime={item.createdAt}>{when(item.createdAt)}</time></td>
              </tr>)}</tbody>
            </table></div>}
          </section>}
          {listing?.tab === 'roles' && <section className="ontology-revisions__listing" aria-label="Team revision roles">
            {!listing.page.items.length ? <div className="ontology-revisions__empty" role="status"><TeamOutlined /><strong>No active team members</strong><p>Revision roles are assigned to active project members.</p></div> : <div className="ontology-revisions__table-wrap"><table className="flow-ontology-revision-table">
              <thead><tr><th scope="col">Member</th><th scope="col">Role</th><th scope="col">Manage</th></tr></thead>
              <tbody>{listing.page.items.map(item => <tr key={item.id}>
                <th scope="row">{item.name}</th><td>{item.manager ? 'Project manager' : [item.canEdit && 'Editor', item.canReview && 'Reviewer'].filter(Boolean).join(', ') || 'No revision role'}</td>
                <td>{!item.manager && <Button size="small" disabled={busy} aria-label={'Edit revision roles for ' + item.name} onClick={() => { setRole(item); setError(''); }}>Edit roles</Button>}</td>
              </tr>)}</tbody>
            </table></div>}
          </section>}
          {listing && (offset > 0 || listing.page.hasMore) && <nav className="ontology-revisions__pagination" aria-label="Revision pagination">
            <span>Page {1 + offset / pageSize}</span><div><Button size="small" disabled={busy || offset === 0} onClick={() => setOffset(value => Math.max(0, value - pageSize))}>Previous</Button>
              <Button size="small" disabled={busy || !listing.page.hasMore || offset + pageSize > 100_000} onClick={() => setOffset(value => value + pageSize)}>Next</Button></div>
          </nav>}
        </>}
        {!composer && detailLoading && <p className="ontology-revisions__loading" role="status"><ReloadOutlined spin />Loading selected revision…</p>}
        {!composer && proposal && <section className="ontology-revisions__panel" aria-label="Proposal details">
          <header className="ontology-revisions__panel-heading"><div><h4>{proposal.summary}</h4><p><span className={`ontology-revisions__status is-${proposal.stale ? 'stale' : proposal.status}`}>{label(proposal)}</span>Based on revision {proposal.baseRevision}{proposal.restoreRevision != null && ' · Restores revision ' + proposal.restoreRevision}</p></div>
            <button type="button" className="ontology-revisions__close" aria-label="Close proposal details" disabled={busy} onClick={() => setSelectedProposal('')}><CloseOutlined /></button>
          </header>
          {proposal.reviewNote && <p className="ontology-revisions__note">{proposal.reviewNote}</p>}
          <Changes changes={proposal.changes} />
          {!proposal.stale && proposal.status === 'pending' && workspace.canReview && !proposal.isAuthor && <>
            <label className="flow-create-field"><span>Review note</span><Input aria-label="Review note" value={reviewNote} maxLength={500} disabled={busy} onChange={event => setReviewNote(event.target.value)} /></label>
            <div className="ontology-revisions__footer">
              <Button size="small" disabled={busy} onClick={() => review(false)}>Reject proposal</Button>
              <Button size="small" type="primary" disabled={busy} onClick={() => review(true)}>Approve proposal</Button>
            </div>
          </>}
          {!proposal.stale && proposal.status === 'approved' && workspace.canEdit && <div className="ontology-revisions__footer"><Button size="small" type="primary" loading={busy} onClick={merge}>Merge proposal</Button></div>}
        </section>}
        {!composer && revision && <section className="ontology-revisions__panel" aria-label="Historical revision">
          <header className="ontology-revisions__panel-heading"><div><h4>Revision {revision.revision}</h4><p>Compared with the current publication.</p></div><button type="button" className="ontology-revisions__close" aria-label="Close historical revision" disabled={busy} onClick={() => setSelectedRevision(undefined)}><CloseOutlined /></button></header>
          <Changes changes={revision.changesFromCurrent} />
          {workspace.canEdit && revision.revision < workspace.revision && revision.changesFromCurrent.length > 0 && <div className="ontology-revisions__footer">
            <Button size="small" disabled={busy} onClick={() => setComposer({
              kind: 'restore', revision: workspace.revision, target: revision.revision, summary: 'Restore revision ' + revision.revision,
            })}>Propose restore</Button></div>}
        </section>}
        {!composer && role && <section className="ontology-revisions__panel" aria-label="Edit team revision roles">
          <header className="ontology-revisions__panel-heading"><div><h4>{role.name}</h4><p>Choose who can propose and independently review model changes.</p></div></header>
          <div className="ontology-revisions__role-options">
            <Checkbox checked={role.canEdit} disabled={busy} onChange={event => setRole({ ...role, canEdit: event.target.checked })}>Editor</Checkbox>
            <Checkbox checked={role.canReview} disabled={busy} onChange={event => setRole({ ...role, canReview: event.target.checked })}>Reviewer</Checkbox>
          </div>
          <div className="ontology-revisions__footer">
            <Button size="small" onClick={() => setRole(undefined)} disabled={busy}>Cancel</Button>
            <Button size="small" type="primary" onClick={saveRole} loading={busy}>Save revision roles</Button>
          </div>
        </section>}
      </>}
    </div>
  </section>;
}
