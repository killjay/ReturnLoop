export default function MetricsPanel({ metrics }) {
  const m = metrics || {};

  return (
    <div className="space-y-6">
      <div>
        <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-3">Returns Today</h3>
        <div className="space-y-2">
          <MetricRow label="Total" value={m.total_returns || 0} />
          <MetricRow label="Prevented" value={m.total_prevented || 0} color="text-emerald-400" />
          <MetricRow label="Rerouted" value={m.total_rerouted || 0} color="text-cyan-400" />
          <MetricRow label="Warehouse" value={(m.status_counts?.warehouse) || 0} color="text-gray-400" />
        </div>
      </div>

      <div className="border-t border-gray-800 pt-4">
        <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-3">Savings</h3>
        <div className="space-y-3">
          <MetricCard
            label="Cost Saved"
            value={`$${(m.total_cost_saved || 0).toFixed(2)}`}
            color="from-emerald-600 to-emerald-800"
          />
          <MetricCard
            label="Miles Saved"
            value={(m.total_miles_saved || 0).toLocaleString()}
            color="from-cyan-600 to-cyan-800"
          />
          <MetricCard
            label="CO2 Avoided"
            value={`${(m.total_co2_saved_kg || 0).toFixed(1)} kg`}
            color="from-green-600 to-green-800"
          />
        </div>
      </div>

      <div className="border-t border-gray-800 pt-4">
        <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-3">Agents</h3>
        <div className="space-y-1.5">
          {['Prophet', 'Whisperer', 'Loop Matcher', 'Recoverer', 'Learner'].map(agent => (
            <div key={agent} className="flex items-center gap-2 text-xs">
              <div className="w-1.5 h-1.5 rounded-full bg-emerald-500 pulse-dot" />
              <span className="text-gray-400">{agent}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function MetricRow({ label, value, color = 'text-white' }) {
  return (
    <div className="flex justify-between items-center">
      <span className="text-sm text-gray-500">{label}</span>
      <span className={`text-lg font-bold ${color} metric-value`}>{value}</span>
    </div>
  );
}

function MetricCard({ label, value, color }) {
  return (
    <div className={`bg-gradient-to-r ${color} rounded-lg p-3`}>
      <div className="text-xs text-white/70">{label}</div>
      <div className="text-xl font-bold text-white metric-value">{value}</div>
    </div>
  );
}
