import { useEffect, useRef } from 'react';

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

  useEffect(() => {
    if (!enabled) return;

    let active = true;
    let timer: ReturnType<typeof setTimeout>;

    const poll = async () => {
      try {
        await savedCallback.current();
      } finally {
        if (active) timer = setTimeout(poll, interval);
      }
    };

    // Let StrictMode clean up its probe mount before sending the first request.
    // Recursive timeouts also guarantee that slow requests never overlap.
    timer = setTimeout(poll, 0);

    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [interval, enabled]);
}
