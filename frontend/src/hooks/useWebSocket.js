import { useEffect, useRef, useState, useCallback } from 'react';

export function useWebSocket() {
  const wsRef = useRef(null);
  const [traces, setTraces] = useState([]);
  const [metrics, setMetrics] = useState(null);
  const [returnUpdates, setReturnUpdates] = useState([]);
  const [voiceStatus, setVoiceStatus] = useState(null);
  const [connected, setConnected] = useState(false);
  const reconnectTimeoutRef = useRef(null);

  const connect = useCallback(() => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws/live`;

    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      setConnected(true);
      console.log('WebSocket connected');
    };

    ws.onmessage = (event) => {
      const message = JSON.parse(event.data);

      switch (message.type) {
        case 'agent_trace':
          setTraces(prev => [message.data, ...prev].slice(0, 100));
          break;
        case 'metrics_update':
          setMetrics(message.data);
          break;
        case 'return_update':
          setReturnUpdates(prev => [message.data, ...prev].slice(0, 50));
          break;
        case 'voice_update':
          setVoiceStatus(message.data);
          break;
      }
    };

    ws.onclose = () => {
      setConnected(false);
      // Reconnect after 2 seconds
      reconnectTimeoutRef.current = setTimeout(connect, 2000);
    };

    ws.onerror = () => {
      ws.close();
    };
  }, []);

  useEffect(() => {
    connect();
    return () => {
      if (wsRef.current) wsRef.current.close();
      if (reconnectTimeoutRef.current) clearTimeout(reconnectTimeoutRef.current);
    };
  }, [connect]);

  const clearTraces = useCallback(() => setTraces([]), []);

  return {
    traces,
    metrics,
    returnUpdates,
    voiceStatus,
    connected,
    clearTraces,
  };
}
