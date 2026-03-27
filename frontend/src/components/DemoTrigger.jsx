import { useState } from 'react';
import { initiateReturn } from '../utils/api';

const DEMO_SCENARIOS = [
  {
    label: 'Alpine Jacket (Sizing)',
    order_id: 'ord-001',
    reason_category: 'sizing',
    reason_detail: 'The jacket is too narrow in the shoulders',
    item_condition: 'like_new',
  },
  {
    label: 'Silk Dress (Preference)',
    order_id: 'ord-005',
    reason_category: 'preference',
    reason_detail: 'The color looks different than in the photos',
    item_condition: 'new',
  },
  {
    label: 'Hiking Boot (Quality)',
    order_id: 'ord-008',
    reason_category: 'quality',
    reason_detail: 'The sole started peeling after one hike',
    item_condition: 'fair',
  },
  {
    label: 'Slim Denim (Sizing)',
    order_id: 'ord-006',
    reason_category: 'sizing',
    reason_detail: 'Way too tight around the waist, size chart is off',
    item_condition: 'like_new',
  },
];

export default function DemoTrigger() {
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);

  const triggerDemo = async (scenario) => {
    setLoading(true);
    setOpen(false);
    try {
      await initiateReturn({
        order_id: scenario.order_id,
        reason_category: scenario.reason_category,
        reason_detail: scenario.reason_detail,
        item_condition: scenario.item_condition,
      });
    } catch (e) {
      console.error('Demo trigger failed:', e);
    }
    setLoading(false);
  };

  return (
    <div className="relative">
      <button
        onClick={() => setOpen(!open)}
        disabled={loading}
        className="bg-gradient-to-r from-emerald-600 to-cyan-600 text-white text-sm font-semibold px-4 py-2 rounded-lg hover:from-emerald-500 hover:to-cyan-500 transition-all disabled:opacity-50"
      >
        {loading ? 'Processing...' : 'Trigger Return'}
      </button>

      {open && (
        <div className="absolute right-0 top-full mt-2 bg-gray-900 border border-gray-700 rounded-lg shadow-xl w-72 z-50">
          <div className="p-2">
            <p className="text-xs text-gray-500 px-2 py-1 mb-1">Select a demo scenario:</p>
            {DEMO_SCENARIOS.map((scenario, i) => (
              <button
                key={i}
                onClick={() => triggerDemo(scenario)}
                className="w-full text-left px-3 py-2.5 rounded-md hover:bg-gray-800 transition-colors"
              >
                <div className="text-sm text-white font-medium">{scenario.label}</div>
                <div className="text-xs text-gray-500 mt-0.5">{scenario.reason_detail}</div>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
