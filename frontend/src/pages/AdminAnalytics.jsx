import { useState } from 'react';
import { Database, FileWarning, MapPin, TriangleAlert } from 'lucide-react';
import KpiCards from '../components/admin/KpiCards.jsx';
import DistrictMap, { coverageColor } from '../components/admin/DistrictMap.jsx';
import SchemeAdoption from '../components/admin/SchemeAdoption.jsx';
import { DISTRICTS, SCHEME_ADOPTION } from '../data/mockData.js';

const coverage = (d) => Math.round((d.enrolled / d.eligible) * 100);

function Panel({ title, subtitle, children, className = '' }) {
  return (
    <section className={`rounded-xl border border-slate-300 bg-white shadow-sm ${className}`}>
      <header className="border-b border-slate-200 px-4 py-3">
        <h2 className="text-sm font-bold text-slate-900">{title}</h2>
        {subtitle && <p className="text-xs text-slate-500">{subtitle}</p>}
      </header>
      <div className="p-4">{children}</div>
    </section>
  );
}

export default function AdminAnalytics() {
  const [selectedId, setSelectedId] = useState('sehore');
  const selected = DISTRICTS.find((d) => d.id === selectedId);
  const ranked = [...DISTRICTS].sort((a, b) => coverage(a) - coverage(b));
  const pct = coverage(selected);

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-slate-950">District & Village Analytics</h1>
          <p className="text-sm text-slate-600">Madhya Pradesh welfare coverage — eligible vs enrolled, underserved areas, document bottlenecks.</p>
        </div>
        <span className="flex items-center gap-1.5 rounded-md border border-slate-300 bg-white px-2 py-1 text-[11px] font-medium text-slate-600">
          <Database className="h-3.5 w-3.5" />
          PySpark batch output (mock) · ai_modules/analytics/coverage_job.py
        </span>
      </div>

      <KpiCards districts={DISTRICTS} />

      <div className="grid gap-5 lg:grid-cols-12">
        <Panel title="MP District Coverage Map" subtitle="Circle size = eligible population · colour = coverage (red < 50%, amber < 65%, green ≥ 65%)" className="lg:col-span-8">
          <DistrictMap districts={DISTRICTS} selectedId={selectedId} onSelect={setSelectedId} />
        </Panel>

        <Panel title={selected.name} subtitle="Selected district" className="lg:col-span-4">
          <div className="space-y-4 text-sm">
            <div>
              <div className="mb-1 flex justify-between text-xs">
                <span className="font-semibold text-slate-700">Welfare coverage</span>
                <strong>{pct}%</strong>
              </div>
              <div className="h-3 overflow-hidden rounded-full bg-slate-200">
                <div className="h-full rounded-full" style={{ width: `${pct}%`, backgroundColor: coverageColor(pct) }} />
              </div>
            </div>
            <dl className="grid grid-cols-3 gap-2 text-center">
              {[
                ['Eligible', selected.eligible],
                ['Enrolled', selected.enrolled],
                ['Gap', selected.eligible - selected.enrolled],
              ].map(([label, n]) => (
                <div key={label} className="rounded-md bg-slate-50 p-2 ring-1 ring-slate-200">
                  <dt className="text-[11px] text-slate-500">{label}</dt>
                  <dd className="font-bold text-slate-950">{n.toLocaleString('en-IN')}</dd>
                </div>
              ))}
            </dl>
            <div>
              <p className="mb-1.5 flex items-center gap-1.5 text-xs font-bold uppercase tracking-wide text-amber-800">
                <FileWarning className="h-3.5 w-3.5" /> Top missing documents
              </p>
              <ul className="list-disc space-y-0.5 pl-5 text-xs text-slate-700">
                {selected.topMissingDocs.map((d) => (
                  <li key={d}>{d}</li>
                ))}
              </ul>
            </div>
            <div>
              <p className="mb-1.5 flex items-center gap-1.5 text-xs font-bold uppercase tracking-wide text-rose-800">
                <TriangleAlert className="h-3.5 w-3.5" /> Underserved blocks
              </p>
              <div className="flex flex-wrap gap-1.5">
                {selected.underservedBlocks.map((b) => (
                  <span key={b} className="rounded-md bg-rose-50 px-2 py-0.5 text-xs font-medium text-rose-800 ring-1 ring-rose-200">
                    {b}
                  </span>
                ))}
              </div>
            </div>
          </div>
        </Panel>
      </div>

      <div className="grid gap-5 lg:grid-cols-12">
        <Panel title="District ranking" subtitle="Lowest coverage first · click to inspect" className="lg:col-span-5">
          <ul className="divide-y divide-slate-200">
            {ranked.map((d) => (
              <li key={d.id}>
                <button
                  type="button"
                  onClick={() => setSelectedId(d.id)}
                  className={`flex w-full items-center gap-3 px-2 py-2 text-left text-sm hover:bg-slate-50 ${d.id === selectedId ? 'bg-slate-100' : ''}`}
                >
                  <MapPin className="h-4 w-4 shrink-0" style={{ color: coverageColor(coverage(d)) }} />
                  <span className="flex-1 font-medium text-slate-800">{d.name}</span>
                  <span className="text-xs text-slate-500">gap {(d.eligible - d.enrolled).toLocaleString('en-IN')}</span>
                  <strong className="w-10 text-right text-slate-950">{coverage(d)}%</strong>
                </button>
              </li>
            ))}
          </ul>
        </Panel>
        <Panel title="Scheme adoption breakdown" subtitle="Enrolled ÷ eligible across sample districts" className="lg:col-span-7">
          <SchemeAdoption schemes={SCHEME_ADOPTION} />
        </Panel>
      </div>
    </div>
  );
}
