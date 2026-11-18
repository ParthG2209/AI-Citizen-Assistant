# Frontend Integration Guide (React prototype → FastAPI backend)

**Audience:** Parth, who owns `frontend/`. This guide explains how to replace the mock data (`src/data/mockData.js`, `src/lib/eligibility.js`) with calls to the real API. Nothing in `frontend/` has been changed by the backend work.

Every example response below comes from a running server. They are saved in [`docs/fixtures/`](fixtures/) (captured 2026-11-18 with a synthetic citizen that has since been deleted, so its ids will not resolve). Every `curl` command in this guide was run against that server. The full schema is in Swagger UI at `/docs` and in [`docs/openapi.json`](openapi.json). The endpoint table is in [`api_spec.md`](api_spec.md).

---

## 1. Connecting

| Item | Value |
|---|---|
| Base URL | `http://localhost:8000/api/v1` (every path below is relative to this) |
| Windows note | If port 8000 fails with `WinError 10013`, Hyper-V has reserved it. Start the API with `--port 8500` and use `http://localhost:8500/api/v1`. |
| Start the backend | [`deployment.md`](deployment.md). Short version: `docker compose up -d postgres neo4j`, `alembic upgrade head`, `scripts/seed_database.py`, then `.venv/Scripts/python.exe -m uvicorn app.main:app --app-dir backend --port 8000` |
| Swagger UI | `http://localhost:8000/docs` |
| Health | `GET /health` → `{"status":"ok"}`. `GET /ready` → 200 or 503 with per-dependency checks ([`ready.json`](fixtures/ready.json)) |

**Frontend environment.** Add `frontend/.env.local` (Vite reads it; don't commit it):

```bash
VITE_API_BASE_URL=http://localhost:8000/api/v1
```

**CORS.** The backend only accepts browser origins listed in `CORS_ORIGINS` in the repository-root `.env`. It is comma-separated and defaults to `http://localhost:5173`. If you use `vite preview` (port 4173) or another host, add it, e.g. `CORS_ORIGINS=http://localhost:5173,http://localhost:4173`, then restart the API. Allowed methods are `GET POST PUT PATCH DELETE OPTIONS`. Allowed request headers are `Content-Type` and `X-Request-ID`. `X-Request-ID` is exposed on every response. Credentials and cookies are not used (`allow_credentials=false`).

**Authentication.** There is none today. Every endpoint is open, which is only acceptable because all data is synthetic, and the backend enforces that:
- `POST /citizens` with `is_synthetic: false` is rejected with **403 `non_synthetic_not_allowed`** unless `ALLOW_NON_SYNTHETIC=true` ([`citizen_create_non_synthetic_rejected.json`](fixtures/citizen_create_non_synthetic_rejected.json)).
- The server refuses to start when `APP_ENV` is not `development`/`dev`/`test`/`testing`/`demo`/`local` unless `ALLOW_UNAUTHENTICATED=true`.
- A wildcard `*` in `CORS_ORIGINS` is always refused at startup.

When auth is added it will most likely be a bearer token in an `Authorization` header. The `request()` helper in §11 is the single place to add it. Plan for that header now, but don't depend on any auth flow.

**Conventions.**
- JSON field names are `snake_case`.
- Ids are UUID strings. Scheme ids are slugs (e.g. `mmvy`).
- Dates are `YYYY-MM-DD`. Timestamps are ISO 8601 UTC with a `Z` suffix.
- Money is a plain INR number (`416000.0`). Format it yourself; `formatINR()` from `eligibility.js` still works.
- **Confidence is a fraction in `[0, 1]`** (`0.9293`). The mock uses whole percentages (`96`), so display `Math.round(c * 100) + '%'`.

## 2. Errors

Every non-2xx response, including validation errors, unknown routes and 500s, has the same body:

```json
{ "error": { "code": "citizen_not_found", "message": "Citizen '00000000-0000-0000-0000-000000000000' not found", "details": null } }
```

Branch on `error.code`, which is stable. Show `error.message` to the user. `details` depends on the code: for `validation_error` and `provenance_not_allowed` it is a list of `{loc, msg, type}`, where `loc[2]` is the profile field name for PATCH profile ([`profile_patch_validation_error.json`](fixtures/profile_patch_validation_error.json), [`error_request_validation.json`](fixtures/error_request_validation.json)). Log the `X-Request-ID` response header with any error report; the backend logs the same id.

| HTTP | `error.code` | When |
|---|---|---|
| 403 | `non_synthetic_not_allowed` | `POST /citizens` with `is_synthetic: false` (synthetic-only deployment) |
| 404 | `citizen_not_found`, `document_not_found`, `evaluation_not_found`, `scheme_not_found`, `district_not_found`, `block_not_found` | Unknown id. `evaluation_not_found` also means "this citizen has no evaluation run yet" (`GET …/evaluations/latest`) |
| 404 | `not_found` | Unknown route |
| 405 | `method_not_allowed` | Wrong HTTP method |
| 409 | `rule_version_conflict` | Rare race: a rule version changed while an evaluation was being saved. Retry once |
| 413 | `payload_too_large` | Upload over `MAX_UPLOAD_MB` (default 10 MB; `details.max_bytes`), or the OCR page/pixel limits (5 pages, 40 MP per page) |
| 415 | `unsupported_media_type` | The file is not PNG, JPEG, WebP or PDF. This is decided from the file's bytes, not its name or MIME type ([`document_upload_unsupported.json`](fixtures/document_upload_unsupported.json)) |
| 422 | `validation_error` | Bad body, query or path value; unknown profile field; wrong enum value; unknown `document_type`. Also: a value out of range, NaN/Infinity, `"1_000"`, a fractional integer, a boolean as a number, or a non-string in a text field (`details[].type = "profile_field"`). Also: an identity-number-like value in a text field or citizen label (`details[].type = "sensitive_identifier"`, [`citizen_create_sensitive_label_rejected.json`](fixtures/citizen_create_sensitive_label_rejected.json)). Also: a stateless `confidence` that is not a JSON number in `[0, 1]` ([`eligibility_evaluate_invalid_confidence.json`](fixtures/eligibility_evaluate_invalid_confidence.json)) |
| 422 | `provenance_not_allowed` | A stateless call (`/eligibility/evaluate`, `/explain`) claimed `provenance: "verified"` ([`eligibility_evaluate_verified_rejected.json`](fixtures/eligibility_evaluate_verified_rejected.json)) |
| 422 | `corrupt_document` | Empty file, or an image/PDF that cannot be opened |
| 422 | `document_type_not_ocr_supported` | `document_type` is `land_record`, `ration_card` or `college_admission`. These cannot be read by OCR; ask the user to enter the values instead (`details.ocr_supported_types`) |
| 500 | `internal_error` | Unexpected error, including the explanation guard firing (the LLM's decision differed from the rule engine's). Show a generic error with the request id |
| 503 | `ocr_unavailable` | PaddleOCR or its image/PDF dependencies are not installed, or the models failed to load |
| 503 | `rag_unavailable` | The FAISS index or RAG packages are missing (`details.hint`: run `scripts/build_vector_index.py`) |
| 503 | `analytics_not_generated` | The analytics outputs have not been generated (`details.hint`: `scripts/run_district_analytics.py --engine pandas`) |
| 503 | `rule_engine_unavailable`, `knowledge_graph_unavailable` | Startup failed; `GET /ready` shows which part. `GET …/evaluations/latest` also needs the rule engine (to compute `stale`) |

## 3. Session flow (what replaces `CitizenDiscovery.jsx` state)

The mock keeps everything in React state and re-evaluates on every keystroke. With the API, the server holds the profile and the evaluations, and the page holds one `citizenId`.

```text
"Load sample" / first visit ─► POST /citizens {label, is_synthetic:true}           → citizenId (keep in state / sessionStorage)
Step 1 upload ─────────────► POST /citizens/{id}/documents (multipart)            → applied_fields / review_required / conflicts
Step 2 edits (debounced) ──► PATCH /citizens/{id}/profile {fields:{…}}             → full profile with provenance
after every profile change ► POST /citizens/{id}/evaluations                       → Step 3 tabs (new persisted run)
                             GET  /citizens/{id}/recommendations                   → Step 4 gaps (reuses that run while it is fresh)
Step 5 "Explain" ──────────► POST /citizens/{id}/schemes/{scheme_id}/explanation   → RAG drawer (6–20 s)
"Reset" ───────────────────► DELETE /citizens/{id} (204), then create a new one
```

- Evaluation is fast. Run it after each saved profile change, then fetch recommendations. Debounce text and number inputs (about 500 ms) so you don't send one PATCH per keystroke.
- For a "what-if" preview that saves nothing, use `POST /eligibility/evaluate` with a profile in the body (§6.3).
- **Freshness.** Every run stores a `profile_fingerprint` (hash of every profile value, provenance and confidence) and a `rule_set_hash` (the rule versions in effect).
  - `GET …/evaluations/latest` returns the stored run as-is, with `stale: true` and `stale_reasons` (`profile_changed`, `rule_version_changed`) when either hash no longer matches ([`evaluation_latest_stale.json`](fixtures/evaluation_latest_stale.json), captured after a PATCH). Show "results out of date" and re-run `POST /evaluations`.
  - Recommendations and citizen explanations never use a stale run. They evaluate and save a new run first and report it with `evaluated_now: true` and the old run's `stale_reasons`.
  - `profile_changed_since_evaluation` on recommendations is **deprecated and always `false`**; don't use it.
- `POST /citizens` with no body also works (label `null`, `is_synthetic: true`). The `label` must never contain a real name or id; ID-like numbers are rejected (422 `sensitive_identifier`).
- `scripts/seed_database.py` creates a few synthetic demo citizens (labels start with `Synthetic demo:`). There is no list endpoint, so create your own citizen per session.

## 4. Step 1: Document upload and OCR (`DocumentUpload.jsx`)

`POST /citizens/{citizen_id}/documents` takes `multipart/form-data`:

| Form field | Required | Notes |
|---|---|---|
| `file` | yes | PNG, JPEG, WebP or PDF (up to 5 pages). Max 10 MB (`MAX_UPLOAD_MB`). The type is sniffed from the bytes, so the browser's MIME type and the file name don't matter |
| `document_type` | no | One of the `document_types[].id` from `GET /profile-fields`. Omit it to let OCR classify the document. OCR-readable types are `income_certificate`, `caste_certificate`, `samagra_domicile` and `class12_marksheet` |

```bash
curl -s -F "file=@data/raw/sample_documents/synthetic_income_certificate_en_01.png" \
     -F "document_type=income_certificate" "$API/citizens/$CID/documents"
```

Response: **201** for a new document ([`document_upload.json`](fixtures/document_upload.json)). If the same file was already uploaded for this citizen, the response is **200** with `duplicate: true`; OCR is not re-run and nothing is applied ([`document_upload_duplicate.json`](fixtures/document_upload_duplicate.json)). In this capture the user had already typed `full_name: "Anita Yadav"` and `district: "Indore"`. Trimmed example:

```json
{
  "document": {
    "id": "2a959206-…", "document_type": "income_certificate", "content_type": "image/png",
    "status": "processed", "mean_confidence": 0.9101, "classification_confidence": 1.0, "page_count": 1,
    "extracted_fields": [
      {"name": "full_name", "value": "Anita Yadav", "confidence": 0.8513, "needs_review": false, "validation_errors": []},
      {"name": "annual_family_income", "value": 416000.0, "confidence": 0.9293, "needs_review": false, "validation_errors": []},
      {"name": "district", "value": "Barwani", "confidence": 0.9498, "needs_review": false, "validation_errors": []}
    ], "missing_fields": [], "warnings": []
  },
  "duplicate": false,
  "type_mismatch": false,
  "applied_fields": [
    {"field": "full_name", "value": "Anita Yadav", "confidence": 0.8513, "previous_provenance": "self_declared"},
    {"field": "annual_family_income", "value": 416000.0, "confidence": 0.9293, "previous_provenance": null}
  ],
  "review_required": [],
  "conflicts": [
    {"field": "district", "extracted_value": "Barwani", "confidence": 0.9498, "current_value": "Indore", "current_provenance": "self_declared"}
  ],
  "missing_fields": [], "warnings": []
}
```

**How to show the result.** The mock's `applyDocFields()` copied every OCR value into the profile. The backend makes that decision instead, and splits the outcome into three lists:

| List | Meaning | UI |
|---|---|---|
| `applied_fields` | Saved to the profile as `document_extracted`, with confidence and `source_document_id`. Only valid values with confidence **≥ 0.80** and no OCR note are applied (exactly 0.80 is applied). `previous_provenance: "self_declared"` means the document confirmed a value the user had typed | "OCR Extracted · 93%" badge. Refresh Step 2 from the response or with `GET /profile` |
| `review_required` | Not saved. Covers confidence below 0.80, an OCR note (e.g. the PDF text layer disagrees), a type mismatch (below), or failed validation. `value` is filled unless validation failed (then `null` with `validation_errors`) | Show the value next to the field with "Please confirm". Confirming sends `PATCH /profile`, which stores it as `self_declared` |
| `conflicts` | The certificate disagrees with a value the user typed (`self_declared`) or a `verified` value. Not applied | "Certificate says *Barwani*, you entered *Indore*." See below for the two choices |

Resolving a conflict:
- **"Keep mine":** do nothing.
- **"Use certificate":** `PATCH /profile` with `extracted_value`. It is stored as `self_declared`. If a rule needs document-level trust for that field (today only `social_category` for caste conditions), the value must come from the document itself: `DELETE /documents/{id}`, then upload the file again. The re-upload finds a matching `self_declared` value and upgrades it to `document_extracted`. This flow was re-tested on the current build.

**Declared type vs content (`type_mismatch`).** If you send a `document_type` and OCR confidently detects a different type:
- **nothing** is applied;
- `type_mismatch` is `true`;
- every extracted field is in `review_required`;
- `warnings` contains a sentence plus the code `declared_type_mismatch:<declared>!=<detected>` ([`document_upload_type_mismatch.json`](fixtures/document_upload_type_mismatch.json): the synthetic income certificate uploaded as `caste_certificate`, with real OCR).

Show "This looks like an *income certificate*, not a *caste certificate*", and let the user re-upload with the right type or without a type. A duplicate upload of that file keeps `type_mismatch: true`.

`document.missing_fields` lists fields the document type normally provides but OCR could not find. `warnings` holds OCR notes; show them in small print. The upload responses contain no raw OCR text and no file bytes, and the server keeps neither.

**Mapping the mock document ids:**

| mock `DOCUMENT_TYPES[].id` | API `document_type` | Fields provided |
|---|---|---|
| `income` | `income_certificate` | `full_name`, `annual_family_income`, `district` |
| `caste` | `caste_certificate` | `full_name`, `social_category`, `district` |
| `samagra` | `samagra_domicile` | `full_name`, `domicile_state`, `district`, `gender`, `age`, `has_samagra_id` (may also return `residence_type`) |
| `marksheet` | `class12_marksheet` | `full_name`, `class12_percentage`, `class12_board`, `class12_passing_year` |
| — | `land_record`, `ration_card`, `college_admission` | Not OCR-readable. Show them as "provide information" items |

The mock's per-document status (`queued` → `ocr` → `done`) becomes the request lifecycle (pending → resolved/failed). The other document endpoints:
- `GET /citizens/{id}/documents` lists documents newest first ([`documents_list.json`](fixtures/documents_list.json)).
- `GET /documents/{id}` returns one document ([`document_get.json`](fixtures/document_get.json)).
- `DELETE /documents/{id}` returns `{document_id, removed_profile_fields, reverted_profile_fields}` ([`document_delete.json`](fixtures/document_delete.json)):
  - Values still sourced from the document are removed (`removed_profile_fields`), like the mock's `removeDoc()`.
  - Values the user had typed identically before the document confirmed them are kept and revert to `self_declared` (`reverted_profile_fields`). In the fixture, `full_name` reverts and `annual_family_income` is removed.
  - Values the user corrected afterwards are untouched.

**Latency.** OCR runs on the CPU while the request is open.
- Measured on the dev machine (current build): first upload after server start **9.1 s** (models load once), later uploads **2.4–7 s**. Earlier runs measured 18.8 s for the first upload and 4.6–7.0 s per page. Expect about 3–7 s per page.
- Show a spinner per document and don't put a short timeout on this request (use at least 60 s).
- Send uploads one at a time. Parallel uploads only queue on the CPU.

## 5. Step 2: Profile with provenance (`ProfilePanel.jsx`, `Badge.jsx`)

- `GET /profile-fields` returns the field catalog ([`profile_fields.json`](fixtures/profile_fields.json)): `fields[] {name, type, label, choices, minimum, maximum, unit, sensitive}`, `document_types[] {id, label, ocr_supported, provides_fields}`, and `provenance_levels[] {id, trust}`. Build the form from it instead of `PROFILE_FIELDS`. Use `minimum`/`maximum` for client-side checks; e.g. `annual_family_income` has `maximum: 1000000000000`, a sanity cap, not a policy limit.
- `GET /citizens/{id}/profile` ([`profile_get.json`](fixtures/profile_get.json)) returns:

```json
{
  "citizen_id": "d47b42be-…",
  "fields": {
    "annual_family_income": {"value": 416000.0, "provenance": "document_extracted", "confidence": 0.9293,
                             "source_document_id": "9d9939d5-…", "updated_at": "2026-11-18T15:59:42.330875Z"},
    "district": {"value": "Indore", "provenance": "self_declared", "confidence": null, "source_document_id": null, "updated_at": "…"}
  },
  "missing_fields": ["family_owns_cultivable_land", "owns_pucca_house", "…"]
}
```

- `PATCH /citizens/{id}/profile` with `{"fields": {name: value | null}}` returns the full profile ([`profile_patch.json`](fixtures/profile_patch.json)). `null` clears a field. All values are validated together and nothing is saved if any is invalid (422, one `details` entry per bad field; [`profile_patch_validation_error.json`](fixtures/profile_patch_validation_error.json) shows `age: 18.5`, an unknown enum, `"1_000"`, `"nan"` and an unknown field all rejected).
- **Strict values.** Numbers must be finite JSON numbers or numeric strings. Integer fields accept `18`, `18.0` and `"18"`, but not `18.5`. Booleans are not numbers. Text and enum fields accept only strings. Send typed values from the form rather than relying on coercion.

```bash
curl -s -X PATCH "$API/citizens/$CID/profile" -H "Content-Type: application/json" \
     -d '{"fields":{"age":19,"gender":"female","domicile_state":"Madhya Pradesh","residence_type":"rural","occupation":"student"}}'
```

**Provenance badges** (mock `fields[key].source` → API):

| Mock `source` | API | Badge |
|---|---|---|
| `ocr` | `provenance: "document_extracted"` + `confidence` + `source_document_id` | OCR Extracted · NN% |
| `edited` (user changed an OCR value) | `self_declared`. A changed value loses its document link. Re-submitting the *same* value keeps `document_extracted` | User Edited. The API doesn't record that the value came from OCR; to keep this badge, remember the OCR value client-side from `applied_fields` |
| `user` | `self_declared` | Unverified · User Entered |
| `missing` | key absent from `fields`; listed in `missing_fields` | Missing |
| — | `verified` (trust 3; nothing sets it today) | Verified |

**Field names and values** (mock key → API `name`; send the API values, enums are lowercase):

| Mock key | API field | Type / values |
|---|---|---|
| `fullName` | `full_name` | string. Text containing a run of 9+ digits (spaces, hyphens or dots allowed between them) is rejected (`sensitive_identifier`) |
| `age` | `age` | integer, years (no fractions) |
| `gender` (Female/Male/Other) | `gender` | `female`, `male`, `transgender` |
| `district` | `district` | free string (not checked against a list; OCR returns one of the 55 MP district names) |
| `residence` (Rural/Urban) | `residence_type` | `rural`, `urban` |
| `domicile` (Madhya Pradesh/Other State) | `domicile_state` | free string; rules compare it to `Madhya Pradesh` ignoring case and extra spaces |
| `annualIncome` | `annual_family_income` | number, INR, 0 to 10¹² |
| `category` (General/OBC/SC/ST) | `social_category` | `general`, `ews`, `obc`, `sc`, `st` |
| `occupation` | `occupation` | `student`, `farmer`, `agricultural_labourer`, `daily_wage_worker`, `self_employed`, `private_salaried`, `government_employee`, `unemployed`, `homemaker`, `other` |
| `class12Percent` | `class12_percentage` | number, % |
| `board` (MP Board (MPBSE)/CBSE/Other) | `class12_board` | `mpbse`, `cbse`, `icse`, `other` |
| `familyLand` (Yes/No) | `family_owns_cultivable_land` | boolean (`true`/`false`; `"yes"`/`"no"` are accepted too) |
| `puccaHouse` (Yes/No) | `owns_pucca_house` | boolean |
| `secc` (Listed/Not listed) | `listed_in_secc_or_nfsa` | boolean |
| `existingBenefits` | — | not a profile field: use enrollments (below) |
| *(new)* | `enrolled_in_higher_education` | boolean. Most student schemes use this rather than `occupation` |
| *(new)* | `class12_passing_year` | integer. The laptop scheme needs 2026 |
| *(new)* | `family_member_income_tax_payer`, `family_member_government_employee`, `family_member_pensioner_above_10k` | booleans. PM-KISAN, MP Kisan Kalyan and PMAY-G exclusions; the mock used an income proxy |
| *(new)* | `has_samagra_id` | boolean. Never send the Samagra number itself |

**Existing benefits → enrollments.**
- Set one with `PUT /citizens/{id}/enrollments/{scheme_id}` and `{"status": "enrolled" | "applied" | "not_enrolled"}` ([`enrollment_put.json`](fixtures/enrollment_put.json)). List them with `GET /citizens/{id}/enrollments`.
- `enrolled` and `applied` count as already claimed in recommendations; the mock checked `claimKey ∈ existingBenefits` instead.
- Only the 9 API scheme ids are accepted. The mock's `Ladli Laxmi Yojana` and `PM Ujjwala Yojana` options have no rules and can't be recorded.

## 6. Step 3: Eligibility tabs (`EligibilityResults.jsx`, `SchemeCard.jsx`)

### 6.1 Evaluate and persist

```bash
curl -s -X POST "$API/citizens/$CID/evaluations" -H "Content-Type: application/json" -d '{}'
```

This returns **201** ([`evaluation_run.json`](fixtures/evaluation_run.json)). The body `{"as_of": "YYYY-MM-DD"}` is optional (it picks which rule versions apply; default is today).

`GET /citizens/{id}/evaluations/latest` returns the same shape, plus `stale`/`stale_reasons` computed against the current profile and rules:
- fresh: [`evaluation_latest.json`](fixtures/evaluation_latest.json);
- after a PATCH: [`evaluation_latest_stale.json`](fixtures/evaluation_latest_stale.json), with `stale: true` and `stale_reasons: ["profile_changed"]`.

`GET /evaluations/{evaluation_id}` returns a single scheme's evaluation plus `run_id`, `profile_fingerprint` and `rule_set_hash` ([`evaluation_detail.json`](fixtures/evaluation_detail.json)).

```json
{
  "run_id": "7c5dd601-…", "citizen_id": "d47b42be-…", "as_of": "2026-11-18", "evaluated_at": "2026-11-18T16:00:01.336216Z",
  "summary": {"eligible": 2, "potentially_eligible": 6, "not_eligible": 1},
  "eligible": [ /* SchemeEvaluation */ ], "potentially_eligible": [ … ], "not_eligible": [ … ],
  "disclaimer": "Indicative result from a local rule engine based on published scheme guidelines. It is not an official approval; the sanctioning authority makes the final decision.",
  "profile_fingerprint": "42f6b44e…", "rule_set_hash": "9c70c9b9…",
  "stale": false, "stale_reasons": [],
  "simulation": false, "client_asserted_provenance": [], "provenance_note": null
}
```

Each **SchemeEvaluation** is `{id, scheme_id, scheme_name, rule_version, rule_hash, verification_status, verification_warning, status, input_hash, as_of, notes[], conditions[]}`. Each condition is:

```json
{
  "condition_id": "category_sc_or_st", "description": "Student belongs to a Scheduled Caste or Scheduled Tribe (caste certificate)",
  "field": "social_category", "operator": "in", "expected": ["sc", "st"], "actual": "obc", "provenance": "self_declared",
  "outcome": "missing", "missing_reason": "insufficient_trust",
  "message": "Social category OBC is self-declared, but this condition needs a value that is at least read from an uploaded document. Provide: Caste Certificate.",
  "resolves_with": ["caste_certificate"], "children": []
}
```

### 6.2 Mapping from `evaluateScheme()` results

| Mock result | API |
|---|---|
| `id` (`mmvy`, `postMatricObc`, …) | `scheme_id` (table in §10) |
| `scheme.name` | `scheme_name` (official names; e.g. `Mukhyamantri Medhavi Vidyarthi Yojana (MMVY)`) |
| `status`: `eligible` / `potential` / `not_eligible` | `status`: `eligible` / **`potentially_eligible`** / `not_eligible`. Tabs map 1:1 to the three arrays and `summary` counts. Rename the mock's `potential` tab id |
| `satisfied[] {label}` | `conditions.filter(c => c.outcome === 'pass')` → label = `description` |
| `failed[] {label, detail}` | `outcome === 'fail'` → `description` + `message` (e.g. "Annual family income INR 4,16,000 exceeds the limit of INR 3,00,000") |
| `missing[] {label, detail, resolve, resolveDocId}` | `outcome === 'missing'` → `description` + `message`. `resolve` → label of `resolves_with[0]` (from `/profile-fields` `document_types`). `resolveDocId` → first entry of `resolves_with` whose type is `ocr_supported`, else `null` ("provide" instead of "upload") |
| *(new)* | `missing_reason`: `no_value` (not provided), `insufficient_trust` (typed in, but a certificate is required), `low_confidence` (OCR value below 0.80) |
| `claimed` | scheme id in `GET /enrollments` with `enrolled`/`applied`, or in `recommendations.enrolled_scheme_ids` |
| `total` | `conditions.length` (top level) |
| *(new)* | `verification_status`: `verified` / `partially_verified` / `unverified`. When not `verified`, `verification_warning` holds a ready-to-show text, e.g. "Partially verified rule: not every condition is confirmed by a primary official document; confirm with the department before relying on this result. …", followed by the scheme's specific evidence gaps. The same text is also the first entry of `notes[]`. Show it next to the status (6 of the 9 schemes are partially verified, including `post_matric_scholarship_obc` 2018.2) |
| *(new)* | `disclaimer`: show it under the tabs. It is never an official approval |

**Groups.** Some conditions are "any of" / "all of" groups: `operator` is `any` or `all`, `field` lists the fields joined by commas, and `children[]` holds the sub-conditions in the same shape. Examples are the MMVY merit routes (MPBSE ≥ 70% **or** CBSE/ICSE ≥ 85%) and PM-JAY (SECC/NFSA listed **or** age ≥ 70). Show the group's `message` and let the user expand `children`. The group's `outcome` is already computed, so don't re-derive it.

### 6.3 Stateless preview (nothing stored)

`POST /eligibility/evaluate` takes `{"profile": {field: {"value", "provenance"?, "confidence"?}}, "as_of"?}`. It returns **200** with the same shape, and `run_id`, `citizen_id` and each `id` are `null`. Every response is marked **`simulation: true`** with a `provenance_note` ([`eligibility_evaluate_stateless.json`](fixtures/eligibility_evaluate_stateless.json)).

Trust rules for caller-supplied provenance:
- `provenance` defaults to `self_declared`.
- `document_extracted` is accepted for what-if checks, but nothing backs it. Those fields are listed in `client_asserted_provenance` ([`eligibility_evaluate_simulation.json`](fixtures/eligibility_evaluate_simulation.json)); label such results "simulation", never "document-backed".
- `verified` is rejected with **422 `provenance_not_allowed`** ([`eligibility_evaluate_verified_rejected.json`](fixtures/eligibility_evaluate_verified_rejected.json)).
- `confidence` must be a JSON number in `[0, 1]`. `true`, `"0.9"`, NaN, Infinity and `1.5` give 422 `validation_error` ([`eligibility_evaluate_invalid_confidence.json`](fixtures/eligibility_evaluate_invalid_confidence.json)).

```bash
curl -s -X POST "$API/eligibility/evaluate" -H "Content-Type: application/json" \
     -d '{"profile":{"domicile_state":{"value":"Madhya Pradesh"},"annual_family_income":{"value":180000,"provenance":"document_extracted","confidence":0.94}}}'
```

## 7. Step 4: Knowledge graph and benefit gaps (`BenefitGaps.jsx`)

```bash
curl -s "$API/citizens/$CID/recommendations"          # add ?refresh=true to force a new evaluation
```

This builds on the latest saved run while it is fresh. If there is none, or it is stale, it evaluates and saves a new run first (older runs are kept). [`recommendations.json`](fixtures/recommendations.json) was captured right after a PATCH, so it shows `evaluated_now: true` and `stale_reasons: ["profile_changed"]`. Top-level keys:

| Key | Contents |
|---|---|
| `unclaimed[]` | Eligible schemes not marked enrolled/applied, ranked. Each item has `{scheme_id, scheme_name, status, verification_status, benefit, rank, rank_reasons[], next_actions[], application_steps[], official_url, warnings[]}` |
| `potentially_eligible[]` | Same shape, for schemes waiting on information. `next_actions[]` say what to do |
| `complementary[]` | Schemes suggested by graph edges from eligible or enrolled schemes: `{scheme_id, scheme_name, presentation, target_status, via[{anchor_scheme, relation, reason, source_url, text}], missing_conditions[], failed_conditions[], benefit, next_actions[], rank, rank_reasons[], official_url, warnings[]}` |
| `document_gaps[]` | Per missing document: `{doc_id, label, schemes[{scheme_id, scheme_name, condition_ids, fields, fully_unlocks}]}` |
| `information_needs[]`, `verification_needs[]`, `low_confidence_fields[]` | Per field: `{kind, field, label, scheme_ids, condition_ids, documents, required_trust, provenance, value}` |
| `unclaimed_totals`, `potential_totals` | Money totals (below) |
| `enrolled_scheme_ids`, `warnings[]`, `notes[]`, `disclaimer` | |
| `citizen_id`, `run_id`, `as_of`, `evaluated_at`, `evaluated_now`, `stale_reasons`, `profile_fingerprint`, `rule_set_hash` | Which run this is based on |
| `evaluations[]` | `{scheme_id, evaluation_id, rule_version, rule_hash, status}` for every scheme in that run |
| `profile_changed_since_evaluation` | **Deprecated, always `false`**: stale runs are re-evaluated automatically |
| `graph_source` | `neo4j`, or `fallback` (in-memory copy of the same graph; same results) |

`next_actions[].kind` is one of:
- `apply`: has a `url`.
- `upload_document`: has a `document_id`, an OCR-readable type, so show an upload button.
- `provide_information`: has a `field`, so show a form input.
- `obtain_verification`: the user must get an official document or verification.
- `recheck_field`: an OCR value had low confidence; ask the user to confirm it.

Each action has a ready-to-show `text`.

**Mapping from `detectGaps()`:**

| Mock | API |
|---|---|
| `complementary[] {from, to, fromName, benefit, annualValue, relation, reason}` | `complementary[]`. `to` → `scheme_name`; `from`/`fromName` → `via[].anchor_scheme` (look up its name); `relation`/`reason` → `via[].relation` / `via[].reason`. `presentation` is `available` (the scheme's own evaluation is eligible) or `check_eligibility`, which you must label "check eligibility", not "you qualify" |
| `blocked[] {schemeId, schemeName, resolve, resolveDocId, benefitShort, annualValue}` | Document blocks → `document_gaps[]` (`doc_id` = `resolveDocId`, `label` = `resolve`, one row per scheme in `schemes[]`; `fully_unlocks: true` means the document alone settles every missing item for that scheme). Non-document blocks → `information_needs[]` |
| `unclaimed[]` | `unclaimed[]` |
| `unclaimedValue` (one ₹/yr number) | `unclaimed_totals` (below). There is no single number |

**Totals semantics** (`unclaimed_totals` / `potential_totals`):

```json
{
  "recurring_annual_cash_inr": 0.0, "recurring_monthly_cash_inr": 0.0, "one_time_cash_inr": 25000.0, "fee_reimbursement_inr": 0.0,
  "non_cash": [], "amount_not_published": ["mmvy"], "not_summed": [], "excluded_mutually_exclusive": [],
  "counted_scheme_ids": ["laptop_protsahan", "mmvy"],
  "lines": [{"scheme_id": "laptop_protsahan", "category": "one_time_cash", "amount_inr": 25000.0, "frequency": "one_time", "description": "…"},
            {"scheme_id": "mmvy", "category": "fee_reimbursement", "amount_inr": null, "frequency": "annual", "description": "…"}]
}
```

- Each scheme is counted once (`counted_scheme_ids`). A scheme that appears in both `unclaimed` and `complementary` (e.g. `laptop_protsahan` above) is **not** added twice. Do not add `complementary` amounts on top, as the mock did.
- Enrolled/applied schemes never count towards unclaimed.
- Keep the categories separate: ₹/year, ₹/month, one-time ₹ and fee reimbursement. Monthly amounts are **not** multiplied to a year, because payment months differ. A label like "₹25,000 one-time + ₹5,000/yr" is correct; "₹30,000/yr" is not.
- `fee_reimbursement_inr` sums annual/one-time fee reimbursements only. A monthly fee reimbursement is put in `not_summed` with `category: "fee_reimbursement_monthly"`. A cash benefit whose type and frequency disagree (e.g. a one-time benefit declared annual) goes to `not_summed` with `category: "cash_inconsistent_frequency"`. List `not_summed` lines individually.
- `non_cash[]` (housing, insurance cover, in-kind; e.g. PMAY-G ₹1.2 lakh, PM-JAY ₹5 lakh cover) is listed, never summed. Show it as "plus health cover up to ₹5 lakh/yr".
- `amount_not_published[]` lists scheme ids whose official amount varies (e.g. MMVY fee reimbursement, hostel assistance). Show "amount varies".
- Of two mutually exclusive schemes, only the higher-ranked is counted; the other is in `excluded_mutually_exclusive`. The current rules have no such pairs.

**Graph edges in the current rules** (they replace `KG_COMPLEMENTS`; all come from the rule YAMLs with an official source URL):
- `gaon_ki_beti COMPLEMENTED_BY mmvy`
- `gaon_ki_beti COMPLEMENTED_BY laptop_protsahan`
- `mp_kisan_kalyan TOPS_UP pm_kisan`
- `mmvy TOPS_UP post_matric_scholarship_obc`

The mock's MMVY → hostel and MMVY → laptop edges are not in the official sources, so they are absent.

## 8. Step 5: RAG drawer (`RagDrawer.jsx`)

```bash
curl -s -X POST "$API/citizens/$CID/schemes/hostel_assistance/explanation" -H "Content-Type: application/json" \
     -d '{"language":"en"}'           # optional "question": "…" (≤ 500 chars); language "en" | "hi"
```

This explains the scheme's evaluation in the citizen's latest saved run if that run is fresh. Otherwise it evaluates and saves a new run first (`evaluated_now`, `stale_reasons`). Fixture: [`explanation_citizen.json`](fixtures/explanation_citizen.json), real Ollama `qwen2.5:7b-instruct`, 15.0 s:

```json
{
  "answer": "The rule engine decided the applicant is potentially eligible for Awas Sahayata Yojana (rent assistance for SC/ST college students) based on the following conditions:\n\n1. The student is a resident of Madhya Pradesh, which is a requirement [2][3]. …",
  "decision": "potentially_eligible", "grounded": true, "provider": "ollama", "model": "qwen2.5:7b-instruct",
  "uncertainty": "The rule definition for this scheme is not fully verified against official sources; treat the decision as indicative.",
  "language": "en", "scheme_id": "hostel_assistance", "refused": false, "flags": ["rule_partially_verified"], "latency_ms": 14949,
  "citations": [{"marker": 1, "chunk_id": "98d196304416f05323f7", "doc_id": "cmhelpline_awas_sahayata_tribal",
                 "source_title": "Awas Sahayata (Tribal Affairs) - CM Helpline scheme details",
                 "url": "https://cmhelpline.mp.gov.in/KnowYourEntitleDetail.aspx?…", "page": 1, "section": null, "score": 0.5739, "excerpt": "। लाभार्थी वर्ग अनुसूचित जनजाति …"}],
  "rule_version": "2016.1", "rule_hash": "ea33…", "evaluation_id": "633c83e5-…", "run_id": "c32f9d27-…",
  "evaluated_at": "2026-11-18T16:00:01.851128Z", "evaluated_now": false, "stale_reasons": [],
  "profile_fingerprint": "ec146d8e…", "rule_set_hash": "9c70c9b9…",
  "simulation": false, "client_asserted_provenance": [], "provenance_note": null, "disclaimer": "Indicative result …"
}
```

**Showing it in the drawer:**
- **Verdict badge.** Use `decision` (or the Step 3 status, which is the same value). The backend fails with 500 if the LLM's decision ever differs from the engine's. The LLM never decides.
- **Condition summary.** Take it from the evaluation conditions (§6), not from `answer`. The 7B model can misstate details that still pass the grounding check. In the current fixture it says the student's Samagra ID was "verified from the Samagra portal", but `has_samagra_id` is a self-declared value. An earlier capture said "belongs to SC/ST" for a self-declared OBC student. Present `answer` as an AI summary under the engine's condition list.
- **Citations.** `answer` contains `[n]` markers matching `citations[].marker`. Render them as superscript links to `url`, and list the sources with `source_title`, `page`, `section` and `excerpt`. Excerpts are often Hindi. This replaces the mock's `rag.sources`.
  - Before the response is returned, each cited sentence is checked against the passage it cites. Markers pointing to a passage that does not support the sentence are removed (flag `unsupported_citations_removed:<n,…>`), and a supporting passage the model forgot to cite is added (`citations_added:<n,…>`).
  - Markers matching no retrieved passage are removed too (`invalid_citations_removed:<n,…>`).
- **`grounded: false`.** Show `uncertainty` prominently, e.g. "The official documents found don't clearly answer this", and treat the answer as unconfirmed ([`scheme_ask_insufficient_evidence.json`](fixtures/scheme_ask_insufficient_evidence.json)). It is set when:
  - the model reports insufficient evidence;
  - no cited sentence is supported (`no_supported_citation`);
  - a number appears in no cited passage, engine fact or the question (`unsupported_numbers:<…>`);
  - claims match neither the engine facts nor a passage (`unsupported_statements:<count>`).

  The answer text is kept so the user can see what was said.
- **`uncertainty`** can also be set when `grounded` is true, e.g. for partially verified rules or when unsupported markers were removed ([`explain_stateless.json`](fixtures/explain_stateless.json)). Show it as a note.
- **`provider: "none"`.** No LLM was reachable (or `RAG_LLM_DISABLED=1`). `answer` is then a plain-text condition summary followed by verbatim official passages; label it "Quoted from official documents (no AI summary)" ([`explanation_provider_none.json`](fixtures/explanation_provider_none.json), flags `llm_unavailable`). `provider: "gemini"` is possible when `GEMINI_API_KEY` is set.
- **`refused: true`.** The question was off-topic or a prompt-injection attempt. Show `answer` (a short refusal) with no citations ([`scheme_ask_refused.json`](fixtures/scheme_ask_refused.json)).
- **`flags`** is informational. Possible values: `rule_partially_verified`, `rule_unverified`, `llm_unavailable`, `insufficient_evidence`, `no_evidence`, `uncited_answer`, `no_supported_citation`, `unsupported_numbers:<…>`, `unsupported_statements:<count>`, `unsupported_citations_removed:<n,…>`, `citations_added:<n,…>`, `invalid_citations_removed:<n,…>`, `llm_answer_contradicted_decision` (the answer was replaced by quoted passages), `prompt_injection_ignored`, `prompt_injection_detected`, `refused_off_topic`, `refused`.
- **Known limit.** The grounding check catches citations of unrelated passages, invented numbers and statements that contradict the decision. It does **not** catch invented but on-topic sentences (e.g. "you must submit a caste certificate" when no passage says so); those can still come back `grounded: true`.
- **Documents checklist and steps.** Use `GET /schemes/{scheme_id}` ([`scheme_detail_mmvy.json`](fixtures/scheme_detail_mmvy.json)): `required_documents[]` (doc ids; labels from `/profile-fields`), `application_steps[]`, `sources[] {title, url, publisher, accessed_on, section}` and `limitations[]`. Recommendation items also include `application_steps` and `official_url`.

**Latency and loading state.**
- Measured on the current build: 15.0 s (citizen explanation), 5.4 s (`/explain`), 3.4–4.4 s (`/ask`) on an RTX 4060 with a warm model. The extractive fallback (`provider: "none"`) took 8.9 s, including embedding-model load. Expect **6–20 s** for LLM answers; a CPU-only 3B model is slower.
- The first explanation after server start also loads the embedding model.
- Open the drawer immediately with a skeleton and "Generating an explanation from official documents…". Use an `AbortController` timeout of about **150 s**: the server waits up to `OLLAMA_TIMEOUT_S` = 120 s before falling back.
- Cancel the request when the drawer closes. Don't fire explanations for every scheme in advance.

**Other RAG endpoints:**
- `POST /explain` with `{scheme_id, profile, as_of?, question?, language}` evaluates one scheme for a supplied profile and explains it, saving nothing. `evaluation_id`/`run_id` are `null` and `simulation: true`. The profile follows the §6.3 rules: `verified` is rejected with 422 `provenance_not_allowed`, and `document_extracted` fields are listed in `client_asserted_provenance` ([`explain_stateless.json`](fixtures/explain_stateless.json)).
- `POST /schemes/{scheme_id}/ask` with `{question, language}` is general Q&A about a scheme with `decision: null`. This is the drawer's Q&A box ([`scheme_ask.json`](fixtures/scheme_ask.json)).

```bash
curl -s -X POST "$API/schemes/pmay_gramin/ask" -H "Content-Type: application/json" \
     -d '{"question":"What is the exclusion criteria for PMAY-G?","language":"en"}'
```

## 9. Admin analytics (`AdminAnalytics.jsx`, `DistrictMap.jsx`, `KpiCards.jsx`, `SchemeAdoption.jsx`)

**All analytics data is SYNTHETIC.** Every response has `is_synthetic: true` and `dataset_label: "SYNTHETIC-MP-DEMO-v1"`, and `GET /analytics/metadata` has a `disclaimer`. The UI **must** show a visible banner such as "Synthetic demonstration data — not government statistics" on this page. Never present these numbers as real coverage.

| Endpoint | Replaces | Fixture |
|---|---|---|
| `GET /analytics/summary` | `KpiCards` sums | [`analytics_summary.json`](fixtures/analytics_summary.json) |
| `GET /analytics/districts` | `DISTRICTS` | [`analytics_districts.json`](fixtures/analytics_districts.json) |
| `GET /analytics/districts/{id}` | selected-district panel (+ blocks, per-scheme rows) | [`analytics_district_detail.json`](fixtures/analytics_district_detail.json) |
| `GET /analytics/blocks?district_id=&underserved=` | — | [`analytics_blocks.json`](fixtures/analytics_blocks.json) |
| `GET /analytics/villages?district_id=&block_id=&underserved=&limit=&offset=` | — (`limit` 1–1000, default 100; returns `total`) | [`analytics_villages_page.json`](fixtures/analytics_villages_page.json) |
| `GET /analytics/schemes` | `SCHEME_ADOPTION` | [`analytics_schemes.json`](fixtures/analytics_schemes.json) |
| `GET /analytics/metadata` | — (thresholds, metric definitions, disclaimer, geo attribution) | [`analytics_metadata.json`](fixtures/analytics_metadata.json) |

```bash
curl -s "$API/analytics/districts/chhindwara"
curl -s "$API/analytics/villages?district_id=chhindwara&underserved=true&limit=3&offset=0"
```

**District mapping** (mock `DISTRICTS[]` → `districts.items[]`):

| Mock | API | Note |
|---|---|---|
| `id`, `name` | `id`, `name` | 12 synthetic-dataset districts (mock: 6). Name `Narmadapuram`, not `Narmadapuram (Hoshangabad)` |
| `position` | `position` `[lat, lng]` | **Polygon centroids**, not city coordinates (Bhopal `[23.478, 77.3961]` vs mock `[23.2599, 77.4126]`) |
| `eligible`, `enrolled` | `eligible`, `enrolled` | **citizen × scheme assessments**, not unique people. Label them "eligible assessments" |
| `eligible - enrolled` | `gap` | |
| `Math.round(enrolled/eligible*100)` | `coverage_pct` | 1 decimal; `null` with `coverage_flag: "no_eligible_population"` when eligible = 0. Don't divide yourself |
| `topMissingDocs: string[]` | `top_missing_documents[] {document_type, label, citizens}` | use `.label`; `citizens` = distinct eligible/potential, not-enrolled citizens lacking it |
| `underservedBlocks: string[]` | `underserved_blocks[] {id, name, coverage_pct, gap}` | use `.name`. Names are generic: "Block 03 (synthetic)" |
| KpiCards "coverage below 55%" | `underserved` (bool) and `summary.underserved.districts` | the server rule is coverage < 50% **and** gap ≥ 500 (block ≥ 100, village ≥ 20); see `metadata.thresholds` |
| — | `assessed`, `potentially_eligible`, `blocks`, `villages`, `is_synthetic` | |

`SCHEME_ADOPTION[] {name, eligible, enrolled}` → `/analytics/schemes` `items[] {scheme_id, name, assessed, eligible, enrolled, gap, potentially_eligible, coverage_pct, coverage_flag, top_missing_documents, is_synthetic}`. The district detail's `schemes[]` uses `scheme_name` instead of `name`.

## 10. Contract differences vs the mock (read before wiring)

| Topic | Mock | Real API |
|---|---|---|
| Scheme ids | `mmvy`, `postMatricObc`, `pmKisan`, `pmjay`, `gaonKiBeti`, `pmayG` (+ KG names "MP Hostel Assistance Scheme", "MP Laptop Protsahan Yojana", "Mukhyamantri Kisan Kalyan Yojana") | `mmvy`, `post_matric_scholarship_obc`, `pm_kisan`, `pm_jay`, `gaon_ki_beti`, `pmay_gramin`, `hostel_assistance`, `laptop_protsahan`, `mp_kisan_kalyan` (`GET /schemes`, [`schemes_list.json`](fixtures/schemes_list.json)) |
| Status ids | `potential` | `potentially_eligible` |
| MMVY income | ≤ ₹6,00,000 | **< ₹8,00,000** (2026 consolidated order). Also needs `has_samagra_id` and `enrolled_in_higher_education`; not `occupation` |
| MMVY merit | MPBSE ≥ 70 / CBSE ≥ 85 | MPBSE ≥ 70 **or** CBSE/ICSE ≥ 85 (group condition) |
| Hostel assistance | complement of MMVY for everyone | `hostel_assistance` (Awas Sahayata) is for **SC/ST** students only, with a caste certificate, income ≤ ₹6 lakh and a Samagra ID. No graph edge from MMVY |
| Post-Matric OBC | `level: Central` | MP scheme (`post_matric_scholarship_obc` version **2018.2**, partially verified: the ₹3 lakh ceiling rests on portal pages, not a located order). OBC **from a certificate** (`document_extracted`), income ≤ ₹3 lakh, college enrolment or student, Samagra ID |
| PM-KISAN | income ≤ ₹7 lakh as an income-tax proxy | explicit booleans: land owned, no income-tax payer, no government employee in family or as applicant, no pensioner ≥ ₹10k/month |
| PM-JAY | SECC listed + MP domicile | SECC/NFSA listed **or** age ≥ 70 |
| PMAY-G | income ≤ ₹1.8 lakh + SECC | rural, no pucca house, no government employee, no income-tax payer |
| Gaon Ki Beti | `occupation === Student` | `enrolled_in_higher_education`, female, rural, Class 12 ≥ 60%, MP domicile |
| Laptop scheme | KG requirement only | its own rule: MPBSE, ≥ 75%, `class12_passing_year` 2026 (version 2026.1) |
| Benefit amounts | `annualValue` per scheme, summed | `benefit {type, description, amount_inr \| null, frequency, is_cash}`, never invented. Totals as in §7 |
| Confidence | 0–100 | 0–1 |
| Field names | camelCase, display-cased enum values | snake_case, lowercase enum values (§5) |
| District coverage | client-computed | `coverage_pct` from the server; synthetic |
| RAG text | static `rag.summary`, `rag.qa`, `rag.sources` | generated per request with real citations; can be ungrounded or refused |

Rule evidence (official sources, verification status, known limitations per scheme) is documented in [`eligibility_rules.md`](eligibility_rules.md). Provenance rules are in [`data_provenance.md`](data_provenance.md).

## 11. Ready-to-paste fetch helpers (`src/lib/api.js`)

Plain `fetch`, no extra dependency. These were run against the live server (Bun runtime) for create → patch → upload → evaluate → recommendations → explanation → error → delete.

```js
// src/lib/api.js
const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000/api/v1').replace(/\/$/, '');

export class ApiError extends Error {
  constructor(status, error, requestId) {
    super(error?.message ?? `HTTP ${status}`);
    this.name = 'ApiError';
    this.status = status;                       // HTTP status (0 = network error / timeout)
    this.code = error?.code ?? 'http_error';    // stable machine code, see the error table
    this.details = error?.details ?? null;
    this.requestId = requestId ?? null;         // X-Request-ID, include in bug reports
  }
}

async function request(path, { method = 'GET', json, form, timeoutMs = 30000, signal } = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  const onAbort = () => controller.abort();
  signal?.addEventListener('abort', onAbort, { once: true });
  const headers = {};
  let body;
  if (json !== undefined) {
    headers['Content-Type'] = 'application/json';
    body = JSON.stringify(json);
  } else if (form !== undefined) {
    body = form; // FormData: let the browser set the multipart boundary, do NOT set Content-Type
  }
  let res;
  try {
    res = await fetch(`${API_BASE}${path}`, { method, headers, body, signal: controller.signal });
  } catch (err) {
    const timedOut = controller.signal.aborted && !signal?.aborted;
    throw new ApiError(0, {
      code: timedOut ? 'timeout' : err.name === 'AbortError' ? 'aborted' : 'network_error',
      message: timedOut ? 'The server took too long to respond' : 'Could not reach the server',
    });
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener('abort', onAbort);
  }
  if (res.status === 204) return null;
  const data = await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(res.status, data?.error, res.headers.get('X-Request-ID'));
  return data;
}

const enc = encodeURIComponent;

export const api = {
  // health
  ready: () => request('/ready'),
  profileFields: () => request('/profile-fields'),

  // citizens & profile
  createCitizen: (label = null) => request('/citizens', { method: 'POST', json: { label, is_synthetic: true } }),
  deleteCitizen: (id) => request(`/citizens/${id}`, { method: 'DELETE' }),
  getProfile: (id) => request(`/citizens/${id}/profile`),
  patchProfile: (id, fields) => request(`/citizens/${id}/profile`, { method: 'PATCH', json: { fields } }),
  listEnrollments: (id) => request(`/citizens/${id}/enrollments`),
  setEnrollment: (id, schemeId, status) =>
    request(`/citizens/${id}/enrollments/${enc(schemeId)}`, { method: 'PUT', json: { status } }),

  // documents (OCR: 3-7 s per page, ~20 s on the first call after server start)
  uploadDocument: (id, file, documentType) => {
    const form = new FormData();
    form.append('file', file, file.name);
    if (documentType) form.append('document_type', documentType);
    return request(`/citizens/${id}/documents`, { method: 'POST', form, timeoutMs: 120000 });
  },
  listDocuments: (id) => request(`/citizens/${id}/documents`),
  deleteDocument: (documentId) => request(`/documents/${documentId}`, { method: 'DELETE' }),

  // schemes & eligibility
  listSchemes: () => request('/schemes'),
  getScheme: (schemeId) => request(`/schemes/${enc(schemeId)}`),
  evaluate: (id, asOf) => request(`/citizens/${id}/evaluations`, { method: 'POST', json: asOf ? { as_of: asOf } : {} }),
  latestEvaluation: (id) => request(`/citizens/${id}/evaluations/latest`),
  evaluateStateless: (profile, asOf) =>
    request('/eligibility/evaluate', { method: 'POST', json: asOf ? { profile, as_of: asOf } : { profile } }),
  recommendations: (id, { refresh = false } = {}) =>
    request(`/citizens/${id}/recommendations${refresh ? '?refresh=true' : ''}`),

  // explanations (6-20 s typical; pass an AbortSignal so closing the drawer cancels)
  explain: (id, schemeId, { question, language = 'en', signal } = {}) =>
    request(`/citizens/${id}/schemes/${enc(schemeId)}/explanation`, {
      method: 'POST', json: question ? { question, language } : { language }, timeoutMs: 150000, signal,
    }),
  ask: (schemeId, question, { language = 'en', signal } = {}) =>
    request(`/schemes/${enc(schemeId)}/ask`, { method: 'POST', json: { question, language }, timeoutMs: 150000, signal }),

  // analytics (SYNTHETIC data, show the banner)
  analyticsSummary: () => request('/analytics/summary'),
  analyticsMetadata: () => request('/analytics/metadata'),
  districts: () => request('/analytics/districts'),
  district: (districtId) => request(`/analytics/districts/${enc(districtId)}`),
  villages: (params = {}) => request(`/analytics/villages?${new URLSearchParams(params)}`),
  schemeAdoption: () => request('/analytics/schemes'),
};
```

Example use in a component:

```js
import { api, ApiError } from '../lib/api.js';

// Step 1: upload
try {
  const r = await api.uploadDocument(citizenId, file);          // let OCR classify
  if (r.duplicate) notify('This file was already uploaded.');
  if (r.type_mismatch) notify('This document does not look like the type you selected. Please check the values below.');
  setProfile(await api.getProfile(citizenId));
  setReview(r.review_required);
  setConflicts(r.conflicts);
} catch (e) {
  if (e instanceof ApiError && e.code === 'unsupported_media_type') notify('Upload a PNG, JPEG, WebP or PDF.');
  else if (e instanceof ApiError && e.code === 'payload_too_large') notify('File is larger than 10 MB.');
  else notify(e.message);
}

// Steps 3 + 4 after a profile change
const run = await api.evaluate(citizenId);
const recs = await api.recommendations(citizenId);

// Step 5: drawer
const ctrl = new AbortController();               // call ctrl.abort() when the drawer closes
const exp = await api.explain(citizenId, 'mmvy', { signal: ctrl.signal });
```

Status-to-tab and provenance-to-badge helpers:

```js
export const STATUS_TABS = ['eligible', 'potentially_eligible', 'not_eligible'];
export const badgeFor = (f) =>
  !f ? { text: 'Missing', tone: 'red' }
  : f.provenance === 'document_extracted' ? { text: `OCR Extracted · ${Math.round(f.confidence * 100)}%`, tone: 'blue' }
  : f.provenance === 'verified' ? { text: 'Verified', tone: 'green' }
  : { text: 'User Entered', tone: 'amber' };
```

## 12. Fixture index

All fixtures in [`docs/fixtures/`](fixtures/) are real responses from the current build (head `2e430e7`), re-captured on 2026-11-18 against a live server with PostgreSQL, Neo4j, PaddleOCR and Ollama:

- `health.json`, `ready.json`, `profile_fields.json`
- `citizen_create.json`, `citizen_get.json`, `citizen_create_non_synthetic_rejected.json` (403), `citizen_create_sensitive_label_rejected.json` (422)
- `profile_patch.json`, `profile_get.json`, `profile_patch_validation_error.json`
- `enrollment_put.json`, `enrollments_list.json`
- Documents:
  - `document_upload.json`: real PaddleOCR on the synthetic income certificate, with one confirmed value and one conflict
  - `document_upload_type_mismatch.json`: the same certificate declared as `caste_certificate`
  - `document_upload_duplicate.json`, `document_upload_unsupported.json`, `documents_list.json`, `document_get.json`
  - `document_delete.json`: one removed and one reverted field
- `schemes_list.json`, `scheme_detail_mmvy.json`
- Evaluations:
  - `evaluation_run.json`, `evaluation_latest.json`, `evaluation_latest_stale.json` (after a PATCH), `evaluation_detail.json`
  - `eligibility_evaluate_stateless.json`, `eligibility_evaluate_simulation.json`, `eligibility_evaluate_verified_rejected.json`, `eligibility_evaluate_invalid_confidence.json`
- `recommendations.json`: Neo4j graph source, `gaon_ki_beti` marked `applied`, auto re-evaluated after a profile edit
- Explanations:
  - `explanation_citizen.json` (real Ollama)
  - `explanation_provider_none.json` (`RAG_LLM_DISABLED=1`; also re-evaluated after the document delete, `stale_reasons: ["profile_changed"]`)
  - `explain_stateless.json` (simulation, `unsupported_citations_removed:3`)
- `scheme_ask.json`, `scheme_ask_insufficient_evidence.json`, `scheme_ask_refused.json`
- `analytics_summary.json`, `analytics_metadata.json`, `analytics_districts.json`, `analytics_district_detail.json`, `analytics_blocks.json`, `analytics_villages_page.json`, `analytics_schemes.json`
- `error_not_found.json`, `error_scheme_not_found.json`, `error_district_not_found.json`, `error_request_validation.json`

In the `curl` examples, `API` is the base URL and `CID` is a citizen id from `POST /citizens`:

```bash
API=http://localhost:8000/api/v1
CID=$(curl -s -X POST "$API/citizens" -H "Content-Type: application/json" \
      -d '{"label":"Synthetic demo: integration test","is_synthetic":true}' | jq -r .id)
```
