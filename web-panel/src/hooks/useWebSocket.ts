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
  const shouldReconnect = useRef(false);

  const savedCallbacks = useRef({ onMessage, onOpen, onClose, onError });
  savedCallbacks.current = { onMessage, onOpen, onClose, onError };

  const connect = useCallback(() => {
    if (
      wsRef.current?.readyState === WebSocket.OPEN
      || wsRef.current?.readyState === WebSocket.CONNECTING
    ) return;

    clearTimeout(reconnectTimer.current);

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
      if (wsRef.current === ws) wsRef.current = null;
      setConnected(false);
      savedCallbacks.current.onClose?.();
      if (shouldReconnect.current) {
        reconnectTimer.current = setTimeout(connect, reconnectInterval);
      }
    };

    ws.onerror = (e) => {
      savedCallbacks.current.onError?.(e);
    };
  }, [url, reconnectInterval]);

  useEffect(() => {
    shouldReconnect.current = true;
    connect();
    return () => {
      shouldReconnect.current = false;
      clearTimeout(reconnectTimer.current);
      const ws = wsRef.current;
      wsRef.current = null;
      if (ws) {
        ws.onclose = null;
        ws.close();
      }
    };
  }, [connect]);

  const send = useCallback((data: string) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(data);
      return true;
    }
    return false;
  }, []);

  return { connected, send };
}
