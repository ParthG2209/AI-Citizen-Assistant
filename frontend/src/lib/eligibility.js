// Deterministic mock rule evaluator. Mirrors the planned Python rule engine:
// each condition returns PASS (true), FAIL (false) or MISSING (null).
//   any FAIL            -> not_eligible
//   no FAIL, any MISSING -> potential
//   all PASS            -> eligible
// The LLM / RAG layer never changes these results; it only explains them.

import { DOCUMENT_TYPES, KG_COMPLEMENTS, PROFILE_FIELDS, SCHEMES } from '../data/mockData.js';

const NUMERIC = new Set(PROFILE_FIELDS.filter((f) => f.type === 'number').map((f) => f.key));
const DOC_NAME = Object.fromEntries(DOCUMENT_TYPES.map((d) => [d.id, d.name]));

export const formatINR = (n) => `₹${Number(n).toLocaleString('en-IN')}`;

// Flatten provenance-tagged fields into plain values; missing -> null.
export function toValues(fields) {
  const values = {};
  for (const [key, field] of Object.entries(fields)) {
    const empty = field.source === 'missing' || field.value === '' || field.value === null;
    if (key === 'existingBenefits') values[key] = field.value;
    else if (empty) values[key] = null;
    else values[key] = NUMERIC.has(key) ? Number(field.value) : field.value;
  }
  return values;
}

const isMP = (v) => (v.domicile === null ? null : v.domicile === 'Madhya Pradesh');
const isStudent = (v) => (v.occupation === null ? null : v.occupation === 'Student');
const incomeAtMost = (limit) => (v) => (v.annualIncome === null ? null : v.annualIncome <= limit);

// Category only counts when backed by a caste certificate (OCR source).
const verifiedCategory = (allowed) => (v, f) => {
  if (v.category === null) return null;
  if (f.category.source !== 'ocr') return null;
  return allowed.includes(v.category);
};

const RULES = {
  mmvy: [
    { label: 'MP domicile', check: isMP, fail: 'Domicile is not Madhya Pradesh', resolve: 'samagra' },
    {
      label: 'Class 12 ≥ 70% (MP Board) or ≥ 85% (CBSE)',
      check: (v) => {
        if (v.class12Percent === null || v.board === null) return null;
        if (v.board === 'MP Board (MPBSE)') return v.class12Percent >= 70;
        if (v.board === 'CBSE') return v.class12Percent >= 85;
        return false;
      },
      fail: (v) => `Class 12 score ${v.class12Percent}% (${v.board}) is below the merit threshold`,
      resolve: 'marksheet',
    },
    { label: 'Family income ≤ ₹6,00,000/yr', check: incomeAtMost(600000), fail: (v) => `Income ${formatINR(v.annualIncome)} exceeds ₹6,00,000 ceiling`, resolve: 'income' },
    { label: 'Enrolled / admitted as a student', check: isStudent, fail: 'Applicant is not a student', resolve: 'Occupation / Status' },
  ],
  postMatricObc: [
    {
      label: 'Category OBC (verified by caste certificate)',
      check: verifiedCategory(['OBC']),
      fail: (v) => `Category is ${v.category}, scheme is for OBC`,
      missing: (v, f) => (v.category !== null && f.category.source !== 'ocr' ? 'Category entered but unverified — caste certificate required' : null),
      resolve: 'caste',
    },
    { label: 'Parental income ≤ ₹3,00,000/yr', check: incomeAtMost(300000), fail: (v) => `Income ${formatINR(v.annualIncome)} exceeds ₹3,00,000 ceiling`, resolve: 'income' },
    { label: 'MP domicile', check: isMP, fail: 'Domicile is not Madhya Pradesh', resolve: 'samagra' },
    { label: 'Studying post-matric (student)', check: isStudent, fail: 'Applicant is not a student', resolve: 'Occupation / Status' },
  ],
  pmKisan: [
    { label: 'Family owns cultivable land', check: (v) => (v.familyLand === null ? null : v.familyLand === 'Yes'), fail: 'Family does not own cultivable land', resolve: 'Land record (Khasra / B-1)' },
    { label: 'No income-tax payer in family (income ≤ ₹7,00,000)', check: incomeAtMost(700000), fail: (v) => `Income ${formatINR(v.annualIncome)} implies income-tax liability`, resolve: 'income' },
    { label: 'Not a serving government employee', check: (v) => (v.occupation === null ? null : v.occupation !== 'Government Employee'), fail: 'Government employees are excluded', resolve: 'Occupation / Status' },
  ],
  pmjay: [
    {
      label: 'Listed in SECC-2011 / NFSA ration card list',
      check: (v) => (v.secc === null ? null : v.secc === 'Listed'),
      fail: 'Household not found in SECC / NFSA beneficiary list',
      resolve: 'Ration card / SECC proof',
    },
    { label: 'MP domicile (state-expanded list)', check: isMP, fail: 'Domicile is not Madhya Pradesh', resolve: 'samagra' },
  ],
  gaonKiBeti: [
    { label: 'Applicant is female', check: (v) => (v.gender === null ? null : v.gender === 'Female'), fail: 'Scheme is for girl students only', resolve: 'samagra' },
    { label: 'Rural residence', check: (v) => (v.residence === null ? null : v.residence === 'Rural'), fail: 'Residence is urban', resolve: 'samagra' },
    { label: 'Class 12 first division (≥ 60%)', check: (v) => (v.class12Percent === null ? null : v.class12Percent >= 60), fail: (v) => `Class 12 score ${v.class12Percent}% is below 60%`, resolve: 'marksheet' },
    { label: 'MP domicile', check: isMP, fail: 'Domicile is not Madhya Pradesh', resolve: 'samagra' },
    { label: 'Admitted to higher education (student)', check: isStudent, fail: 'Applicant is not a student', resolve: 'Occupation / Status' },
  ],
  pmayG: [
    { label: 'Rural household', check: (v) => (v.residence === null ? null : v.residence === 'Rural'), fail: 'Urban households fall under PMAY-Urban', resolve: 'samagra' },
    { label: 'Does not own a pucca house', check: (v) => (v.puccaHouse === null ? null : v.puccaHouse === 'No'), fail: 'Household owns pucca house', resolve: 'Household survey' },
    { label: 'Household income ≤ ₹1,80,000/yr', check: incomeAtMost(180000), fail: (v) => `Income ${formatINR(v.annualIncome)} exceeds threshold`, resolve: 'income' },
    { label: 'Listed in Awaas+ / SECC survey', check: (v) => (v.secc === null ? null : v.secc === 'Listed'), fail: 'Household not in Awaas+ / SECC list', resolve: 'Ration card / SECC proof' },
  ],
};

const resolveLabel = (resolve) => DOC_NAME[resolve] ?? resolve;

export function evaluateScheme(id, fields) {
  const v = toValues(fields);
  const scheme = SCHEMES[id];
  const satisfied = [];
  const failed = [];
  const missing = [];

  for (const rule of RULES[id]) {
    const result = rule.check(v, fields);
    if (result === true) satisfied.push({ label: rule.label });
    else if (result === false) {
      const detail = typeof rule.fail === 'function' ? rule.fail(v) : rule.fail;
      failed.push({ label: rule.label, detail: `Condition Failed: ${detail}` });
    } else {
      const custom = rule.missing?.(v, fields);
      missing.push({
        label: rule.label,
        detail: custom ?? 'Information not available',
        resolve: resolveLabel(rule.resolve),
        resolveDocId: DOC_NAME[rule.resolve] ? rule.resolve : null,
      });
    }
  }

  const status = failed.length ? 'not_eligible' : missing.length ? 'potential' : 'eligible';
  const claimed = v.existingBenefits.includes(scheme.claimKey);
  return { id, scheme, status, satisfied, failed, missing, claimed, total: RULES[id].length };
}

export function evaluateAll(fields) {
  return Object.keys(RULES).map((id) => evaluateScheme(id, fields));
}

// Knowledge-graph traversal + benefit gap detection over the deterministic results.
export function detectGaps(results, fields) {
  const v = toValues(fields);
  const eligibleIds = new Set(results.filter((r) => r.status === 'eligible').map((r) => r.id));

  const complementary = KG_COMPLEMENTS.filter((edge) => eligibleIds.has(edge.from))
    .filter((edge) => !edge.requires || edge.requires(v))
    .filter((edge) => !v.existingBenefits.includes(edge.to))
    .map((edge) => ({ ...edge, fromName: SCHEMES[edge.from].name }));

  const blocked = [];
  for (const r of results.filter((x) => x.status === 'potential')) {
    const byResolve = new Map();
    for (const m of r.missing) {
      if (!byResolve.has(m.resolve)) byResolve.set(m.resolve, m);
    }
    for (const m of byResolve.values()) {
      blocked.push({
        schemeId: r.id,
        schemeName: r.scheme.name,
        resolve: m.resolve,
        resolveDocId: m.resolveDocId,
        benefitShort: r.scheme.benefitShort,
        annualValue: r.scheme.annualValue,
      });
    }
  }

  const unclaimed = results.filter((r) => r.status === 'eligible' && !r.claimed);
  const unclaimedValue =
    unclaimed.reduce((sum, r) => sum + r.scheme.annualValue, 0) +
    complementary.reduce((sum, c) => sum + c.annualValue, 0);

  return { complementary, blocked, unclaimed, unclaimedValue };
}
