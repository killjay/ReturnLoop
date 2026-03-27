import { useState, useEffect } from 'react';

export default function DataSourcePanel() {
  const [status, setStatus] = useState(null);
  const [syncing, setSyncing] = useState(false);
  const [shopifyConfig, setShopifyConfig] = useState({ shop_name: '', api_key: '' });
  const [activeSource, setActiveSource] = useState(null);

  useEffect(() => {
    fetchStatus();
    const interval = setInterval(fetchStatus, 5000);
    return () => clearInterval(interval);
  }, []);

  const fetchStatus = async () => {
    try {
      const res = await fetch('/api/airbyte/status');
      const data = await res.json();
      setStatus(data);
      // Pre-populate Shopify config from server-side env vars (only if user hasn't typed anything)
      if (data.shopify_config && !shopifyConfig.shop_name) {
        setShopifyConfig(prev => ({
          shop_name: prev.shop_name || data.shopify_config.shop_name || '',
          api_key: prev.api_key,
        }));
      }
    } catch (e) { /* backend not ready */ }
  };

  const triggerDemoSync = async () => {
    setSyncing(true);
    try {
      await fetch('/api/airbyte/sync-demo', { method: 'POST' });
      await fetchStatus();
    } catch (e) {
      console.error('Sync failed:', e);
    }
    setSyncing(false);
  };

  const triggerShopifySync = async () => {
    const hasServerConfig = status?.shopify_config?.has_api_key && status?.shopify_config?.shop_name;
    if (!hasServerConfig && (!shopifyConfig.shop_name || !shopifyConfig.api_key)) return;
    setSyncing(true);
    try {
      await fetch('/api/airbyte/sync-shopify', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(shopifyConfig),
      });
      await fetchStatus();
    } catch (e) {
      console.error('Shopify sync failed:', e);
    }
    setSyncing(false);
    setActiveSource(null);
  };

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h3 className="text-lg font-semibold text-white">Data Sources</h3>
          <p className="text-xs text-gray-500 mt-0.5">
            Powered by Airbyte {status?.airbyte_available ? '' : '(demo mode)'}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <div className={`w-2 h-2 rounded-full ${
            status?.sync_status === 'completed' ? 'bg-emerald-500' :
            status?.sync_status === 'syncing' ? 'bg-amber-500 pulse-dot' :
            'bg-gray-600'
          }`} />
          <span className="text-xs text-gray-500">
            {status?.sync_status === 'syncing' ? 'Syncing...' :
             status?.last_sync ? `Last sync: ${new Date(status.last_sync).toLocaleTimeString()}` :
             'Not synced'}
          </span>
        </div>
      </div>

      {/* Connected Sources */}
      {status?.connected_sources?.length > 0 && (
        <div className="mb-4 p-3 bg-emerald-500/10 border border-emerald-500/30 rounded-lg">
          <p className="text-xs font-semibold text-emerald-400 mb-1">Connected Sources</p>
          <div className="flex gap-2">
            {status.connected_sources.map((src, i) => (
              <span key={i} className="text-xs bg-emerald-500/20 text-emerald-300 px-2 py-0.5 rounded">
                {src}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Record Counts */}
      {status?.record_counts && (status.record_counts.customers > 0 || status.record_counts.products > 0) && (
        <div className="grid grid-cols-3 gap-3 mb-6">
          {Object.entries(status.record_counts).map(([key, count]) => (
            <div key={key} className="bg-gray-800 rounded-lg p-3 text-center">
              <div className="text-xl font-bold text-white">{count}</div>
              <div className="text-xs text-gray-500 capitalize">{key}</div>
            </div>
          ))}
        </div>
      )}

      {/* Source Cards */}
      <div className="space-y-3">
        {/* Demo Sync */}
        <div className="bg-gray-800/50 border border-gray-700 rounded-lg p-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-lg bg-gradient-to-br from-emerald-500 to-cyan-500 flex items-center justify-center text-lg">
                A
              </div>
              <div>
                <h4 className="text-sm font-semibold text-white">Demo Sync</h4>
                <p className="text-xs text-gray-500">Simulate Airbyte pipeline with seed data</p>
              </div>
            </div>
            <button
              onClick={triggerDemoSync}
              disabled={syncing}
              className="text-xs bg-emerald-600 text-white px-3 py-1.5 rounded-md hover:bg-emerald-500 disabled:opacity-50 transition-colors"
            >
              {syncing ? 'Syncing...' : 'Sync Now'}
            </button>
          </div>
        </div>

        {/* Shopify */}
        <div className="bg-gray-800/50 border border-gray-700 rounded-lg p-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-lg bg-[#96bf48] flex items-center justify-center text-lg font-bold text-white">
                S
              </div>
              <div>
                <h4 className="text-sm font-semibold text-white">Shopify</h4>
                <p className="text-xs text-gray-500">Sync orders, customers, products</p>
              </div>
            </div>
            <button
              onClick={() => setActiveSource(activeSource === 'shopify' ? null : 'shopify')}
              className={`text-xs px-3 py-1.5 rounded-md transition-colors ${
                status?.connected_sources?.includes('shopify')
                  ? 'bg-emerald-600/20 text-emerald-400 border border-emerald-500/40 hover:bg-emerald-600/30'
                  : 'bg-gray-700 text-gray-300 hover:bg-gray-600'
              }`}
            >
              {activeSource === 'shopify' ? 'Cancel' : status?.connected_sources?.includes('shopify') ? 'Synced ✓' : 'Connect'}
            </button>
          </div>
          {activeSource === 'shopify' && (
            <div className="mt-3 space-y-2">
              <input
                type="text"
                placeholder="Shop name (e.g. my-store)"
                value={shopifyConfig.shop_name}
                onChange={e => setShopifyConfig(p => ({ ...p, shop_name: e.target.value }))}
                className="w-full bg-gray-900 border border-gray-700 rounded px-3 py-1.5 text-sm text-white placeholder-gray-600"
              />
              <input
                type="password"
                placeholder="API Key"
                value={shopifyConfig.api_key}
                onChange={e => setShopifyConfig(p => ({ ...p, api_key: e.target.value }))}
                className="w-full bg-gray-900 border border-gray-700 rounded px-3 py-1.5 text-sm text-white placeholder-gray-600"
              />
              {status?.shopify_config?.has_api_key && !shopifyConfig.api_key && (
                <p className="text-xs text-emerald-400">
                  API key configured on server
                </p>
              )}
              <button
                onClick={triggerShopifySync}
                disabled={syncing || (!shopifyConfig.shop_name && !status?.shopify_config?.shop_name)}
                className="w-full text-xs bg-[#96bf48] text-white py-1.5 rounded-md hover:opacity-90 disabled:opacity-50"
              >
                {syncing ? 'Syncing...' : 'Sync from Shopify'}
              </button>
            </div>
          )}
        </div>

      </div>

      {/* Airbyte badge */}
      <div className="mt-6 text-center">
        <span className="text-xs text-gray-600">
          Data pipelines powered by <span className="text-gray-400 font-semibold">Airbyte</span>
        </span>
      </div>
    </div>
  );
}
