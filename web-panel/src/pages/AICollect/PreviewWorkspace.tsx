import { useEffect, useState } from 'react';
import { Button, Dropdown, Modal } from '@/components/ui';
import PreviewResults, { previewFile, type PreviewResult } from './PreviewResults';
import { ArtifactIcon, type StageArtifact } from './StageArtifacts';
import ScoutToolbarIcon from './ScoutToolbarIcon';

type Props = {
  open: boolean; turn?: PreviewResult; step: number; running: boolean;
  onSelect: (artifact: StageArtifact) => void; onClose: () => void;
  summaryOpen: boolean; summaryPinned: boolean; canPin: boolean;
  onToggleSummary: () => void; onTogglePinned: () => void; onNotice: (message: string) => void;
};

export default function PreviewWorkspace(props: Props) {
  const { open, turn, step, running, onSelect, onClose, summaryOpen, summaryPinned, canPin, onToggleSummary, onTogglePinned, onNotice } = props;
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
        <div className="scout-file-actions" role="toolbar" aria-label="File actions">
          <Dropdown trigger={['click']} placement="bottomRight" overlayClassName="scout-file-menu" menu={{ items: [{ key: 'copy', label: 'Copy file contents' }, { key: 'download', label: 'Download file' }, { type: 'divider' }, { key: 'others', label: 'Close other tabs', disabled: tabs.length < 2 }, { key: 'close', label: 'Close file preview' }], onClick: ({ key }) => { if (key === 'copy') void copy(); else if (key === 'download') download(); else if (key === 'others') setTabs(current => current.filter(tab => tab.key === activeKey)); else onClose(); } }}><button type="button" className="scout-toolbar-button" aria-label="More file actions" title="More"><ScoutToolbarIcon name="more" /></button></Dropdown>
          <button type="button" className="scout-toolbar-button" aria-label="Share file" title="Share" onClick={() => { setCopied(false); setCanShare(Boolean(navigator.canShare?.({ files: [new File([file.content], file.name, { type: 'text/plain' })] }))); setSharing(true); }}><ScoutToolbarIcon name="share" /></button>
          <Dropdown trigger={['click']} placement="bottomRight" overlayClassName="scout-file-menu" menu={{ selectedKeys: [structured ? 'structured' : '', wrap ? 'wrap' : '', lineNumbers ? 'numbers' : ''], items: [{ key: 'structured', label: 'Structured template preview' }, { key: 'wrap', label: 'Wrap lines' }, { key: 'numbers', label: 'Line numbers' }, { type: 'divider' }, { key: 'summary', label: summaryOpen ? 'Hide summary' : 'Show summary' }], onClick: ({ key }) => { if (key === 'structured') setStructured(value => !value); else if (key === 'wrap') setWrap(value => !value); else if (key === 'numbers') setLineNumbers(value => !value); else onToggleSummary(); } }}><button type="button" className="scout-toolbar-button is-round" aria-label="Preview options" title="Preview options"><ScoutToolbarIcon name="summary" /></button></Dropdown>
          <button type="button" className="scout-toolbar-button scout-summary-pin" aria-label={summaryPinned ? 'Unpin summary panel' : 'Pin summary panel'} title={canPin ? summaryPinned ? 'Unpin summary panel' : 'Pin summary panel' : 'Pin summary on a wider window'} aria-pressed={summaryPinned} disabled={!canPin} onClick={onTogglePinned}><ScoutToolbarIcon name="panel" active={summaryPinned} /></button>
        </div>
      </header>
      <div id="scout-file-panel" className="scout-file-body" role="tabpanel" aria-labelledby={activeKey ? `scout-tab-${activeKey}` : undefined}>
        <PreviewResults turn={turn} step={step} running={running} structured={structured} wrap={wrap} lineNumbers={lineNumbers} />
      </div>
    </aside>
    <Modal open={sharing} title={`Share ${file.name}`} onCancel={() => setSharing(false)} width={560} className="scout-share-dialog" footer={<><Button onClick={download}>Download file</Button><Button onClick={() => void copy()}>{copied ? 'Copied' : 'Copy contents'}</Button>{canShare && <Button type="primary" onClick={() => void systemShare()}>System share</Button>}</>}>
      <p>Share the contents of the current file.</p>
      <textarea aria-label="Shared file contents" readOnly value={file.content} rows={10} />
    </Modal>
  </>;
}
