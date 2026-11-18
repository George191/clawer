import { useEffect, useState } from 'react';
import NativeDot, { type DotActivity } from './index';

type Anchor = { left: number; top: number; width: number; height: number; activity: DotActivity };

// Keep the iframe mounted outside route and task transitions.
export default function ScoutDot() {
  const [anchor, setAnchor] = useState<Anchor | null>(null);
  useEffect(() => {
    let frame = 0;
    const update = () => {
      const element = document.querySelector<HTMLElement>('.ai-collect-dot');
      const rect = element?.getBoundingClientRect();
      const next = rect && rect.width && rect.height ? { left: rect.left, top: rect.top, width: rect.width, height: rect.height, activity: element!.dataset.activity as DotActivity } : null;
      setAnchor(previous => JSON.stringify(previous) === JSON.stringify(next) ? previous : next);
      frame = requestAnimationFrame(update);
    };
    update();
    return () => cancelAnimationFrame(frame);
  }, []);
  return <div className="scout-dot-host" style={{ position: 'fixed', left: anchor?.left ?? -200, top: anchor?.top ?? 0, width: anchor?.width ?? 112, height: anchor?.height ?? 112, zIndex: 20, pointerEvents: anchor ? 'auto' : 'none' }}><NativeDot activity={anchor?.activity ?? 'Sleep'} preload /></div>;
}
