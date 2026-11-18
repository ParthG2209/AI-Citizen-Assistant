# Backend, AI & Data Implementation Plan

Branch: `master-work-1` (never `main`). The frontend (`frontend/`) is owned by Parth and is **not** modified by this work.

Status legend: ⬜ Not implemented · 🟨 In progress · 🟩 Implemented · ✅ Tested · ⛔ Blocked

## 1. Audit (starting point, commit `a364712`)

| Area | Finding |
|---|---|
| `backend/`, `ai_modules/`, `scripts/`, `docs/`, root config | 0-byte placeholders |
| Frontend | React prototype on mock data (`frontend/src/data/mockData.js`, `frontend/src/lib/eligibility.js`). It defines the UX contract: provenance badges; Eligible / Potentially Eligible / Not Eligible tabs; condition lists; KG complementary + document-blocked gaps; RAG drawer (explanation, documents, steps, citations, Q&A); district map (coverage %, eligible vs enrolled, top missing docs, underserved blocks) |
| Mock scheme rules | Simplified and **not** verified; must be re-derived from official sources |
| Local environment | Python 3.11 + uv, Docker (PostgreSQL 16, Neo4j 5 Community), Ollama with `qwen2.5:7b-instruct`, no system Java (PySpark needs a JDK) |

## 2. Module checklist

The ✅ test counts are the module owners' final runs on 2026-11-18, including slow and integration tests against live services. They were not re-run for this document.

| # | Module | Path | Depends on | Status |
|---|---|---|---|---|
| 1 | Shared field / provenance / document-type contract | `ai_modules/rule_engine/fields.py`, `definitions.py` | — | ✅ |
| 2 | Rule engine result contract | `ai_modules/rule_engine/result.py` | 1 | ✅ |
| 3 | Rule loader + schema validation | `ai_modules/rule_engine/loader.py` | 1, 2 | ✅ |
| 4 | Operators (typed, PASS/FAIL/MISSING) | `ai_modules/rule_engine/operators.py` | 1 | ✅ |
| 5 | Engine (evaluate, reproducible hashes) | `ai_modules/rule_engine/engine.py` | 3, 4 | ✅ (rule engine: 165 tests) |
| 6 | Scheme rule YAML (official sources) | `ai_modules/rule_engine/rules/{central,mp}/*.yaml` | 3 | ✅ 9 schemes / 10 versions (3 verified, 6 partially verified) |
| 7 | Backend config / logging / errors | `backend/app/config.py`, `errors.py`, `utils/` | — | ✅ |
| 8 | PostgreSQL models + Alembic migrations | `backend/app/models/`, `backend/migrations/` (head `6cc2e7e4d5a8`) | 7 | ✅ |
| 9 | Pydantic schemas + services + routes | `backend/app/schemas/`, `services/`, `api/` (31 operations) | 5, 8 | ✅ (backend: 104 tests) |
| 10 | OCR pipeline (PaddleOCR) | `ai_modules/ocr/` | 1 | ✅ (122 tests; 204/204 fields correct on 16 extra synthetic documents) |
| 11 | Knowledge graph (Neo4j) | `ai_modules/knowledge_graph/` | 3, 6 | ✅ (152 nodes / 244 relationships) |
| 12 | Recommendation / benefit gaps | `ai_modules/recommendation/` | 2, 3, 11 | ✅ (KG + recommendation: 42 tests, 6 against live Neo4j) |
| 13 | RAG (ingest, FAISS, Ollama/Gemini) | `ai_modules/rag/` | 2, 3 | ✅ (61 tests incl. live Ollama; 25 official documents, 571 chunks) |
| 14 | District analytics (PySpark + Pandas + Plotly) | `ai_modules/analytics/` | — | ✅ (33 tests; Spark = Pandas outputs) |
| 15 | Scripts (seed, index, graph, analytics, synthetic documents) | `scripts/` | 8, 11, 13, 14 | 🟩 (run successfully by module owners; no unit tests) |
| 16 | Docker, `.env.example`, CI | `backend/Dockerfile`, `docker-compose.yml`, `.github/workflows/ci.yml` | all | 🟩 (CI workflow not run in this environment) |
| 17 | Frontend integration guide + OpenAPI | `docs/frontend_integration_guide.md`, `docs/api_spec.md`, `docs/architecture.md`, `docs/openapi.json`, `docs/fixtures/` | 9 | 🟩 (fixtures captured from the running API; guide examples executed) |

## 3. Interface contracts (as implemented)

### 3.1 Profile fields & provenance: `ai_modules/rule_engine/fields.py`
- `PROFILE_FIELDS` is the typed catalog of 20 fields, each `FieldSpec(name, type, label, choices, minimum, maximum, unit, sensitive)`. `coerce_value(name, value)` raises `ValueError`. Enum strings are lowercased, booleans accept `yes/no/true/false/1/0`, and string rules compare case-insensitively.
- `DOCUMENT_TYPES`: `income_certificate`, `caste_certificate`, `samagra_domicile` and `class12_marksheet` are OCR-supported; `land_record`, `ration_card` and `college_admission` are not.
- `Provenance`: `self_declared` (1) < `document_extracted` (2) < `verified` (3) (`TRUST` map). A user correction of an OCR value becomes `self_declared`. OCR text is **never** `verified`, and no code path sets `verified` today.
- `FieldValue(value, provenance, confidence=None, source_document_id=None)`.
- No Aadhaar numbers, Samagra ID numbers or raw certificate images are persisted. The backend rejects 9+ digit runs in string fields (`sensitive_identifier`).

### 3.2 Rule engine: `ai_modules/rule_engine`
```python
from ai_modules.rule_engine import RuleEngine, load_rules
engine = RuleEngine(rules_dir=None, *, rules=None, confidence_threshold=0.80)  # loads rules/**/*.yaml
engine.schemes(as_of=None) -> list[SchemeRule]      # one effective version per scheme
engine.get(scheme_id, as_of=None) -> SchemeRule
engine.evaluate(scheme_id, profile: Mapping[str, FieldValue], as_of=None) -> SchemeEvaluation
engine.evaluate_all(profile, as_of=None) -> list[SchemeEvaluation]
engine.scheme_ids(); engine.versions(scheme_id); engine.evaluate_rule(rule, profile, as_of)   # as_of required here
```
- `SchemeRule` exposes `scheme_id, version, name, level ("central"|"state"), state, department, effective_from, effective_to, verification_status, sources[{title, url, publisher, accessed_on, section}], benefit{type, amount_inr, frequency, description, is_cash}, required_documents, conditions, relationships[{scheme_id, relation, reason, source_url}], limitations, application_steps, rule_hash`.
- Conditions are `condition` (`field`, `operator`, `value`, `min_trust`, `resolves_with`, `failure_message`, `source_ref`) or `group` (`mode: all|any`, `items`).
- Benefit `type` ∈ `recurring_cash | one_time_cash | fee_reimbursement | in_kind | insurance_cover | housing_assistance`. `frequency` ∈ `annual | monthly | one_time | null`. `amount_inr` is `null` when the official amount is variable or unknown; it is never invented.
- Relationship direction: `A TOPS_UP B` means "A tops up B". The other relations are `COMPLEMENTED_BY`, `PREREQUISITE_FOR` and `MUTUALLY_EXCLUSIVE_WITH`.
- Results live in `result.py`: `Outcome` (pass/fail/missing), `MissingReason` (no_value/insufficient_trust/low_confidence), `EligibilityStatus`, `ConditionResult` (with `children` for groups) and `SchemeEvaluation` (`input_hash`, `rule_hash`, `notes`).
- Errors: `RuleValidationError`, `UnknownSchemeError`, `NoEffectiveRuleError`, `ProfileValueError`.

### 3.3 OCR: `ai_modules/ocr`
```python
from ai_modules.ocr import process_document, ocr_available
process_document(data: bytes, filename: str, declared_type: str | None = None, *, engine=None, as_of=None) -> OcrDocumentResult
# OcrDocumentResult: document_type, classification_confidence, page_count, engine, text_length,
#   fields: list[ExtractedField(name, value, raw_text, confidence, needs_review, validation_errors)],
#   missing_fields, warnings, detected_type, elapsed_ms
# Errors: UnsupportedFileError (+ UnsupportedDocumentTypeError), CorruptDocumentError, FileTooLargeError, OcrUnavailableError
```
- `value` is `None` whenever `validation_errors` is non-empty.
- The file type comes from magic bytes only. Aadhaar-like numbers are stripped and Samagra numbers become `has_samagra_id=True`.
- The backend never stores or returns `raw_text`.

### 3.4 Knowledge graph & recommendations
```python
from ai_modules.knowledge_graph import KnowledgeGraph
kg = KnowledgeGraph.connect(rules=None, districts=None, settings=None, use_neo4j=True, auto_seed=True)
kg.source  # "neo4j" | "fallback"
kg.complementary_schemes(ids); kg.documents_unlocking(pairs); kg.schemes_requiring_document(doc_id)
kg.schemes_available_in(region_id); kg.counts()

from ai_modules.recommendation import build_recommendations
build_recommendations(evaluations, enrolled_scheme_ids, complements, rules) -> RecommendationReport  # .to_dict()
```
- Complementary edges come from the rule YAML `relationships`.
- Neo4j traversal is primary. When Neo4j is down, a deterministic in-memory traversal of the same data takes over, with identical results (tested).
- A complementary scheme is presented as `available` only when its own evaluation is eligible; otherwise it is `check_eligibility`.
- Totals never double-count. Cash categories are separate sums, and non-cash benefits are listed, not summed.

### 3.5 RAG
```python
from ai_modules.rag import ExplanationService
svc = ExplanationService(index_dir=..., retriever=None, router=None, top_k=5)
svc.explain(evaluation: SchemeEvaluation, rule: SchemeRule, question: str | None = None, language="en") -> Explanation
svc.answer_question(question, *, scheme_id=None, scheme_name=None, language="en") -> Explanation   # decision None
# Explanation: answer, decision (copied from engine), citations[{marker, chunk_id, doc_id, source_title, url, page, section, score, excerpt}],
#   grounded, provider ("ollama"|"gemini"|"none"), model, uncertainty, language, scheme_id, refused, flags, latency_ms
```
The backend additionally asserts that `explanation.decision == evaluation.status` and returns 500 otherwise.

### 3.6 Analytics outputs: `data/processed/analytics/` (synthetic, labelled `is_synthetic: true`)
- JSON files: `summary.json`, `districts.json`, `blocks.json`, `villages.json`, `scheme_adoption.json`, `district_schemes.json` and `metadata.json`.
- Also written: Parquet/CSV mirrors, `rejected_rows.csv` and the Plotly `report.html`.
- The backend loads them with `ai_modules.analytics.load_outputs(dir)`.

## 4. Milestones
1. Foundations: branch, dependencies, databases, contracts, plan ✅
2. Rule engine + rules derived from official sources + backend core + eligibility API ✅
3. OCR + document API ✅
4. Knowledge graph + recommendations API ✅
5. RAG + explanation API ✅
6. Analytics + API ✅
7. Infra, CI, integration guide, final verification 🟩 (docs and fixtures done; CI workflow not executed here)

## 5. Delivered

| Capability | Endpoints | Evidence |
|---|---|---|
| Citizens, profile with provenance, enrollments | `/citizens`, `/citizens/{id}/profile`, `/citizens/{id}/enrollments/{scheme_id}`, `/profile-fields` | fixtures `citizen_*`, `profile_*`, `enrollment_*` |
| Certificate upload with real PaddleOCR (PP-OCRv5, en + hi), provenance merge, conflicts, review list, duplicates by SHA-256 | `POST /citizens/{id}/documents`, `GET/DELETE /documents/{id}` | `document_upload.json` (synthetic income certificate, conflict on district) |
| Deterministic eligibility for 9 MP + central schemes with condition traces, persisted runs, stateless preview | `/citizens/{id}/evaluations`, `/evaluations/{id}`, `/eligibility/evaluate` | `evaluation_run.json`, `eligibility_evaluate_stateless.json` |
| Scheme catalog with official sources, verification status, limitations, application steps | `/schemes`, `/schemes/{id}` | `schemes_list.json`, `scheme_detail_mmvy.json`, `docs/eligibility_rules.md` |
| Benefit gaps, complementary schemes via Neo4j (in-memory fallback), ranked next actions, totals without double counting | `/citizens/{id}/recommendations` | `recommendations.json` |
| Grounded explanations and scheme Q&A over 25 official documents (FAISS), Ollama → Gemini → extractive fallback, decision guard, injection/off-topic refusal | `/citizens/{id}/schemes/{sid}/explanation`, `/explain`, `/schemes/{sid}/ask` | `explanation_citizen.json`, `explanation_provider_none.json`, `scheme_ask*.json` |
| District / block / village coverage on labelled synthetic data (Pandas or PySpark), underserved detection, Plotly report | `/analytics/*` | `analytics_*.json` |
| Health / readiness with dependency checks | `/health`, `/ready` | `ready.json` |
| Docs | — | `README.md`, `docs/{frontend_integration_guide,api_spec,architecture,deployment,data_provenance,eligibility_rules}.md`, `docs/openapi.json`, `docs/fixtures/` |

### Known limitations (carried into the README)
- **No authentication or authorisation.** Synthetic data only.
- **Rule gaps.**
  - Six of the nine schemes are `partially_verified`.
  - Some official criteria cannot be modelled with the current fields: the PM-KISAN Group D exception, the PMAY-G ₹15,000/month member-income exclusion, and course/institution types.
  - Details per scheme are in `docs/eligibility_rules.md`.
- **OCR** has been measured only on synthetic layouts, not real MP certificates. It takes about 3–7 s per page on CPU.
- **RAG.**
  - The 7B model can cite a retrieved passage that doesn't support the sentence, or misstate a condition. The decision itself is guarded.
  - Some official sources are scanned PDFs with no text layer and are not indexed.
- **Analytics** is entirely synthetic: generic block/village names and random eligibility draws, not rule-engine output.
- **`verified` provenance** is defined but nothing sets it (no DigiLocker/e-District integration).
- **Environment variables.** Fixed after the first docs pass: the backend now loads `.env` into the process environment (`load_dotenv(override=False)`), so `ai_modules` settings apply in a native run. Standalone `scripts/` read only the shell environment.
- **Database timeout.** Fixed: `DB_CONNECT_TIMEOUT_S` (default 5 s) makes requests and `/ready` fail fast when PostgreSQL is down.
