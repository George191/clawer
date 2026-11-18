import { useEffect, useRef, useState } from 'react';
import { Button, Checkbox, Input } from '@/components/ui';
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
type Props = { definitionId: string; onMerged: () => void };
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

export default function OntologyRevisions({ definitionId, onMerged }: Props) {
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
  const [notice, setNotice] = useState('');
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
    setRole(undefined); setError(''); setNotice('');
  };
  const run = async (work: () => Promise<void>) => {
    if (locked.current) return;
    locked.current = true; setBusy(true); setError(''); setNotice('');
    try { await work(); }
    catch (failure) { if (active.current) setError(describe(failure)); }
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
      setSelectedProposal(result.id); setNotice('Proposal submitted for independent review.'); refresh();
    });
  };
  const review = (approve: boolean) => {
    if (!proposal) return;
    void run(async () => {
      await reviewRevisionProposal(definitionId, proposal.id, approve, reviewNote.trim());
      if (!active.current) return;
      setReviewNote(''); setNotice(approve ? 'Proposal approved.' : 'Proposal rejected.'); refresh();
    });
  };
  const merge = () => {
    if (!proposal) return;
    void run(async () => {
      const result = await mergeRevisionProposal(definitionId, proposal.id, proposal.baseRevision);
      if (!active.current) return;
      setNotice('Published revision ' + result.mergedRevision + '.');
      refresh(); onMerged();
    });
  };
  const saveRole = () => {
    if (!role) return;
    void run(async () => {
      await setRevisionRoles(definitionId, role);
      if (!active.current) return;
      setRole(undefined); setNotice('Revision roles saved.'); refresh();
    });
  };

  return <section className="flow-create-section flow-ontology-revisions" aria-label="Model revisions">
    <header className="flow-create-heading">
      <h3>Model revisions</h3>
      {workspace && <p>Published revision {workspace.revision}</p>}
    </header>
    {error && <p className="flow-create-error" role="alert">{error}<Button size="small" onClick={() => { setError(''); refresh(); }}>Refresh</Button></p>}
    {notice && <p className="flow-create-notice" role="status">{notice}</p>}
    {!workspace && !error && <p role="status">Loading revision workspace…</p>}
    {workspace && <>
      <nav className="flow-ontology-revision-actions" aria-label="Revision views">
        <Button size="small" aria-pressed={tab === 'proposals'} disabled={busy} onClick={() => switchTab('proposals')}>Proposals</Button>
        <Button size="small" aria-pressed={tab === 'history'} disabled={busy} onClick={() => switchTab('history')}>Revision history</Button>
        {workspace.canManageRoles && <Button size="small" aria-pressed={tab === 'roles'} disabled={busy} onClick={() => switchTab('roles')}>Revision roles</Button>}
        <Button size="small" disabled={busy || !!composer} onClick={() => { setError(''); refresh(); }}>Refresh revisions</Button>
        {workspace.canEdit && <Button size="small" type="primary" disabled={busy || !!composer} onClick={() => {
          setError(''); setNotice(''); setPrincipalQuery('');
          setComposer({ kind: 'revision', revision: workspace.revision, definition: workspace.definition, summary: '' });
        }}>Propose change</Button>}
      </nav>

      {composer ? <section className="flow-semantic-config" aria-label={composer.kind === 'restore' ? 'Restore proposal' : 'Change proposal'}>
        <h4>{composer.kind === 'restore' ? 'Propose restoring revision ' + composer.target : 'Propose changes to revision ' + composer.revision}</h4>
        <label className="flow-create-field"><span>Proposal summary</span><Input aria-label="Proposal summary" value={composer.summary} maxLength={500}
          disabled={busy} onChange={event => setComposer({ ...composer, summary: event.target.value })} /></label>
        {composer.kind === 'revision' && <OntologyRevisionsEditor initial={workspace.definition} definition={composer.definition} principals={principals}
          disabled={busy} onPrincipalSearch={setPrincipalQuery} onChange={definition => setComposer({ ...composer, definition })} />}
        <div className="flow-ontology-revision-actions">
          <Button size="small" onClick={() => setComposer(undefined)} disabled={busy}>Cancel proposal</Button>
          <Button size="small" type="primary" onClick={submit} loading={busy} disabled={!composer.summary.trim()}>Submit proposal</Button>
        </div>
      </section> : <>
        {!listing && !error && <p role="status">Loading revisions…</p>}
        {listing?.tab === 'proposals' && <section aria-label="Revision proposals">
          {!listing.page.items.length ? <p>No revision proposals.</p> : <table className="flow-ontology-revision-table">
            <thead><tr><th scope="col">Proposal</th><th scope="col">Base</th><th scope="col">Status</th></tr></thead>
            <tbody>{listing.page.items.map(item => <tr key={item.id}>
              <th scope="row"><Button size="small" type="link" disabled={busy} onClick={() => { setSelectedProposal(item.id); setReviewNote(''); setError(''); setNotice(''); }}>{item.summary}</Button></th>
              <td>{item.baseRevision}</td><td>{label(item)}</td>
            </tr>)}</tbody>
          </table>}
        </section>}

        {listing?.tab === 'history' && <section aria-label="Published revisions">
          {!listing.page.items.length ? <p>No published revisions.</p> : <table className="flow-ontology-revision-table">
            <thead><tr><th scope="col">Revision</th><th scope="col">Change</th><th scope="col">Published</th></tr></thead>
            <tbody>{listing.page.items.map(item => <tr key={item.revision}>
              <th scope="row"><Button size="small" type="link" disabled={busy} onClick={() => { setSelectedRevision(item.revision); setError(''); }}>{'Revision ' + item.revision}</Button></th>
              <td>{item.restoreRevision != null ? 'Restored revision ' + item.restoreRevision : item.proposalId ? 'Merged proposal' : 'Initial publication'}</td>
              <td><time dateTime={item.createdAt}>{when(item.createdAt)}</time></td>
            </tr>)}</tbody>
          </table>}
        </section>}

        {listing?.tab === 'roles' && <section aria-label="Team revision roles">
          {!listing.page.items.length ? <p>No active team members.</p> : <table className="flow-ontology-revision-table">
            <thead><tr><th scope="col">Member</th><th scope="col">Role</th><th scope="col">Manage</th></tr></thead>
            <tbody>{listing.page.items.map(item => <tr key={item.id}>
              <th scope="row">{item.name}</th><td>{item.manager ? 'Project manager' : [item.canEdit && 'Editor', item.canReview && 'Reviewer'].filter(Boolean).join(', ') || 'No revision role'}</td>
              <td>{!item.manager && <Button size="small" disabled={busy} aria-label={'Edit revision roles for ' + item.name} onClick={() => { setRole(item); setError(''); }}>Edit roles</Button>}</td>
            </tr>)}</tbody>
          </table>}
        </section>}

        {listing && (offset > 0 || listing.page.hasMore) && <div className="flow-ontology-revision-actions" aria-label="Revision pagination">
          <Button size="small" disabled={busy || offset === 0} onClick={() => setOffset(value => Math.max(0, value - pageSize))}>Previous page</Button>
          <Button size="small" disabled={busy || !listing.page.hasMore || offset + pageSize > 100_000} onClick={() => setOffset(value => value + pageSize)}>Next page</Button>
        </div>}
      </>}

      {!composer && detailLoading && <p role="status">Loading selected revision…</p>}
      {!composer && proposal && <section className="flow-semantic-config" aria-label="Proposal details">
        <h4>{proposal.summary}</h4>
        <p>{label(proposal)} · Based on revision {proposal.baseRevision}{proposal.restoreRevision != null && ' · Restores revision ' + proposal.restoreRevision}</p>
        {proposal.reviewNote && <p>{proposal.reviewNote}</p>}
        <Changes changes={proposal.changes} />
        {!proposal.stale && proposal.status === 'pending' && workspace.canReview && !proposal.isAuthor && <>
          <label className="flow-create-field"><span>Review note</span><Input aria-label="Review note" value={reviewNote} maxLength={500} disabled={busy} onChange={event => setReviewNote(event.target.value)} /></label>
          <div className="flow-ontology-revision-actions">
            <Button size="small" disabled={busy} onClick={() => review(false)}>Reject proposal</Button>
            <Button size="small" type="primary" disabled={busy} onClick={() => review(true)}>Approve proposal</Button>
          </div>
        </>}
        {!proposal.stale && proposal.status === 'approved' && workspace.canEdit && <Button size="small" type="primary" loading={busy} onClick={merge}>Merge proposal</Button>}
      </section>}
      {!composer && revision && <section className="flow-semantic-config" aria-label="Historical revision">
        <h4>Revision {revision.revision} compared with current publication</h4>
        <Changes changes={revision.changesFromCurrent} />
        {workspace.canEdit && revision.revision < workspace.revision && revision.changesFromCurrent.length > 0 &&
          <Button size="small" disabled={busy} onClick={() => setComposer({
            kind: 'restore', revision: workspace.revision, target: revision.revision, summary: 'Restore revision ' + revision.revision,
          })}>Propose restore</Button>}
      </section>}
      {!composer && role && <section className="flow-semantic-config" aria-label="Edit team revision roles">
        <h4>{role.name}</h4>
        <div className="flow-ontology-revision-actions">
          <Checkbox checked={role.canEdit} disabled={busy} onChange={event => setRole({ ...role, canEdit: event.target.checked })}>Editor</Checkbox>
          <Checkbox checked={role.canReview} disabled={busy} onChange={event => setRole({ ...role, canReview: event.target.checked })}>Reviewer</Checkbox>
        </div>
        <div className="flow-ontology-revision-actions">
          <Button size="small" onClick={() => setRole(undefined)} disabled={busy}>Cancel</Button>
          <Button size="small" type="primary" onClick={saveRole} loading={busy}>Save revision roles</Button>
        </div>
      </section>}
    </>}
  </section>;
}
