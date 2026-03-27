const API_BASE = '/api';

export async function fetchMetrics() {
  const res = await fetch(`${API_BASE}/dashboard/metrics`);
  return res.json();
}

export async function fetchActiveReturns() {
  const res = await fetch(`${API_BASE}/dashboard/active`);
  return res.json();
}

export async function fetchAgentStatus() {
  const res = await fetch(`${API_BASE}/dashboard/agent-status`);
  return res.json();
}

export async function fetchTraces(returnId) {
  const res = await fetch(`${API_BASE}/dashboard/traces/${returnId}`);
  return res.json();
}

export async function fetchReturns(status = null) {
  const url = status ? `${API_BASE}/returns/?status=${status}` : `${API_BASE}/returns/`;
  const res = await fetch(url);
  return res.json();
}

export async function initiateReturn(data) {
  const res = await fetch(`${API_BASE}/returns/initiate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return res.json();
}
