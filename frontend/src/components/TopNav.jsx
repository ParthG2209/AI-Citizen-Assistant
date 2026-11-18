import { BarChart3, FlaskConical, Landmark, Users } from 'lucide-react';

const MODES = [
  { id: 'citizen', label: 'Citizen & NGO Discovery', icon: Users },
  { id: 'admin', label: 'District & Village Analytics', icon: BarChart3 },
];

export default function TopNav({ mode, onChange }) {
  return (
    <header className="sticky top-0 z-[1100] border-b border-slate-800 bg-slate-950 text-white">
      <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-3 px-4 py-3">
        <div className="flex items-center gap-2.5">
          <div className="grid h-9 w-9 place-items-center rounded-lg bg-amber-400 text-slate-950">
            <Landmark className="h-5 w-5" />
          </div>
          <div className="leading-tight">
            <p className="text-sm font-bold tracking-tight">AI Citizen Assistant</p>
            <p className="text-[11px] text-slate-400">Welfare Intelligence · Madhya Pradesh + Central Schemes</p>
          </div>
        </div>

        <nav className="ml-auto flex rounded-lg bg-slate-800 p-1" aria-label="Mode">
          {MODES.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              type="button"
              onClick={() => onChange(id)}
              aria-pressed={mode === id}
              className={`flex items-center gap-2 rounded-md px-3 py-1.5 text-sm font-medium transition ${
                mode === id ? 'bg-white text-slate-950 shadow' : 'text-slate-300 hover:text-white'
              }`}
            >
              <Icon className="h-4 w-4" />
              <span className="hidden sm:inline">{label}</span>
            </button>
          ))}
        </nav>

        <span className="flex items-center gap-1.5 rounded-md border border-amber-400/40 px-2 py-1 text-[11px] font-medium text-amber-300">
          <FlaskConical className="h-3.5 w-3.5" />
          Prototype · mock data, backend not connected
        </span>
      </div>
    </header>
  );
}
