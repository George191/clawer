import { useEffect, useRef, useState } from 'react';
import NativeDot, { type DotActivity } from './index';

type Anchor = { left: number; top: number; width: number; height: number; activity: DotActivity; idle: boolean };

// Keep the iframe mounted outside route and task transitions.
export default function ScoutDot() {
  const [anchor, setAnchor] = useState<Anchor | null>(null);
  const [awake, setAwake] = useState(false);
  const host = useRef<HTMLDivElement>(null);
  const sleepTimer = useRef<ReturnType<typeof setTimeout>>();
  const idle = anchor?.idle ?? false;
  useEffect(() => {
    clearTimeout(sleepTimer.current);
    setAwake(false);
    if (!idle) return;
    const scheduleSleep = () => {
      clearTimeout(sleepTimer.current);
      sleepTimer.current = setTimeout(() => setAwake(false), 5 * 60 * 1000);
    };
    const interact = (event: MessageEvent) => {
      if (event.origin !== location.origin || event.source !== host.current?.querySelector('iframe')?.contentWindow || event.data?.type !== 'helio-dot-status' || event.data.state !== 'interacted') return;
      setAwake(true);
      scheduleSleep();
    };
    scheduleSleep();
    window.addEventListener('message', interact);
    return () => {
      clearTimeout(sleepTimer.current);
      window.removeEventListener('message', interact);
    };
  }, [idle]);
  useEffect(() => {
    let frame = 0;
    const update = () => {
      const element = document.querySelector<HTMLElement>('.ai-collect-dot');
      const rect = element?.getBoundingClientRect();
      const next = rect && rect.width && rect.height ? { left: rect.left, top: rect.top, width: rect.width, height: rect.height, activity: element!.dataset.activity as DotActivity, idle: element!.classList.contains('is-landing') } : null;
      setAnchor(previous => JSON.stringify(previous) === JSON.stringify(next) ? previous : next);
      frame = requestAnimationFrame(update);
    };
    update();
    return () => cancelAnimationFrame(frame);
  }, []);
  return <div ref={host} className="scout-dot-host" style={{ position: 'fixed', left: anchor?.left ?? -200, top: anchor?.top ?? 0, width: anchor?.width ?? 112, height: anchor?.height ?? 112, zIndex: 20, pointerEvents: anchor ? 'auto' : 'none' }}><NativeDot activity={idle && awake ? 'None' : anchor?.activity ?? 'Paused'} preload /></div>;
}
