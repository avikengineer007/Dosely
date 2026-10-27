# Dosely — Clinical Prescription Assistant & Dosing Engine

[![Security & Quality CI](https://github.com/avikengineer007/Dosely/actions/workflows/security.yml/badge.svg)](https://github.com/avikengineer007/Dosely/actions/workflows/security.yml)
[![Python Version](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue)](https://www.python.org/)
[![Test Suite](https://img.shields.io/badge/tests-413%20passed-success)](https://github.com/avikengineer007/Dosely)
[![Security Scanned](https://img.shields.io/badge/bandit-0%20vulns-brightgreen)](https://github.com/PyCQA/bandit)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

> **Dosely** is an AI-assisted clinical medication assistant designed around conservative medical safety principles. It extracts handwritten and printed prescriptions using Vision-LLMs with a local PyTorch/EasyOCR fallback, guides patients through a side-by-side human confirmation gate, matches against WHO ATC and Indian clinical pharmacopeias with LASA (Look-Alike Sound-Alike) protection, and schedules doses using a strictly deterministic rule engine with **zero LLM involvement in the dosing path**.

---

## Key Features

* **Dual-Engine Handwriting Recognition**:
  * **Cloud**: Claude 3.5 Sonnet Vision for complex multi-language handwriting.
  * **Local Offline Fallback**: PyTorch/EasyOCR running on quantized CPU with OpenCV auto-deskewing and 2D spatial word-clustering for zero-credit offline use.
* **Side-by-Side Prescription Verification**:
  * Interactive UI displays the original decrypted prescription image/PDF beside extracted fields.
  * Patients can adjust dosages, frequencies, and add missing entries via `+ Add Medicine`.
* **Deterministic Rule-Based Scheduler**:
  * **No LLM in the dosing path**. All dosage intervals and slot timings (morning `06:30`, afternoon `13:00`, evening `20:00`, bedtime, or `as needed`) are calculated strictly via verified pharmacological timing rules.
* **LASA (Look-Alike Sound-Alike) Safety Guard**:
  * High-risk phonetically similar pairs (e.g., *Hydralazine* vs *Hydroxyzine*, *Metformin* vs *Metoprolol*) trigger explicit human review flags rather than auto-substituting.
* **Combination Drug Preservation**:
  * Multi-ingredient brands (e.g., *Augmentin*, *Duolin*, *Telma-AM*) preserve all active ingredients rather than dropping secondary components.
* **Defense-in-Depth Security & Privacy**:
  * **Encrypted at Rest**: Uploaded documents are stored using AES-256 MultiFernet encryption.
  * **Zero Log Leaks**: Strict redaction filters prevent drug names, health details, or PII from appearing in logs even under `DEBUG` levels.
  * **Upload Hardening**: Magic-byte inspection rejects executables, scripts, and polyglots (HTTP 415). Decompression bomb protection caps pixel dimensions (HTTP 413).
  * **Strict CSP & Permissions**: Landing page features an interactive Three.js 3D visualization with Subresource Integrity (SRI) hashes and zero external CDN script calls.

---

## Global Clinical Safety Rules

Dosely strictly adheres to five core clinical safety mandates:

1. **Rule 1 (Conservative Extraction)**: Low-confidence tokens (`confidence < 0.70`), unresolvable generics, or ambiguous directions are explicitly flagged with `needs_review=True`.
2. **Rule 2 (Mandatory Human Confirmation)**: Nothing is matched or scheduled until the patient confirms the extracted fields via `PUT /prescriptions/:id/confirm`. Unconfirmed prescriptions are blocked from schedule generation.
3. **Rule 3 (Suggested Timing Disclaimers)**: Default or inferred dosing times are distinctly labeled as suggestions with explicit clinical consultation notices.
4. **Rule 4 (Zero Health Data in Logs)**: No medicine names, patient identifiers, or clinical notes are emitted to log streams.
5. **Rule 5 (Encrypted Document Storage)**: All uploaded images and PDFs are encrypted before writing to disk and decrypted only on-demand in memory for authorized sessions.

---

## Architecture Pipeline

```
Prescription Upload (Image / PDF)
       │
       ▼
 ┌───────────────────────────────────────────────┐
 │ 1. Upload Sanitizer & Decompression Guard     │
 │    • Magic byte detection (rejects scripts)   │
 │    • PDF JavaScript / launch element stripper │
 └───────────────────────┬───────────────────────┘
                         │
                         ▼
 ┌───────────────────────────────────────────────┐
 │ 2. MultiFernet AES-256 Encrypted Storage      │
 └───────────────────────┬───────────────────────┘
                         │
                         ▼
 ┌───────────────────────────────────────────────┐
 │ 3. Preprocessing (OpenCV)                     │
 │    • Auto-deskew (Hough line transform)       │
 │    • CLAHE contrast normalization             │
 └───────────────────────┬───────────────────────┘
                         │
                         ▼
 ┌───────────────────────────────────────────────┐
 │ 4. Multi-Modal Vision Extraction              │
 │    • Claude 3.5 Sonnet Vision                 │
 │    • Local EasyOCR + Spatial Lexicon Fallback │
 └───────────────────────┬───────────────────────┘
                         │
                         ▼
 ┌───────────────────────────────────────────────┐
 │ 5. Patient Confirmation Gate (Side-by-Side)   │
 │    • Original preview vs editable inputs      │
 │    • PUT /prescriptions/:id/confirm           │
 └───────────────────────┬───────────────────────┘
                         │
                         ▼
 ┌───────────────────────────────────────────────┐
 │ 6. Drug Normalization & LASA Checker          │
 │    • WHO ATC / NLEM Generic Database          │
 │    • Indian Brands Pharmacopeia               │
 └───────────────────────┬───────────────────────┘
                         │
                         ▼
 ┌───────────────────────────────────────────────┐
 │ 7. Deterministic Schedule Engine (No LLM)     │
 │    • Food instructions & timing slots         │
 │    • Drug-drug interaction cross-checks       │
 └───────────────────────────────────────────────┘
```

---

## API Reference

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `POST` | `/auth/token` | Obtain JWT access token for patient session |
| `POST` | `/prescriptions/upload` | Upload image/PDF, encrypt, and run extraction pipeline |
| `GET` | `/prescriptions/{id}` | Retrieve prescription details and extraction state |
| `GET` | `/prescriptions/{id}/image`| Stream decrypted original document for side-by-side preview |
| `PUT` | `/prescriptions/{id}/confirm`| Confirm medicine names and trigger normalization & scheduling |
| `GET` | `/prescriptions/{id}/schedule`| Retrieve generated daily medication schedule and warnings |
| `POST` | `/prescriptions/{id}/dose-action`| Track dose status (`taken`, `skipped`, `snoozed`) |
| `POST` | `/admin/purge` | Purge data older than retention limit (authenticated) |

---

## Getting Started

### Prerequisites
* Python 3.11 or 3.12
* [Optional] Anthropic API key for Cloud Vision (offline OCR functions without it)

### Installation

```bash
# Clone the repository
git clone https://github.com/avikengineer007/Dosely.git
cd Dosely

# Create virtual environment
python -m venv .venv
.venv\Scripts\activate      # Windows
# source .venv/bin/activate # Linux / macOS

# Install dependencies
pip install -r requirements.txt
```

### Configuration

Copy the example configuration:
```bash
cp .env.example .env
```

Set encryption and secret keys:
```env
IMAGE_ENCRYPTION_KEY=your-32-byte-base64-or-hex-key
JWT_SECRET_KEY=your-32-byte-secret-key
ANTHROPIC_API_KEY=sk-ant-api03-optional-cloud-key
```

### Running Locally

```bash
python -m uvicorn main:app --reload --port 8000
```

* **Landing Page**: [http://127.0.0.1:8000/](http://127.0.0.1:8000/)
* **Prescription Assistant App**: [http://127.0.0.1:8000/app](http://127.0.0.1:8000/app)
* **Interactive API Docs (Swagger)**: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)

---

## Running Verification Tests & Audits

```bash
# Run complete test suite (413 tests)
python -m pytest

# Run Independent QA Audit Checks (1-9)
python audit/run_check1.py  # LASA Sound-Alike Guard
python audit/run_check2.py  # Combination Product Integrity
python audit/run_check3.py  # Zero Log Leak Audit
python audit/run_check5.py  # Unconfirmed Prescription Gate
python audit/run_check6.py  # KB Clinical Quality
python audit/run_check7.py  # Multi-worker Purge & Retention
python audit/run_check8.py  # CSP & Header Security
python audit/run_check9.py  # Security Sweep & Content Type

# Run Security Scanners
bandit -r dosely -ll
pip-audit -r requirements.txt
cd frontend && npm audit
```

---

## License & Disclaimer

**Medical Disclaimer**: Dosely is a decision-support technology demonstration and does not provide formal medical advice, diagnosis, or treatment. Always follow your prescribing physician's instructions.

Distributed under the MIT License. See `LICENSE` for details.
