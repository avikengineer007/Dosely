# Real Handwriting Holdout Evaluation Set

## Mandatory Privacy and Safety Rules
1. **NEVER COMMIT REAL PRESCRIPTIONS**: This directory (`data/real_handwriting/`) is strictly git-ignored. Never stage, commit, or push real medical records, prescription images, or identifiable patient details.
2. **CONSENT & DE-IDENTIFICATION**:
   - Only add prescription photos where explicit user/patient consent was obtained for evaluation.
   - Every photo MUST be completely de-identified and de-labeled before placing it here (physically cover or digitally black out patient names, addresses, phone numbers, prescriber details, clinic letterheads, and dates).
3. **THRESHOLD REQUIREMENTS**:
   - To support handwriting in production, the system requires:
     - 0 confident-wrong drug names.
     - Drug-name coverage >= 90% on at least 100 real lines from at least 5 different doctors/writers.
   - Until these criteria are verified on real handwriting, Dosely enforces:
     `HANDWRITING: NOT YET SUPPORTED`.

## Ground Truth Format (`ground_truth.json`)
```json
[
  {
    "image_filename": "real_rx_001.jpg",
    "writer_id": "dr_writer_1",
    "line_index": 1,
    "ground_truth_drug": "metformin",
    "ground_truth_strength": "500 mg",
    "ground_truth_frequency": "BD",
    "ground_truth_duration": 30
  }
]
```
