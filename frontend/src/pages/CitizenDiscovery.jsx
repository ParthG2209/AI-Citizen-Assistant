import { useEffect, useMemo, useRef, useState } from 'react';
import { ArrowRight, BookOpen, FileUp, Network, Scale, UserRound } from 'lucide-react';
import DocumentUpload from '../components/citizen/DocumentUpload.jsx';
import ProfilePanel from '../components/citizen/ProfilePanel.jsx';
import EligibilityResults from '../components/citizen/EligibilityResults.jsx';
import BenefitGaps from '../components/citizen/BenefitGaps.jsx';
import RagDrawer from '../components/citizen/RagDrawer.jsx';
import { DOCUMENT_TYPES, PROFILE_FIELDS, SAMPLE_USER_INPUT } from '../data/mockData.js';
import { detectGaps, evaluateAll } from '../lib/eligibility.js';

const DOC_BY_ID = Object.fromEntries(DOCUMENT_TYPES.map((d) => [d.id, d]));

const emptyFields = () =>
  Object.fromEntries(PROFILE_FIELDS.map((f) => [f.key, { value: f.type === 'multi' ? [] : '', source: 'missing' }]));

const PIPELINE = [
  { n: 1, label: 'Document OCR', icon: FileUp },
  { n: 2, label: 'Welfare Profile', icon: UserRound },
  { n: 3, label: 'Rule Engine', icon: Scale },
  { n: 4, label: 'Knowledge Graph Gaps', icon: Network },
  { n: 5, label: 'RAG Explanation', icon: BookOpen },
];

function Step({ n, title, subtitle, icon: Icon, children }) {
  return (
    <section className="rounded-xl border border-slate-300 bg-white shadow-sm">
      <header className="flex items-start gap-3 border-b border-slate-200 px-4 py-3">
        <span className="grid h-7 w-7 shrink-0 place-items-center rounded-md bg-slate-900 text-xs font-bold text-white">{n}</span>
        <div className="min-w-0">
          <h2 className="flex items-center gap-1.5 text-sm font-bold text-slate-900">
            <Icon className="h-4 w-4 text-slate-500" />
            {title}
          </h2>
          <p className="text-xs text-slate-500">{subtitle}</p>
        </div>
      </header>
      <div className="p-4">{children}</div>
    </section>
  );
}

export default function CitizenDiscovery() {
  const [fields, setFields] = useState(emptyFields);
  const [docs, setDocs] = useState({}); // docId -> { fileName, origin, status: 'queued' | 'ocr' | 'done' }
  const [notice, setNotice] = useState('');
  const [ragId, setRagId] = useState(null);
  const timers = useRef([]);
  const runs = useRef({}); // docId -> run token, invalidates in-flight OCR on remove/reset

  useEffect(() => () => timers.current.forEach(clearTimeout), []);

  const results = useMemo(() => evaluateAll(fields), [fields]);
  const gaps = useMemo(() => detectGaps(results, fields), [results, fields]);

  const schedule = (fn, ms) => timers.current.push(setTimeout(fn, ms));

  function applyDocFields(docId) {
    setFields((prev) => {
      const next = { ...prev };
      for (const [key, { value, confidence }] of Object.entries(DOC_BY_ID[docId].fields)) {
        if (prev[key].source === 'edited') continue; // never overwrite a deliberate user correction
        next[key] = { value, source: 'ocr', confidence, docId, ocrValue: value };
      }
      return next;
    });
  }

  function runOcr(docId, fileName, origin, delay = 0) {
    const token = (runs.current[docId] ?? 0) + 1;
    runs.current[docId] = token;
    const alive = () => runs.current[docId] === token;
    setDocs((prev) => ({ ...prev, [docId]: { fileName, origin, status: 'queued' } }));
    schedule(() => alive() && setDocs((prev) => ({ ...prev, [docId]: { ...prev[docId], status: 'ocr' } })), delay + 300);
    schedule(() => {
      if (!alive()) return;
      setDocs((prev) => ({ ...prev, [docId]: { ...prev[docId], status: 'done' } }));
      applyDocFields(docId);
    }, delay + 1100);
  }

  function removeDoc(docId) {
    runs.current[docId] = (runs.current[docId] ?? 0) + 1;
    setDocs(({ [docId]: _removed, ...rest }) => rest);
    setFields((prev) => {
      const next = { ...prev };
      for (const [key, field] of Object.entries(prev)) {
        if (field.docId !== docId) continue;
        next[key] = field.source === 'edited' ? { value: field.value, source: 'user' } : { value: '', source: 'missing' };
      }
      return next;
    });
  }

  function invalidateAll() {
    for (const d of DOCUMENT_TYPES) runs.current[d.id] = (runs.current[d.id] ?? 0) + 1;
  }

  function reset() {
    invalidateAll();
    setDocs({});
    setFields(emptyFields());
    setNotice('');
  }

  function loadSample() {
    invalidateAll();
    setDocs({});
    const base = emptyFields();
    for (const [key, value] of Object.entries(SAMPLE_USER_INPUT)) base[key] = { value, source: 'user' };
    setFields(base);
    setNotice('');
    DOCUMENT_TYPES.forEach((d, i) => runOcr(d.id, d.sampleFile, 'sample', i * 250));
  }

  function toggleSample(docId) {
    if (docs[docId]) removeDoc(docId);
    else runOcr(docId, DOC_BY_ID[docId].sampleFile, 'sample');
  }

  function handleFiles(files) {
    const taken = new Set(Object.keys(docs));
    const skipped = [];
    for (const file of files) {
      const name = file.name.toLowerCase();
      const type =
        DOCUMENT_TYPES.find((d) => d.keywords.some((k) => name.includes(k))) ?? DOCUMENT_TYPES.find((d) => !taken.has(d.id));
      if (!type) {
        skipped.push(file.name);
        continue;
      }
      taken.add(type.id);
      runOcr(type.id, file.name, 'upload');
    }
    setNotice(skipped.length ? `Could not classify: ${skipped.join(', ')} (all document slots filled — remove one first).` : '');
  }

  function editField(key, value) {
    setFields((prev) => {
      const field = prev[key];
      if (key === 'existingBenefits') return { ...prev, [key]: { value, source: 'user' } };
      if (value === '') return { ...prev, [key]: { value: '', source: 'missing' } };
      if (field.ocrValue !== undefined) {
        const source = String(value) === String(field.ocrValue) ? 'ocr' : 'edited';
        return { ...prev, [key]: { ...field, value, source } };
      }
      return { ...prev, [key]: { value, source: 'user' } };
    });
  }

  const ragResult = results.find((r) => r.id === ragId) ?? null;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-slate-950">Citizen & NGO Discovery Workflow</h1>
          <p className="max-w-2xl text-sm text-slate-600">
            Documents → structured profile → deterministic eligibility → knowledge-graph gaps → grounded explanation. The rule engine
            decides; AI only reads documents and explains.
          </p>
        </div>
        <ol className="flex flex-wrap items-center gap-1.5 text-xs font-medium text-slate-600">
          {PIPELINE.map(({ n, label, icon: Icon }, i) => (
            <li key={n} className="flex items-center gap-1.5">
              <span className="flex items-center gap-1 rounded-md border border-slate-300 bg-white px-2 py-1">
                <Icon className="h-3.5 w-3.5" />
                {label}
              </span>
              {i < PIPELINE.length - 1 && <ArrowRight className="h-3 w-3 text-slate-400" />}
            </li>
          ))}
        </ol>
      </div>

      <div className="grid gap-5 xl:grid-cols-12">
        <div className="space-y-5 xl:col-span-5">
          <Step n={1} icon={FileUp} title="Document Upload & OCR" subtitle="PaddleOCR extraction with field-level confidence (simulated)">
            <DocumentUpload
              docs={docs}
              fields={fields}
              notice={notice}
              onFiles={handleFiles}
              onToggle={toggleSample}
              onLoadSample={loadSample}
              onReset={reset}
            />
          </Step>
          <Step n={2} icon={UserRound} title="Structured Citizen Welfare Profile" subtitle="Every field carries provenance; edits re-run the rule engine instantly">
            <ProfilePanel fields={fields} onEdit={editField} />
          </Step>
        </div>

        <div className="space-y-5 xl:col-span-7">
          <Step n={3} icon={Scale} title="Deterministic Eligibility Evaluation" subtitle="Rule engine is the sole authority · missing info never becomes rejection">
            <EligibilityResults results={results} onExplain={setRagId} />
          </Step>
          <Step n={4} icon={Network} title="Knowledge Graph & Benefit Gap Detection" subtitle="Complementary schemes, document-blocked and unclaimed benefits">
            <BenefitGaps gaps={gaps} onExplain={setRagId} />
          </Step>
        </div>
      </div>

      <RagDrawer result={ragResult} docs={docs} onClose={() => setRagId(null)} />
    </div>
  );
}
