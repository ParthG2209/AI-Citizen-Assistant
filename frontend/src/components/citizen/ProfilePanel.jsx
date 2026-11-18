import Badge, { ProvenanceBadge } from '../Badge.jsx';
import { PROFILE_FIELDS } from '../../data/mockData.js';

const inputClass =
  'w-full rounded-md border border-slate-300 bg-white px-2 py-1.5 text-sm text-slate-900 focus:border-slate-900 focus:outline-none focus:ring-1 focus:ring-slate-900';

const COUNTS = [
  { source: 'ocr', label: 'OCR Extracted', tone: 'blue' },
  { source: 'edited', label: 'User Edited', tone: 'violet' },
  { source: 'user', label: 'Unverified', tone: 'amber' },
  { source: 'missing', label: 'Missing', tone: 'red' },
];

function FieldInput({ def, field, onEdit }) {
  if (def.type === 'multi') {
    const selected = field.value;
    const toggle = (opt) => onEdit(def.key, selected.includes(opt) ? selected.filter((o) => o !== opt) : [...selected, opt]);
    return (
      <div className="flex flex-wrap gap-1.5">
        {def.options.map((opt) => (
          <button
            key={opt}
            type="button"
            onClick={() => toggle(opt)}
            aria-pressed={selected.includes(opt)}
            className={`rounded-md border px-2 py-1 text-xs font-medium ${
              selected.includes(opt) ? 'border-slate-900 bg-slate-900 text-white' : 'border-slate-300 text-slate-700 hover:bg-slate-50'
            }`}
          >
            {opt}
          </button>
        ))}
      </div>
    );
  }
  if (def.type === 'select') {
    return (
      <select className={inputClass} value={field.value} onChange={(e) => onEdit(def.key, e.target.value)}>
        <option value="">— Not provided —</option>
        {def.options.map((opt) => (
          <option key={opt}>{opt}</option>
        ))}
      </select>
    );
  }
  return (
    <input
      className={inputClass}
      type={def.type}
      min={def.type === 'number' ? 0 : undefined}
      step={def.key === 'annualIncome' ? 10000 : def.key === 'class12Percent' ? 0.1 : 1}
      value={field.value}
      placeholder="Not provided"
      onChange={(e) => onEdit(def.key, e.target.value)}
    />
  );
}

export default function ProfilePanel({ fields, onEdit }) {
  const counts = Object.fromEntries(COUNTS.map((c) => [c.source, 0]));
  for (const def of PROFILE_FIELDS) if (def.type !== 'multi') counts[fields[def.key].source] += 1;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-1.5">
        {COUNTS.map((c) => (
          <Badge key={c.source} tone={c.tone}>
            {counts[c.source]} {c.label}
          </Badge>
        ))}
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        {PROFILE_FIELDS.map((def) => {
          const Wrapper = def.type === 'multi' ? 'div' : 'label';
          return (
            <Wrapper key={def.key} className={`block space-y-1 ${def.type === 'multi' ? 'sm:col-span-2' : ''}`}>
              <span className="flex items-center justify-between gap-2">
                <span className="text-xs font-semibold text-slate-700">{def.label}</span>
                {def.type === 'multi' ? <Badge tone="slate">Self-declared</Badge> : <ProvenanceBadge field={fields[def.key]} />}
              </span>
              <FieldInput def={def} field={fields[def.key]} onEdit={onEdit} />
            </Wrapper>
          );
        })}
      </div>
      <p className="text-[11px] text-slate-500">
        Category is only trusted when backed by a caste certificate — a typed-in category stays <strong>Unverified</strong> and
        caste-based conditions return Missing Info.
      </p>
    </div>
  );
}
