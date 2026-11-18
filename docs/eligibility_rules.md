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

## Adding or updating a scheme

1. Read the official guideline / government order (not news or the frontend mock). Note the document date,
   para numbers and quotes.
2. Add `rules/<central|mp>/<scheme_id>.yaml`. Map each criterion to a field in `fields.PROFILE_FIELDS`
   (add a field there only if it is genuinely needed and update OCR / backend consumers). Criteria that cannot
   be expressed go into `limitations`, never into a guessed condition.
3. Set `verification_status` honestly: `verified` only if every condition traces to an official section you read.
4. For a revised rule, add a new file with a new `version` and set `effective_to` on the old one.
5. Run `.venv/Scripts/python.exe -m pytest ai_modules/rule_engine/tests -q` - the shipped-file tests check
   loading, https sources, `source_ref`s and boundary values of every numeric threshold automatically.

## Scheme evidence

All sources accessed on 2026-11-18. "Verified" = condition quoted from a primary official document;
"partial" = official but indirect (department page, CM Helpline register, archived page, press release) or inferred.

### PM-KISAN (`pm_kisan` 2020.1, central) - verified

| Condition | Source section | Status |
|---|---|---|
| Family owns cultivable land (`family_owns_cultivable_land` is true) | [Operational Guidelines 29.03.2020](https://pmkisan.gov.in/Documents/RevisedPM-KISANOperationalGuidelines(English).pdf) para 3, 5.1; [FAQ](https://static.pib.gov.in/WriteReadData/specificdocs/documents/2021/nov/doc2021112361.pdf) Q6, Q13 | verified |
| No income-tax payer in family | Guidelines para 4.1(b)(v) | verified |
| No serving/retired govt employee in family; applicant not a govt employee | Guidelines para 4.1(b)(iii) | verified (Group D carve-out not modellable) |
| No pensioner with >= INR 10,000/month | Guidelines para 4.1(b)(iv) | verified |

Benefit INR 6,000/year in 3 instalments (para 10.1). Limitations: MTS/Class IV/Group D carve-out; institutional
landholders, constitutional posts, elected representatives, professionals, NRIs; 01.02.2019 land cut-off; eKYC.

### Mukhyamantri Kisan Kalyan Yojana (`mp_kisan_kalyan` 2024.1, MP) - partially verified

| Condition | Source section | Status |
|---|---|---|
| MP resident | [CM Helpline scheme 606](https://cmhelpline.mp.gov.in/Schmedetail.aspx?Schemeid=606) (MP Revenue Dept scheme) | partial (scope) |
| PM-KISAN conditions (land, income tax, govt employee, pensioner) | Scheme 606 para 2.4.1 "PM-KISAN eligibility conditions apply" | verified |

Benefit INR 6,000/year on top of PM-KISAN (`TOPS_UP pm_kisan`). Limitations: register page, not the order; the
date the benefit rose from INR 4,000 is unknown; PM-KISAN registration/eKYC not checked.

### AB PM-JAY (`pm_jay` 2024.1, central) - partially verified

| Condition | Source section | Status |
|---|---|---|
| ANY: listed in SECC-2011 / MP NFSA slip | [Lok Sabha USQ 3915 Annex-I](https://sansad.in/getFile/loksabhaquestions/annex/1711/AU3915.pdf?source=pqals) item 7; [MP Niramayam page (archived 2021)](https://web.archive.org/web/20221127184358/https://ayushmanbharat.mp.gov.in/about-us) | SECC verified; MP NFSA expansion partial |
| ANY: age >= 70 (any income) | [NHA senior-citizen FAQ](https://nha.gov.in/img/resources/English_FAQs_related_to_the_benefits_for_senior_citizens.pdf) Q3, Q11; [PIB 20-12-2024](https://pib.gov.in/PressReleasePage.aspx?PRID=2086513) | verified |

Benefit INR 5 lakh cover per family per year. Limitations: SECC criteria not recomputed; Sambal and ASHA/AWW
inclusion not modelled; CGHS/ECHS/CAPF one-time choice; regular card document list unconfirmed.

### PMAY-Gramin (`pmay_gramin` 2024.1, central) - verified

| Condition | Source section | Status |
|---|---|---|
| Rural household | [Framework for Implementation 2022](https://pmayg.dord.gov.in/netiayHome/Uploaded/Guidelines-English_Book_Final.pdf) para 4.1.1 | verified |
| Not living in a pucca house | [PIB 03-12-2024](https://www.pib.gov.in/PressReleasePage.aspx?PRID=2080091) Step 1 | verified |
| No govt employee in household / applicant not govt employee | PIB 03-12-2024 Step 2 parameter 4 | verified |
| No income-tax payer | PIB 03-12-2024 Step 2 parameter 7 | verified |

Benefit INR 1.20 lakh (plains). Limitations: member earning > INR 15,000/month not derivable from family income;
vehicles, KCC >= 50,000, enterprises, professional tax, land size; PWL/Awaas+ listing and Gram Sabha.

### Post-Matric Scholarship for OBC, MP (`post_matric_scholarship_obc` 2018.1) - partially verified

| Condition | Source section | Status |
|---|---|---|
| MP resident | [Regulations 2013 + amendments](https://www.bcwelfare.mp.gov.in/Public/RulesforPostMatricsScholarship.pdf) para 2, 3.1 | verified |
| Category OBC, **caste certificate required** (`min_trust: document_extracted`) | Regulations para 3.1, 3.5; [PM YASASVI](https://scholarships.gov.in/public/schemeGuidelines/3069_G.pdf) para 8.3 | verified |
| Income <= INR 3,00,000 | Amendment 19.06.2018 para 3.11; [Portal 2.0 OBC page](https://hescholarship.mp.gov.in/Public/OtherSchemes/GB.aspx?ID=MTM%3D) | partial (CM Helpline says "< 3 lakh") |
| ANY: enrolled in college / occupation student | Regulations para 3.4; amended group 4 table (class 11-12) | partial (class level not in profile) |
| Has Samagra ID | Portal 2.0 OBC page (registration) | verified |

Limitations: income boundary wording conflict; pre-matric students not distinguishable; attendance,
two-children rule, concurrent scholarships; central ceiling INR 2.5 lakh noted.

### Mukhyamantri Medhavi Vidyarthi Yojana (`mmvy` 2026.1, MP) - verified

| Condition | Source section | Status |
|---|---|---|
| MP resident | [Consolidated order 09/07/2026](https://www.medhavikalyan.mp.gov.in/MedhaviChhatra/Resources/Files/Consolidated_Order_Latest_2026.pdf) para 2.1; [FAQ](https://www.medhavikalyan.mp.gov.in/MedhaviChhatra/Resources/Files/FAQMMVY(1).pdf) Q1 | verified |
| Income < INR 8,00,000 (strict) | Order para 2.2 (raised from 6 lakh by [order 18-08-2023](https://www.medhavikalyan.mp.gov.in/MedhaviChhatra/Resources/Files/Government_Order_Corrigendum6.pdf)) | verified |
| ANY: MPBSE >= 70% / CBSE or ICSE >= 85% | Order para 2.3 | verified |
| Admitted to a graduate-level course | Order para 3 | verified |
| Has Samagra ID | FAQ Q24 | verified |

Benefit: fee reimbursement, amount variable. `TOPS_UP post_matric_scholarship_obc` (para 4.3, difference amount).
Limitations: course/institution coverage and JEE/NEET ranks; other boards; special SC/ST/BPL case; pre-2018
MPBSE 75% (FAQ Q4) not modelled. Note: a Higher Education sub-site still shows the outdated INR 6 lakh limit.

### Gaon Ki Beti Yojana (`gaon_ki_beti` 2005.1, MP) - partially verified

| Condition | Source section | Status |
|---|---|---|
| Female | [HED scheme page](https://highereducation.mp.gov.in/?leftid=ZnGWU%2BxYVl5%2BNBOUmwU4dg%3D%3D&page=a%2Fl6c0hzsecoQ0GSYBnewA%3D%3D) | verified (dept page) |
| Lives in a village (rural) | HED scheme page | verified (dept page) |
| Class 12 >= 60% | HED scheme page | verified (dept page) |
| Studying in a graduate class | HED scheme page | verified (dept page) |
| MP resident | inferred from scope | partial |

Benefit INR 5,000/year. Stackable: `COMPLEMENTED_BY mmvy`, `laptop_protsahan` (page: beneficiaries may take
other schemes). Limitations: no GO found; "village school" not checkable; INR 750/month technical rate only on
the [district page](https://khargone.nic.in/en/scheme/gaon-ki-beti-yojana/); documents unconfirmed.

### Awas Sahayata Yojana (`hostel_assistance` 2016.1, MP) - partially verified

| Condition | Source section | Status |
|---|---|---|
| SC or ST, **caste certificate required** | [Rules 2016](https://hescholarship.mp.gov.in/Public/pdf/Letters/Awas_Sahayata_New_Rule_2016.pdf) title/preamble | verified |
| MP resident | Rules 2016 rule 1(2) | verified |
| Regularly admitted after class 12 | Rules 2016 rule 4(1) | verified |
| Income <= INR 6,00,000 (PMS limit) | Rules 4(5); [HED SC page](https://highereducation.mp.gov.in/?leftid=ZnGWU%2BxYVl5%2BNBOUmwU4dg%3D%3D&page=vLuWikaQay75hcoaHzpQew%3D%3D) | partial (ST ceiling ambiguous) |
| Has Samagra ID | Rules 2016 rule 6(1) | verified |

Benefit INR 2,000 / 1,250 / 1,000 per month for 12 months by town tier (amount null: depends on rented town).
Limitations: not in a govt hostel, different local body from home, failed previous year, rate tier, ST income.

### Pratibhashali Vidyarthi Protsahan Yojana - laptop (`laptop_protsahan` 2025.1 and 2026.1, MP) - partially verified

| Condition | Source section | Status |
|---|---|---|
| MPBSE class 12 | [Education Portal 3.0 scheme card](https://educationportal3.in/SchoolDetail/TalentedStudentIncentiveSchemeManagementSystem) | verified |
| Class 12 >= 75% | Scheme card; [AIR News 31-08-2026](https://newsonair.gov.in/madhya-pradesh-to-provide-25-thousand-rupees-for-laptops-to-meritorious-class-12-students/); DPR MP press release 02-07-2025 | verified (year order not found) |
| Exam year = 2026 (2026.1) / 2025 (2025.1) | Release pattern (payout per exam year) | partial (inferred) |

Benefit INR 25,000 one time, automatic (no application). Limitations: "first attempt" not checkable; the
year-specific order (reportedly No. 580 dated 11-05-2026) was not found on an official site.

### Researched but not shipped

- Central PM YASASVI post-matric OBC component (INR 2.5 lakh): MP students are awarded by the State scheme above.
- Mukhyamantri Jan Kalyan (Shiksha Protsahan) Yojana: needs a "parent registered as unorganised worker" field.
