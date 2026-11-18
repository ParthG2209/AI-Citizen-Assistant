# Backend, AI & Data Implementation Plan

Branch: `master-work-1` (never `main`). Frontend (`frontend/`) is owned by Parth and is **not** modified by this work.

Status legend: ⬜ Not implemented · 🟨 In progress · 🟩 Implemented · ✅ Tested · ⛔ Blocked

## 1. Audit (starting point, commit `a364712`)

| Area | Finding |
|---|---|
| `backend/`, `ai_modules/`, `scripts/`, `docs/`, root config | 0-byte placeholders |
| Frontend | React prototype on mock data (`frontend/src/data/mockData.js`, `frontend/src/lib/eligibility.js`); defines the UX contract: provenance badges, Eligible / Potentially Eligible / Not Eligible tabs, condition lists, KG complementary + document-blocked gaps, RAG drawer (explanation, documents, steps, citations, Q&A), district map (coverage %, eligible vs enrolled, top missing docs, underserved blocks) |
| Mock scheme rules | Simplified, **not** verified; must be re-derived from official sources |
| Local environment | Python 3.11 + uv, Docker (PostgreSQL 16, Neo4j 5 Community), Ollama with `qwen2.5:7b-instruct`, no system Java (PySpark needs a JDK) |

## 2. Module checklist

| # | Module | Path | Depends on | Status |
|---|---|---|---|---|
| 1 | Shared field / provenance / document-type contract | `ai_modules/rule_engine/fields.py` | — | 🟩 |
| 2 | Rule engine result contract | `ai_modules/rule_engine/result.py` | 1 | 🟩 |
| 3 | Rule loader + schema validation | `ai_modules/rule_engine/loader.py` | 1, 2 | ⬜ |
| 4 | Operators (typed, PASS/FAIL/MISSING) | `ai_modules/rule_engine/operators.py` | 1 | ⬜ |
| 5 | Engine (evaluate, reproducible hashes) | `ai_modules/rule_engine/engine.py` | 3, 4 | ⬜ |
| 6 | Scheme rule YAML (official sources) | `ai_modules/rule_engine/rules/**` | 3 | ⬜ |
| 7 | Backend config / logging / errors | `backend/app/config.py`, `utils/` | — | ⬜ |
| 8 | PostgreSQL models + Alembic migrations | `backend/app/models/`, `backend/migrations/` | 7 | ⬜ |
| 9 | Pydantic schemas + services + routes | `backend/app/schemas/`, `services/`, `api/` | 5, 8 | ⬜ |
| 10 | OCR pipeline (PaddleOCR) | `ai_modules/ocr/` | 1 | ⬜ |
| 11 | Knowledge graph (Neo4j) | `ai_modules/knowledge_graph/` | 3, 6 | ⬜ |
| 12 | Recommendation / benefit gaps | `ai_modules/recommendation/` | 2, 3, 11 | ⬜ |
| 13 | RAG (ingest, FAISS, Ollama/Gemini) | `ai_modules/rag/` | 2, 3 | ⬜ |
| 14 | District analytics (PySpark + Pandas + Plotly) | `ai_modules/analytics/` | — | ⬜ |
| 15 | Scripts (seed, index, graph, analytics) | `scripts/` | 8, 11, 13, 14 | ⬜ |
| 16 | Docker, `.env.example`, CI | root, `.github/workflows/` | all | ⬜ |
| 17 | Frontend integration guide + OpenAPI | `docs/` | 9 | ⬜ |

## 3. Interface contracts (binding for all modules)

### 3.1 Profile fields & provenance — `ai_modules/rule_engine/fields.py`
- `PROFILE_FIELDS` (typed catalog), `coerce_value(name, value)`, `DOCUMENT_TYPES` (`income_certificate`, `caste_certificate`, `samagra_domicile`, `class12_marksheet` are OCR-supported; `land_record`, `ration_card`, `college_admission` are not).
- `Provenance`: `self_declared` < `document_extracted` < `verified` (`TRUST` map). A user correction of an OCR value becomes `self_declared`. OCR text is **never** `verified`.
- `FieldValue(value, provenance, confidence, source_document_id)`.
- No Aadhaar numbers, Samagra ID numbers or raw certificate images are persisted.

### 3.2 Rule engine — `ai_modules/rule_engine`
```python
from ai_modules.rule_engine import RuleEngine, load_rules
engine = RuleEngine()                       # loads ai_modules/rule_engine/rules/**/*.yaml
engine.schemes() -> list[SchemeRule]        # active rule definitions
engine.get(scheme_id) -> SchemeRule
engine.evaluate(scheme_id, profile: Mapping[str, FieldValue], as_of: date | None = None) -> SchemeEvaluation
engine.evaluate_all(profile, as_of=None) -> list[SchemeEvaluation]   # only rules effective on as_of
```
`SchemeRule` exposes: `scheme_id, version, name, level ("central"|"state"), state, department, effective_from, effective_to, verification_status, sources[ {title, url, publisher, accessed_on, section} ], benefit {type, amount_inr, frequency, description}, required_documents[doc ids], conditions, relationships[ {scheme_id, relation, reason, source_url} ], limitations[str], rule_hash`.
Benefit `type` ∈ `recurring_cash | one_time_cash | fee_reimbursement | in_kind | insurance_cover | housing_assistance`; `frequency` ∈ `annual | monthly | one_time | null`; `amount_inr` is `null` when the official amount is variable/unknown (never invented).
Results: `ai_modules/rule_engine/result.py` (`Outcome`, `MissingReason`, `EligibilityStatus`, `ConditionResult`, `SchemeEvaluation`).

### 3.3 OCR — `ai_modules/ocr`
```python
from ai_modules.ocr import process_document, ocr_available
process_document(data: bytes, filename: str, declared_type: str | None = None) -> OcrDocumentResult
# OcrDocumentResult: document_type, classification_confidence, page_count, engine, text_length,
#   fields: list[ExtractedField(name, value, raw_text, confidence, needs_review, validation_errors)],
#   missing_fields: list[str], warnings: list[str]
# Errors: UnsupportedFileError, CorruptDocumentError, FileTooLargeError, OcrUnavailableError
```

### 3.4 Knowledge graph & recommendations
```python
from ai_modules.knowledge_graph import KnowledgeGraph          # Neo4j; seeds from SchemeRule objects
from ai_modules.recommendation import build_recommendations    # pure function over engine results
build_recommendations(evaluations, enrolled_scheme_ids, complements, rules) -> RecommendationReport
```
Complementary edges come from rule YAML `relationships`; Neo4j traversal is primary, a deterministic in-memory traversal of the same data is the fallback when Neo4j is down. A complementary scheme is only presented as available after its own rule evaluation.

### 3.5 RAG
```python
from ai_modules.rag import ExplanationService
ExplanationService().explain(evaluation: SchemeEvaluation, rule: SchemeRule, question: str | None = None, language="en") -> Explanation
# Explanation: answer, decision (copied from engine), citations[{chunk_id, source_title, url, page, section, score, excerpt}],
#   grounded: bool, provider ("ollama"|"gemini"|"none"), model, uncertainty: str | None
```

### 3.6 Analytics outputs — `data/processed/analytics/` (synthetic, labelled `is_synthetic: true`)
`summary.json`, `districts.json`, `blocks.json`, `villages.json`, `scheme_adoption.json`, Parquet/CSV mirrors, Plotly HTML report.

## 4. Milestones
1. Foundations: branch, dependencies, databases, contracts, plan ← this document
2. Rule engine + verified rules + backend core + eligibility API
3. OCR + document API
4. Knowledge graph + recommendations API
5. RAG + explanation API
6. Analytics + API
7. Infra, CI, integration guide, final verification
