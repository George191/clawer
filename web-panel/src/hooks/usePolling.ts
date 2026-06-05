import { useEffect, useRef, useCallback } from 'react';

/**
 * 轮询 hook — 按指定间隔调用回调
 * @param callback  需轮询的异步或同步函数
 * @param interval  轮询间隔 ms（默认 5000）
 * @param enabled   是否启用轮询（默认 true）
 */
export function usePolling(
  callback: () => void | Promise<void>,
  interval = 5000,
  enabled = true,
) {
  const savedCallback = useRef(callback);
  savedCallback.current = callback;

  const poll = useCallback(() => {
    savedCallback.current();
  }, []);

  useEffect(() => {
    if (!enabled) return;
    poll();
    const id = setInterval(poll, interval);
    return () => clearInterval(id);
  }, [interval, enabled, poll]);
}