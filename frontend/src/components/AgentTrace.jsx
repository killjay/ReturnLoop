const AGENT_COLORS = {
  prophet: { bg: 'bg-purple-500/10', border: 'border-purple-500/30', dot: 'bg-purple-500', text: 'text-purple-400' },
  whisperer: { bg: 'bg-blue-500/10', border: 'border-blue-500/30', dot: 'bg-blue-500', text: 'text-blue-400' },
  loop_matcher: { bg: 'bg-cyan-500/10', border: 'border-cyan-500/30', dot: 'bg-cyan-500', text: 'text-cyan-400' },
  recoverer: { bg: 'bg-amber-500/10', border: 'border-amber-500/30', dot: 'bg-amber-500', text: 'text-amber-400' },
  learner: { bg: 'bg-emerald-500/10', border: 'border-emerald-500/30', dot: 'bg-emerald-500', text: 'text-emerald-400' },
};

const AGENT_LABELS = {
  prophet: 'Prophet',
  whisperer: 'Whisperer',
  loop_matcher: 'Loop Matcher',
  recoverer: 'Recoverer',
  learner: 'Learner',
};

export default function AgentTrace({ traces }) {
  if (!traces || traces.length === 0) {
    return (
      <div className="flex items-center justify-center h-96 text-gray-600">
        <div className="text-center">
          <div className="text-4xl mb-3">&#x1F916;</div>
          <p className="text-sm">Waiting for agent activity...</p>
          <p className="text-xs text-gray-700 mt-1">Trigger a return to see agents in action</p>
        </div>
      </div>
    );
  }

  // Group traces by return_request_id and extract context
  const groups = [];
  let currentGroup = null;
  for (const trace of traces) {
    const rid = trace.return_request_id || '';
    if (!currentGroup || currentGroup.rid !== rid) {
      // Extract customer name and order ID from data_used
      const d = trace.data_used || {};
      currentGroup = {
        rid,
        customerName: d.customer_name || '',
        orderId: d.order_id || '',
        productName: d.product_name || '',
        traces: [],
      };
      groups.push(currentGroup);
    }
    currentGroup.traces.push(trace);
    // Update context from later traces if earlier ones didn't have it
    const d = trace.data_used || {};
    if (d.customer_name && !currentGroup.customerName) currentGroup.customerName = d.customer_name;
    if (d.order_id && !currentGroup.orderId) currentGroup.orderId = d.order_id;
    if (d.product_name && !currentGroup.productName) currentGroup.productName = d.product_name;
  }

  return (
    <div className="p-4 space-y-2 max-h-[calc(100vh-200px)] overflow-y-auto">
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-sm font-semibold text-gray-400">Live Agent Reasoning</h3>
        <span className="text-xs text-gray-600">{traces.length} steps</span>
      </div>

      {groups.map((group, gi) => (
        <div key={group.rid || gi}>
          {/* Return context header */}
          {(group.customerName || group.orderId) && (
            <div className="flex items-center gap-3 mb-2 mt-4 first:mt-0 px-1">
              <div className="w-6 h-6 rounded-full bg-gray-800 flex items-center justify-center text-xs text-white font-bold">
                {(group.customerName || '?')[0].toUpperCase()}
              </div>
              <div>
                <span className="text-sm font-semibold text-white">{group.customerName || 'Customer'}</span>
                {group.orderId && <span className="text-xs text-gray-500 ml-2">Order {group.orderId}</span>}
                {group.productName && <span className="text-xs text-gray-600 ml-2">· {group.productName}</span>}
              </div>
            </div>
          )}

          {group.traces.map((trace, i) => {
        const colors = AGENT_COLORS[trace.agent_name] || AGENT_COLORS.prophet;
        const label = AGENT_LABELS[trace.agent_name] || trace.agent_name;

        return (
          <div
            key={trace.id || i}
            className={`trace-entry ${colors.bg} border ${colors.border} rounded-lg p-3`}
          >
            <div className="flex items-center gap-2 mb-1.5">
              <div className={`w-2 h-2 rounded-full ${colors.dot} pulse-dot`} />
              <span className={`text-xs font-semibold ${colors.text}`}>{label}</span>
              <span className="text-xs text-gray-600">Step {trace.step_number}</span>
              <span className="text-xs text-gray-700 ml-auto">{trace.action}</span>
            </div>

            <p className="text-sm text-gray-300 leading-relaxed">{trace.reasoning}</p>

            {trace.decision && (
              <div className="mt-2 flex items-center gap-2">
                <span className="text-xs text-gray-500">Decision:</span>
                <span className={`text-xs font-semibold ${colors.text}`}>{trace.decision}</span>
                {trace.confidence > 0 && (
                  <span className="text-xs text-gray-600 ml-auto">
                    {(trace.confidence * 100).toFixed(0)}% confidence
                  </span>
                )}
              </div>
            )}

            {trace.data_used && Object.keys(trace.data_used).length > 0 && (
              <DataBadges data={trace.data_used} />
            )}
          </div>
        );
      })}
        </div>
      ))}
    </div>
  );
}

function DataBadges({ data }) {
  const displayKeys = ['cost_saved', 'miles_saved', 'co2_saved_kg', 'risk_score', 'distance_miles',
    'lifetime_value', 'return_rate', 'sizing_complaint_pct', 'direct_miles', 'warehouse_miles'];

  const badges = Object.entries(data)
    .filter(([key]) => displayKeys.includes(key))
    .slice(0, 4);

  if (badges.length === 0) return null;

  return (
    <div className="mt-2 flex flex-wrap gap-1.5">
      {badges.map(([key, value]) => (
        <span key={key} className="text-xs bg-gray-800 text-gray-400 px-2 py-0.5 rounded">
          {formatKey(key)}: {formatValue(key, value)}
        </span>
      ))}
    </div>
  );
}

function formatKey(key) {
  return key.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

function formatValue(key, value) {
  if (typeof value === 'number') {
    if (key.includes('cost') || key.includes('value') || key.includes('price')) return `$${value.toFixed(2)}`;
    if (key.includes('rate') || key.includes('score') || key.includes('pct')) return `${(value * (value > 1 ? 1 : 100)).toFixed(0)}%`;
    if (key.includes('miles')) return `${value.toFixed(0)} mi`;
    if (key.includes('co2')) return `${value.toFixed(1)} kg`;
    return value.toFixed(1);
  }
  return String(value);
}
