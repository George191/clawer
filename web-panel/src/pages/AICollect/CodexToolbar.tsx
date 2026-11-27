import { forwardRef, useState, type ButtonHTMLAttributes, type ReactNode } from 'react';
import * as Menu from '@radix-ui/react-dropdown-menu';
import type { MenuProps } from '@/components/ui';
import ScoutToolbarIcon from './ScoutToolbarIcon';
import CodexReplyIcon from './CodexReplyIcon';
import './codexToolbar.css';

// Port of Codex Sm6l's uniform toolbar / ghostActive button and W4GL.HeaderButton.
// Application state and analytics are supplied by Scout rather than Codex's scope store.
export const CodexToolbarButton = forwardRef<HTMLButtonElement, ButtonHTMLAttributes<HTMLButtonElement>>(function CodexToolbarButton({ className = '', ...props }, ref) {
  return <button ref={ref} type="button" {...props} className={`scout-toolbar-button conversation-header-control ${className}`} />;
});

// Codex uses Radix menu primitives for focus, dismissal, submenus and selection.
export function CodexToolbarMenu({ children, menu, shortcuts = {}, contextMenu = false }: { children: ReactNode; menu: MenuProps; shortcuts?: Record<string, string>; contextMenu?: boolean }) {
  const [open, setOpen] = useState(false);
  function items(entries: MenuProps['items']): ReactNode {
    return entries?.map((item, index) => {
      if (!item) return null;
      if (item.type === 'divider') return <Menu.Separator key={index} className="codex-menu-separator" />;
      if (item.children) return <Menu.Sub key={item.key}><Menu.SubTrigger disabled={item.disabled} className="codex-menu-item">{item.icon}{item.label}<span className="codex-menu-trailing codex-submenu-chevron"><ScoutToolbarIcon name="chevron" /></span></Menu.SubTrigger><Menu.Portal><Menu.SubContent className="codex-menu" sideOffset={4} collisionPadding={8}>{items(item.children)}</Menu.SubContent></Menu.Portal></Menu.Sub>;
      return <Menu.Item key={item.key} disabled={item.disabled} className="codex-menu-item" data-danger={item.key === 'delete' || undefined} onSelect={() => menu.onClick?.({ key: item.key })}>{item.icon}{item.label}{shortcuts[String(item.key)] ? <kbd aria-hidden="true" className="codex-menu-trailing">{shortcuts[String(item.key)]}</kbd> : menu.selectedKeys?.includes(item.key!) && <span className="codex-menu-trailing"><CodexReplyIcon name="copied" /></span>}</Menu.Item>;
    });
  }
  return <Menu.Root open={open} onOpenChange={setOpen}><Menu.Trigger asChild onPointerDown={event => { if (contextMenu) event.preventDefault(); }} onContextMenu={event => { if (contextMenu) { event.preventDefault(); setOpen(true); } }} onKeyDown={event => { if (!contextMenu) return; if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); event.currentTarget.click(); } else if (event.key === 'ContextMenu' || event.shiftKey && event.key === 'F10') { event.preventDefault(); setOpen(true); } }}>{children}</Menu.Trigger><Menu.Portal><Menu.Content className="codex-menu" align="end" sideOffset={4} collisionPadding={8} onKeyDown={event => {
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    const key = Object.keys(shortcuts).find(key => shortcuts[key].toLowerCase() === event.key.toLowerCase());
    if (key && menu.items?.some(item => item && String(item.key) === key && !item.disabled)) { event.preventDefault(); menu.onClick?.({ key }); setOpen(false); }
  }}>{items(menu.items)}</Menu.Content></Menu.Portal></Menu.Root>;
}

// Nwje.Section's accordion markup; React compiler caches and Codex analytics removed.
export function CodexSummarySection({ title, action, children }: { title: string; action?: ReactNode; children: ReactNode }) {
  const [expanded, setExpanded] = useState(true);
  return <section role="presentation" className="scout-context-section">
    <header className="scout-context-section-head">
      <button type="button" aria-expanded={expanded} onClick={() => setExpanded(value => !value)}><span>{title}</span><ScoutToolbarIcon name="chevron" /></button>
      {action && <div>{action}</div>}
    </header>
    <div className={`scout-context-section-content ${expanded ? 'is-expanded' : ''}`} aria-hidden={!expanded}><div>{children}</div></div>
  </section>;
}
