# Eligibility rules

The rule engine (`ai_modules/rule_engine`) decides scheme eligibility **deterministically** from versioned YAML
rule files. The LLM / RAG layer never decides or changes eligibility; it only explains engine results.

```python
from datetime import date
from ai_modules.rule_engine import FieldValue, Provenance, RuleEngine

engine = RuleEngine()                                  # loads ai_modules/rule_engine/rules/**/*.yaml
profile = {"annual_family_income": FieldValue(450000, Provenance.DOCUMENT_EXTRACTED, confidence=0.93)}
evaluation = engine.evaluate("mmvy", profile, as_of=date(2026, 11, 18))
evaluation.status            # eligible | potentially_eligible | not_eligible
evaluation.to_dict()         # JSON-ready, reproducible
```

| API | Meaning |
|---|---|
| `RuleEngine(rules_dir=None, *, rules=None, confidence_threshold=0.80)` | Load and validate every rule file (or use already-parsed `rules`). |
| `scheme_ids()` / `versions(scheme_id)` | All schemes with any version / every version of one scheme, oldest first. |
| `schemes(as_of=None)` / `get(scheme_id, as_of=None)` | Version(s) effective on `as_of` (default today). |
| `evaluate(scheme_id, profile, as_of=None)` | `SchemeEvaluation` for the version effective on `as_of`. |
| `evaluate_all(profile, as_of=None)` | Every scheme with a version effective on `as_of`; not-yet-effective rules are excluded. |
| `evaluate_rule(rule, profile, as_of)` | Evaluate one specific version (audits). |
| `load_rules(path)`, `load_rule_file(path)`, `parse_rule(dict)` | Loader with strict validation (`RuleValidationError.errors` lists every problem). |

Errors: `UnknownSchemeError` (no such scheme), `NoEffectiveRuleError` (no version effective on that date),
`ProfileValueError` (profile value of the wrong type, e.g. `social_category: "brahmin"`), `RuleValidationError`.

### Profile value validation

`fields.coerce_value(name, value)` (used by the backend, OCR validator, seed script, loader and engine) raises
`ValueError` unless the value matches the declared field; the engine re-applies it to every `FieldValue`
it reads, so values passed directly (not via the API) are checked too and fail with `ProfileValueError`.

| Field type | Accepted | Rejected |
|---|---|---|
| `integer` (`age`, `class12_passing_year`) | `int`, integral floats/decimals and numeric strings (`18`, `18.0`, `"18.0"`, `" 18 "`) → `int` | fractions (`18.5`, `"18.5"`), bools, NaN / ±inf, out-of-range |
| `number` (`annual_family_income`, `class12_percentage`) | finite `int`/`float`/`Decimal` and plain decimal strings (`"450000"`, `"4.5e5"`) → `float` (`-0.0` → `0.0`) | NaN / ±inf (`"nan"`, `"inf"`), `"12abc"`, `""`, `" "`, `"1_000"`, `"1,00,000"`, bytes, lists / dicts, bools |
| `string` / `enum` | `str` only (trimmed; enums lower-cased and checked against the choices) | numbers, bools, lists, dicts, empty strings |
| `boolean` | `bool`, or the strings `true/yes/1`, `false/no/0` | `1`, `0`, other strings |

Ranges: `age` 0-120, `class12_percentage` 0-100, `class12_passing_year` 1950-2100, `annual_family_income`
0 to INR 1,00,000 crore (`fields.MAX_ANNUAL_FAMILY_INCOME_INR` = 10^12). The income maximum is a sanity bound
against overflow and garbage such as `1e30`, not a policy value; real high incomes are accepted and simply fail
income ceilings (the OCR validator additionally flags extracted incomes above INR 10 crore for review).
`FieldValue.confidence` must be `None` or a finite number in [0, 1] (a NaN confidence would otherwise slip past
the threshold), and `provenance` must be a `Provenance` value.

## YAML grammar

One file = one version of one scheme. Unknown keys are rejected everywhere; duplicate YAML keys are rejected.

```yaml
scheme_id: mmvy                    # required, snake_case, stable across versions
version: "2026.1"                  # required, QUOTED string; (scheme_id, version) must be unique
name: Mukhyamantri Medhavi Vidyarthi Yojana (MMVY)
level: state                       # central | state
state: Madhya Pradesh              # required when level is state
department: Technical Education, Skill Development and Employment Department, GoMP
effective_from: 2026-07-09         # date of the official document version used
effective_to: null                 # optional, inclusive; required on older versions of the same scheme
verification_status: verified      # verified | partially_verified | unverified
sources:                           # required, non-empty; url must be absolute http(s)
  - title: Revised consolidated order dated 09/07/2026
    url: https://www.medhavikalyan.mp.gov.in/MedhaviChhatra/Resources/Files/Consolidated_Order_Latest_2026.pdf
    publisher: Technical Education ... Department, Government of Madhya Pradesh
    accessed_on: 2026-11-18
    section: Paras 2.1-2.4                       # optional
benefit:
  type: fee_reimbursement          # recurring_cash | one_time_cash | fee_reimbursement | in_kind | insurance_cover | housing_assistance
  description: Admission and tuition fee ...
  amount_inr: null                 # null when variable / not published - never invented
  frequency: annual                # annual | monthly | one_time | null
  is_cash: true                    # false for insurance cover / in-kind / housing construction support
required_documents: [samagra_domicile, income_certificate]   # ids from fields.DOCUMENT_TYPES
conditions:                        # required, non-empty; every item is a leaf or a group
  - id: income_below_8_lakh        # unique within the scheme, including inside groups
    description: Father's / guardian's annual income is less than INR 8,00,000
    field: annual_family_income    # must exist in fields.PROFILE_FIELDS
    operator: lt                   # see the operator table
    value: 800000                  # type-checked against the field
    min_trust: self_declared       # optional: self_declared (default) | document_extracted | verified
    resolves_with: [income_certificate]   # DOCUMENT_TYPES ids or PROFILE_FIELDS names
    failure_message: optional sentence appended to FAIL messages
    source_ref: Consolidated order para 2.2      # required on every leaf
  - id: class12_merit              # group: exactly one of `all:` / `any:`; groups nest
    description: MP Board 70% or CBSE/ICSE 85%
    any:
      - id: mpbse_route
        description: MP Board route
        all: [ ...leaf conditions... ]
relationships:                     # optional; target must be a loaded scheme id
  - scheme_id: post_matric_scholarship_obc
    relation: TOPS_UP              # COMPLEMENTED_BY | TOPS_UP | PREREQUISITE_FOR | MUTUALLY_EXCLUSIVE_WITH
    reason: Para 4.3 - the student receives the difference amount
    source_url: https://...        # required: relationships need an official source
limitations: [ "what the rule cannot check" ]
application_steps: [ "from official sources" ]
```

Relationships read subject-verb-object from the declaring file: `A: {scheme_id: B, relation: TOPS_UP}` means
"A tops up B"; `COMPLEMENTED_BY` means "A is complemented by B".

### Operators

| Operator | Field types | `value` | PASS when |
|---|---|---|---|
| `eq` / `ne` | any | one value | equal / not equal |
| `in` / `not_in` | enum, string, integer | non-empty list, no duplicates | value in / not in the list |
| `lt`, `lte`, `gt`, `gte` | integer, number | number | `<`, `<=`, `>`, `>=` |
| `between` | integer, number | `[low, high]`, low <= high | `low <= value <= high` (inclusive) |
| `is_true` / `is_false` | boolean | none | value is true / false |

String comparisons ignore case and repeated whitespace. Enum values must be one of the field's choices, and
numeric values must lie within the field's declared range (e.g. `class12_percentage` 0-100); both are checked at
load time.

## Outcomes, trust and confidence

Each leaf condition is checked in this order:

1. No value in the profile → **MISSING** (`no_value`).
2. Provenance trust below `min_trust` → **MISSING** (`insufficient_trust`). Trust order:
   `self_declared` < `document_extracted` < `verified`. Untrusted values never decide either way: a
   self-declared `social_category: sc` on a rule requiring a caste certificate is MISSING, not FAIL.
3. `document_extracted` value whose OCR confidence is missing or below the threshold (default **0.80**,
   `RuleEngine(confidence_threshold=...)`) → **MISSING** (`low_confidence`). Self-declared and verified values
   have no confidence check. A citizen's correction of an OCR value becomes `self_declared`.
4. Otherwise the operator decides → **PASS** / **FAIL**.

Groups: `all` → FAIL if any child FAILs, else MISSING if any child is MISSING, else PASS.
`any` → PASS if any child PASSes, else MISSING if any child is MISSING, else FAIL.

Scheme status: any top-level FAIL → `not_eligible`; else any MISSING → `potentially_eligible`; else `eligible`.

`SchemeEvaluation.conditions` holds one `ConditionResult` per top-level item. A group result has
`operator` `all`/`any`, `field` = its leaf fields joined by `, `, and `children` with the nested results.
`resolves_with` of a MISSING group lists the documents / fields of its MISSING children. Messages are
deterministic and show actual vs expected values, e.g.
`Annual family income INR 7,00,000 exceeds the limit of INR 6,00,000`.

Policy for `min_trust`: conditions on `social_category` require at least `document_extracted` (a caste
certificate), because the official rules require a certificate issued by the competent authority. Other
fields accept self-declared values so a citizen gets an indicative result before uploading documents; the
required documents are still listed per scheme.

### Verification warning

`SchemeEvaluation.verification_warning` (also in `to_dict()`; mirrored as the first entry of `notes`) is
`None` for `verified` rules. For `partially_verified` / `unverified` rules it is a deterministic text built only
from the rule file (`rule_engine.verification_warning(rule)`):

- `partially_verified`: *"Partially verified rule: not every condition is confirmed by a primary official
  document; confirm with the department before relying on this result."*
- `unverified`: *"Unverified rule: the conditions are not traced to an official source; this result is for
  demonstration only."*

followed by every `limitations` entry that starts with `Partially verified` or `Unverified` (case-insensitive) -
the convention for describing a rule's evidence gaps. Every shipped non-verified rule has at least one such
limitation (enforced by `test_rule_real_files.py`); without one the text ends with *"See the scheme's
limitations for what could not be confirmed."* The warning never changes the status.

### Contradictory profiles

Conditions are evaluated independently on the recorded values; nothing is silently "fixed". Example:
`occupation = government_employee` with `family_member_government_employee = false` (the applicant is a family
member). In PM-KISAN the occupation-based exclusion FAILs, so the scheme is `not_eligible`, and the evaluation
carries a note: *"Profile is inconsistent: …"*. The same input always yields the same result.

### Reproducibility

- `rule_hash` = sha256 of the canonical JSON (sorted keys, no whitespace) of the parsed rule definition.
- `input_hash` = sha256 of canonical JSON of `{rule_hash, as_of, confidence_threshold, fields}` where `fields`
  holds only the fields the rule references, each as coerced value + provenance + confidence (or null).
  Unrelated profile fields do not change the hash; identical inputs give identical hashes and identical
  `to_dict()` output. Nothing reads the clock except the default `as_of` (today).

## Versioning

Several files may define the same `scheme_id` with different `version`s. Validity periods
(`effective_from` .. `effective_to`, inclusive) must not overlap; the engine picks the version effective on
`as_of`, and schemes with no effective version are left out of `evaluate_all` / `schemes`. Example:
`laptop_protsahan` 2025.1 (2025 exam round, 2025-07-02..2026-08-30) and 2026.1 (2026 round, from 2026-08-31).

Any content change to a rule file changes its `rule_hash`, so it must also change `version` (the backend
refuses a known version whose hash changed). A substantive change (different condition, threshold or
effective period) gets a new file and `effective_to` on the old one, so the old version stays loadable. An
evidence-only correction with identical conditions bumps `version` in place; the backend keeps the old
definition in its stored snapshot. Example: `post_matric_scholarship_obc` 2018.1 → 2018.2 (2026-11-18 recheck:
income evidence and limitations corrected, conditions unchanged).

## Adding or updating a scheme

1. Read the official guideline / government order (not news or the frontend mock). Note the document date,
   para numbers and quotes.
2. Add `rules/<central|mp>/<scheme_id>.yaml`. Map each criterion to a field in `fields.PROFILE_FIELDS`
   (add a field there only if it is genuinely needed and update OCR / backend consumers). Criteria that cannot
   be expressed go into `limitations`, never into a guessed condition.
3. Set `verification_status` honestly: `verified` only if every condition traces to an official section you
   read. Otherwise add a limitation starting with `Partially verified:` (or `Unverified:`) naming the gap; it
   is shown to users as the evaluation's `verification_warning`.
4. For a revised rule, add a new file with a new `version` and set `effective_to` on the old one (see Versioning).
5. Run `.venv/Scripts/python.exe -m pytest ai_modules/rule_engine/tests -q` - the shipped-file tests check
   loading, https sources, `source_ref`s, evidence-gap limitations and boundary values of every numeric
   threshold automatically.

## Scheme evidence

Recheck of 2026-11-18: the deciding source of every condition was re-opened (official PDFs downloaded and read;
scanned orders read from the page images). Supporting sources not re-opened in this recheck: PM-KISAN FAQ (PIB
copy), NHA senior-citizen FAQ, PM YASASVI guidelines, Khargone district page, DPR MP press release 02-07-2025,
CM Helpline scheme 812 and Tribal Affairs page (hostel), PMAY-G Awaas+ SOP and PMO release. Status per condition:

- **verified** - stated in a primary official document (guideline, government order / rules, Parliament reply,
  PIB release of a Cabinet decision, official scheme FAQ) at the cited section;
- **secondary source** - official but indirect (department web page, CM Helpline register, scholarship portal
  page, archived page, public-broadcaster news) or derived from the scheme's scope;
- **unresolved** - official sources conflict or are missing; the rule's choice is stated.

No shipped condition contradicts its official source. One evidence attribution was wrong and is fixed in
`post_matric_scholarship_obc` 2018.2 (see below).

### PM-KISAN (`pm_kisan` 2020.1, central) - verified

Source: [Operational Guidelines revised as on 29.03.2020](https://pmkisan.gov.in/Documents/RevisedPM-KISANOperationalGuidelines(English).pdf).

| Condition | Source section | Status |
|---|---|---|
| Family owns cultivable land | Guidelines para 3 ("owns cultivable land as per land records"), 5.1; [FAQ](https://static.pib.gov.in/WriteReadData/specificdocs/documents/2021/nov/doc2021112361.pdf) Q6, Q13 | verified |
| No income-tax payer in family | Guidelines para 4.1(b)(v) | verified |
| No serving/retired govt employee in family; applicant not a govt employee | Guidelines para 4.1(b)(iii) | verified (MTS / Class IV / Group D carve-out not modellable) |
| No pensioner with >= INR 10,000/month | Guidelines para 4.1(b)(iv) | verified |

Benefit INR 6,000/year (para 2). Limitations: Group D carve-out; institutional landholders, constitutional
posts, elected representatives, professionals, NRIs; 01.02.2019 land cut-off (para 5.2); eKYC.

### Mukhyamantri Kisan Kalyan Yojana (`mp_kisan_kalyan` 2024.1, MP) - partially verified

Source: [CM Helpline scheme 606](https://cmhelpline.mp.gov.in/Schmedetail.aspx?Schemeid=606) (Revenue Department,
updated 28-08-2024).

| Condition | Source section | Status |
|---|---|---|
| MP resident | Scheme 606 objective ("pradesh ke kisanon"), state-sponsored scheme | secondary source |
| Owns cultivable land | Scheme 606 beneficiary class "bhumidhari krishak"; "PM-KISAN eligibility conditions apply" | secondary source |
| No income-tax payer / govt employee / pensioner >= INR 10,000 | Scheme 606 para 2.4.1 (e), (c), (d) - same text as PM-KISAN guidelines para 4.1 | secondary source |

Benefit INR 6,000/year in three instalments on top of PM-KISAN (`TOPS_UP pm_kisan`). Unresolved: no
government order found; the page gives both 22-09-2020 and "September 2022" as start dates; the date the
benefit rose from INR 4,000 to INR 6,000 is unknown (version dated from the page update).

### AB PM-JAY (`pm_jay` 2024.1, central) - partially verified

| Condition | Source section | Status |
|---|---|---|
| ANY: listed in SECC-2011 | [Lok Sabha USQ 3915 (24-03-2023) Annexure-I](https://sansad.in/getFile/loksabhaquestions/annex/1711/AU3915.pdf?source=pqals) item 7 | verified |
| ANY: holds an MP NFSA eligibility slip | [MP Niramayam "About us", archived 27-11-2022](https://web.archive.org/web/20221127184358/https://ayushmanbharat.mp.gov.in/about-us) (NFSA and Sambal families added by the State) | secondary source (live page unreachable) |
| ANY: age >= 70 (any income) | [PIB 11-09-2024 Cabinet decision](https://www.pib.gov.in/PressReleasePage.aspx?PRID=2053883); [PIB 20-12-2024](https://pib.gov.in/PressReleasePage.aspx?PRID=2086513) ("on 29.10.2024 ... all senior citizens aged 70 years and above, irrespective of their socio-economic status"); [NHA senior-citizen FAQ](https://nha.gov.in/img/resources/English_FAQs_related_to_the_benefits_for_senior_citizens.pdf) Q3, Q11 | verified |

Benefit INR 5 lakh cover per family per year (Annexure-I item 3). Limitations: SECC criteria not recomputed;
Sambal and ASHA/AWW inclusion not modelled; CGHS/ECHS/CAPF one-time choice; regular card document list
unconfirmed.

### PMAY-Gramin (`pmay_gramin` 2024.1, central) - verified

| Condition | Source section | Status |
|---|---|---|
| Rural household | Scheme scope (Gramin); [Framework for Implementation 2022](https://pmayg.dord.gov.in/netiayHome/Uploaded/Guidelines-English_Book_Final.pdf) para 4.1.1 (universe = SECC houseless / kutcha households, Awaas+) | verified |
| Not living in a pucca house | [PIB 03-12-2024](https://pib.gov.in/PressReleasePage.aspx?PRID=2080091) Step 1 ("pucca roof and/or pucca wall ... more than 2 rooms are filtered out") | verified |
| No govt employee in household / applicant not govt employee | PIB 03-12-2024 Step 2 ("Household with any member as a Government employee") | verified |
| No income-tax payer | PIB 03-12-2024 Step 2 ("Paying income tax") | verified |

Benefit INR 1.20 lakh (plains). Limitations: member earning > INR 15,000/month not derivable from family income;
vehicles, KCC >= 50,000, enterprises, professional tax, land size; PWL/Awaas+ listing and Gram Sabha.

### Post-Matric Scholarship for OBC, MP (`post_matric_scholarship_obc` 2018.2) - partially verified

Primary source: [Regulations 2013 with amendment orders](https://www.bcwelfare.mp.gov.in/Public/RulesforPostMatricsScholarship.pdf)
(20 scanned pages: regulations of 12.12.2013 pp. 1-11; orders of 21.09.2016, 05.10.2016, 05.07.2017,
19.06.2018, 09.07.2018, 2019 and 22.07.2020 pp. 12-20).

| Condition | Source section | Status |
|---|---|---|
| MP resident | Regulations para 2, 3.1 ("Madhya Pradesh mein vastavik roop se sthayi niwas"), 3.5 | verified |
| Category OBC, **caste certificate required** (`min_trust: document_extracted`) | Regulations para 3.1 (MP-notified backward class, proof certified to the Assistant Director), 3.5; [PM YASASVI](https://scholarships.gov.in/public/schemeGuidelines/3069_G.pdf) para 8.3 | verified |
| Income <= INR 3,00,000 | [Scholarship Portal 2.0 OBC page](https://hescholarship.mp.gov.in/Public/OtherSchemes/GB.aspx?ID=MTM%3D) ("300000 rupaye tak ... 100% chhatravritti"); [CM Helpline 950](https://cmhelpline.mp.gov.in/Schmedetail.aspx?Schemeid=950) ("3.00 lakh se kam") | **unresolved** - see below |
| ANY: enrolled in college / occupation student | Regulations para 3.4 (courses after class 12); order 19.06.2018 amended para 5.1 table, group 4 (class 11-12, 10+2) | verified (class level not in profile) |
| Has Samagra ID | Scholarship Portal 2.0 OBC page (registration needs Samagra ID) | secondary source |

Recheck finding (fixed in 2018.2): version 2018.1 cited "amendment order 19.06.2018 (para 3.11)" for the
general income ceiling. That order (pp. 15-17) amends para 3.11, which concerns **employed** students only
(income incl. parents up to INR 3,00,000); the general income test (para 4, p. 4) still reads INR 75,000 in the
compilation and no order raising it is included. The INR 3,00,000 ceiling for all students therefore rests on
the portal and CM Helpline pages, which also disagree on the boundary ("up to" vs "less than"; `lte` kept).
Conditions are unchanged; `source_ref`, sources and limitations were corrected and the version bumped.
Other limitations: pre-matric students not distinguishable; attendance (3.2), two-children rule (3.13),
concurrent scholarships (3.14); central ceiling INR 2.5 lakh (commissioner's letter 22.07.2020, p. 20).

### Mukhyamantri Medhavi Vidyarthi Yojana (`mmvy` 2026.1, MP) - verified

Source: [Consolidated order 09/07/2026](https://www.medhavikalyan.mp.gov.in/MedhaviChhatra/Resources/Files/Consolidated_Order_Latest_2026.pdf) (scanned; pp. 1-2 read).

| Condition | Source section | Status |
|---|---|---|
| MP resident | Order para 2.1; [FAQ](https://www.medhavikalyan.mp.gov.in/MedhaviChhatra/Resources/Files/FAQMMVY(1).pdf) Q1 | verified |
| Income < INR 8,00,000 (strict) | Order para 2.2 ("8 lakh rupaye se kam"; raised from 6 lakh by [order 18-08-2023](https://www.medhavikalyan.mp.gov.in/MedhaviChhatra/Resources/Files/Government_Order_Corrigendum6.pdf)) | verified |
| ANY: MPBSE >= 70% / CBSE or ICSE >= 85% | Order para 2.3 | verified |
| Admitted to a graduate-level course | Order para 3 ("snatak star ki shiksha") | verified |
| Has Samagra ID | FAQ Q24 ("vidyarthi ka Samagra ID aavashyak hai") | verified (official scheme FAQ) |

Benefit: fee reimbursement, amount variable. `TOPS_UP post_matric_scholarship_obc` (para 4.3). Limitations:
course/institution coverage and JEE/NEET ranks (para 3.1-3.5); lateral entry route (para 2.4); other boards;
special SC/ST/BPL case (para 2.2); pre-2016 pass-outs (para 2.3) and FAQ Q4 75% for pre-2018 MPBSE not modelled.
Note: a Higher Education sub-site still shows the outdated INR 6 lakh limit.

### Gaon Ki Beti Yojana (`gaon_ki_beti` 2005.1, MP) - partially verified

Source: [Higher Education Department scheme page](https://highereducation.mp.gov.in/?leftid=ZnGWU%2BxYVl5%2BNBOUmwU4dg%3D%3D&page=a%2Fl6c0hzsecoQ0GSYBnewA%3D%3D) ("Aavedak ki patrata" column).

| Condition | Source section | Status |
|---|---|---|
| Female ("chhatra") | HED page, eligibility | secondary source (department page) |
| Lives in a village (rural) | HED page ("chhatra gaon ki niwasi ho") | secondary source |
| Class 12 >= 60% | HED page ("12vi kaksha nyuntam 60 pratishat ankon se uttirn") | secondary source |
| Studying in a graduate class | HED page ("snatak kaksha mein adhyayanrat") | secondary source |
| MP resident | Scheme scope only (MP state scheme) | **unresolved** (not stated) |

Benefit INR 500/month for 10 months = INR 5,000/year. Stackable: `COMPLEMENTED_BY mmvy`, `laptop_protsahan`
(page: incentive scheme, other schemes may be taken). Unresolved: no government order found; "village school"
not checkable; INR 750/month technical rate only on the [district page](https://khargone.nic.in/en/scheme/gaon-ki-beti-yojana/);
documents unconfirmed.

### Awas Sahayata Yojana (`hostel_assistance` 2016.1, MP) - partially verified

Source: [Navin Niyam 2016, order F 12-06/2013/25-2/1860 dated 23/12/2016](https://hescholarship.mp.gov.in/Public/pdf/Letters/Awas_Sahayata_New_Rule_2016.pdf) (scanned; pp. 1-3 read).

| Condition | Source section | Status |
|---|---|---|
| SC or ST, **caste certificate required** | Rules 2016 title, preamble, rule 2 | verified |
| MP resident | Rules 2016 rule 1(2) ("vistar sampurna Madhya Pradesh"), preamble | verified |
| Regularly admitted after class 12 | Rules 2016 rule 4(1) | verified |
| Income <= INR 6,00,000 | Rule 4(5) (post-matric scholarship income limit); [HED SC page](https://highereducation.mp.gov.in/?leftid=ZnGWU%2BxYVl5%2BNBOUmwU4dg%3D%3D&page=vLuWikaQay75hcoaHzpQew%3D%3D) ("vartaman mein Rs 6.00 lakh") | secondary source for SC; **unresolved** for ST |
| Has Samagra ID | Rules 2016 rule 6(1) (application verified from Samagra ID details) | verified |

Benefit INR 2,000 / 1,250 / 1,000 per month for 12 months by town tier (rule 4(4); amount null). Limitations:
not in a govt hostel (4(1)), same local body (4(6)), failed previous year (4(8)), rate tier, ST income ceiling.

### Pratibhashali Vidyarthi Protsahan Yojana - laptop (`laptop_protsahan` 2025.1 and 2026.1, MP) - partially verified

| Condition | Source section | Status |
|---|---|---|
| MPBSE class 12 | [Education Portal 3.0 scheme card](https://educationportal3.in/SchoolDetail/TalentedStudentIncentiveSchemeManagementSystem) "Labharthi ke liye aavashyak sharten" | secondary source (department page) |
| Class 12 >= 75% | Scheme card ("pratham prayas mein 75 pratishat ya usse adhik"); [AIR News 31-08-2026](https://newsonair.gov.in/madhya-pradesh-to-provide-25-thousand-rupees-for-laptops-to-meritorious-class-12-students/) (2026.1); DPR MP press release 02-07-2025 (2025.1) | secondary source |
| Exam year = 2026 (2026.1) / 2025 (2025.1) | Scheme card "samay seema" (payout after each year's results on a State-fixed date); release dates | secondary source (inferred) |

Benefit INR 25,000 one time, automatic (no application). Unresolved: the year-specific orders fixing each
round's threshold (2026: reportedly No. 580 dated 11-05-2026) were not found on an official site; "first
attempt" not checkable.

### Unresolved items (summary)

1. `post_matric_scholarship_obc` income ceiling: no order raising the para 4 income test to INR 3,00,000 for
   all students was found; portal "up to" vs CM Helpline "less than"; start date of the general ceiling unknown.
2. `hostel_assistance` income ceiling for ST students (CM Helpline: no limit in government institutions;
   Scholarship Portal 2.0: INR 5-6 lakh); INR 6,00,000 applied to SC and ST.
3. `gaon_ki_beti` MP residence is not stated explicitly; no government order found.
4. `mp_kisan_kalyan`: no government order; benefit-increase date and start date unclear.
5. `laptop_protsahan`: year-specific threshold orders not found (both rounds).
6. `pm_jay` MP NFSA expansion: only an archived 2021 state page.

### Researched but not shipped

- Central PM YASASVI post-matric OBC component (INR 2.5 lakh): MP students are awarded by the State scheme above.
- Mukhyamantri Jan Kalyan (Shiksha Protsahan) Yojana: needs a "parent registered as unorganised worker" field.
