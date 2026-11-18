import React, { useEffect, useRef, useState } from 'react';
import './style.css';

export const DOT_ACTIVITIES = {
  None: { label: '空闲', detail: '原生待机与休息动作' },
  Working: { label: '工作', detail: '原版全息终端与操作动作' },
  Searching: { label: '搜索', detail: '原版搜索道具与动作' },
  Creating: { label: '创建', detail: '原版笔记与编辑动作' },
  Payment: { label: '支付', detail: '原版支付道具与动作' },
  Thinking: { label: '思考', detail: '原版思考动作与灯泡' },
  NeedsInput: { label: '等待输入', detail: '原版等待输入提示' },
  Ready: { label: '完成', detail: '原版完成反馈' },
  Paused: { label: '暂停 / 睡眠', detail: '原版休息与 ZZZ 动画' },
  Error: { label: '错误', detail: '原版异常反馈' },
} as const;
export type DotActivity = keyof typeof DOT_ACTIVITIES;

export interface NativeDotProps {
  activity?: DotActivity;
  paused?: boolean;
  dark?: boolean;
  preload?: boolean;
}

export default function NativeDot({ activity = 'None', paused = false, dark = true, preload = false }: NativeDotProps) {
  const frameRef = useRef<HTMLIFrameElement>(null);
  const desiredRef = useRef({ activity, paused, dark });
  const visibilityRef = useRef(true);
  const reducedRef = useRef(false);
  const [state, setState] = useState('loading');
  const [error, setError] = useState('');
  desiredRef.current = { activity, paused, dark };
  function configure() {
    frameRef.current?.contentWindow?.postMessage({ type: 'helio-dot-config', ...desiredRef.current, active: (visibilityRef.current || preload && state === 'loading') && !document.hidden, reduced: reducedRef.current }, location.origin);
  }
  useEffect(() => {
    const frame = frameRef.current;
    if (!frame) return;
    const motion = window.matchMedia('(prefers-reduced-motion: reduce)');
    reducedRef.current = motion.matches;
    const observer = new IntersectionObserver(entries => { visibilityRef.current = entries[0]?.isIntersecting ?? false; configure(); });
    observer.observe(frame);
    const onMotion = () => { reducedRef.current = motion.matches; configure(); };
    const onMessage = (event: MessageEvent) => {
      if (event.source !== frame.contentWindow || event.origin !== location.origin || event.data?.type !== 'helio-dot-status') return;
      if (event.data.state === 'loaded') configure();
      if (event.data.state === 'ready') setState('ready');
      if (event.data.state === 'failed') { setError(String(event.data.message)); setState('failed'); }
    };
    window.addEventListener('message', onMessage);
    document.addEventListener('visibilitychange', configure);
    motion.addEventListener('change', onMotion);
    configure();
    return () => {
      observer.disconnect();
      window.removeEventListener('message', onMessage);
      document.removeEventListener('visibilitychange', configure);
      motion.removeEventListener('change', onMotion);
    };
  }, [preload, state]);
  useEffect(configure, [activity, paused, dark, preload, state]);
  useEffect(() => {
    const move = (event: PointerEvent) => {
      if (!visibilityRef.current || event.pointerType === 'touch') return;
      const rect = frameRef.current?.getBoundingClientRect();
      if (!rect) return;
      frameRef.current?.contentWindow?.postMessage({ type: 'helio-dot-pointer', x: Math.max(0, Math.min(1, 0.5 + (event.clientX - rect.left - rect.width / 2) / innerWidth)), y: Math.max(0, Math.min(1, 0.5 + (event.clientY - rect.top - rect.height / 2) / innerHeight)) }, location.origin);
    };
    window.addEventListener('pointermove', move, { passive: true });
    return () => window.removeEventListener('pointermove', move);
  }, []);
  return <div className="native-dot-view" data-renderer="dot-original" data-state={state} data-activity={activity}>
    <iframe ref={frameRef} className="native-dot-frame" src="/dot/frame.html" title={`Helio · ${DOT_ACTIVITIES[activity].label}`} onLoad={configure} />
    {state === 'loading' && <span className="native-dot-loading" role="status" aria-label="伙伴正在准备"><i /><i /><i /></span>}
    {error && <span className="native-dot-message is-error" role="alert">{error}</span>}
  </div>;
}
