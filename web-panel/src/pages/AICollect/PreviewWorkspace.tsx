import { useEffect, useState } from 'react';
import { Button, Modal, type MenuProps } from '@/components/ui';
import * as Popover from '@radix-ui/react-popover';
import { CodexToolbarButton, CodexToolbarMenu } from './CodexToolbar';
import PreviewResults, { previewFile, type PreviewResult } from './PreviewResults';
import { ArtifactIcon, type StageArtifact } from './StageArtifacts';
import ScoutToolbarIcon from './ScoutToolbarIcon';

type Props = {
  visible: boolean; conversationMenu: MenuProps;
  open: boolean; turn?: PreviewResult; step: number; running: boolean;
  onSelect: (artifact: StageArtifact) => void; onClose: () => void;
  summaryOpen: boolean; onTogglePreview: () => void;
  onNotice: (message: string) => void;
};

export default function PreviewWorkspace(props: Props) {
  const { visible, conversationMenu, open, turn, step, running, onSelect, onClose, summaryOpen, onTogglePreview, onNotice } = props;
  const [tabs, setTabs] = useState<StageArtifact[]>([]);
  const [structured, setStructured] = useState(false);
  const [wrap, setWrap] = useState(false);
  const [lineNumbers, setLineNumbers] = useState(true);
  const [sharing, setSharing] = useState(false);
  const [copied, setCopied] = useState(false);
  const [canShare, setCanShare] = useState(false);
  const activeKey = turn ? `${turn.id}-${step}` : '';
  const file = previewFile(turn, step);
  useEffect(() => {
    if (!open || !turn) return;
    const artifact = { key: `${turn.id}-${step}`, turn: turn.id, step };
    setTabs(current => current.some(tab => tab.key === artifact.key) ? current : [...current, artifact]);
  }, [open, turn?.id, step]);
  useEffect(() => { if (open) document.getElementById(`scout-tab-${activeKey}`)?.scrollIntoView({ block: 'nearest', inline: 'nearest' }); }, [open, activeKey, tabs.length]);

  function closeTab(tab: StageArtifact) {
    const next = tabs.filter(item => item.key !== tab.key);
    setTabs(next);
    if (tab.key === activeKey) {
      const replacement = next[Math.min(tabs.indexOf(tab), next.length - 1)];
      if (replacement) { onSelect(replacement); requestAnimationFrame(() => document.getElementById(`scout-tab-${replacement.key}`)?.focus()); }
      else onClose();
    }
  }
  async function copy() {
    try { await navigator.clipboard.writeText(file.content); setCopied(true); onNotice('File contents copied'); }
    catch { onNotice('Unable to copy. Select the file contents in Share file to copy them.'); }
  }
  function download() {
    const url = URL.createObjectURL(new Blob([file.content], { type: 'text/plain;charset=utf-8' }));
    const anchor = document.createElement('a');
    anchor.href = url; anchor.download = file.name; anchor.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  async function systemShare() {
    try { await navigator.share({ title: file.name, files: [new File([file.content], file.name, { type: 'text/plain' })] }); setSharing(false); }
    catch (error) { if (!(error instanceof DOMException && error.name === 'AbortError')) onNotice('Unable to open system sharing.'); }
  }
  return <>
    <aside id="scout-collection-results" className="scout-collection-results" aria-label="Collection results" aria-hidden={!open}>
      <header className="scout-file-header">
        <div className="scout-file-tabs" role="tablist" aria-label="Open files">
          {tabs.map((tab, index) => <div key={tab.key} className={`scout-file-tab ${tab.key === activeKey ? 'is-active' : ''}`}>
            <button type="button" role="tab" id={`scout-tab-${tab.key}`} aria-controls="scout-file-panel" aria-selected={tab.key === activeKey} tabIndex={tab.key === activeKey ? 0 : -1} title={`${previewFile(undefined, tab.step).name} · Response ${tab.turn}`} onClick={() => onSelect(tab)} onKeyDown={event => {
              const next = event.key === 'ArrowRight' ? tabs[(index + 1) % tabs.length] : event.key === 'ArrowLeft' ? tabs[(index - 1 + tabs.length) % tabs.length] : event.key === 'Home' ? tabs[0] : event.key === 'End' ? tabs[tabs.length - 1] : null;
              if (next) { event.preventDefault(); onSelect(next); document.getElementById(`scout-tab-${next.key}`)?.focus(); }
              else if (event.key === 'Delete') { event.preventDefault(); closeTab(tab); }
            }}><ArtifactIcon step={tab.step} /><span>{previewFile(undefined, tab.step).name}</span></button>
            <button type="button" className="scout-tab-close" aria-label={`Close ${previewFile(undefined, tab.step).name}`} title="Close tab" onClick={() => closeTab(tab)}><ScoutToolbarIcon name="close" /></button>
          </div>)}
        </div>

      </header>
      <div id="scout-file-panel" className="scout-file-body" role="tabpanel" aria-labelledby={activeKey ? `scout-tab-${activeKey}` : undefined}>
        <PreviewResults turn={turn} step={step} running={running} structured={structured} wrap={wrap} lineNumbers={lineNumbers} />
      </div>
    </aside>
    {visible && <Popover.Anchor asChild><div className="scout-conversation-toolbar" role="toolbar" aria-label="Conversation actions">
      <CodexToolbarMenu menu={{ selectedKeys: [...(conversationMenu.selectedKeys ?? []), ...(structured ? ['file:structured'] : []), ...(wrap ? ['file:wrap'] : []), ...(lineNumbers ? ['file:numbers'] : [])], items: [
        ...(conversationMenu.items ?? []),
        ...(open && turn ? [{ type: 'divider' as const }, { key: 'file:copy', label: 'Copy file contents' }, { key: 'file:download', label: 'Download file' }, { key: 'file:structured', label: 'Structured template preview' }, { key: 'file:wrap', label: 'Wrap lines' }, { key: 'file:numbers', label: 'Line numbers' }, { type: 'divider' as const }, { key: 'file:others', label: 'Close other tabs', disabled: tabs.length < 2 }, { key: 'file:close', label: 'Close file preview' }] : []),
      ], onClick: ({ key }) => { if (key === 'file:copy') void copy(); else if (key === 'file:download') download(); else if (key === 'file:structured') setStructured(value => !value); else if (key === 'file:wrap') setWrap(value => !value); else if (key === 'file:numbers') setLineNumbers(value => !value); else if (key === 'file:others') setTabs(current => current.filter(tab => tab.key === activeKey)); else if (key === 'file:close') onClose(); else conversationMenu.onClick?.({ key }); } }}><CodexToolbarButton aria-label="More conversation actions" title="More"><ScoutToolbarIcon name="more" /></CodexToolbarButton></CodexToolbarMenu>
      <CodexToolbarButton aria-label="Share file" title="Share file" disabled={!turn} onClick={() => { setCopied(false); setCanShare(Boolean(navigator.canShare?.({ files: [new File([file.content], file.name, { type: 'text/plain' })] }))); setSharing(true); }}><ScoutToolbarIcon name="share" /></CodexToolbarButton>
      <Popover.Trigger asChild><CodexToolbarButton className="scout-context-toggle" aria-label="Conversation context" title="Toggle summary" aria-pressed={summaryOpen} aria-controls="scout-conversation-context"><ScoutToolbarIcon name="summary" /></CodexToolbarButton></Popover.Trigger>
      <CodexToolbarButton aria-label={open ? 'Hide file preview' : 'Show file preview'} title={open ? 'Hide file preview' : 'Show file preview'} aria-pressed={open} aria-controls="scout-collection-results" onClick={onTogglePreview}><ScoutToolbarIcon name="panel" /></CodexToolbarButton>
    </div></Popover.Anchor>}
    <Modal open={sharing} title={`Share ${file.name}`} onCancel={() => setSharing(false)} width={560} className="scout-share-dialog" footer={<><Button onClick={download}>Download file</Button><Button onClick={() => void copy()}>{copied ? 'Copied' : 'Copy contents'}</Button>{canShare && <Button type="primary" onClick={() => void systemShare()}>System share</Button>}</>}>
      <p>Share the contents of the current file.</p>
      <textarea aria-label="Shared file contents" readOnly value={file.content} rows={10} />
    </Modal>
  </>;
}
