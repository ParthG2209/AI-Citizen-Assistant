import { useState } from 'react';
import { ShieldCheck } from 'lucide-react';
import SchemeCard from './SchemeCard.jsx';

const TABS = [
  { id: 'eligible', label: 'Eligible', active: 'border-emerald-600 text-emerald-800', count: 'bg-emerald-600' },
  { id: 'potential', label: 'Potentially Eligible / Insufficient Info', active: 'border-amber-500 text-amber-900', count: 'bg-amber-500' },
  { id: 'not_eligible', label: 'Not Eligible', active: 'border-rose-600 text-rose-800', count: 'bg-rose-600' },
];

const EMPTY = {
  eligible: 'No scheme has all conditions satisfied yet. Load the sample demo or fill the profile.',
  potential: 'No scheme is waiting on missing information.',
  not_eligible: 'No scheme has a failed condition.',
};

export default function EligibilityResults({ results, onExplain }) {
  const [tab, setTab] = useState('eligible');
  const visible = results.filter((r) => r.status === tab);

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 rounded-md bg-slate-50 px-3 py-2 text-[11px] text-slate-600 ring-1 ring-slate-200">
        <ShieldCheck className="h-4 w-4 shrink-0 text-slate-700" />
        <p>
          Deterministic rule engine (mock mirror of <code className="font-mono">ai_modules/rule_engine</code>) · each condition returns
          PASS / FAIL / MISSING · LLM is excluded from eligibility decisions.
        </p>
      </div>

      <div className="flex flex-wrap border-b border-slate-200" role="tablist">
        {TABS.map((t) => {
          const n = results.filter((r) => r.status === t.id).length;
          return (
            <button
              key={t.id}
              type="button"
              role="tab"
              aria-selected={tab === t.id}
              onClick={() => setTab(t.id)}
              className={`-mb-px flex items-center gap-2 border-b-2 px-3 py-2 text-xs font-semibold ${
                tab === t.id ? t.active : 'border-transparent text-slate-500 hover:text-slate-800'
              }`}
            >
              {t.label}
              <span className={`rounded px-1.5 py-0.5 text-[10px] text-white ${t.count}`}>{n}</span>
            </button>
          );
        })}
      </div>

      {visible.length === 0 ? (
        <p className="rounded-md border border-dashed border-slate-300 px-3 py-6 text-center text-xs text-slate-500">{EMPTY[tab]}</p>
      ) : (
        <div className="space-y-3">
          {visible.map((r) => (
            <SchemeCard key={r.id} result={r} onExplain={onExplain} />
          ))}
        </div>
      )}
    </div>
  );
}
