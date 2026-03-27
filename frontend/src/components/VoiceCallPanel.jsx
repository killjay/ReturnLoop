export default function VoiceCallPanel({ voiceStatus }) {
  if (!voiceStatus) {
    return (
      <div className="flex items-center justify-center h-96 text-gray-600">
        <div className="text-center">
          <div className="text-4xl mb-3">&#x1F4DE;</div>
          <p className="text-sm">No active voice calls</p>
          <p className="text-xs text-gray-700 mt-1">Voice calls appear here when Whisperer negotiates with customers</p>
        </div>
      </div>
    );
  }

  const isActive = voiceStatus.status === 'active';

  return (
    <div className="p-6">
      {/* Call header */}
      <div className={`flex items-center gap-4 p-4 rounded-xl mb-4 ${
        isActive ? 'bg-blue-500/10 border border-blue-500/30' : 'bg-gray-800 border border-gray-700'
      }`}>
        <div className={`w-12 h-12 rounded-full flex items-center justify-center text-xl ${
          isActive ? 'bg-blue-500/20' : 'bg-gray-700'
        }`}>
          {isActive ? <span className="pulse-dot">&#x1F4DE;</span> : '&#x2705;'}
        </div>
        <div>
          <div className="flex items-center gap-2">
            <h3 className="text-white font-semibold">{voiceStatus.customer_name || 'Customer'}</h3>
            {isActive && <span className="text-xs bg-blue-500 text-white px-2 py-0.5 rounded-full">LIVE</span>}
            {!isActive && <span className="text-xs bg-gray-600 text-gray-300 px-2 py-0.5 rounded-full">ENDED</span>}
          </div>
          <p className="text-sm text-gray-400">
            {voiceStatus.product_name || 'Product'} &middot; Strategy: {voiceStatus.strategy || 'negotiating'}
          </p>
        </div>
        {isActive && (
          <div className="ml-auto flex items-center gap-2">
            <div className="flex gap-0.5">
              {[1,2,3,4,5].map(i => (
                <div key={i} className="w-1 bg-blue-500 rounded-full pulse-dot" style={{
                  height: `${8 + Math.random() * 16}px`,
                  animationDelay: `${i * 0.15}s`,
                }} />
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Transcript */}
      {voiceStatus.transcript_summary && (
        <div className="bg-gray-800/50 rounded-lg p-4">
          <h4 className="text-xs font-semibold text-gray-500 uppercase mb-2">Conversation</h4>
          <div className="space-y-3">
            <TranscriptLine speaker="agent" text={voiceStatus.transcript_summary} />
            {voiceStatus.outcome === 'full_return' && (
              <TranscriptLine speaker="customer" text="I appreciate the offer, but I'd like to go ahead with the return." />
            )}
            {voiceStatus.outcome === 'keep_with_refund' && (
              <TranscriptLine speaker="customer" text="That sounds great, I'll keep it! Thank you." />
            )}
          </div>
        </div>
      )}

      {/* Outcome */}
      {voiceStatus.outcome && (
        <div className={`mt-4 p-3 rounded-lg ${
          voiceStatus.prevented ? 'bg-emerald-500/10 border border-emerald-500/30' : 'bg-cyan-500/10 border border-cyan-500/30'
        }`}>
          <div className="flex items-center gap-2">
            <span className="text-lg">{voiceStatus.prevented ? '&#x2705;' : '&#x1F4E6;'}</span>
            <div>
              <p className={`text-sm font-semibold ${voiceStatus.prevented ? 'text-emerald-400' : 'text-cyan-400'}`}>
                {voiceStatus.prevented ? 'Return Prevented' : 'Return Accepted -- Routing to Loop Matcher'}
              </p>
              <p className="text-xs text-gray-500">
                Outcome: {voiceStatus.outcome?.replace(/_/g, ' ')}
              </p>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function TranscriptLine({ speaker, text }) {
  const isAgent = speaker === 'agent';
  return (
    <div className={`flex gap-3 ${isAgent ? '' : 'flex-row-reverse'}`}>
      <div className={`w-7 h-7 rounded-full flex items-center justify-center text-xs shrink-0 ${
        isAgent ? 'bg-blue-500/20 text-blue-400' : 'bg-gray-700 text-gray-400'
      }`}>
        {isAgent ? 'AI' : 'C'}
      </div>
      <div className={`max-w-[80%] p-3 rounded-lg text-sm ${
        isAgent ? 'bg-blue-500/10 text-gray-300' : 'bg-gray-800 text-gray-300'
      }`}>
        {text}
      </div>
    </div>
  );
}
