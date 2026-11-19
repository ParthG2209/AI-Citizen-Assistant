# `data/raw/census/` — district analytics inputs

> **SYNTHETIC DEMONSTRATION DATA — NOT GOVERNMENT DATA.**
> Nothing in `synthetic_mp_v1/` comes from the Census of India, Samagra, PFMS, scheme MIS portals or any
> other government record. It contains **no real people**. Only the **district names and centroids** are
> real (from `data/geo/mp_districts.geojson`, see `data/geo/README.md`).

## How it is produced

`ai_modules/analytics/synthetic.py` generates the dataset deterministically from a seed
(`random.Random(seed)`; default seed `20261118`, env `SYNTHETIC_SEED` or `--seed`):

```bash
python scripts/run_district_analytics.py              # generates synthetic_mp_v1/ if absent, then runs the job
python scripts/run_district_analytics.py --regenerate # rebuild it
```

Same seed + same parameters + same scheme catalog ⇒ byte-identical files (tested). The generated folder is
git-ignored (`data/raw/census/.gitignore`) because it can always be rebuilt (~10 MB).

* Districts: the 12 districts listed in the frontend (`bhopal`, `sehore`, `raisen`, `vidisha`,
  `narmadapuram`, `indore`, `jabalpur`, `gwalior`, `ujjain`, `sagar`, `rewa`, `chhindwara`).
* Blocks / villages: generic names (`Block 03 (synthetic)`, `Village 007 (synthetic)`). Real block/tehsil
  names are **not** used because no official block list is bundled in this repository.
* Households / citizens: random household sizes (1–6) and ages; household heads 25–70.
* Scheme ids: taken from the rule engine catalog (`ai_modules/rule_engine/rules/**`); missing documents
  are drawn from each rule's `required_documents`.
* Eligibility status / enrolment: **random draws, not rule-engine evaluations**. Target groups and rates
  in `SCHEME_DRAWS` (e.g. student schemes are drawn for ages 16–25, PM-KISAN once per household) only make
  the demo look plausible; they are not the schemes' eligibility rules. District/block/village "effects"
  shift enrolment probabilities so that some areas lag behind.

## Files and schema (`synthetic_mp_v1/`)

All CSVs are UTF-8, comma-separated, with a header row. Empty field = null. Every row carries
`is_synthetic` (`true`/`false`) and `dataset_label`; both must match `manifest.json` or the row is rejected.

### `manifest.json`
`dataset_label` (`SYNTHETIC-MP-DEMO-v1`), `is_synthetic` (`true`), `description`, `generator`,
`generator_version`, `config` (seed and size ranges), `scheme_ids`, `counts`.

### `blocks.csv`
| column | type | rule |
|---|---|---|
| `block_id` | string | required, unique (`<district_id>_bNN`) |
| `block_name` | string | required |
| `district_id` | string | required, must exist in `mp_districts.geojson` |
| `is_synthetic`, `dataset_label` | bool, string | required, must match the manifest |

### `villages.csv`
| column | type | rule |
|---|---|---|
| `village_id` | string | required, unique (`<block_id>_vNNN`) |
| `village_name` | string | required |
| `block_id` | string | required, must be an accepted block |
| `district_id` | string | required, must equal the block's district |
| `households`, `population` | integer | required, `>= 0` |
| `is_synthetic`, `dataset_label` | bool, string | required, must match the manifest |

### `scheme_status.csv` — one row per citizen × scheme assessment
| column | type | rule |
|---|---|---|
| `record_id` | string | required, unique (`<citizen_id>:<scheme_id>`) |
| `citizen_id` | string | required; (`citizen_id`, `scheme_id`) unique. Synthetic id, not Aadhaar/Samagra |
| `household_id` | string | required |
| `village_id` | string | required, must be an accepted village |
| `scheme_id` | string | required, must be in the rule-engine catalog |
| `eligibility_status` | enum | `eligible` \| `potentially_eligible` \| `not_eligible` |
| `enrolled` | bool | required; `true` only allowed when `eligibility_status = eligible` |
| `missing_documents` | string \| null | `|`-separated document type ids from `ai_modules/rule_engine/fields.py` `DOCUMENT_TYPES` |
| `is_synthetic`, `dataset_label` | bool, string | required, must match the manifest |

Rows failing a rule are **not** silently dropped: the analytics job lists them in
`data/processed/analytics/rejected_rows.csv` (`table`, `record_key`, `reason`) and counts them in
`metadata.json` (`rejected_by_reason`). A missing file, a missing column or an invalid manifest aborts the run.
