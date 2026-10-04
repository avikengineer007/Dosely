# Prescription Assistant — "Never Guess" Rules

> **Purpose**: These rules govern what the system MUST do when extraction
> confidence is low or when any field is absent or ambiguous. They exist to
> protect patients from dosing errors caused by incorrect AI inference.

---

## Core Principle

> The system is an *extraction* tool, not a *completion* tool.
> Its job is to faithfully report what is on the prescription — no more, no less.
> Uncertainty must be surfaced visibly; it must never be silently papered over.

---

## Rules

### 1. Unknown Drug Name → Flag, Do Not Invent

When OCR produces a string that cannot be matched to any drug in the
knowledge base (fuzzy match score < 0.80), the system MUST:

- Set `name_generic = None`.
- Set `drug_class = DrugClass.UNKNOWN`.
- Set `purpose = None`.
- Add `"Generic name could not be resolved"` to `review_reasons`.
- **Never** substitute the closest-sounding drug name.

*Rationale*: "Metformin" and "Methotrexate" share a fuzzy prefix. An incorrect
substitution could result in a chemotherapy drug being shown where a
diabetes drug was intended.

---

### 2. Ambiguous Drug Name → Show All Candidates, Pick None

When OCR produces a string that matches **two or more** drugs with similar
scores (top-2 scores within 0.10 of each other), the system MUST:

- Set `name_generic = None`.
- Populate `review_reasons` with all candidates and their scores.
- **Never** silently select the top candidate.
- Present candidates to the user for manual selection in the UI.

---

### 3. Missing Strength → Explicit None, Not a Default

If strength is absent from the prescription text or illegible, the system MUST:

- Set `strength = None`.
- **Never** fill in a "standard" dose (e.g., do not default Amoxicillin to
  "500 mg" just because that is the most common strength).
- **Never** copy strength from a different medicine in the same prescription.

---

### 4. Illegible Strength → None, Not a Best Guess

If the strength is partially readable (e.g., "50? mg") the system MUST:

- Set `strength = None`.
- Include the partial raw text in `name_raw` or `special_instructions` for
  the reviewer to see.
- **Never** round to the nearest "standard" strength.

---

### 5. Unknown Frequency Abbreviation → None, Not Assumed

If `frequency_raw` contains an abbreviation not in the known mapping table,
the system MUST:

- Set `frequency_normalized = None`.
- Set `timing_slots = None`.
- Add the unknown abbreviation verbatim to `review_reasons`.
- **Never** map it to the most similar known abbreviation.

*Example*: "STAT" (immediately, once) must not be mapped to "OD" (once daily).

---

### 6. Missing Frequency → None, Not the Drug's "Usual" Frequency

If frequency is absent entirely, the system MUST:

- Set `frequency_raw = None`, `frequency_normalized = None`, `timing_slots = None`.
- **Never** infer frequency from drug class or common practice
  (e.g., do not assume Pantoprazole is always OD).

---

### 7. Missing Duration → None, Not a "Typical Course"

If duration is not written on the prescription, the system MUST:

- Set `duration_days = None`.
- **Never** infer duration from antibiotic class, disease context, or
  population norms (e.g., do not default antibiotics to 5 days).

---

### 8. Poor Image Quality → Whole Prescription Flagged

If `image_quality == ImageQuality.POOR`, the system MUST:

- Set `needs_human_review = True` on the `Prescription` object regardless
  of individual medicine confidence scores.
- Display a prominent banner to the user: "Image quality is poor. All
  extracted data must be verified against the original prescription."
- **Never** present POOR-quality extractions as reliable output.

---

### 9. Confidence Threshold Enforcement

If any medicine's `confidence < 0.70`, the system MUST:

- Set `needs_review = True` for that medicine.
- **Never** hide the confidence score from the UI.
- Provide a visual warning (e.g., amber highlight) on that medicine row.

---

### 10. Food Instruction Unknown → Show "Unknown", Not a Drug Default

If food instruction cannot be extracted, the system MUST:

- Set `food_instruction = FoodInstruction.UNKNOWN`.
- **Never** default to "after food" or any class-based assumption.
- Display "Food instructions not found — check with your pharmacist."

---

### 11. Patient Name Absent → Do Not Infer

The system MUST:

- Set `patient_name = None` if the name is not explicitly printed on the
  prescription.
- **Never** populate it from user account data, session context, or inference.

---

### 12. Numeric Look-alikes → Preserve Raw, Flag for Review

OCR frequently confuses: `0 ↔ O`, `1 ↔ l ↔ I`, `5 ↔ S`, `6 ↔ b`.
When a strength or duration field contains characters that trigger these
substitution heuristics, the system MUST:

- Store the raw OCR string in `name_raw` / `special_instructions`.
- Set the relevant structured field to `None`.
- Add a `review_reason` noting the potential numeric OCR error.

---

### 13. Two Similar Drug Names on the Same Prescription → Explicit Disambiguation

When two medicines on the same prescription have name similarity ≥ 0.85
(e.g., "Metformin 500" and "Metformin 1000", or "Amoxicillin" and
"Amoxicillin + Clavulanate"):

- Each must be extracted as a **separate** `Medicine` entry.
- The system MUST add a `review_reason`: "Similar drug name exists on this
  prescription — verify these are distinct entries."
- **Never** merge or deduplicate them silently.

---

### 14. Empty Prescription → Fail Loudly

If no medicines can be extracted (empty `medicines` list), the system MUST:

- Return a `Prescription` with `medicines = []` and `needs_human_review = True`.
- Include a review_summary entry: "No medicines extracted — manual entry required."
- **Never** fabricate a placeholder medicine or return a partial result as complete.

---

## Severity Matrix

| Scenario                         | Action Required            | UI Treatment              |
|----------------------------------|----------------------------|---------------------------|
| name_generic unresolved          | needs_review = True        | Red badge + tooltip       |
| strength = None                  | needs_review = True        | Amber badge               |
| frequency_normalized = None      | needs_review = True        | Amber badge               |
| confidence < 0.70                | needs_review = True        | Amber row highlight       |
| image_quality = POOR             | needs_human_review = True  | Full-page red banner      |
| medicines list empty             | needs_human_review = True  | Blocking error screen     |
| Two similar drug names           | Both flagged               | Side-by-side diff view    |
| Unknown frequency abbreviation   | frequency_normalized = None| Inline warning with raw   |

---

## Implementation Checklist

- [ ] Knowledge-base drug matcher returns confidence score per candidate.
- [ ] Frequency abbreviation map is exhaustive and version-controlled.
- [ ] Image quality classifier runs before any field extraction.
- [ ] All `None` fields surface a visible placeholder in the UI (not empty string).
- [ ] Review reasons are shown to the pharmacist/user, not just logged internally.
- [ ] Unit tests cover all 14 rules with at least one passing and one failing case each.
