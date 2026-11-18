import { useRef, useState } from 'react';
import { CircleCheck, FileText, LoaderCircle, Plus, RotateCcw, Sparkles, Trash2, Upload } from 'lucide-react';
import Badge from '../Badge.jsx';
import { DOCUMENT_TYPES, PROFILE_FIELDS } from '../../data/mockData.js';
import { formatINR } from '../../lib/eligibility.js';

const LABEL = Object.fromEntries(PROFILE_FIELDS.map((f) => [f.key, f.label]));

const STATUS = {
  queued: { text: 'Queued', tone: 'slate' },
  ocr: { text: 'Running PaddleOCR…', tone: 'blue' },
  done: { text: 'Fields extracted', tone: 'green' },
};

function fieldTag(field, docId, done) {
  if (!done) return <Badge tone="red">Missing</Badge>;
  if (field.docId === docId && field.source === 'ocr') return <Badge tone="blue">OCR Extracted - {field.confidence}%</Badge>;
  if (field.source === 'edited' || field.source === 'user') return <Badge tone="violet">User Entered</Badge>;
  return <Badge tone="red">Missing</Badge>;
}

const display = (key, value) => (key === 'annualIncome' && value !== '' ? formatINR(value) : key === 'class12Percent' && value !== '' ? `${value}%` : String(value));

export default function DocumentUpload({ docs, fields, notice, onFiles, onToggle, onLoadSample, onReset }) {
  const inputRef = useRef(null);
  const [dragging, setDragging] = useState(false);

  const drop = (e) => {
    e.preventDefault();
    setDragging(false);
    if (e.dataTransfer.files.length) onFiles([...e.dataTransfer.files]);
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={onLoadSample}
          className="flex items-center gap-2 rounded-lg bg-amber-400 px-3 py-2 text-sm font-semibold text-slate-950 hover:bg-amber-300"
        >
          <Sparkles className="h-4 w-4" />
          Load Sample MP Citizen Demo
        </button>
        <button
          type="button"
          onClick={onReset}
          className="flex items-center gap-2 rounded-lg border border-slate-300 px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
        >
          <RotateCcw className="h-4 w-4" />
          Reset
        </button>
      </div>

      <div
        role="button"
        tabIndex={0}
        onClick={() => inputRef.current?.click()}
        onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && inputRef.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={drop}
        className={`flex cursor-pointer flex-col items-center gap-1 rounded-lg border-2 border-dashed px-4 py-5 text-center transition ${
          dragging ? 'border-blue-500 bg-blue-50' : 'border-slate-300 hover:border-slate-400 hover:bg-slate-50'
        }`}
      >
        <Upload className="h-6 w-6 text-slate-500" />
        <p className="text-sm font-semibold text-slate-800">Drop certificates here or click to browse</p>
        <p className="text-xs text-slate-500">PDF / JPG / PNG · auto-classified by file name (income, caste, samagra, marksheet)</p>
        <input
          ref={inputRef}
          type="file"
          multiple
          accept=".pdf,.jpg,.jpeg,.png"
          className="hidden"
          onChange={(e) => {
            if (e.target.files.length) onFiles([...e.target.files]);
            e.target.value = '';
          }}
        />
      </div>
      {notice && <p className="rounded-md bg-rose-50 px-3 py-2 text-xs font-medium text-rose-800">{notice}</p>}

      <ul className="space-y-2">
        {DOCUMENT_TYPES.map((type) => {
          const doc = docs[type.id];
          const done = doc?.status === 'done';
          return (
            <li key={type.id} className={`rounded-lg border ${doc ? 'border-slate-300' : 'border-dashed border-slate-300 bg-slate-50'}`}>
              <div className="flex items-center gap-2 px-3 py-2">
                {done ? (
                  <CircleCheck className="h-4 w-4 shrink-0 text-emerald-600" />
                ) : doc ? (
                  <LoaderCircle className="h-4 w-4 shrink-0 animate-spin text-blue-600" />
                ) : (
                  <FileText className="h-4 w-4 shrink-0 text-slate-400" />
                )}
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-semibold text-slate-900">{type.name}</p>
                  <p className="truncate text-[11px] text-slate-500">{doc ? `${doc.fileName} · ${type.issuer}` : 'Not uploaded'}</p>
                </div>
                {doc && <Badge tone={STATUS[doc.status].tone}>{STATUS[doc.status].text}</Badge>}
                <button
                  type="button"
                  onClick={() => onToggle(type.id)}
                  title={doc ? 'Remove document' : 'Add sample document'}
                  className="rounded-md border border-slate-300 p-1.5 text-slate-600 hover:bg-slate-100"
                >
                  {doc ? <Trash2 className="h-3.5 w-3.5" /> : <Plus className="h-3.5 w-3.5" />}
                </button>
              </div>
              {doc && (
                <dl className="space-y-1 border-t border-slate-200 px-3 py-2">
                  {Object.keys(type.fields).map((key) => (
                    <div key={key} className="flex items-center justify-between gap-2 text-xs">
                      <dt className="truncate text-slate-500">{LABEL[key]}</dt>
                      <dd className="flex items-center gap-1.5">
                        {done && fields[key].value !== '' && <span className="font-medium text-slate-900">{display(key, fields[key].value)}</span>}
                        {fieldTag(fields[key], type.id, done)}
                      </dd>
                    </div>
                  ))}
                </dl>
              )}
            </li>
          );
        })}
      </ul>
      <p className="text-[11px] text-slate-500">Engine: PaddleOCR PP-OCRv4 mobile (CPU) → field extractor → normalizer → validator. Output simulated in this prototype.</p>
    </div>
  );
}
