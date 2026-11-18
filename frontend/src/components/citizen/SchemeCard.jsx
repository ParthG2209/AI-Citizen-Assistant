import { BookOpen, CircleCheck, CircleHelp, CircleX } from 'lucide-react';
import Badge, { STATUS_META } from '../Badge.jsx';

function ConditionList({ title, icon: Icon, tone, items, empty, render }) {
  return (
    <div className={`rounded-md border p-2.5 ${tone}`}>
      <p className="mb-1.5 flex items-center gap-1 text-[11px] font-bold uppercase tracking-wide">
        <Icon className="h-3.5 w-3.5" />
        {title} ({items.length})
      </p>
      {items.length === 0 ? (
        <p className="text-xs opacity-70">{empty}</p>
      ) : (
        <ul className="space-y-1.5">
          {items.map((item) => (
            <li key={item.label} className="text-xs leading-snug">
              {render(item)}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default function SchemeCard({ result, onExplain }) {
  const { scheme, status, satisfied, failed, missing, claimed, total } = result;
  const meta = STATUS_META[status];

  return (
    <article className="rounded-lg border border-slate-300 bg-white p-3.5">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="mb-1 flex flex-wrap items-center gap-1.5">
            <Badge tone={scheme.level === 'MP' ? 'amber' : 'blue'}>{scheme.level === 'MP' ? 'MP State' : 'Central'}</Badge>
            <Badge tone={meta.tone}>{meta.label}</Badge>
            {claimed && <Badge tone="dark">Already enrolled</Badge>}
          </div>
          <h3 className="text-sm font-bold text-slate-950">{scheme.name}</h3>
          <p className="text-[11px] text-slate-500">{scheme.department}</p>
          <p className="mt-1 text-xs text-slate-700">{scheme.benefit}</p>
        </div>
        <div className="text-right">
          <p className="text-lg font-bold text-slate-950">
            {satisfied.length}/{total}
          </p>
          <p className="text-[11px] text-slate-500">conditions met</p>
        </div>
      </div>

      <div className="mt-3 grid gap-2 md:grid-cols-3">
        <ConditionList
          title="Satisfied"
          icon={CircleCheck}
          tone="border-emerald-200 bg-emerald-50/60 text-emerald-900"
          items={satisfied}
          empty="None yet"
          render={(c) => c.label}
        />
        <ConditionList
          title="Not Satisfied"
          icon={CircleX}
          tone="border-rose-200 bg-rose-50/60 text-rose-900"
          items={failed}
          empty="No failed conditions"
          render={(c) => <strong className="font-semibold">{c.detail}</strong>}
        />
        <ConditionList
          title="Missing Info"
          icon={CircleHelp}
          tone="border-amber-200 bg-amber-50/60 text-amber-950"
          items={missing}
          empty="Nothing missing"
          render={(c) => (
            <>
              <span className="font-semibold">{c.label}</span>
              <span className="block opacity-80">
                {c.detail} → {c.resolveDocId ? 'upload' : 'provide'} {c.resolve}
              </span>
            </>
          )}
        />
      </div>

      <div className="mt-3 flex justify-end">
        <button
          type="button"
          onClick={() => onExplain(result.id)}
          className="flex items-center gap-1.5 rounded-md bg-slate-900 px-3 py-1.5 text-xs font-semibold text-white hover:bg-slate-700"
        >
          <BookOpen className="h-3.5 w-3.5" />
          Explain via RAG
        </button>
      </div>
    </article>
  );
}
