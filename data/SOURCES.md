# Data Sources & Licences

## 1. Drug Knowledge Base (`data/seed_drugs.json`)
- **Primary Source**: National List of Essential Medicines (NLEM) 2022, Ministry of Health and Family Welfare, Government of India.
  - URL: https://cdsco.gov.in/opencms/opencms/en/Home/
  - Licence: Open Government Data (OGD) Platform India Licence (Public Domain).
- **Secondary Source**: U.S. National Library of Medicine (NLM) RxNorm and openFDA drug dataset.
  - URL: https://open.fda.gov/ / https://www.nlm.nih.gov/research/umls/rxnorm/
  - Licence: RxNorm and openFDA data are public domain works of the United States Government.
- **Content**: Common generic drug names, therapeutic classifications, and patient-friendly non-alarming single-sentence purpose descriptions.

## 2. Indian Brand Names Mapping (`data/indian_brands.csv`)
- **Source**: Indian National Formulary & Central Drugs Standard Control Organisation (CDSCO) published brand references.
- **Licence**: Public Domain / Educational & Clinical Guidance Fair Use.

## 3. Look-Alike / Sound-Alike (LASA) Guard Pairs (`data/lasa_pairs.json`)
- **Source**: Institute for Safe Medication Practices (ISMP) List of Confused Drug Names and WHO Patient Safety Guidance.
- **Licence**: ISMP / WHO Patient Safety Open Access Resources for clinical risk mitigation.

## 4. Combination Products & Synonyms (`data/synonyms.json`, `data/indian_brands.csv`)
- **Source**: CDSCO approved fixed-dose combinations (FDC) list and standard pharmacopoeial synonym mappings.
- **Note**: Brand-to-combination mappings and multi-ingredient entries require formal sign-off by a registered clinical pharmacist.

## 5. MVP Class Allow-lists (`data/class_allowlists.json`)
- **Authoritative Source**: World Health Organization (WHO) Collaborating Centre for Drug Statistics Methodology, Anatomical Therapeutic Chemical (ATC) Classification Index (2024).
  - URL: https://www.whocc.no/atc_ddd_index/
  - Codes:
    - `analgesic_nsaid`: M01A (Anti-inflammatory and antirheumatic products, non-steroids) & N02BA/N02BE (Other analgesics and antipyretics).
    - `antacid_ppi`: A02A (Antacids) & A02B (Drugs for peptic ulcer and gastro-oesophageal reflux disease / PPIs / H2-receptor antagonists).
    - `antibiotic`: J01 (Antibacterials for systemic use), J04A (Drugs for treatment of tuberculosis), P01AB (Nitroimidazole derivatives).
    - `antihistamine`: R06A (Antihistamines for systemic use).
- **Rule**: Any generic categorized into one of these four MVP classes must strictly match an entry in `data/class_allowlists.json`. All other therapeutic classes (e.g., `opioid_analgesic`, `other`) reside outside the MVP allow-lists and require human review.
