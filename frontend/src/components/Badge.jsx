const TONES = {
  slate: 'bg-slate-100 text-slate-700 ring-slate-300',
  blue: 'bg-blue-50 text-blue-800 ring-blue-300',
  green: 'bg-emerald-50 text-emerald-800 ring-emerald-300',
  amber: 'bg-amber-50 text-amber-900 ring-amber-300',
  red: 'bg-rose-50 text-rose-800 ring-rose-300',
  violet: 'bg-violet-50 text-violet-800 ring-violet-300',
  dark: 'bg-slate-900 text-white ring-slate-900',
};

export default function Badge({ tone = 'slate', icon: Icon, children, className = '' }) {
  return (
    <span
      className={`inline-flex items-center gap-1 whitespace-nowrap rounded-md px-1.5 py-0.5 text-[11px] font-semibold ring-1 ring-inset ${TONES[tone]} ${className}`}
    >
      {Icon && <Icon className="h-3 w-3" aria-hidden />}
      {children}
    </span>
  );
}

export function ProvenanceBadge({ field }) {
  switch (field.source) {
    case 'ocr':
      return <Badge tone="blue">OCR Extracted · {field.confidence}%</Badge>;
    case 'edited':
      return <Badge tone="violet">User Edited</Badge>;
    case 'user':
      return <Badge tone="amber">Unverified · User Entered</Badge>;
    default:
      return <Badge tone="red">Missing</Badge>;
  }
}

export const STATUS_META = {
  eligible: { label: 'Eligible', tone: 'green' },
  potential: { label: 'Potentially Eligible', tone: 'amber' },
  not_eligible: { label: 'Not Eligible', tone: 'red' },
};
