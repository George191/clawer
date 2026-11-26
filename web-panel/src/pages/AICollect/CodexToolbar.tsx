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
export function CodexToolbarMenu({ children, menu }: { children: ReactNode; menu: MenuProps }) {
  function items(entries: MenuProps['items']): ReactNode {
    return entries?.map((item, index) => {
      if (!item) return null;
      if (item.type === 'divider') return <Menu.Separator key={index} className="codex-menu-separator" />;
      if (item.children) return <Menu.Sub key={item.key}><Menu.SubTrigger disabled={item.disabled} className="codex-menu-item">{item.label}<span className="codex-menu-trailing codex-submenu-chevron"><ScoutToolbarIcon name="chevron" /></span></Menu.SubTrigger><Menu.Portal><Menu.SubContent className="codex-menu" sideOffset={4} collisionPadding={8}>{items(item.children)}</Menu.SubContent></Menu.Portal></Menu.Sub>;
      return <Menu.Item key={item.key} disabled={item.disabled} className="codex-menu-item" onSelect={() => menu.onClick?.({ key: item.key })}>{item.icon}{item.label}{menu.selectedKeys?.includes(item.key!) && <span className="codex-menu-trailing"><CodexReplyIcon name="copied" /></span>}</Menu.Item>;
    });
  }
  return <Menu.Root><Menu.Trigger asChild>{children}</Menu.Trigger><Menu.Portal><Menu.Content className="codex-menu" align="end" sideOffset={8} collisionPadding={8}>{items(menu.items)}</Menu.Content></Menu.Portal></Menu.Root>;
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
