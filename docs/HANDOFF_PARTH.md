# Backend handoff for Parth (frontend)

This is a two-page summary. The full mapping from `mockData.js` / `eligibility.js` to the API, with fetch helpers, is in [`frontend_integration_guide.md`](frontend_integration_guide.md). The endpoint table is in [`api_spec.md`](api_spec.md). Every example links to a **real captured response** in [`fixtures/`](fixtures/) (head `2e430e7`).

## 1. Start the backend (from the repo root)

```bash
docker compose up -d postgres neo4j                     # PostgreSQL on host port 5433, Neo4j 7687 (Neo4j optional)
cp .env.example .env                                     # once; defaults are fine for local dev
.venv/Scripts/python.exe -m alembic -c backend/alembic.ini upgrade head
.venv/Scripts/python.exe scripts/seed_database.py        # schemes + rule versions + 3 synthetic demo citizens
.venv/Scripts/python.exe -m uvicorn app.main:app --app-dir backend --port 8000
```

- **Readiness:** `http://localhost:8000/api/v1/ready` should report `"status":"ready"` ([`ready.json`](fixtures/ready.json)). Swagger UI is at `/docs`.
- **Windows:** if port 8000 fails with `WinError 10013` (reserved by Hyper-V), use `--port 8500` and the matching base URL.
- **Optional pieces:** without Ollama, explanations quote official passages (`provider: "none"`). Without Neo4j, the graph uses an identical in-memory fallback. The FAISS index (`scripts/build_vector_index.py`) and analytics outputs (`scripts/run_district_analytics.py --engine pandas`) must exist, or those endpoints return 503 with a hint.
- **Full setup** (Python env, OCR models, Ollama model choice, 8 GB memory budget): [`deployment.md`](deployment.md).

**Environment (repo-root `.env`):**

| Variable | Default | Effect |
|---|---|---|
| `CORS_ORIGINS` | `http://localhost:5173` | Comma-separated; add `http://localhost:4173` for `vite preview`. `*` is refused at startup |
| `APP_ENV` | `development` | Outside development/dev/test/testing/demo/local the API refuses to start unless `ALLOW_UNAUTHENTICATED=true` |
| `ALLOW_NON_SYNTHETIC` | `false` | Keep false; non-synthetic citizens get 403 |
| `DB_CONNECT_TIMEOUT_S` | `5` | Fail fast if PostgreSQL is down |
| `OLLAMA_MODEL` | `qwen2.5:3b-instruct` in `.env.example` | Use `qwen2.5:7b-instruct` with a GPU |

**Frontend:** put `VITE_API_BASE_URL=http://localhost:8000/api/v1` in `frontend/.env.local`. There is no auth and no cookies. Send JSON with `Content-Type: application/json`, and send uploads as `FormData` (don't set Content-Type yourself).

## 2. Endpoints per UI step (paths relative to `/api/v1`)

| UI | Call | Fixture |
|---|---|---|
| Session start | `POST /citizens` `{"label":"Synthetic demo: …","is_synthetic":true}` → keep `id` | [`citizen_create.json`](fixtures/citizen_create.json) |
| Form definition | `GET /profile-fields` (fields, enums, min/max, document types) | [`profile_fields.json`](fixtures/profile_fields.json) |
| **Step 1** upload/OCR | `POST /citizens/{id}/documents` multipart `file`, optional `document_type` → `applied_fields`, `review_required`, `conflicts`, `type_mismatch` (201 new, 200 duplicate) | [`document_upload.json`](fixtures/document_upload.json), [`document_upload_type_mismatch.json`](fixtures/document_upload_type_mismatch.json), [`document_delete.json`](fixtures/document_delete.json) |
| **Step 2** profile + badges | `GET` / `PATCH /citizens/{id}/profile` `{"fields":{name:value\|null}}` → each field has `value, provenance (self_declared \| document_extracted \| verified), confidence (0–1), source_document_id` | [`profile_get.json`](fixtures/profile_get.json), [`profile_patch.json`](fixtures/profile_patch.json) |
| Existing benefits | `PUT /citizens/{id}/enrollments/{scheme_id}` `{"status":"enrolled\|applied\|not_enrolled"}` | [`enrollment_put.json`](fixtures/enrollment_put.json) |
| **Step 3** eligibility tabs | `POST /citizens/{id}/evaluations` → arrays `eligible` / `potentially_eligible` / `not_eligible`, each with `conditions[]` (`outcome` pass/fail/missing, `message`, `resolves_with`) and `verification_warning` | [`evaluation_run.json`](fixtures/evaluation_run.json) |
| "Results out of date?" | `GET /citizens/{id}/evaluations/latest` → `stale`, `stale_reasons` | [`evaluation_latest_stale.json`](fixtures/evaluation_latest_stale.json) |
| What-if preview | `POST /eligibility/evaluate` (nothing stored, `simulation: true`) | [`eligibility_evaluate_simulation.json`](fixtures/eligibility_evaluate_simulation.json) |
| **Step 4** gaps / KG | `GET /citizens/{id}/recommendations` → `unclaimed`, `potentially_eligible`, `complementary` (`presentation: available \| check_eligibility`), `document_gaps`, `information_needs`, `unclaimed_totals` / `potential_totals` | [`recommendations.json`](fixtures/recommendations.json) |
| **Step 5** RAG drawer | `POST /citizens/{id}/schemes/{scheme_id}/explanation` `{"language":"en"}` → `answer` with `[n]` markers, `citations[]`, `decision`, `grounded`, `uncertainty`, `provider`, `flags` | [`explanation_citizen.json`](fixtures/explanation_citizen.json), [`explanation_provider_none.json`](fixtures/explanation_provider_none.json) |
| Drawer Q&A | `POST /schemes/{scheme_id}/ask` `{"question":"…","language":"en"}` (`decision: null`) | [`scheme_ask.json`](fixtures/scheme_ask.json), [`scheme_ask_insufficient_evidence.json`](fixtures/scheme_ask_insufficient_evidence.json) |
| Docs checklist / steps | `GET /schemes/{scheme_id}` (`required_documents`, `application_steps`, `sources`, `limitations`) | [`scheme_detail_mmvy.json`](fixtures/scheme_detail_mmvy.json) |
| **Admin** map / KPIs | `GET /analytics/summary`, `/analytics/districts`, `/analytics/districts/{id}`, `/analytics/schemes`, `/analytics/villages?district_id=&limit=&offset=` | [`analytics_districts.json`](fixtures/analytics_districts.json), [`analytics_summary.json`](fixtures/analytics_summary.json) |
| Reset | `DELETE /citizens/{id}` (204) | — |

Call order after any profile change: `PATCH profile`, then `POST evaluations` (Step 3), then `GET recommendations` (Step 4). Recommendations and explanations re-evaluate on their own if the stored run is stale (`evaluated_now: true`, `stale_reasons`).

```bash
API=http://localhost:8000/api/v1
CID=$(curl -s -X POST "$API/citizens" -H "Content-Type: application/json" -d '{"label":"Synthetic demo: handoff","is_synthetic":true}' | jq -r .id)
curl -s -F "file=@data/raw/sample_documents/synthetic_income_certificate_en_01.png" "$API/citizens/$CID/documents" | jq '{applied_fields, conflicts, type_mismatch}'
curl -s -X POST "$API/citizens/$CID/evaluations" -H "Content-Type: application/json" -d '{}' | jq .summary
curl -s "$API/citizens/$CID/recommendations" | jq '{unclaimed: [.unclaimed[].scheme_id], unclaimed_totals}'
curl -s -o /dev/null -w "%{http_code}\n" -X DELETE "$API/citizens/$CID"
```

## 3. Errors

Every error has the same body: `{"error": {"code", "message", "details"}}`. Branch on `code`, show `message`, and log the `X-Request-ID` header. Key codes:

| HTTP | Codes | UI |
|---|---|---|
| 404 | `citizen_not_found`, `scheme_not_found`, `document_not_found`, `evaluation_not_found` (no run yet) | Re-create the session / show "not found" |
| 403 | `non_synthetic_not_allowed` | Always send `is_synthetic: true` |
| 413 / 415 / 422 | `payload_too_large` (> 10 MB), `unsupported_media_type` (not PNG/JPEG/WebP/PDF), `corrupt_document`, `document_type_not_ocr_supported` | Upload messages |
| 422 | `validation_error` (`details[].loc[2]` = field; `type` `profile_field` / `sensitive_identifier`), `provenance_not_allowed` (stateless `verified`) | Inline field errors |
| 503 | `ocr_unavailable`, `rag_unavailable`, `analytics_not_generated`, `rule_engine_unavailable` | "Service not set up", plus `details.hint` |
| 500 | `internal_error` | Generic error + request id |

## 4. Demo-data limits (must be visible in the UI)

- **Synthetic only, no authentication.** Never enter real names or ID numbers; 9+ digit runs are rejected. Show a "demo / synthetic data" notice.
- **Analytics is synthetic.** Every response has `is_synthetic: true` and `dataset_label: "SYNTHETIC-MP-DEMO-v1"`. Show a banner. Counts are citizen × scheme assessments, not people. Map positions are polygon centroids.
- **Results are indicative.** Show `disclaimer` on results. Show `verification_warning` for the 6 partially verified schemes, including `post_matric_scholarship_obc` 2018.2.

## 5. Known backend constraints

- **Latency.**
  - OCR: about 3–7 s per page on CPU; the first upload after server start takes about 9–19 s while models load. Use a spinner and a timeout of 60 s or more.
  - Explanations: 6–20 s (measured 15 s; `/ask` about 4 s). Show a loading state and use an `AbortController` timeout of about 150 s.
  - Evaluation and recommendations: under 1 s.
- **Conflict acceptance.** "Use the certificate's value" via PATCH stores it as `self_declared`. If the rule needs a document (caste category), the user must **delete the document and upload it again** to get `document_extracted`.
- **Type mismatch.** If the selected document type contradicts the content, nothing is applied and every value goes to `review_required` (`type_mismatch: true`).
- **The LLM explains, never decides.**
  - Take the verdict and condition list from the rule-engine fields (`decision` / `conditions`), not from `answer`.
  - Claim-level grounding removes unsupported citations and flags unsupported numbers (`grounded: false` + `uncertainty`). Invented but on-topic sentences can still pass; the captured explanation says a Samagra ID was "verified from the portal" when it was self-declared.
- **Stateless results are simulations.** In `POST /eligibility/evaluate`, `document_extracted` is client-asserted (`client_asserted_provenance`) and `verified` is rejected. Never show those results as document-backed.
- **Contract differences from the mock** (scheme ids, `potentially_eligible`, MMVY income < ₹8 lakh, hostel assistance is SC/ST only, confidence 0–1, snake_case fields): see [integration guide §10](frontend_integration_guide.md#10-contract-differences-vs-the-mock-read-before-wiring).
- `profile_changed_since_evaluation` is deprecated and always `false`; use `stale` / `stale_reasons`.
