import { useEffect, useState } from 'react';
import type { MenuProps } from '@/components/ui';
import { CodexShareDialog } from './CodexChatDialogs';
import { persistTurns, type SavedChat, type SavedTurn, type ShareSnapshot } from './chatStore';
import { CodexToolbarButton, CodexToolbarMenu } from './CodexToolbar';
import CodexChangeReview from './CodexChangeReview';
import PreviewResults, { type PreviewResult } from './PreviewResults';
import ScoutToolbarIcon from './ScoutToolbarIcon';

type Props = {
  chatId: string; visible: boolean; conversationMenu: MenuProps;
  snapshot: ShareSnapshot; onSave: () => Promise<SavedChat>;
  onRefresh: () => Promise<void>;
  open: boolean; turn?: PreviewResult; step: number; running: boolean;
  selectionRequest: number;
  onClose: () => void; onReviewChange: (turn: SavedTurn) => Promise<void>;
  onRequestChanges: (text: string) => void;
  summaryOpen: boolean; onToggleSummary: () => void; onTogglePreview: () => void;
  onNotice: (message: string) => void;
};

export default function PreviewWorkspace(props: Props) {
  const { chatId, visible, conversationMenu, snapshot, onSave, onRefresh, open, turn, step, running, selectionRequest, onReviewChange, onRequestChanges, summaryOpen, onToggleSummary, onTogglePreview, onNotice } = props;
  const [sharing, setSharing] = useState<ShareSnapshot>();
  useEffect(() => { setSharing(undefined); }, [chatId]);
  const browsing = running && step === 1 && turn?.id === snapshot.turns[snapshot.turns.length - 1]?.id;
  return <>
    <aside id="scout-collection-results" className="scout-collection-results" aria-label="Collection results" aria-hidden={!open}>
      {browsing && <><header className="scout-file-header">https://example.org/articles</header><div className="scout-file-body"><PreviewResults turn={turn} step={step} running={running} /></div></>}
      <div className="scout-review-surface" hidden={browsing}><CodexChangeReview chatId={chatId} turns={snapshot.turns} activeTurn={turn?.id} activeStep={step} selectionRequest={selectionRequest} running={running} onChange={onReviewChange} onRefresh={onRefresh} onNotice={onNotice} onRequestChanges={onRequestChanges} /></div>
    </aside>
    {visible && <div className="scout-conversation-toolbar" role="toolbar" aria-label="Conversation actions">
      <CodexToolbarMenu menu={conversationMenu} shortcuts={{ rename: 'R', pin: 'P', archive: 'A' }}><CodexToolbarButton aria-label="More conversation actions" title="More"><ScoutToolbarIcon name="more" /></CodexToolbarButton></CodexToolbarMenu>
      <CodexToolbarButton aria-label="Share chat" title="Share chat" disabled={!snapshot.turns.length} onClick={() => { void persistTurns(snapshot.turns).then(turns => setSharing({ title: snapshot.title, turns })).catch(error => onNotice(error.message)); }}><ScoutToolbarIcon name="share" /></CodexToolbarButton>
      <CodexToolbarButton className="scout-context-toggle" aria-label="Conversation context" title="Summary" aria-pressed={summaryOpen} aria-expanded={summaryOpen} onClick={onToggleSummary} aria-controls="scout-conversation-context"><ScoutToolbarIcon name="summary" /></CodexToolbarButton>
      <CodexToolbarButton className="scout-preview-toggle" aria-label={open ? 'Hide file preview' : 'Show file preview'} title={open ? 'Hide file preview' : 'Show file preview'} aria-pressed={open} aria-controls="scout-collection-results" onClick={onTogglePreview}><ScoutToolbarIcon name={open ? 'panel' : 'panelHidden'} /></CodexToolbarButton>
    </div>}
    {sharing && <CodexShareDialog snapshot={sharing} onSave={onSave} onClose={() => setSharing(undefined)} />}
  </>;
}
