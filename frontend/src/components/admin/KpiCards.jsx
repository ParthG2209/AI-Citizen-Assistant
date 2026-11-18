import { Gauge, MapPinned, UserRoundX, Users } from 'lucide-react';

export default function KpiCards({ districts }) {
  const eligible = districts.reduce((s, d) => s + d.eligible, 0);
  const enrolled = districts.reduce((s, d) => s + d.enrolled, 0);
  const underserved = districts.filter((d) => d.enrolled / d.eligible < 0.55).length;

  const cards = [
    { label: 'Eligible citizens (profiled)', value: eligible.toLocaleString('en-IN'), icon: Users, note: `${districts.length} sample districts` },
    { label: 'Eligible but not enrolled', value: (eligible - enrolled).toLocaleString('en-IN'), icon: UserRoundX, note: 'benefit gap population' },
    { label: 'Average welfare coverage', value: `${Math.round((enrolled / eligible) * 100)}%`, icon: Gauge, note: 'enrolled ÷ eligible' },
    { label: 'Underserved districts', value: underserved, icon: MapPinned, note: 'coverage below 55%' },
  ];

  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      {cards.map(({ label, value, icon: Icon, note }) => (
        <div key={label} className="rounded-xl border border-slate-300 bg-white p-4 shadow-sm">
          <div className="flex items-center justify-between">
            <p className="text-xs font-semibold text-slate-500">{label}</p>
            <Icon className="h-4 w-4 text-slate-400" />
          </div>
          <p className="mt-1 text-2xl font-bold tracking-tight text-slate-950">{value}</p>
          <p className="text-[11px] text-slate-500">{note}</p>
        </div>
      ))}
    </div>
  );
}
