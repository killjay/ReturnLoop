import { useState } from 'react';
import MetricsPanel from './MetricsPanel';
import AgentTrace from './AgentTrace';
import ReturnMap from './ReturnMap';
import VoiceCallPanel from './VoiceCallPanel';
import DemoTrigger from './DemoTrigger';
import DataSourcePanel from './DataSourcePanel';

export default function Dashboard({ metrics, returns, traces, voiceStatus, returnUpdates, connected }) {
  const [activeTab, setActiveTab] = useState('trace');

  return (
    <div className="min-h-screen bg-gray-950">
      {/* Header */}
      <header className="border-b border-gray-800 px-6 py-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-emerald-500 to-cyan-500 flex items-center justify-center text-lg font-bold">
              R
            </div>
            <div>
              <h1 className="text-xl font-bold text-white">Return Loop</h1>
              <p className="text-xs text-gray-500">Closing the loop on ecommerce returns</p>
            </div>
          </div>
          <div className="flex items-center gap-4">
            <DemoTrigger />
            <div className="flex items-center gap-2">
              <div className={`w-2 h-2 rounded-full ${connected ? 'bg-emerald-500 pulse-dot' : 'bg-red-500'}`} />
              <span className="text-xs text-gray-500">{connected ? 'Live' : 'Connecting...'}</span>
            </div>
          </div>
        </div>
      </header>

      <div className="flex">
        {/* Sidebar - Metrics */}
        <aside className="w-64 border-r border-gray-800 p-4 min-h-[calc(100vh-73px)]">
          <MetricsPanel metrics={metrics} />
        </aside>

        {/* Main Content */}
        <main className="flex-1 p-4">
          {/* Tabs */}
          <div className="flex gap-1 mb-4 bg-gray-900 rounded-lg p-1 w-fit">
            {[
              { id: 'trace', label: 'Agent Trace' },
              { id: 'map', label: 'Route Map' },
              { id: 'voice', label: 'Voice Call' },
              { id: 'data', label: 'Data Sources' },
            ].map(tab => (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`px-4 py-2 rounded-md text-sm font-medium transition-colors ${
                  activeTab === tab.id
                    ? 'bg-gray-800 text-white'
                    : 'text-gray-500 hover:text-gray-300'
                }`}
              >
                {tab.label}
              </button>
            ))}
          </div>

          {/* Tab Content */}
          <div className="bg-gray-900 rounded-xl border border-gray-800 min-h-[calc(100vh-180px)]">
            {activeTab === 'trace' && <AgentTrace traces={traces} />}
            {activeTab === 'map' && <ReturnMap returnUpdates={returnUpdates} />}
            {activeTab === 'voice' && <VoiceCallPanel voiceStatus={voiceStatus} />}
            {activeTab === 'data' && <DataSourcePanel />}
          </div>
        </main>
      </div>
    </div>
  );
}
