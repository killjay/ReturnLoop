import { useState, useEffect, useRef } from 'react';
import { useWebSocket } from './hooks/useWebSocket';
import { fetchMetrics, fetchReturns, fetchTraces } from './utils/api';
import Dashboard from './components/Dashboard';

function App() {
  const ws = useWebSocket();
  const [metrics, setMetrics] = useState(null);
  const [returns, setReturns] = useState([]);
  const [dbTraces, setDbTraces] = useState([]);
  const prevReturnCountRef = useRef(0);

  // Poll metrics + returns every 3 seconds
  useEffect(() => {
    const load = async () => {
      try {
        const [m, r] = await Promise.all([fetchMetrics(), fetchReturns()]);
        setMetrics(m);
        setReturns(r);

        // Detect new returns and fetch their traces from DB
        if (r.length > prevReturnCountRef.current) {
          const newReturns = r.slice(0, r.length - prevReturnCountRef.current);
          for (const ret of newReturns) {
            try {
              const traces = await fetchTraces(ret.id);
              if (traces && traces.length > 0) {
                setDbTraces(prev => {
                  const existingIds = new Set(prev.map(t => t.id));
                  const newTraces = traces.filter(t => !existingIds.has(t.id));
                  return [...newTraces.reverse(), ...prev].slice(0, 100);
                });
              }
            } catch (e) { /* ignore */ }
          }
        }
        prevReturnCountRef.current = r.length;
      } catch (e) {
        console.log('Backend not ready yet');
      }
    };
    load();
    const interval = setInterval(load, 3000);
    return () => clearInterval(interval);
  }, []);

  // Also poll latest traces from all recent returns every 2 seconds
  useEffect(() => {
    const pollTraces = async () => {
      try {
        const r = await fetchReturns();
        if (r.length > 0) {
          // Fetch traces from the most recent return
          const latest = r[0];
          const traces = await fetchTraces(latest.id);
          if (traces && traces.length > 0) {
            setDbTraces(prev => {
              const existingIds = new Set(prev.map(t => t.id));
              const newTraces = traces.filter(t => !existingIds.has(t.id));
              if (newTraces.length === 0) return prev;
              return [...newTraces.reverse(), ...prev].slice(0, 100);
            });
          }
        }
      } catch (e) { /* ignore */ }
    };
    const interval = setInterval(pollTraces, 2000);
    return () => clearInterval(interval);
  }, []);

  // Merge WebSocket metric updates
  useEffect(() => {
    if (ws.metrics && metrics) {
      setMetrics(prev => ({
        ...prev,
        total_cost_saved: round(prev.total_cost_saved + (ws.metrics.cost_saved || 0)),
        total_miles_saved: round(prev.total_miles_saved + (ws.metrics.miles_saved || 0)),
        total_co2_saved_kg: round(prev.total_co2_saved_kg + (ws.metrics.co2_saved_kg || 0)),
      }));
    }
  }, [ws.metrics]);

  // Combine WebSocket traces + DB-polled traces (deduplicated)
  const allTraces = (() => {
    const combined = [...ws.traces, ...dbTraces];
    const seen = new Set();
    return combined.filter(t => {
      if (seen.has(t.id)) return false;
      seen.add(t.id);
      return true;
    });
  })();

  return (
    <Dashboard
      metrics={metrics}
      returns={returns}
      traces={allTraces}
      voiceStatus={ws.voiceStatus}
      returnUpdates={ws.returnUpdates}
      connected={ws.connected}
    />
  );
}

function round(n) {
  return Math.round(n * 100) / 100;
}

export default App;
