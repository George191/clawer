import { useEffect, useRef, useCallback, useState } from 'react';

interface UseWebSocketOptions {
  onMessage: (data: string) => void;
  onOpen?: () => void;
  onClose?: () => void;
  onError?: (e: Event) => void;
  reconnectInterval?: number;
}

/**
 * WebSocket hook — 自动重连
 * @param url                  WebSocket URL
 * @param options.onMessage    收到消息回调
 */
export function useWebSocket(url: string, options: UseWebSocketOptions) {
  const { onMessage, onOpen, onClose, onError, reconnectInterval = 3000 } = options;
  const wsRef = useRef<WebSocket | null>(null);
  const [connected, setConnected] = useState(false);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout>>();

  const savedCallbacks = useRef({ onMessage, onOpen, onClose, onError });
  savedCallbacks.current = { onMessage, onOpen, onClose, onError };

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {
      setConnected(true);
      savedCallbacks.current.onOpen?.();
    };

    ws.onmessage = (e) => {
      savedCallbacks.current.onMessage(e.data);
    };

    ws.onclose = () => {
      setConnected(false);
      savedCallbacks.current.onClose?.();
      reconnectTimer.current = setTimeout(connect, reconnectInterval);
    };

    ws.onerror = (e) => {
      savedCallbacks.current.onError?.(e);
    };
  }, [url, reconnectInterval]);

  useEffect(() => {
    connect();
    return () => {
      clearTimeout(reconnectTimer.current);
      wsRef.current?.close();
    };
  }, [connect]);

  const send = useCallback((data: string) => {
    wsRef.current?.send(data);
  }, []);

  return { connected, send };
}