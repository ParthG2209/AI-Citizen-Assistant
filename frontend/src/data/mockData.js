// Illustrative mock data for the prototype. Scheme criteria and amounts are simplified
// for demo purposes and must be checked against official notifications.

export const MP_DISTRICTS = [
  'Bhopal',
  'Sehore',
  'Raisen',
  'Vidisha',
  'Narmadapuram',
  'Indore',
  'Jabalpur',
  'Gwalior',
  'Ujjain',
  'Sagar',
  'Rewa',
  'Chhindwara',
];

export const BENEFIT_OPTIONS = [
  'Ladli Laxmi Yojana',
  'PM-KISAN',
  'Mukhyamantri Kisan Kalyan Yojana',
  'Ayushman Bharat PM-JAY',
  'Post-Matric Scholarship (OBC)',
  'MMVY',
  'PM Ujjwala Yojana',
];

export const PROFILE_FIELDS = [
  { key: 'fullName', label: 'Full Name', type: 'text' },
  { key: 'age', label: 'Age', type: 'number' },
  { key: 'gender', label: 'Gender', type: 'select', options: ['Female', 'Male', 'Other'] },
  { key: 'district', label: 'District (MP)', type: 'select', options: MP_DISTRICTS },
  { key: 'residence', label: 'Residence', type: 'select', options: ['Rural', 'Urban'] },
  { key: 'domicile', label: 'Domicile', type: 'select', options: ['Madhya Pradesh', 'Other State'] },
  { key: 'annualIncome', label: 'Annual Household Income (₹)', type: 'number' },
  { key: 'category', label: 'Category', type: 'select', options: ['General', 'OBC', 'SC', 'ST'] },
  {
    key: 'occupation',
    label: 'Occupation / Status',
    type: 'select',
    options: ['Student', 'Farmer', 'Daily Wage Worker', 'Self-employed', 'Private Salaried', 'Government Employee', 'Unemployed'],
  },
  { key: 'class12Percent', label: 'Class 12 Percentage', type: 'number' },
  { key: 'board', label: 'Class 12 Board', type: 'select', options: ['MP Board (MPBSE)', 'CBSE', 'Other'] },
  { key: 'familyLand', label: 'Family owns cultivable land', type: 'select', options: ['Yes', 'No'] },
  { key: 'puccaHouse', label: 'Household owns pucca house', type: 'select', options: ['Yes', 'No'] },
  { key: 'secc', label: 'Ration card / SECC (Ayushman) listing', type: 'select', options: ['Listed', 'Not listed'] },
  { key: 'existingBenefits', label: 'Existing Benefits', type: 'multi', options: BENEFIT_OPTIONS },
];

// Simulated PaddleOCR output for each supported document type.
export const DOCUMENT_TYPES = [
  {
    id: 'income',
    name: 'MP Income Certificate',
    sampleFile: 'income_certificate_sehore_2025.pdf',
    issuer: 'Tehsildar, Sehore · MP e-District',
    keywords: ['income', 'aay'],
    fields: {
      fullName: { value: 'Priya Verma', confidence: 98 },
      annualIncome: { value: 180000, confidence: 96 },
    },
  },
  {
    id: 'caste',
    name: 'Caste Certificate (OBC)',
    sampleFile: 'caste_certificate_obc.pdf',
    issuer: 'SDO (Revenue), Ashta · Sehore',
    keywords: ['caste', 'jati', 'obc'],
    fields: {
      category: { value: 'OBC', confidence: 94 },
    },
  },
  {
    id: 'samagra',
    name: 'Samagra ID / Domicile',
    sampleFile: 'samagra_domicile_certificate.jpg',
    issuer: 'Samagra Portal · Gram Panchayat Jhilela',
    keywords: ['samagra', 'domicile', 'mool', 'niwas'],
    fields: {
      age: { value: 18, confidence: 91 },
      gender: { value: 'Female', confidence: 97 },
      district: { value: 'Sehore', confidence: 97 },
      residence: { value: 'Rural', confidence: 92 },
      domicile: { value: 'Madhya Pradesh', confidence: 95 },
    },
  },
  {
    id: 'marksheet',
    name: 'Class 12 Marksheet',
    sampleFile: 'mpbse_class12_marksheet_2025.pdf',
    issuer: 'MP Board of Secondary Education',
    keywords: ['marksheet', 'marks', '12', 'mpbse', 'result'],
    fields: {
      class12Percent: { value: 86.4, confidence: 93 },
      board: { value: 'MP Board (MPBSE)', confidence: 95 },
    },
  },
];

// Values the citizen / NGO volunteer types in during the sample demo (no backing document).
export const SAMPLE_USER_INPUT = {
  occupation: 'Student',
  familyLand: 'Yes',
  puccaHouse: 'Yes',
  existingBenefits: ['Ladli Laxmi Yojana'],
};

export const SCHEMES = {
  mmvy: {
    name: 'Mukhyamantri Medhavi Vidyarthi Yojana (MMVY)',
    level: 'MP',
    department: 'MP Higher & Technical Education Dept',
    benefit: 'Full tuition fee for UG admission (Engineering, Medical, Law, Graduation)',
    benefitShort: 'tuition fee waiver (≈₹50,000/yr)',
    annualValue: 50000,
    claimKey: 'MMVY',
    rag: {
      summary:
        'MMVY reimburses the tuition fee of meritorious MP students who secure the required Class 12 score and take admission in a recognised undergraduate course, provided the family income is within the notified ceiling.',
      documents: [
        { label: 'Class 12 Marksheet', docId: 'marksheet' },
        { label: 'MP Income Certificate (current year)', docId: 'income' },
        { label: 'Samagra ID / MP Domicile', docId: 'samagra' },
        { label: 'College admission letter & fee receipt' },
        { label: 'Aadhaar-linked bank account' },
      ],
      steps: [
        'Take admission in an eligible UG course (state/central college or approved private institute).',
        'Register on scholarshipportal.mp.nic.in with Samagra ID and Aadhaar e-KYC.',
        'Upload marksheet, income certificate and fee receipt; submit to the college.',
        'College verifies and forwards to the sanctioning authority.',
        'Fee is reimbursed directly to the institution / student account via DBT.',
      ],
      sources: [
        { title: 'MP Higher Education Dept — MMVY Guidelines (revised)', ref: 'Sec. 3: Eligibility; Sec. 5: Income ceiling', url: 'https://scholarshipportal.mp.nic.in', score: 0.91 },
        { title: 'myScheme.gov.in — Mukhyamantri Medhavi Vidyarthi Yojana', ref: 'Eligibility & Documents', url: 'https://www.myscheme.gov.in', score: 0.86 },
      ],
      qa: 'Per the MMVY guidelines (Sec. 3), the score threshold applies to the Class 12 board result only; entrance exam ranks are used separately for Engineering/Medical seats. The fee waiver covers tuition, not hostel or mess charges, which is why the Knowledge Graph links MMVY to hostel assistance.',
    },
  },
  postMatricObc: {
    name: 'Post-Matric Scholarship for OBC (MP)',
    level: 'Central',
    department: 'MP Backward Classes & Minority Welfare Dept (Centrally sponsored)',
    benefit: 'Maintenance allowance + non-refundable fees for post-Class 10 studies',
    benefitShort: '₹12,000/yr additional aid',
    annualValue: 12000,
    claimKey: 'Post-Matric Scholarship (OBC)',
    rag: {
      summary:
        'The Post-Matric Scholarship supports OBC students studying beyond Class 10 whose parental income is within the notified ceiling. Category must be backed by a valid caste certificate issued by the competent MP authority.',
      documents: [
        { label: 'Caste Certificate (OBC) — digitally issued', docId: 'caste' },
        { label: 'MP Income Certificate (current year)', docId: 'income' },
        { label: 'Samagra ID / MP Domicile', docId: 'samagra' },
        { label: 'Previous exam marksheet', docId: 'marksheet' },
        { label: 'Aadhaar-linked bank account' },
      ],
      steps: [
        'Register on scholarshipportal.mp.nic.in using Samagra ID.',
        'Select "Post-Matric Scholarship (OBC)" and fill course + institution details.',
        'Upload caste and income certificates; complete Aadhaar e-KYC.',
        'Institution verifies the application online.',
        'Scholarship credited via DBT to the Aadhaar-seeded account.',
      ],
      sources: [
        { title: 'MP BC & Minority Welfare Dept — Post-Matric Scholarship Rules', ref: 'Rule 4: Income ceiling; Rule 6: Documents', url: 'https://scholarshipportal.mp.nic.in', score: 0.89 },
        { title: 'Ministry of Social Justice — Post-Matric Scholarship for OBC/EBC/DNT', ref: 'Scheme guidelines', url: 'https://socialjustice.gov.in', score: 0.82 },
      ],
      qa: 'Rule 6 requires the caste certificate to be issued by an MP competent authority (SDO Revenue or above). A self-declared category is not accepted, so the rule engine treats an unverified category as Missing Info rather than a pass.',
    },
  },
  pmKisan: {
    name: 'PM-KISAN Samman Nidhi',
    level: 'Central',
    department: 'Ministry of Agriculture & Farmers Welfare',
    benefit: '₹6,000/yr income support to landholding farmer families (3 instalments)',
    benefitShort: '₹6,000/yr income support',
    annualValue: 6000,
    claimKey: 'PM-KISAN',
    rag: {
      summary:
        'PM-KISAN provides ₹6,000 per year to landholding farmer families. Institutional landholders, constitutional post holders, serving government employees and income-tax payers are excluded.',
      documents: [
        { label: 'Land record (Khasra / B-1 copy)' },
        { label: 'Samagra ID / MP Domicile', docId: 'samagra' },
        { label: 'Aadhaar + Aadhaar-seeded bank account' },
      ],
      steps: [
        'Visit pmkisan.gov.in → "New Farmer Registration" or contact the Patwari / CSC.',
        'Enter Aadhaar, land details (Khasra) and bank account.',
        'Patwari / Tehsil verifies land records.',
        'Complete e-KYC (OTP or biometric) — mandatory for instalment release.',
      ],
      sources: [
        { title: 'PM-KISAN Operational Guidelines', ref: 'Para 2: Eligibility; Para 3: Exclusion criteria', url: 'https://pmkisan.gov.in', score: 0.93 },
        { title: 'myScheme.gov.in — PM-KISAN', ref: 'Eligibility', url: 'https://www.myscheme.gov.in', score: 0.84 },
      ],
      qa: 'The benefit is per family (husband, wife and minor children) and is paid to the landholding member. Para 3 excludes families where any member paid income tax in the last assessment year.',
    },
  },
  pmjay: {
    name: 'Ayushman Bharat PM-JAY',
    level: 'Central',
    department: 'National Health Authority / MP State Health Agency',
    benefit: 'Cashless health cover up to ₹5 lakh per family per year',
    benefitShort: '₹5 lakh/yr cashless health cover',
    annualValue: 0,
    claimKey: 'Ayushman Bharat PM-JAY',
    rag: {
      summary:
        'PM-JAY eligibility is determined by inclusion in the SECC-2011 deprivation list or the state-expanded beneficiary list (NFSA ration card holders in MP). It cannot be inferred from income alone.',
      documents: [
        { label: 'Ration card (NFSA) or SECC listing proof' },
        { label: 'Samagra ID / MP Domicile', docId: 'samagra' },
        { label: 'Aadhaar for e-KYC' },
      ],
      steps: [
        'Check eligibility at beneficiary.nha.gov.in using mobile / ration card number.',
        'Complete Aadhaar e-KYC at a CSC, empanelled hospital or via the Ayushman app.',
        'Download the Ayushman card.',
        'Use cashless treatment at any empanelled hospital.',
      ],
      sources: [
        { title: 'NHA — PM-JAY Beneficiary Identification Guidelines', ref: 'Sec. 2: SECC & state lists', url: 'https://beneficiary.nha.gov.in', score: 0.9 },
        { title: 'MP State Health Agency — Ayushman Bharat Niramayam', ref: 'NFSA extension circular', url: 'https://ayushmanbharat.mp.gov.in', score: 0.83 },
      ],
      qa: 'Your listing status is unknown, so the rule engine marks PM-JAY as Potentially Eligible instead of rejecting it. Entering a ration card number or checking beneficiary.nha.gov.in resolves the missing condition.',
    },
  },
  gaonKiBeti: {
    name: 'MP Gaon Ki Beti Yojana',
    level: 'MP',
    department: 'MP Higher Education Dept',
    benefit: '₹500/month for 10 months per year to rural girls pursuing higher education',
    benefitShort: '₹5,000/yr scholarship',
    annualValue: 5000,
    claimKey: 'Gaon Ki Beti Yojana',
    rag: {
      summary:
        'Gaon Ki Beti Yojana encourages rural girls who pass Class 12 in first division from a village school to pursue higher education, providing ₹5,000 per academic year.',
      documents: [
        { label: 'Class 12 Marksheet (first division)', docId: 'marksheet' },
        { label: 'Samagra ID / rural residence proof', docId: 'samagra' },
        { label: 'College admission proof' },
        { label: 'Aadhaar-linked bank account' },
      ],
      steps: [
        'Apply on scholarshipportal.mp.nic.in under "Gaon Ki Beti Yojana".',
        'Upload marksheet and rural residence proof (Gram Panchayat / Samagra).',
        'College verifies admission; sanction by the Higher Education Dept.',
        'Amount credited via DBT each academic year.',
      ],
      sources: [
        { title: 'MP Higher Education Dept — Gaon Ki Beti Yojana Guidelines', ref: 'Clause 2: Eligibility', url: 'https://scholarshipportal.mp.nic.in', score: 0.88 },
      ],
      qa: 'Clause 2 requires the student to have studied in a village school and to reside in a rural area; urban residence fails the condition regardless of marks.',
    },
  },
  pmayG: {
    name: 'PM Awas Yojana – Gramin (PMAY-G)',
    level: 'Central',
    department: 'Ministry of Rural Development / MP Panchayat & Rural Development',
    benefit: '₹1.2 lakh assistance to build a pucca house (plains)',
    benefitShort: '₹1.2 lakh housing assistance',
    annualValue: 0,
    claimKey: 'PMAY-G',
    rag: {
      summary:
        'PMAY-G supports houseless rural households or those living in kutcha / dilapidated houses, identified through SECC-2011 / Awaas+ survey, subject to exclusion criteria such as owning a pucca house or higher household income.',
      documents: [
        { label: 'Samagra ID / rural residence proof', docId: 'samagra' },
        { label: 'MP Income Certificate', docId: 'income' },
        { label: 'Awaas+ survey / Gram Sabha verification' },
        { label: 'Aadhaar + bank account / MGNREGA job card' },
      ],
      steps: [
        'Household must appear in the Awaas+ / SECC permanent wait list.',
        'Gram Sabha validates the priority list.',
        'Sanction issued by the Block / District Panchayat.',
        'Assistance released in instalments linked to geo-tagged construction stages.',
      ],
      sources: [
        { title: 'MoRD — PMAY-G Framework for Implementation', ref: 'Sec. 4.2: Exclusion criteria', url: 'https://pmayg.nic.in', score: 0.9 },
      ],
      qa: 'Sec. 4.2 automatically excludes households that already own a pucca house. Because this condition FAILED in the rule engine, no explanation can make the household eligible; the LLM only restates the failed rule.',
    },
  },
};

// Knowledge-graph edges: Scheme -[:COMPLEMENTED_BY]-> Scheme (with optional extra condition).
export const KG_COMPLEMENTS = [
  {
    from: 'mmvy',
    to: 'MP Hostel Assistance Scheme',
    benefit: 'up to ₹2,000/month hostel / rent support',
    annualValue: 20000,
    relation: 'COMPLEMENTED_BY',
    reason: 'MMVY covers tuition only; accommodation support is linked to MMVY / scholarship holders studying away from home.',
  },
  {
    from: 'mmvy',
    to: 'MP Laptop Protsahan Yojana',
    benefit: '₹25,000 one-time laptop grant',
    annualValue: 25000,
    relation: 'COMPLEMENTED_BY',
    requires: (v) => v.class12Percent !== null && v.class12Percent >= 75 && v.board === 'MP Board (MPBSE)',
    reason: 'MP Board students scoring ≥75% in Class 12 share the merit node with MMVY.',
  },
  {
    from: 'pmKisan',
    to: 'Mukhyamantri Kisan Kalyan Yojana',
    benefit: '₹6,000/yr state top-up',
    annualValue: 6000,
    relation: 'TOPS_UP',
    reason: 'MP pays an additional state instalment to every PM-KISAN beneficiary family.',
  },
];

// Mock PySpark batch output (district level).
export const DISTRICTS = [
  {
    id: 'bhopal',
    name: 'Bhopal',
    position: [23.2599, 77.4126],
    eligible: 184200,
    enrolled: 131700,
    topMissingDocs: ['Income Certificate (expired)', 'Aadhaar–bank seeding'],
    underservedBlocks: ['Berasia'],
  },
  {
    id: 'sehore',
    name: 'Sehore',
    position: [23.2032, 77.0844],
    eligible: 126500,
    enrolled: 66900,
    topMissingDocs: ['Caste Certificate', 'Income Certificate', 'Land record e-KYC'],
    underservedBlocks: ['Ashta', 'Ichhawar', 'Nasrullaganj'],
  },
  {
    id: 'raisen',
    name: 'Raisen',
    position: [23.3327, 77.7824],
    eligible: 112300,
    enrolled: 53600,
    topMissingDocs: ['Caste Certificate', 'Ration card / SECC proof'],
    underservedBlocks: ['Silwani', 'Begamganj', 'Udaipura'],
  },
  {
    id: 'vidisha',
    name: 'Vidisha',
    position: [23.5251, 77.8081],
    eligible: 121800,
    enrolled: 70600,
    topMissingDocs: ['Income Certificate', 'Domicile Certificate'],
    underservedBlocks: ['Lateri', 'Sironj'],
  },
  {
    id: 'narmadapuram',
    name: 'Narmadapuram (Hoshangabad)',
    position: [22.7514, 77.7277],
    eligible: 98400,
    enrolled: 66200,
    topMissingDocs: ['Land record e-KYC', 'Aadhaar–bank seeding'],
    underservedBlocks: ['Kesla'],
  },
  {
    id: 'indore',
    name: 'Indore',
    position: [22.7196, 75.8577],
    eligible: 201500,
    enrolled: 157800,
    topMissingDocs: ['Income Certificate (expired)'],
    underservedBlocks: ['Depalpur'],
  },
];

// Mock statewide (sample districts) adoption: eligible vs enrolled beneficiaries.
export const SCHEME_ADOPTION = [
  { name: 'PM-KISAN', eligible: 312000, enrolled: 251000 },
  { name: 'Ayushman Bharat PM-JAY', eligible: 405000, enrolled: 262000 },
  { name: 'Post-Matric Scholarship (OBC)', eligible: 88000, enrolled: 41500 },
  { name: 'MMVY', eligible: 24600, enrolled: 15900 },
  { name: 'Gaon Ki Beti Yojana', eligible: 31200, enrolled: 12800 },
  { name: 'PMAY-G', eligible: 83900, enrolled: 63300 },
];
