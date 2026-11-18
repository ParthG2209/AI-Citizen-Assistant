import { ArrowRight, FileWarning, Gift, Link2, Wallet } from 'lucide-react';
import { formatINR } from '../../lib/eligibility.js';

function Node({ children, tone = 'border-slate-300 bg-white text-slate-800' }) {
  return <span className={`rounded-md border px-1.5 py-0.5 text-[11px] font-semibold ${tone}`}>{children}</span>;
}

function Edge({ label }) {
  return (
    <span className="flex items-center gap-0.5 font-mono text-[10px] text-slate-500">
      {label}
      <ArrowRight className="h-3 w-3" />
    </span>
  );
}

function GapGroup({ icon: Icon, title, tone, children, empty, count }) {
  return (
    <div className="space-y-2">
      <h3 className={`flex items-center gap-1.5 text-xs font-bold uppercase tracking-wide ${tone}`}>
        <Icon className="h-4 w-4" />
        {title} ({count})
      </h3>
      {count === 0 ? <p className="text-xs text-slate-500">{empty}</p> : <ul className="space-y-2">{children}</ul>}
    </div>
  );
}

export default function BenefitGaps({ gaps, onExplain }) {
  const { complementary, blocked, unclaimed, unclaimedValue } = gaps;

  // Document -[:UNLOCKS]-> Scheme, grouped by the document/info that resolves the gap.
  const byResolve = new Map();
  for (const b of blocked) {
    if (!byResolve.has(b.resolve)) byResolve.set(b.resolve, { ...b, schemes: [] });
    byResolve.get(b.resolve).schemes.push(b);
  }
  const blockedGroups = [...byResolve.values()];

  return (
    <div className="space-y-5">
      <div className="flex items-center gap-3 rounded-lg bg-slate-900 px-4 py-3 text-white">
        <Wallet className="h-6 w-6 text-amber-400" />
        <div>
          <p className="text-lg font-bold">{formatINR(unclaimedValue)}/yr</p>
          <p className="text-[11px] text-slate-300">estimated unclaimed cash / scholarship value (eligible + complementary, excl. health & housing cover)</p>
        </div>
      </div>

      <GapGroup icon={Link2} title="Complementary schemes via Knowledge Graph" tone="text-violet-800" count={complementary.length} empty="No complementary scheme unlocked yet — appears once a linked scheme is Eligible.">
        {complementary.map((c) => (
          <li key={c.to} className="rounded-lg border border-violet-200 bg-violet-50/60 p-3">
            <p className="text-sm text-slate-900">
              <strong>Complementary Scheme Detected via Knowledge Graph:</strong> Since you qualify for {c.fromName}, you also qualify for{' '}
              <strong>{c.to}</strong> ({c.benefit}).
            </p>
            <p className="mt-1 text-xs text-slate-600">{c.reason}</p>
            <div className="mt-2 flex flex-wrap items-center gap-1.5">
              <Node>Citizen</Node>
              <Edge label="ELIGIBLE_FOR" />
              <Node tone="border-emerald-300 bg-emerald-50 text-emerald-900">{c.fromName.split(' (')[0]}</Node>
              <Edge label={c.relation} />
              <Node tone="border-violet-300 bg-violet-100 text-violet-900">{c.to}</Node>
            </div>
          </li>
        ))}
      </GapGroup>

      <GapGroup icon={FileWarning} title="Document-blocked benefits" tone="text-amber-800" count={blockedGroups.length} empty="No benefit is blocked by a missing document.">
        {blockedGroups.map((g) => (
          <li key={g.resolve} className="rounded-lg border border-amber-200 bg-amber-50/60 p-3">
            <p className="text-sm text-slate-900">
              <strong>Document-Blocked Benefit:</strong> {g.resolveDocId ? 'Upload' : 'Provide'} <strong>{g.resolve}</strong> to unlock{' '}
              {g.schemes.map((s) => s.benefitShort).join(' + ')}.
            </p>
            <div className="mt-2 flex flex-wrap items-center gap-1.5">
              <Node tone="border-amber-300 bg-amber-100 text-amber-950">{g.resolve}</Node>
              <Edge label="UNLOCKS" />
              {g.schemes.map((s) => (
                <button key={s.schemeId} type="button" onClick={() => onExplain(s.schemeId)} className="hover:underline">
                  <Node>{s.schemeName}</Node>
                </button>
              ))}
            </div>
          </li>
        ))}
      </GapGroup>

      <GapGroup icon={Gift} title="Eligible but not yet claimed" tone="text-emerald-800" count={unclaimed.length} empty="No unclaimed eligible schemes.">
        {unclaimed.map((r) => (
          <li key={r.id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-emerald-200 bg-emerald-50/60 px-3 py-2">
            <span className="text-sm font-semibold text-slate-900">{r.scheme.name}</span>
            <span className="text-xs text-emerald-900">{r.scheme.benefitShort}</span>
          </li>
        ))}
      </GapGroup>
    </div>
  );
}
