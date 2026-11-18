export default function SchemeAdoption({ schemes }) {
  return (
    <ul className="space-y-3">
      {schemes.map((s) => {
        const pct = Math.round((s.enrolled / s.eligible) * 100);
        return (
          <li key={s.name}>
            <div className="mb-1 flex items-baseline justify-between gap-2 text-xs">
              <span className="font-semibold text-slate-800">{s.name}</span>
              <span className="text-slate-500">
                {s.enrolled.toLocaleString('en-IN')} / {s.eligible.toLocaleString('en-IN')} · <strong className="text-slate-900">{pct}%</strong>
              </span>
            </div>
            <div className="h-2.5 overflow-hidden rounded-full bg-slate-200">
              <div
                className={`h-full rounded-full ${pct < 50 ? 'bg-rose-500' : pct < 65 ? 'bg-amber-500' : 'bg-emerald-600'}`}
                style={{ width: `${pct}%` }}
              />
            </div>
          </li>
        );
      })}
    </ul>
  );
}
