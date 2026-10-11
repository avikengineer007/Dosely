// SVG-based realistic medical prescription demo images for immediate interactive testing

export function generatePrescriptionDataUrl(title, medicines, doctor = "Dr. S. K. Sharma, MD", rxNumber = "RX-2026-9041") {
  const medicineLines = medicines.map((m, idx) => `
    <g transform="translate(60, ${240 + idx * 75})">
      <circle cx="10" cy="10" r="14" fill="#e0f2fe" stroke="#0284c7" stroke-width="1.5"/>
      <text x="10" y="15" font-family="'JetBrains Mono', monospace" font-size="13" font-weight="bold" fill="#0369a1" text-anchor="middle">${idx + 1}</text>
      
      <text x="40" y="8" font-family="'Plus Jakarta Sans', sans-serif" font-size="16" font-weight="700" fill="#0f172a">
        ${m.name}
      </text>
      
      <text x="40" y="28" font-family="'Plus Jakarta Sans', sans-serif" font-size="13" fill="#334155">
        Sig: <tspan font-weight="600" fill="#1e293b">${m.frequency}</tspan> | <tspan fill="#475569">${m.instruction || 'As advised'}</tspan>
      </text>
      
      <text x="420" y="18" font-family="'Plus Jakarta Sans', sans-serif" font-size="13" font-weight="600" fill="#0284c7">
        Duration: ${m.duration || 'Not specified'}
      </text>
      
      <line x1="0" y1="46" x2="520" y2="46" stroke="#e2e8f0" stroke-width="1" stroke-dasharray="3,3"/>
    </g>
  `).join('');

  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 840" width="640" height="840">
    <defs>
      <filter id="paper-shadow" x="-5%" y="-5%" width="110%" height="110%">
        <feDropShadow dx="0" dy="8" stdDeviation="12" flood-color="#000" flood-opacity="0.15"/>
      </filter>
      <pattern id="rx-cross" width="40" height="40" patternUnits="userSpaceOnUse">
        <path d="M 20 15 L 20 25 M 15 20 L 25 20" stroke="#f1f5f9" stroke-width="1.5"/>
      </pattern>
    </defs>

    <!-- Paper Background -->
    <rect width="640" height="840" fill="#fdfbf7" filter="url(#paper-shadow)" rx="8"/>
    <rect width="640" height="840" fill="url(#rx-cross)" rx="8"/>

    <!-- Header / Clinic Banner -->
    <rect x="0" y="0" width="640" height="120" fill="#0f172a" rx="8 8 0 0"/>
    <circle cx="50" cy="60" r="26" fill="#0284c7"/>
    <path d="M 50 45 L 50 75 M 35 60 L 65 60" stroke="#ffffff" stroke-width="6" stroke-linecap="round"/>
    
    <text x="90" y="48" font-family="'Plus Jakarta Sans', sans-serif" font-size="20" font-weight="800" fill="#f8fafc">
      CITY MEDICAL CENTRE &amp; SPECIALITY CARE
    </text>
    <text x="90" y="70" font-family="'Plus Jakarta Sans', sans-serif" font-size="13" font-weight="500" fill="#94a3b8">
      102 Park Healthcare Avenue • Reg: DMC-99824 • Emergency: +91 98765 43210
    </text>
    <text x="90" y="90" font-family="'Plus Jakarta Sans', sans-serif" font-size="12" font-weight="600" fill="#38bdf8">
      ${doctor}
    </text>

    <!-- Patient Details Bar -->
    <rect x="30" y="140" width="580" height="50" fill="#f8fafc" stroke="#e2e8f0" stroke-width="1.5" rx="6"/>
    <text x="50" y="162" font-family="'Plus Jakarta Sans', sans-serif" font-size="12" font-weight="600" fill="#64748b">PATIENT NAME:</text>
    <text x="145" y="162" font-family="'Plus Jakarta Sans', sans-serif" font-size="14" font-weight="700" fill="#0f172a">Rahul Verma (M / 34y)</text>

    <text x="360" y="162" font-family="'Plus Jakarta Sans', sans-serif" font-size="12" font-weight="600" fill="#64748b">DATE:</text>
    <text x="405" y="162" font-family="'Plus Jakarta Sans', sans-serif" font-size="14" font-weight="700" fill="#0f172a">08 Oct 2026</text>

    <text x="50" y="180" font-family="'Plus Jakarta Sans', sans-serif" font-size="11" font-weight="500" fill="#94a3b8">RX ID: ${rxNumber}</text>
    <text x="360" y="180" font-family="'Plus Jakarta Sans', sans-serif" font-size="11" font-weight="500" fill="#94a3b8">DIAGNOSIS: Acute Bronchitis &amp; Gastric Irritation</text>

    <!-- Rx Symbol -->
    <g transform="translate(45, 205)">
      <text font-family="'JetBrains Mono', serif" font-size="34" font-weight="900" fill="#0284c7" font-style="italic">Rx</text>
    </g>

    <!-- Medicine Items -->
    ${medicineLines}

    <!-- Physician Notes / Clinical Stamp -->
    <g transform="translate(60, 560)">
      <rect width="520" height="60" fill="#f8fafc" stroke="#cbd5e1" stroke-width="1" stroke-dasharray="4,4" rx="6"/>
      <text x="16" y="24" font-family="'Plus Jakarta Sans', sans-serif" font-size="12" font-weight="700" fill="#475569">SPECIAL INSTRUCTIONS:</text>
      <text x="16" y="44" font-family="'Plus Jakarta Sans', sans-serif" font-size="12" fill="#334155">
        Take plenty of warm liquids. Finish the entire antibiotic course without skipping.
      </text>
    </g>

    <!-- Doctor Stamp & Signature -->
    <g transform="translate(400, 680)">
      <circle cx="110" cy="40" r="45" fill="none" stroke="#2563eb" stroke-width="2" stroke-dasharray="3,2" opacity="0.6"/>
      <text x="110" y="32" font-family="'Plus Jakarta Sans', sans-serif" font-size="10" font-weight="800" fill="#2563eb" text-anchor="middle" opacity="0.7">VERIFIED &amp; SIGNED</text>
      <text x="110" y="48" font-family="'Plus Jakarta Sans', sans-serif" font-size="9" font-weight="600" fill="#2563eb" text-anchor="middle" opacity="0.7">CITY CLINIC</text>
      
      <!-- Hand signature squiggle -->
      <path d="M 40 70 Q 70 20 100 65 T 160 50 T 180 60" fill="none" stroke="#1d4ed8" stroke-width="2.5" stroke-linecap="round"/>
      <line x1="30" y1="80" x2="190" y2="80" stroke="#94a3b8" stroke-width="1"/>
      <text x="110" y="96" font-family="'Plus Jakarta Sans', sans-serif" font-size="11" font-weight="600" fill="#475569" text-anchor="middle">Registered Medical Practitioner</text>
    </g>

    <!-- Footer Bar -->
    <rect x="0" y="810" width="640" height="30" fill="#f1f5f9" rx="0 0 8 8"/>
    <text x="320" y="830" font-family="'Plus Jakarta Sans', sans-serif" font-size="11" fill="#94a3b8" text-anchor="middle">
      Dosely Clinical Assistant • End-to-End Encrypted Verification
    </text>
  </svg>`;

  return `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`;
}

export const SAMPLE_PRESETS = [
  {
    id: "sample_flagged_bronchitis",
    label: "Rx #1: Bronchitis & Acidity (⚠️ 2 Flagged Fields Needing Review)",
    description: "Contains Tab. Azithromycin (missing frequency) & Cap. Omeprazole (missing duration, low confidence). Perfect to test the amber lock!",
    prescription_id: "rx-demo-amber-01",
    patient_name: "Rahul Verma",
    prescription_date: "2026-10-08",
    image_quality: "fair",
    prescriber: {
      name: "Dr. S. K. Gupta",
      clinic_or_hospital: "City Health Speciality Clinic",
      registration_number: "DMC-44812"
    },
    medicines: [
      {
        id: "m_1",
        name_raw: "Tab. Amoxicillin 500 mg",
        name_generic: "amoxicillin",
        drug_class: "antibiotic",
        purpose: "Kills bacteria causing the infection",
        strength: "500 mg",
        form: "tablet",
        frequency_raw: "TDS",
        frequency_normalized: "Three times daily (morning, afternoon & night)",
        timing_slots: ["morning", "afternoon", "night"],
        duration_days: 5,
        food_instruction: "after_food",
        confidence: 0.97,
        needs_review: false,
        review_reasons: []
      },
      {
        id: "m_2",
        name_raw: "Tab. Azithromycin 500 mg",
        name_generic: "azithromycin",
        drug_class: "antibiotic",
        purpose: "Treats bacterial respiratory and skin infections",
        strength: "500 mg",
        form: "tablet",
        frequency_raw: "",
        frequency_normalized: "",
        timing_slots: [],
        duration_days: 3,
        food_instruction: "any_time",
        confidence: 0.74,
        needs_review: true,
        review_reasons: ["Frequency is missing or could not be normalised from prescription text."]
      },
      {
        id: "m_3",
        name_raw: "Cap. Omeprazole 20 mg",
        name_generic: "omeprazole",
        drug_class: "antacid_ppi",
        purpose: "Reduces stomach acid production",
        strength: "20 mg",
        form: "capsule",
        frequency_raw: "OD",
        frequency_normalized: "Once daily in the morning",
        timing_slots: ["morning"],
        duration_days: null,
        food_instruction: "before_food",
        confidence: 0.68,
        needs_review: true,
        review_reasons: [
          "Duration is not specified on prescription.",
          "Overall OCR confidence (0.68) is below threshold (0.70)."
        ]
      }
    ],
    image_data: generatePrescriptionDataUrl(
      "Bronchitis & Acidity",
      [
        { name: "Tab. Amoxicillin 500 mg", frequency: "TDS (Three times daily)", instruction: "After food", duration: "5 days" },
        { name: "Tab. Azithromycin 500 mg", frequency: "[Unclear / Missing]", instruction: "Any time", duration: "3 days" },
        { name: "Cap. Omeprazole 20 mg", frequency: "OD (Once daily)", instruction: "Before breakfast", duration: "[Duration missing]" }
      ],
      "Dr. S. K. Gupta, MD (Pulmonology)",
      "RX-2026-4481"
    )
  },
  {
    id: "sample_clean_infection",
    label: "Rx #2: Dental Infection (✓ Clean Extraction, No Flags)",
    description: "All fields complete with 98% confidence. Ready to confirm immediately.",
    prescription_id: "rx-demo-clean-02",
    patient_name: "Anita Desai",
    prescription_date: "2026-10-08",
    image_quality: "good",
    prescriber: {
      name: "Dr. R. Mehra",
      clinic_or_hospital: "Smile Dental & Maxillofacial",
      registration_number: "KA-88192"
    },
    medicines: [
      {
        id: "m_201",
        name_raw: "Tab. Amoxicillin Clavulanate 625 mg",
        name_generic: "amoxicillin",
        drug_class: "antibiotic",
        purpose: "Broad-spectrum antibacterial treatment",
        strength: "625 mg",
        form: "tablet",
        frequency_raw: "BD",
        frequency_normalized: "Twice daily (morning and night)",
        timing_slots: ["morning", "night"],
        duration_days: 5,
        food_instruction: "after_food",
        confidence: 0.98,
        needs_review: false,
        review_reasons: []
      },
      {
        id: "m_202",
        name_raw: "Tab. Ibuprofen 400 mg",
        name_generic: "ibuprofen",
        drug_class: "analgesic_nsaid",
        purpose: "Relieves dental pain and swelling",
        strength: "400 mg",
        form: "tablet",
        frequency_raw: "TDS",
        frequency_normalized: "Three times daily as needed",
        timing_slots: ["morning", "afternoon", "night"],
        duration_days: 3,
        food_instruction: "after_food",
        confidence: 0.96,
        needs_review: false,
        review_reasons: []
      }
    ],
    image_data: generatePrescriptionDataUrl(
      "Dental Infection",
      [
        { name: "Tab. Augmentin 625 mg", frequency: "BD (Twice daily)", instruction: "After meals", duration: "5 days" },
        { name: "Tab. Ibuprofen 400 mg", frequency: "TDS (Three times daily)", instruction: "Strictly after food", duration: "3 days" }
      ],
      "Dr. R. Mehra, MDS",
      "RX-2026-7712"
    )
  }
];
