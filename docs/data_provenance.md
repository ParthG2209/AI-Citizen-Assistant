# Data Provenance & Sensitive-Data Policy

Every citizen-profile value is stored with **where it came from**. The rule engine uses this to decide how much to trust a value; the UI uses it to show badges.

## Provenance levels

Defined in `ai_modules/rule_engine/fields.py` (`Provenance`, `TRUST`).

| Level | Trust | Set when | UI badge (frontend mock) |
|---|---|---|---|
| `self_declared` | 1 | Citizen / NGO volunteer types a value, **or edits** a value that OCR produced | `Unverified · User Entered` / `User Edited` |
| `document_extracted` | 2 | PaddleOCR extracts the value from an uploaded certificate (with a confidence in `[0, 1]`) | `OCR Extracted · NN%` |
| `verified` | 3 | Confirmed against an authoritative source (e.g. a future DigiLocker / e-District API). **No code path sets this today.** | — |
| *(absent)* | — | No value stored | `Missing` |

OCR text is **never** treated as verification: reading a certificate proves only that the document says something, not that the government has confirmed it.

## Transitions (`backend/app/utils/provenance.py`)

| From | Event | To |
|---|---|---|
| absent | OCR extracts a valid, confident value | `document_extracted` (confidence + `source_document_id` kept) |
| `self_declared`, same value | OCR extracts it | upgraded to `document_extracted` |
| `self_declared`, different value | OCR extracts a value | **kept**; returned as a `conflict` so the UI can ask the user |
| `document_extracted` (earlier document) | a newer document provides the field | replaced by the newer document's value |
| any | OCR value fails validation or is below the review threshold | profile unchanged; returned in `review_required` (user confirms via PATCH → `self_declared`) |
| `document_extracted` | user submits the **same** value | unchanged |
| `document_extracted` | user submits a **different** value | `self_declared` (confidence and source document cleared) |
| `verified` | OCR extracts a value | unchanged (OCR never overwrites `verified`) |
| any | user clears the field (`null`) | absent |
| any | source document deleted | values extracted from that document are removed unless the user has since edited them |

## How rules use provenance

Each rule condition has `min_trust` (default `self_declared`). The engine returns **MISSING**, never FAIL, when:

- the value is absent → `no_value`
- the value's trust is below `min_trust` (e.g. a typed-in caste category where a caste certificate is required) → `insufficient_trust`
- the value is `document_extracted` with OCR confidence below the threshold (default 0.80) → `low_confidence`

So uncertainty leads to **Potentially Eligible** plus a next action (upload, provide, re-check, obtain verification), never to a rejection.

## Sensitive data

- No Aadhaar numbers, Samagra ID numbers, bank details or phone numbers are stored. The profile catalog has no such fields; free-text values containing runs of 9+ digits are rejected (`sensitive_identifier`). OCR returns only `has_samagra_id = true` and never returns Aadhaar-like numbers.
- Uploaded certificate files are processed in memory and **not retained**; only metadata (type, size, SHA-256, OCR status, extracted field values) is stored.
- Logs contain request ids, entity ids and field names, never profile values or document content.
- `DELETE /api/v1/citizens/{id}` hard-deletes the citizen and all dependent rows (profile, documents metadata, evaluations, enrollments); an audit row without personal values is kept.
- This prototype has **no authentication**. It must only be used with synthetic data (`is_synthetic = true` by default). Do not load real citizen records until authentication, authorisation and encryption at rest are added.

## Real vs synthetic data

| Data | Nature |
|---|---|
| Scheme rules (`ai_modules/rule_engine/rules/`) | Derived from official sources; per-scheme `verification_status`, sources and limitations recorded in each YAML and `docs/eligibility_rules.md` |
| RAG corpus (`data/raw/circulars/manifest.yaml`) | Official public government documents (downloaded, not committed) |
| Sample certificates (`data/raw/sample_documents/`) | Synthetic, watermarked |
| Demo citizens (`scripts/seed_database.py`) | Synthetic (`is_synthetic = true`) |
| District analytics (`data/raw/census/`, `data/processed/analytics/`) | Synthetic, every record labelled `is_synthetic = true`; not government statistics |
