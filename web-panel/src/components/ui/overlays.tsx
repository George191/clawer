import React, { useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { createRoot, type Root } from 'react-dom/client';
import './overlays.css';

type AnyProps = { [key: string]: any };
type Container = HTMLElement | (() => HTMLElement) | string | false;
const cx = (...values: any[]) => values.filter(Boolean).join(' ');
const focusable = 'button:not(:disabled),a[href],input:not(:disabled),select:not(:disabled),textarea:not(:disabled),[tabindex]:not([tabindex="-1"])';
const visibleItems = (node: HTMLElement | null) => node ? Array.from(node.querySelectorAll<HTMLElement>(focusable)).filter(el => !el.hidden && el.getClientRects().length > 0 && !el.closest('[hidden]')) : [];
const dialogStack: string[] = [];
const popupStack: string[] = [];
let bodyLocks = 0;
let bodyStyle = { overflow: '', paddingRight: '' };
function lockBody() {
  if (bodyLocks++ === 0) {
    bodyStyle = { overflow: document.body.style.overflow, paddingRight: document.body.style.paddingRight };
    const gap = Math.max(0, window.innerWidth - document.documentElement.clientWidth);
    document.body.style.overflow = 'hidden';
    if (gap) document.body.style.paddingRight = `${parseFloat(getComputedStyle(document.body).paddingRight || '0') + gap}px`;
  }
  return () => { if (--bodyLocks === 0) Object.assign(document.body.style, bodyStyle); };
}
function Portal({ children, container }: { children: React.ReactNode; container?: Container }) {
  if (container === false) return <>{children}</>;
  const target = typeof container === 'function' ? container() : typeof container === 'string' ? document.querySelector(container) : container;
  return createPortal(children, target || document.body);
}
function useDialog(open: boolean, ref: React.RefObject<HTMLElement>, close: (e?: any) => void, keyboard = true) {
  const id = useId();
  const wasOpen=useRef(false); const opener=useRef<HTMLElement|null>(null);
  if (open && !wasOpen.current) opener.current=document.activeElement as HTMLElement|null;
  wasOpen.current=open;
  const latest = useRef({ close, keyboard }); latest.current = { close, keyboard };
  useLayoutEffect(() => {
    if (!open) return;
    const previous = opener.current;
    const unlock = lockBody(); dialogStack.push(id);
    const timer = window.setTimeout(() => { if (dialogStack[dialogStack.length - 1] === id && !ref.current?.contains(document.activeElement)) (ref.current?.querySelector<HTMLElement>('[autofocus]') || visibleItems(ref.current)[0] || ref.current)?.focus(); }, 0);
    const key = (event: KeyboardEvent) => {
      if (dialogStack[dialogStack.length - 1] !== id) return;
      if (event.key === 'Escape' && popupStack.length) return;
      if (event.key === 'Escape' && latest.current.keyboard) { event.preventDefault(); event.stopPropagation(); latest.current.close(event); }
      if (event.key === 'Tab') {
        const nodes = visibleItems(ref.current); const first = nodes[0]; const last = nodes[nodes.length - 1];
        if (!first) { event.preventDefault(); ref.current?.focus(); }
        else if (event.shiftKey && (document.activeElement === first || !ref.current?.contains(document.activeElement))) { event.preventDefault(); last?.focus(); }
        else if (!event.shiftKey && (document.activeElement === last || !ref.current?.contains(document.activeElement))) { event.preventDefault(); first.focus(); }
      }
    };
    const contain = (event: FocusEvent) => { if (dialogStack[dialogStack.length - 1] === id && event.target instanceof Node && !ref.current?.contains(event.target) && !(event.target instanceof Element && event.target.closest('[data-ui-popup]'))) (visibleItems(ref.current)[0] || ref.current)?.focus(); };
    document.addEventListener('keydown', key, true); document.addEventListener('focusin', contain);
    return () => { window.clearTimeout(timer); document.removeEventListener('keydown', key, true); document.removeEventListener('focusin', contain); dialogStack.splice(dialogStack.indexOf(id), 1); unlock(); if (previous?.isConnected) previous.focus(); };
  }, [open, id, ref]);
}
function NativeButton({ children, loading, danger, type, className, icon, ...props }: AnyProps) {
  return <button {...props} type="button" disabled={props.disabled || loading} aria-busy={loading || undefined} className={cx('ui-btn', type === 'primary' && 'ui-btn-primary', danger && 'ui-btn-danger', className)}>{loading ? <span className="ui-overlay-spinner" aria-hidden="true" /> : icon}{children}</button>;
}
export interface ModalProps extends AnyProps {
  open?: boolean; title?: React.ReactNode; children?: React.ReactNode; onOk?: (e?: any) => unknown; onCancel?: (e?: any) => unknown;
}
function ModalView(props: ModalProps) {
  const { open = false, title, children, onCancel, onOk, width = 520, footer, confirmLoading, okText = 'OK', cancelText = 'Cancel', okButtonProps = {}, cancelButtonProps = {}, closable = true, closeIcon = '×', keyboard = true, maskClosable = true, mask = true, centered, className, rootClassName, style, styles = {}, getContainer, zIndex = 1000, forceRender, destroyOnClose, destroyOnHidden, afterClose, afterOpenChange } = props;
  const ref = useRef<HTMLDivElement>(null); const titleId = useId(); const [visited, setVisited] = useState(open); const [pending, setPending] = useState(false); const active = useRef(true); const previous = useRef(open);
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  useEffect(() => { if (open) setVisited(true); if (previous.current !== open) { afterOpenChange?.(open); if (!open) afterClose?.(); } previous.current = open; }, [open, afterClose, afterOpenChange]);
  const cancel = (event?: any) => { if (!pending && !confirmLoading) onCancel?.(event); };
  useDialog(open, ref, cancel, keyboard);
  if (!open && (!visited && !forceRender || destroyOnClose || destroyOnHidden)) return null;
  const confirm = async (event: React.MouseEvent) => {
    if (pending || confirmLoading) return;
    try { const result = onOk?.(event); if (result && typeof (result as Promise<unknown>).then === 'function') { setPending(true); await result; } }
    catch { /* The caller owns the error message; keep the dialog and its input intact. */ }
    finally { if (active.current) setPending(false); }
  };
  return <Portal container={getContainer}><div className={cx('ui-modal-root', rootClassName)} style={{ zIndex, display: open ? undefined : 'none' }} aria-hidden={!open || undefined}>
    {mask && <div className="ui-modal-mask" style={styles.mask} />}
    <div className={cx('ui-modal-wrap', centered && 'ui-modal-centered')} style={styles.wrapper} onMouseDown={event => { if (event.target === event.currentTarget && maskClosable && mask) cancel(event); }}>
      <div ref={ref} role="dialog" aria-modal="true" aria-labelledby={title ? titleId : undefined} aria-label={title ? undefined : props['aria-label'] || 'Dialog'} tabIndex={-1} className={cx('ui-modal', className)} style={{ width, ...style }}>
        <div className="ui-modal-content" style={styles.content}>
          {closable && <button type="button" className="ui-modal-close" aria-label="Close dialog" onClick={cancel} disabled={pending || confirmLoading}>{closeIcon}</button>}
          {title != null && <div className="ui-modal-header" style={styles.header}><div className="ui-modal-title" id={titleId}>{title}</div></div>}
          <div className="ui-modal-body" style={styles.body}>{children}</div>
          {footer !== null && <div className="ui-modal-footer" style={styles.footer}>{footer === undefined ? <><NativeButton {...cancelButtonProps} onClick={cancel} disabled={cancelButtonProps.disabled || pending || confirmLoading}>{cancelText}</NativeButton><NativeButton type="primary" {...okButtonProps} onClick={confirm} loading={pending || confirmLoading}>{okText}</NativeButton></> : typeof footer === 'function' ? footer(null, { OkBtn: () => <NativeButton type="primary" onClick={confirm}>{okText}</NativeButton>, CancelBtn: () => <NativeButton onClick={cancel}>{cancelText}</NativeButton> }) : footer}</div>}
        </div>
      </div>
    </div>
  </div></Portal>;
}
export interface ModalFuncProps extends ModalProps { content?: React.ReactNode; onOk?: (close?: any) => unknown; }
export interface ModalHandle { destroy: () => void; update: (next: ModalFuncProps | ((previous: ModalFuncProps) => ModalFuncProps)) => void; }
const modalInstances = new Set<ModalHandle>();
function staticModal(initial: ModalFuncProps): ModalHandle {
  const host = document.createElement('div'); host.dataset.uiStaticModal = ''; document.body.appendChild(host); const root = createRoot(host);
  let config = { ...initial }; let destroyed = false; let pending = false;
  const destroy = () => { if (destroyed) return; destroyed = true; modalInstances.delete(handle); root.render(null); window.setTimeout(() => { root.unmount(); host.remove(); }, 0); };
  const handle: ModalHandle = { destroy, update(next) { if (destroyed) return; config = typeof next === 'function' ? next(config) : { ...config, ...next }; render(); } };
  const invoke = async (kind: 'onOk' | 'onCancel') => {
    if (pending) return;
    const handler = config[kind];
    try {
      const result = handler?.(destroy);
      if (result && typeof (result as Promise<unknown>).then === 'function') { pending = true; render(); await result; pending = false; destroy(); }
      else if (result !== false && (!handler || handler.length === 0)) destroy();
    } catch { pending = false; render(); }
  };
  const render = () => { if (!destroyed) root.render(<ModalView {...config} rootClassName={cx('ui-confirmation',config.rootClassName)} open centered={config.centered ?? true} confirmLoading={pending || config.confirmLoading} onOk={() => invoke('onOk')} onCancel={() => invoke('onCancel')}>{config.content}</ModalView>); };
  modalInstances.add(handle); render(); return handle;
}
export const Modal = Object.assign(ModalView, {
  confirm: staticModal,
  info: (config: ModalFuncProps) => staticModal({ ...config, cancelButtonProps: { style: { display: 'none' }, ...config.cancelButtonProps } }),
  success: (config: ModalFuncProps) => staticModal({ ...config, cancelButtonProps: { style: { display: 'none' }, ...config.cancelButtonProps } }),
  warning: (config: ModalFuncProps) => staticModal({ ...config, cancelButtonProps: { style: { display: 'none' }, ...config.cancelButtonProps } }),
  error: (config: ModalFuncProps) => staticModal({ ...config, cancelButtonProps: { style: { display: 'none' }, ...config.cancelButtonProps } }),
  destroyAll: () => [...modalInstances].forEach(instance => instance.destroy()),
});
export interface DrawerProps extends AnyProps { open?: boolean; title?: React.ReactNode; children?: React.ReactNode; onClose?: (event?: any) => void; }
export function Drawer({ open = false, title, children, onClose, width = 378, height = 378, placement = 'right', closable = true, closeIcon = '×', mask = true, maskClosable = true, keyboard = true, extra, footer, className, rootClassName, style, styles = {}, zIndex = 1000, getContainer, destroyOnClose, destroyOnHidden, forceRender, afterOpenChange }: DrawerProps) {
  const ref = useRef<HTMLDivElement>(null); const titleId = useId(); const [visited, setVisited] = useState(open); const previous = useRef(open);
  useEffect(() => { if (open) setVisited(true); if (previous.current !== open) afterOpenChange?.(open); previous.current = open; }, [open, afterOpenChange]);
  useDialog(open, ref, event => onClose?.(event), keyboard);
  if (!open && (!visited && !forceRender || destroyOnClose || destroyOnHidden)) return null;
  const vertical = placement === 'top' || placement === 'bottom';
  return <Portal container={getContainer}><div className={cx('ui-drawer', `ui-drawer-${placement}`, rootClassName)} style={{ zIndex, display: open ? undefined : 'none', ...styles.wrapper }} aria-hidden={!open || undefined}>
    {mask && <div className="ui-drawer-mask" style={styles.mask} onClick={event => { if (maskClosable) onClose?.(event); }} />}
    <div className={cx('ui-drawer-content-wrapper', className)} style={{ [vertical ? 'height' : 'width']: vertical ? height : width, ...style }}>
      <div ref={ref} tabIndex={-1} role="dialog" aria-modal={mask || undefined} aria-labelledby={title ? titleId : undefined} aria-label={title ? undefined : 'Details'} className="ui-drawer-content" style={styles.content}>
        <div className="ui-drawer-header" style={styles.header}><div className="ui-drawer-header-title">{closable && <button type="button" className="ui-drawer-close" aria-label="Close drawer" onClick={onClose}>{closeIcon}</button>}<div className="ui-drawer-title" id={titleId}>{title}</div></div>{extra && <div className="ui-drawer-extra">{extra}</div>}</div>
        <div className="ui-drawer-body" style={styles.body}>{children}</div>
        {footer != null && <div className="ui-drawer-footer" style={styles.footer}>{footer}</div>}
      </div>
    </div>
  </div></Portal>;
}

function useOpen(controlled: boolean | undefined, initial = false, change?: (open: boolean, info?: any) => void) {
  const [state, setState] = useState(initial); const open = controlled === undefined ? state : controlled;
  const setOpen = (next: boolean, info?: any) => { if (controlled === undefined) setState(next); if (next !== open) change?.(next, info); };
  return [open, setOpen] as const;
}
function usePopup(open: boolean, anchor: React.RefObject<HTMLElement>, popup: React.RefObject<HTMLElement>, placement = 'bottomLeft', point?: { x: number; y: number } | null) {
  const [style, setStyle] = useState<React.CSSProperties>({ visibility: 'hidden' });
  useLayoutEffect(() => {
    if (!open) return;
    const update = () => {
      if (!anchor.current || !popup.current) return;
      const a = anchor.current.getBoundingClientRect(); const box = popup.current.getBoundingClientRect(); const gap = 6; const pad = 8; const isTop = placement.startsWith('top'); const isLeft = placement.startsWith('left'); const isRight = placement.startsWith('right');
      let x = point?.x ?? (isLeft ? a.left - box.width - gap : isRight ? a.right + gap : placement.endsWith('Right') ? a.right - box.width : placement === 'top' || placement === 'bottom' ? a.left + (a.width - box.width) / 2 : a.left);
      let y = point?.y ?? (isTop ? a.top - box.height - gap : isLeft || isRight ? a.top + (a.height - box.height) / 2 : a.bottom + gap);
      if (!point && !isLeft && !isRight && y + box.height > innerHeight - pad && a.top >= box.height + gap) y = a.top - box.height - gap;
      if (!point && isTop && y < pad) y = a.bottom + gap;
      x = Math.max(pad, Math.min(x, innerWidth - box.width - pad)); y = Math.max(pad, Math.min(y, innerHeight - box.height - pad));
      const next: React.CSSProperties = { position: 'fixed', left: x, top: y, visibility: 'visible', maxHeight: Math.max(80, innerHeight - y - pad), maxWidth: 'calc(100vw - 16px)' };
      setStyle(old => old.left === next.left && old.top === next.top && old.maxHeight === next.maxHeight && old.visibility === 'visible' ? old : next);
    };
    update(); const observer = new ResizeObserver(update); if (anchor.current) observer.observe(anchor.current); if (popup.current) observer.observe(popup.current);
    window.addEventListener('resize', update); window.addEventListener('scroll', update, true);
    return () => { observer.disconnect(); window.removeEventListener('resize', update); window.removeEventListener('scroll', update, true); };
  }, [open, anchor, popup, placement, point]);
  return style;
}
function useDismiss(open: boolean, anchor: React.RefObject<HTMLElement>, popup: React.RefObject<HTMLElement>, dismiss: () => void, restore = true) {
  const id = useId();
  const latest = useRef(dismiss); latest.current = dismiss;
  useEffect(() => {
    if (!open) return;
    popupStack.push(id);
    const outside = (event: PointerEvent) => { if (event.target instanceof Node && !anchor.current?.contains(event.target) && !popup.current?.contains(event.target)) latest.current(); };
    const key = (event: KeyboardEvent) => { if (event.key === 'Escape' && popupStack[popupStack.length - 1] === id) { event.preventDefault(); event.stopPropagation(); latest.current(); if (restore) (anchor.current?.querySelector<HTMLElement>(focusable) || anchor.current)?.focus(); } };
    document.addEventListener('pointerdown', outside); document.addEventListener('keydown', key);
    return () => { document.removeEventListener('pointerdown', outside); document.removeEventListener('keydown', key); popupStack.splice(popupStack.indexOf(id),1); };
  }, [open, anchor, popup, restore,id]);
}
function childWithAria(children: React.ReactNode, aria: AnyProps) {
  return React.isValidElement(children) ? React.cloneElement(children as React.ReactElement<any>, { ...aria, tabIndex: (children.props as any).tabIndex ?? (typeof children.type === 'string' && !['button','a','input'].includes(children.type) ? 0 : undefined) }) : children;
}
export interface MenuItem extends AnyProps { key?: React.Key; label?: React.ReactNode; icon?: React.ReactNode; children?: MenuItem[]; disabled?: boolean; type?: 'divider' | 'group'; }
export interface MenuProps extends AnyProps { items?: (MenuItem | null | false)[]; onClick?: (info: any) => void; selectedKeys?: React.Key[]; }
export interface DropdownProps extends AnyProps { children?: React.ReactNode; menu?: MenuProps; open?: boolean; onOpenChange?: (open:boolean, info?: {source:string}) => void; }
function MenuList({ items = [], menu, close, parentPath = [] }: { items?: (MenuItem | null | false)[]; menu: AnyProps; close: () => void; parentPath?: string[] }) {
  const [expanded, setExpanded] = useState<string | null>(null);
  const list=useRef<HTMLUListElement>(null); const pendingChildFocus=useRef<string|null>(null);
  useLayoutEffect(()=>{if(pendingChildFocus.current===expanded && expanded!==null){(list.current?.querySelector(`:scope > li[data-menu-key="${CSS.escape(expanded)}"] > ul button:not(:disabled)`) as HTMLElement)?.focus();pendingChildFocus.current=null;}},[expanded]);
  const keydown = (event: React.KeyboardEvent<HTMLUListElement>) => {
    const own = Array.from(event.currentTarget.querySelectorAll<HTMLElement>(':scope > li > button:not(:disabled)')); const index = own.indexOf(document.activeElement as HTMLElement);
    if (['ArrowDown','ArrowUp','Home','End'].includes(event.key)) { event.preventDefault(); event.stopPropagation(); const next = event.key === 'Home' ? 0 : event.key === 'End' ? own.length - 1 : (index + (event.key === 'ArrowDown' ? 1 : -1) + own.length) % own.length; own[next]?.focus(); }
    if (event.key === 'Tab') close();
    if (event.key === 'ArrowLeft' && parentPath.length) { event.preventDefault(); event.stopPropagation(); (event.currentTarget.parentElement?.querySelector(':scope > button') as HTMLElement)?.focus(); }
  };
  return <ul ref={list} className={cx('ui-dropdown-menu', parentPath.length && 'ui-dropdown-menu-sub')} role="menu" onKeyDown={keydown}>{items.filter(Boolean).map((entry, index) => {
    const item = entry as MenuItem; const key = String(item.key ?? index);
    if (item.type === 'divider') return <li key={key} role="separator" className="ui-dropdown-menu-item-divider" />;
    if (item.type === 'group') return <li key={key} role="none" className="ui-dropdown-menu-item-group"><div className="ui-dropdown-menu-item-group-title">{item.label}</div><MenuList items={item.children} menu={menu} close={close} parentPath={[...parentPath,key]} /></li>;
    const selected = menu.selectedKeys?.map(String).includes(key); const hasChildren = !!item.children?.length;
    const select = (domEvent: React.MouseEvent) => { if (hasChildren) { setExpanded(expanded === key ? null : key); return; } const info = { key, keyPath:[key,...parentPath.slice().reverse()], domEvent }; item.onClick?.(info); menu.onClick?.(info); close(); };
    return <li key={key} role="none" data-menu-key={key} className="ui-dropdown-menu-entry" onMouseEnter={() => setExpanded(hasChildren ? key : null)}><button type="button" role="menuitem" disabled={item.disabled} aria-disabled={item.disabled || undefined} aria-haspopup={hasChildren ? 'menu' : undefined} aria-expanded={hasChildren ? expanded === key : undefined} className={cx('ui-dropdown-menu-item', selected && 'ui-dropdown-menu-item-selected', item.danger && 'ui-dropdown-menu-item-danger', item.className)} onClick={select} onKeyDown={event => { if (event.key === 'ArrowRight' && hasChildren) { event.preventDefault(); event.stopPropagation(); if(expanded===key)(event.currentTarget.parentElement?.querySelector('ul button:not(:disabled)') as HTMLElement)?.focus();else{pendingChildFocus.current=key;setExpanded(key);} } }}>{item.icon && <span className="ui-dropdown-menu-item-icon">{item.icon}</span>}<span className="ui-dropdown-menu-title-content">{item.label}</span>{hasChildren && <span aria-hidden="true">›</span>}</button>{hasChildren && expanded === key && <MenuList items={item.children} menu={menu} close={close} parentPath={[...parentPath,key]} />}</li>;
  })}</ul>;
}
export function Dropdown({ children, menu = {}, open: controlled, defaultOpen, onOpenChange, disabled, trigger = ['hover'], placement = 'bottomLeft', overlayClassName, rootClassName, overlayStyle, dropdownRender, popupRender, getPopupContainer, mouseEnterDelay = .12, mouseLeaveDelay = .15 }: DropdownProps) {
  const [open, setOpen] = useOpen(controlled, defaultOpen, onOpenChange); const anchor = useRef<HTMLSpanElement>(null); const popup = useRef<HTMLDivElement>(null); const [point, setPoint] = useState<{x:number;y:number}|null>(null); const timer = useRef<number>(); const id = useId(); const keyboard = useRef(false);
  const change = (next: boolean, source = 'trigger') => { window.clearTimeout(timer.current); if (!disabled) setOpen(next, {source}); };
  const style = usePopup(open, anchor, popup, placement, point); useDismiss(open, anchor, popup, () => change(false));
  useEffect(() => () => window.clearTimeout(timer.current), []);
  useEffect(() => { if (open && keyboard.current) { const timer = window.setTimeout(() => visibleItems(popup.current)[0]?.focus(), 0); return () => window.clearTimeout(timer); } }, [open]);
  const hover = (next: boolean) => { if (!trigger.includes('hover')) return; window.clearTimeout(timer.current); timer.current = window.setTimeout(() => change(next), 1000 * (next ? mouseEnterDelay : mouseLeaveDelay)); };
  const close = () => change(false, 'menu'); const content = <MenuList items={menu.items} menu={menu} close={close} />;
  return <><span ref={anchor} className="ui-dropdown-trigger" onMouseEnter={() => hover(true)} onMouseLeave={() => hover(false)} onClick={() => { if (trigger.includes('click')) { keyboard.current = false; setPoint(null); change(!open); } }} onContextMenu={event => { if (trigger.includes('contextMenu')) { event.preventDefault(); keyboard.current = true; setPoint({ x:event.clientX,y:event.clientY }); change(true); } }} onKeyDown={event => { if (event.key === 'ArrowDown' || (event.shiftKey && event.key === 'F10') || event.key === 'ContextMenu') { event.preventDefault(); keyboard.current = true; setPoint(null); change(true); } }}>{childWithAria(children, { 'aria-haspopup':'menu', 'aria-expanded':open, 'aria-controls':open?id:undefined, 'aria-disabled':disabled || undefined })}</span>
    {open && !disabled && <Portal container={getPopupContainer ? () => getPopupContainer(anchor.current) : undefined}><div ref={popup} id={id} data-ui-popup className={cx('ui-dropdown', overlayClassName, rootClassName)} style={{ ...style,...overlayStyle }} onMouseEnter={() => hover(true)} onMouseLeave={() => hover(false)}>{(popupRender || dropdownRender)?.(content) ?? content}</div></Portal>}
  </>;
}
export interface TooltipProps extends AnyProps { title?: React.ReactNode | (() => React.ReactNode); children?: React.ReactNode; }
export function Tooltip({ title, children, placement = 'top', open: controlled, defaultOpen, onOpenChange, rootClassName, overlayClassName, overlayStyle, getPopupContainer, mouseEnterDelay = .25, mouseLeaveDelay = .08 }: TooltipProps) {
  const [open, setOpen] = useOpen(controlled, defaultOpen, onOpenChange); const anchor = useRef<HTMLSpanElement>(null); const popup = useRef<HTMLDivElement>(null); const timer = useRef<number>(); const id = useId(); const show = open && title != null && title !== ''; const style = usePopup(show, anchor, popup, placement);
  const change = (next: boolean, delay = 0) => { window.clearTimeout(timer.current); if (delay) timer.current = window.setTimeout(() => setOpen(next), delay * 1000); else setOpen(next); };
  useEffect(() => () => window.clearTimeout(timer.current), []);
  useDismiss(show, anchor, popup, () => change(false), false);
  return <><span ref={anchor} className="ui-tooltip-trigger" onMouseEnter={() => change(true,mouseEnterDelay)} onMouseLeave={() => change(false,mouseLeaveDelay)} onFocus={() => change(true)} onBlur={() => change(false)}>{childWithAria(children, { 'aria-describedby': show ? id : undefined })}</span>{show && <Portal container={getPopupContainer ? () => getPopupContainer(anchor.current) : undefined}><div ref={popup} id={id} role="tooltip" data-ui-popup className={cx('ui-tooltip',rootClassName,overlayClassName)} style={{...style,...overlayStyle}} onMouseEnter={() => change(true)} onMouseLeave={() => change(false,mouseLeaveDelay)}><div className="ui-tooltip-inner">{typeof title === 'function' ? title() : title}</div></div></Portal>}</>;
}
export interface PopconfirmProps extends TooltipProps { description?: React.ReactNode; onConfirm?: (event?: any) => unknown; onCancel?: (event?: any) => unknown; }
export function Popconfirm({ children, title, description, onConfirm, onCancel, open: controlled, defaultOpen, onOpenChange, disabled, placement = 'top', okText = 'OK', cancelText = 'Cancel', okButtonProps = {}, cancelButtonProps = {}, rootClassName, overlayClassName, getPopupContainer, icon }: PopconfirmProps) {
  const [open,setOpen] = useOpen(controlled,defaultOpen,onOpenChange); const [pending,setPending] = useState(false); const anchor = useRef<HTMLSpanElement>(null); const popup = useRef<HTMLDivElement>(null); const id = useId(); const active = useRef(true); const style = usePopup(open,anchor,popup,placement);
  useEffect(() => { active.current=true; return () => { active.current=false; }; }, []);
  useLayoutEffect(()=>{if(!open||disabled)return;const previous=document.activeElement as HTMLElement|null;const panel=popup.current;const timer=window.setTimeout(()=>visibleItems(panel)[0]?.focus(),0);return()=>{window.clearTimeout(timer);if(previous?.isConnected&&(document.activeElement===document.body||panel?.contains(document.activeElement)))previous.focus();};},[open,disabled]);
  const close = () => { if (!pending) setOpen(false); }; useDismiss(open,anchor,popup,close);
  const confirm = async (event: React.MouseEvent) => { if (pending) return; try { const result=onConfirm?.(event); if (result && typeof (result as Promise<unknown>).then==='function') { setPending(true); await result; } if (active.current) setOpen(false); } catch { /* Keep confirmation open on failure. */ } finally { if (active.current) setPending(false); } };
  return <><span ref={anchor} className="ui-popconfirm-trigger" onClick={event => { if (!disabled) { event.stopPropagation(); setOpen(!open); } }} onKeyDown={event => { if (!disabled && (event.key==='Enter'||event.key===' ')) { event.preventDefault(); setOpen(!open); } }}>{childWithAria(children,{'aria-haspopup':'dialog','aria-expanded':open,'aria-controls':open?id:undefined})}</span>{open && !disabled && <Portal container={getPopupContainer ? () => getPopupContainer(anchor.current) : undefined}><div ref={popup} id={id} role="alertdialog" tabIndex={-1} aria-labelledby={`${id}-title`} aria-describedby={description?`${id}-description`:undefined} data-ui-popup className={cx('ui-popover','ui-popconfirm',rootClassName,overlayClassName)} style={style} onKeyDown={event=>{if(event.key!=='Tab')return;const buttons=visibleItems(popup.current);const first=buttons[0];const last=buttons[buttons.length-1];if(!first){event.preventDefault();popup.current?.focus();}else if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}}}><div className="ui-popover-inner"><div className="ui-popconfirm-message"><span className="ui-popconfirm-message-icon" aria-hidden="true">{icon ?? '!'}</span><div><div id={`${id}-title`} className="ui-popconfirm-title">{typeof title==='function'?title():title}</div>{description && <div id={`${id}-description`} className="ui-popconfirm-description">{description}</div>}</div></div><div className="ui-popconfirm-buttons"><NativeButton {...cancelButtonProps} disabled={pending || cancelButtonProps.disabled} onClick={(event:React.MouseEvent) => { onCancel?.(event); close(); }}>{cancelText}</NativeButton><NativeButton type="primary" {...okButtonProps} loading={pending} onClick={confirm}>{okText}</NativeButton></div></div></div></Portal>}</>;
}

export type MessageType = 'success' | 'error' | 'warning' | 'info' | 'loading';
export interface MessageConfig extends AnyProps { content?: React.ReactNode; key?: React.Key; type?: MessageType; duration?: number; onClose?: () => void; }
type Notice = MessageConfig & { key: React.Key; finish: () => void; timer?: ReturnType<typeof setTimeout> };
export interface MessageResult extends Promise<void> { (): void; }
let noticeRoot: Root | null = null; let noticeHost: HTMLDivElement | null = null; let sequence = 0;
let messageSettings: AnyProps = { top:24,duration:3,maxCount:5 }; const notices = new Map<React.Key,Notice>();
const symbols: Record<MessageType,string> = {success:'✓',error:'×',warning:'!',info:'i',loading:''};
function renderNotices() {
  if (!noticeHost) { noticeHost=document.createElement('div'); noticeHost.dataset.uiMessages=''; (messageSettings.getContainer?.() || document.body).appendChild(noticeHost); noticeRoot=createRoot(noticeHost); }
  noticeRoot!.render(<div className={cx('ui-message',messageSettings.rtl && 'ui-message-rtl')} style={{top:messageSettings.top}}>{[...notices.values()].map(notice => <div key={notice.key} className={cx('ui-message-notice',notice.className)} style={notice.style}><div className={cx('ui-message-notice-content',`ui-message-${notice.type || 'info'}`)} role={notice.type==='error' || notice.type==='warning'?'alert':'status'} aria-live={notice.type==='error'?'assertive':'polite'} onClick={notice.onClick}><span aria-hidden="true" className={cx('ui-message-icon',notice.type==='loading' && 'ui-overlay-spinner')}>{notice.icon ?? symbols[notice.type || 'info']}</span><span>{notice.content}</span></div></div>)}</div>);
}
function removeNotice(key: React.Key) { const notice=notices.get(key); if (!notice) return; clearTimeout(notice.timer); notices.delete(key); notice.finish(); notice.onClose?.(); renderNotices(); }
function openMessage(config: MessageConfig): MessageResult {
  const key=config.key ?? `message-${++sequence}`; const old=notices.get(key); clearTimeout(old?.timer);
  let resolve!: () => void; const promise=new Promise<void>(finish=>{resolve=finish;});
  const finish=()=>{old?.finish();resolve();};
  const notice: Notice={...config,key,finish}; const duration=config.duration ?? (config.type==='loading'?0:messageSettings.duration);
  notices.set(key,notice); if (duration>0) notice.timer=setTimeout(()=>removeNotice(key),duration*1000);
  while (notices.size>messageSettings.maxCount) removeNotice(notices.keys().next().value!);
  renderNotices(); const result=(()=>removeNotice(key)) as MessageResult; result.then=promise.then.bind(promise);result.catch=promise.catch.bind(promise);result.finally=promise.finally.bind(promise);Object.defineProperty(result,Symbol.toStringTag,{value:'Promise'}); return result;
}
const typedMessage=(type:MessageType)=>(content:React.ReactNode|MessageConfig,duration?:number|(()=>void),onClose?:()=>void)=>openMessage(typeof content==='object' && content!==null && !React.isValidElement(content) && 'content' in content ? {...content,type} : {content:content as React.ReactNode,type,duration:typeof duration==='number'?duration:undefined,onClose:typeof duration==='function'?duration:onClose});
export const message={
  config(config:AnyProps) { messageSettings={...messageSettings,...config}; if (noticeRoot) renderNotices(); },
  open:openMessage, success:typedMessage('success'),error:typedMessage('error'),warning:typedMessage('warning'),info:typedMessage('info'),loading:typedMessage('loading'),
  destroy(key?:React.Key) { if (key!==undefined) removeNotice(key); else [...notices.keys()].forEach(removeNotice); },
  useMessage: () => [message,null] as const,
};
export const notification={
  open:(config:AnyProps)=>openMessage({...config,content:<div><strong>{config.message}</strong>{config.description && <div>{config.description}</div>}</div>,duration:config.duration??4.5}),
  success:(config:AnyProps)=>notification.open({...config,type:'success'}),error:(config:AnyProps)=>notification.open({...config,type:'error'}),warning:(config:AnyProps)=>notification.open({...config,type:'warning'}),info:(config:AnyProps)=>notification.open({...config,type:'info'}),
  destroy:message.destroy,config:message.config,useNotification:()=>[notification,null] as const,
};
