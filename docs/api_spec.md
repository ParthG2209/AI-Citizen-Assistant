# API Specification — AI Citizen Assistant (v0.1.0)

- **Base URL:** `http://localhost:8000/api/v1`. Use port 8500 on Windows hosts where Hyper-V reserves 8000.
- **Interactive docs:** `http://localhost:8000/docs` (Swagger UI).
- **Machine-readable schema:** `http://localhost:8000/openapi.json`, exported to [`openapi.json`](openapi.json). It has 31 operations.

Request/response examples for every endpoint are real captures in [`fixtures/`](fixtures/). How the frontend should use them is in [`frontend_integration_guide.md`](frontend_integration_guide.md).

Regenerate `openapi.json` after changing routes or schemas:

```bash
PYTHONPATH=".;backend" .venv/Scripts/python.exe -c "import json; from app.main import app; open('docs/openapi.json','w',encoding='utf-8').write(json.dumps(app.openapi(), indent=2, ensure_ascii=False))"
```

(On Linux/macOS use `PYTHONPATH=".:backend"` and `.venv/bin/python`.)

## Conventions

- JSON field names are `snake_case`. Ids are UUIDs, except scheme, district, block and village ids, which are slugs.
- Dates are `YYYY-MM-DD`. Timestamps are ISO 8601 UTC. Money is INR as a number. Confidences are in `[0, 1]`.
- Request bodies reject unknown keys (`extra="forbid"`), which gives a 422 `validation_error`.
- **Auth:** none. This is a prototype for synthetic data only; see [`data_provenance.md`](data_provenance.md). The app enforces three safety rules:
  - It refuses to start when `APP_ENV` is not `development`/`dev`/`test`/`testing`/`demo`/`local` unless `ALLOW_UNAUTHENTICATED=true`.
  - It refuses `*` in `CORS_ORIGINS`.
  - It rejects non-synthetic citizens unless `ALLOW_NON_SYNTHETIC=true`.
- **CORS:** origins come from `CORS_ORIGINS` (default `http://localhost:5173`). Allowed request headers are `Content-Type` and `X-Request-ID`. `X-Request-ID` is echoed on every response, and generated if the client didn't send one.
- **Body limit:** any `POST`/`PUT`/`PATCH` with `Content-Length` greater than `MAX_UPLOAD_MB` (10 MB) + 64 KiB is rejected with 413 before the route runs.

## Error envelope

```json
{ "error": { "code": "scheme_not_found", "message": "Scheme 'nope' not found", "details": null } }
```

Every error uses this envelope: `ApiError`, FastAPI validation errors, Starlette HTTP errors (`not_found`, `method_not_allowed`) and unhandled exceptions (`internal_error`, with no internal details exposed). For validation errors, `details` is a list of `{loc, msg, type}`; rejected input values are never echoed. The full code list with meanings is in the [integration guide §2](frontend_integration_guide.md#2-errors).

Not an error envelope: `GET /ready` returns **503** with `{"status": "not_ready", "checks": {…}}` when PostgreSQL or the rule engine is not ready.

## Endpoints

"OpenAPI errors" is what `openapi.json` declares; the routers declare generic 404/422 sets. "Actual error codes" is what the code can return, checked against the services and against the running server. OpenAPI does not declare the 403 on `POST /citizens` or the 503 on `GET …/evaluations/latest`; both are listed here.

### Health

| Method | Path | Summary | Success | Actual error codes | Fixture |
|---|---|---|---|---|---|
| GET | `/health` | Liveness probe | 200 `{"status":"ok"}` | — | [`health.json`](fixtures/health.json) |
| GET | `/ready` | Readiness: `database`, `rule_engine` (required); `neo4j`, `ollama`, `knowledge_graph`, `rag` (reported only) | 200 | 503 `not_ready` body (see above) | [`ready.json`](fixtures/ready.json) |

### Citizens, profile, enrollments (tag `citizens`)

| Method | Path | Summary | Success | OpenAPI errors | Actual error codes | Fixture |
|---|---|---|---|---|---|---|
| GET | `/profile-fields` | Profile field catalog, document types, provenance levels | 200 | 404, 422 | — | [`profile_fields.json`](fixtures/profile_fields.json) |
| POST | `/citizens` | Create a citizen. Body optional: `{label?: str ≤120, is_synthetic?: bool = true}` | 201 | 404, 422 | 403 `non_synthetic_not_allowed` (`is_synthetic: false` without `ALLOW_NON_SYNTHETIC`); 422 `validation_error` (incl. `details[].type = sensitive_identifier` for ID-like labels) | [`citizen_create.json`](fixtures/citizen_create.json), [`citizen_create_non_synthetic_rejected.json`](fixtures/citizen_create_non_synthetic_rejected.json), [`citizen_create_sensitive_label_rejected.json`](fixtures/citizen_create_sensitive_label_rejected.json) |
| GET | `/citizens/{citizen_id}` | Citizen record | 200 | 404, 422 | 404 `citizen_not_found`, 422 | [`citizen_get.json`](fixtures/citizen_get.json) |
| DELETE | `/citizens/{citizen_id}` | Hard delete (profile, documents, evaluations and enrollments cascade; an audit row without PII is kept) | 204 | 404, 422 | 404 `citizen_not_found` | — |
| GET | `/citizens/{citizen_id}/profile` | `{citizen_id, fields{name: {value, provenance, confidence, source_document_id, updated_at}}, missing_fields[]}` | 200 | 404, 422 | 404 `citizen_not_found` | [`profile_get.json`](fixtures/profile_get.json) |
| PATCH | `/citizens/{citizen_id}/profile` | Body `{fields: {name: value \| null}}` (≥ 1 key). Changed values become `self_declared`; `null` clears. Strict typing: NaN/±Infinity, `"1_000"`, fractional integers, booleans as numbers and non-string text are rejected | 200 | 404, 422 | 404 `citizen_not_found`, 422 `validation_error` (`details[].type` = `profile_field` or `sensitive_identifier`) | [`profile_patch.json`](fixtures/profile_patch.json), [`profile_patch_validation_error.json`](fixtures/profile_patch_validation_error.json) |
| GET | `/citizens/{citizen_id}/enrollments` | `[{scheme_id, status, provenance, updated_at}]` | 200 | 404, 422 | 404 `citizen_not_found` | [`enrollments_list.json`](fixtures/enrollments_list.json) |
| PUT | `/citizens/{citizen_id}/enrollments/{scheme_id}` | Body `{status: enrolled \| applied \| not_enrolled}` | 200 | 404, 422 | 404 `citizen_not_found` / `scheme_not_found`, 422 | [`enrollment_put.json`](fixtures/enrollment_put.json) |

### Documents (tag `documents`)

| Method | Path | Summary | Success | OpenAPI errors | Actual error codes | Fixture |
|---|---|---|---|---|---|---|
| POST | `/citizens/{citizen_id}/documents` | `multipart/form-data`: `file` (required), `document_type` (optional). Runs OCR and applies valid values with confidence ≥ 0.80 and no OCR note. Response `{document, duplicate, type_mismatch, applied_fields, review_required, conflicts, missing_fields, warnings}`. When the declared type contradicts the detected content, `type_mismatch: true` and nothing is applied | 201 new, 200 duplicate | 404, 422, 413, 415, 503 | 404 `citizen_not_found`; 413 `payload_too_large`; 415 `unsupported_media_type`; 422 `validation_error` / `corrupt_document` / `document_type_not_ocr_supported`; 503 `ocr_unavailable` (OCR or its image/PDF dependencies missing) | [`document_upload.json`](fixtures/document_upload.json), [`document_upload_type_mismatch.json`](fixtures/document_upload_type_mismatch.json), [`document_upload_duplicate.json`](fixtures/document_upload_duplicate.json), [`document_upload_unsupported.json`](fixtures/document_upload_unsupported.json) |
| GET | `/citizens/{citizen_id}/documents` | Document metadata list, newest first | 200 | 404, 422, 413, 415, 503 | 404 `citizen_not_found` | [`documents_list.json`](fixtures/documents_list.json) |
| GET | `/documents/{document_id}` | One document's metadata | 200 | 404, 422, 413, 415, 503 | 404 `document_not_found` | [`document_get.json`](fixtures/document_get.json) |
| DELETE | `/documents/{document_id}` | Delete the document: `{document_id, removed_profile_fields[], reverted_profile_fields[]}`. Values still sourced from it are removed; values the citizen had typed identically before the document confirmed them revert to `self_declared` | 200 | 404, 422, 413, 415, 503 | 404 `document_not_found` | [`document_delete.json`](fixtures/document_delete.json) |

For the three non-upload document routes, 413/415/503 appear in OpenAPI only because the error set is declared on the router; those routes never return them.

### Schemes (tag `schemes`)

| Method | Path | Summary | Success | OpenAPI errors | Actual error codes | Fixture |
|---|---|---|---|---|---|---|
| GET | `/schemes` | Effective schemes: `{scheme_id, name, level, state, department, version, effective_from, effective_to, verification_status, benefit, required_documents}` | 200 | 404, 422 | 503 `rule_engine_unavailable` | [`schemes_list.json`](fixtures/schemes_list.json) |
| GET | `/schemes/{scheme_id}` | Full rule: summary fields + `conditions` (condition/group tree), `sources`, `relationships`, `limitations`, `application_steps`, `rule_hash` | 200 | 404, 422 | 404 `scheme_not_found` | [`scheme_detail_mmvy.json`](fixtures/scheme_detail_mmvy.json), [`error_scheme_not_found.json`](fixtures/error_scheme_not_found.json) |

### Eligibility (tag `eligibility`)

| Method | Path | Summary | Success | OpenAPI errors | Actual error codes | Fixture |
|---|---|---|---|---|---|---|
| POST | `/citizens/{citizen_id}/evaluations` | Evaluate the stored profile against every effective scheme and persist one run. Body optional `{as_of?}` | 201 | 404, 422 | 404 `citizen_not_found`, 409 `rule_version_conflict`, 422, 503 `rule_engine_unavailable` | [`evaluation_run.json`](fixtures/evaluation_run.json) |
| GET | `/citizens/{citizen_id}/evaluations/latest` | Latest persisted run as stored, plus `stale` and `stale_reasons` (`profile_changed`, `rule_version_changed`) computed against the current profile and rules | 200 | 404, 422 | 404 `citizen_not_found` / `evaluation_not_found`; 503 `rule_engine_unavailable` | [`evaluation_latest.json`](fixtures/evaluation_latest.json), [`evaluation_latest_stale.json`](fixtures/evaluation_latest_stale.json) |
| GET | `/evaluations/{evaluation_id}` | One scheme evaluation + `run_id`, `citizen_id`, `evaluated_at`, `profile_fingerprint`, `rule_set_hash`, `disclaimer` | 200 | 404, 422 | 404 `evaluation_not_found` | [`evaluation_detail.json`](fixtures/evaluation_detail.json) |
| POST | `/eligibility/evaluate` | Stateless: `{profile: {field: {value, provenance?, confidence?}}, as_of?}`; nothing stored, ids `null`, always `simulation: true`. `provenance` may be `self_declared` (default) or `document_extracted` (listed in `client_asserted_provenance`); `confidence` must be a JSON number in [0, 1] | 200 | 404, 422 | 422 `provenance_not_allowed` (`verified`); 422 `validation_error` | [`eligibility_evaluate_stateless.json`](fixtures/eligibility_evaluate_stateless.json), [`eligibility_evaluate_simulation.json`](fixtures/eligibility_evaluate_simulation.json), [`eligibility_evaluate_verified_rejected.json`](fixtures/eligibility_evaluate_verified_rejected.json), [`eligibility_evaluate_invalid_confidence.json`](fixtures/eligibility_evaluate_invalid_confidence.json) |

Run shape: `{run_id, citizen_id, as_of, evaluated_at, summary{eligible, potentially_eligible, not_eligible}, eligible[], potentially_eligible[], not_eligible[], disclaimer, profile_fingerprint, rule_set_hash, stale, stale_reasons[], simulation, client_asserted_provenance[], provenance_note}`.
- `stale` and `stale_reasons` are only computed by `GET …/latest`.
- `simulation`, `client_asserted_provenance` and `provenance_note` are only set by the stateless endpoint.
- `profile_fingerprint` hashes every profile value, provenance and confidence, with no timestamps. `rule_set_hash` covers the effective rule versions and the engine's confidence threshold. Runs stored before fingerprints existed have `null` and count as stale.
- Each scheme evaluation is `{id, scheme_id, scheme_name, rule_version, rule_hash, verification_status, verification_warning, status, input_hash, as_of, notes[], conditions[]}`. `verification_warning` is `null` for verified rules; otherwise it is also the first `notes` entry.
- Each condition is `{condition_id, description, field, operator, expected, actual, provenance, outcome: pass|fail|missing, missing_reason: no_value|insufficient_trust|low_confidence|null, message, resolves_with[], children[]}`.

### Recommendations (tag `recommendations`)

| Method | Path | Summary | Success | OpenAPI errors | Actual error codes | Fixture |
|---|---|---|---|---|---|---|
| GET | `/citizens/{citizen_id}/recommendations?refresh=false` | Built from the latest persisted run while it matches the current profile and rules. Otherwise, if none exists, or with `refresh=true`, a new run is evaluated and persisted first (older runs are kept) | 200 | 404, 422 | 404 `citizen_not_found`, 409 `rule_version_conflict` (when it evaluates), 503 `rule_engine_unavailable` / `knowledge_graph_unavailable` | [`recommendations.json`](fixtures/recommendations.json) |

Keys:
- Scheme lists: `unclaimed[]`, `potentially_eligible[]`, `complementary[]`.
- Gaps: `document_gaps[]`, `information_needs[]`, `verification_needs[]`, `low_confidence_fields[]`.
- Totals: `unclaimed_totals`, `potential_totals`.
- Other: `enrolled_scheme_ids`, `warnings[]`, `notes[]`, `disclaimer`.
- Run context: `citizen_id`, `run_id`, `as_of`, `evaluated_at`, `evaluated_now`, `stale_reasons`, `profile_fingerprint`, `rule_set_hash`, `evaluations[{scheme_id, evaluation_id, rule_version, rule_hash, status}]`, `graph_source` (`neo4j` | `fallback`).
- `profile_changed_since_evaluation`: **deprecated, always `false`**.
- `TotalLine.category`: `recurring_annual_cash`, `recurring_monthly_cash`, `one_time_cash`, `fee_reimbursement`, `non_cash`. The categories `fee_reimbursement_monthly`, `cash_inconsistent_frequency` and `recurring_cash_unspecified_frequency` are listed in `not_summed`.

### Explanations (tag `explanations`)

| Method | Path | Summary | Success | OpenAPI errors | Actual error codes | Fixture |
|---|---|---|---|---|---|---|
| POST | `/citizens/{citizen_id}/schemes/{scheme_id}/explanation` | Body optional `{question? ≤500, language: en\|hi}`. Explains the scheme's evaluation in the latest persisted run while it is fresh; otherwise (or if none exists) a new run is evaluated and persisted first | 200 | 404, 422, 503 | 404 `citizen_not_found` / `scheme_not_found`; 422; 500 `internal_error` (decision guard); 503 `rag_unavailable` | [`explanation_citizen.json`](fixtures/explanation_citizen.json), [`explanation_provider_none.json`](fixtures/explanation_provider_none.json) |
| POST | `/explain` | `{scheme_id, profile, as_of?, question?, language}`. Evaluates one scheme and explains; nothing persisted; `simulation: true`; same provenance/confidence rules as `/eligibility/evaluate` | 200 | 404, 422, 503 | 404 `scheme_not_found`; 422 `provenance_not_allowed` / `validation_error`; 500; 503 `rag_unavailable` | [`explain_stateless.json`](fixtures/explain_stateless.json) |
| POST | `/schemes/{scheme_id}/ask` | `{question (1–500), language}`. General Q&A; `decision` is `null`; off-topic and injection attempts are refused | 200 | 404, 422, 503 | 404 `scheme_not_found`; 422; 503 `rag_unavailable` | [`scheme_ask.json`](fixtures/scheme_ask.json), [`scheme_ask_insufficient_evidence.json`](fixtures/scheme_ask_insufficient_evidence.json), [`scheme_ask_refused.json`](fixtures/scheme_ask_refused.json) |

Response:
- Explanation: `answer`, `decision`, `citations[{marker, chunk_id, doc_id, source_title, url, page, section, score, excerpt}]`, `grounded`, `provider` (`ollama` | `gemini` | `none`), `model`, `uncertainty`, `language`, `scheme_id`, `refused`, `flags[]`, `latency_ms`.
- Rule and run context: `rule_version`, `rule_hash`, `evaluation_id`, `run_id`, `evaluated_at`, `evaluated_now`, `stale_reasons`, `profile_fingerprint`, `rule_set_hash`, `simulation`, `client_asserted_provenance`, `provenance_note`, `disclaimer`.
- Grounding is checked per claim. Unsupported `[n]` markers are removed and missing ones added. `grounded` is `false` when no cited sentence is supported, a number is unsupported, or a statement matches neither the engine facts nor a passage.
- Grounding flags: `unsupported_citations_removed:<n,…>`, `citations_added:<n,…>`, `no_supported_citation`, `unsupported_numbers:<…>`, `unsupported_statements:<count>`. Other flags are listed in the integration guide §8.

Typical latency is 6–20 s with local Ollama. When no LLM is reachable, `provider` is `none` and the answer quotes official passages.

### Analytics (tag `analytics`), SYNTHETIC data

Every response carries `dataset_label` (`SYNTHETIC-MP-DEMO-v1`), `is_synthetic: true`, `generated_at` and `engine`. List endpoints wrap rows as `{…, count, items[]}`; villages use `{…, total, limit, offset, items[]}`. Counts are citizen × scheme assessments.

| Method | Path | Summary | Success | OpenAPI errors | Actual error codes | Fixture |
|---|---|---|---|---|---|---|
| GET | `/analytics/summary` | State totals and underserved counts | 200 | 404, 422, 503 | 503 `analytics_not_generated` | [`analytics_summary.json`](fixtures/analytics_summary.json) |
| GET | `/analytics/metadata` | Disclaimer, thresholds, metric definitions, rejected-row counts, geo attribution | 200 | 404, 422, 503 | 503 | [`analytics_metadata.json`](fixtures/analytics_metadata.json) |
| GET | `/analytics/districts` | Districts with `position`, coverage, `top_missing_documents`, `underserved_blocks` | 200 | 404, 422, 503 | 503 | [`analytics_districts.json`](fixtures/analytics_districts.json) |
| GET | `/analytics/districts/{district_id}` | `{district, blocks[], schemes[]}` | 200 | 404, 422, 503 | 404 `district_not_found`, 503 | [`analytics_district_detail.json`](fixtures/analytics_district_detail.json), [`error_district_not_found.json`](fixtures/error_district_not_found.json) |
| GET | `/analytics/blocks?district_id=&underserved=` | Blocks | 200 | 404, 422, 503 | 404 `district_not_found`, 422, 503 | [`analytics_blocks.json`](fixtures/analytics_blocks.json) |
| GET | `/analytics/villages?district_id=&block_id=&underserved=&limit=100&offset=0` | Villages, paginated (`limit` 1–1000) | 200 | 404, 422, 503 | 404 `district_not_found` / `block_not_found`, 422, 503 | [`analytics_villages_page.json`](fixtures/analytics_villages_page.json) |
| GET | `/analytics/schemes` | Per-scheme eligible vs enrolled | 200 | 404, 422, 503 | 503 | [`analytics_schemes.json`](fixtures/analytics_schemes.json) |

## Generic errors (any route)

| HTTP | Code | Fixture |
|---|---|---|
| 404 | `not_found` (unknown route) | — |
| 405 | `method_not_allowed` | — |
| 422 | `validation_error` (bad path/query/body) | [`error_request_validation.json`](fixtures/error_request_validation.json) |
| 413 | `payload_too_large` (Content-Length check) | — |
| 500 | `internal_error` | — |
