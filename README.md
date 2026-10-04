# Dosely — Prescription Extraction API

> Upload a prescription image or PDF → get a structured JSON of medicines,
> dosing schedules, and review flags. Powered by Claude vision + OpenCV.

---

## Project Structure

```
Dosely/
├── dosely/
│   ├── models.py          # Pydantic models (Medicine, Prescription, enums)
│   ├── preprocessing.py   # OpenCV pipeline (deskew, grayscale, CLAHE)
│   ├── prompts.py         # System prompt + schema injection + retry prompt
│   ├── extractor.py       # Orchestrator: preprocess → Claude → validate → retry
│   └── api/
│       └── router.py      # FastAPI routes (/extract, /extract/batch, /health)
├── eval/
│   └── evaluate.py        # Offline/online evaluation script
├── tests/
│   ├── conftest.py        # Fixtures (PNG generators, Claude mocks)
│   ├── test_models.py     # Pydantic model unit tests
│   ├── test_preprocessing.py  # OpenCV pipeline unit tests
│   └── test_extractor.py  # Extractor logic unit tests (mocked API)
├── data/
│   └── test_cases.json    # 40 synthetic ground-truth test cases
├── main.py                # FastAPI application entry point
├── requirements.txt
└── README.md
```

---

## Prerequisites

- Python 3.11+
- An [Anthropic API key](https://console.anthropic.com/)

---

## Installation

```bash
# 1. Create and activate a virtual environment
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS / Linux

# 2. Install dependencies
pip install -r requirements.txt
```

---

## Configuration

Set your Anthropic API key as an environment variable:

```powershell
# PowerShell (Windows)
$env:ANTHROPIC_API_KEY = "sk-ant-..."

# Optional overrides
$env:CLAUDE_MODEL      = "claude-3-5-sonnet-20241022"   # default
$env:CLAUDE_MAX_TOKENS = "2048"                          # default
```

---

## Running the API

```bash
# Development (auto-reload on file changes)
uvicorn main:app --reload --port 8000

# Production (2 workers)
gunicorn main:app -k uvicorn.workers.UvicornWorker -w 2 --bind 0.0.0.0:8000
```

Open **http://localhost:8000/docs** for the interactive Swagger UI.

### Example cURL Request

```bash
curl -X POST http://localhost:8000/api/v1/extract \
     -F "file=@prescription.jpg;type=image/jpeg" \
     -F "prescription_id=rx-001"
```

### Example Response (truncated)

```json
{
  "prescription_id": "rx-001",
  "image_quality": "good",
  "medicines": [
    {
      "name_raw": "Tab. Amoxicillin 500 mg",
      "name_generic": "amoxicillin",
      "drug_class": "antibiotic",
      "purpose": "Kills bacteria causing the infection",
      "strength": "500 mg",
      "form": "tablet",
      "frequency_raw": "TDS",
      "frequency_normalized": "Three times daily (morning, afternoon & night)",
      "timing_slots": ["morning", "afternoon", "night"],
      "duration_days": 5,
      "food_instruction": "after_food",
      "confidence": 0.97,
      "needs_review": false,
      "review_reasons": []
    }
  ],
  "needs_human_review": false,
  "review_summary": []
}
```

---

## Extraction Pipeline

```
Raw bytes (image / PDF)
        │
        ▼
  ┌─────────────────────────────────────────────┐
  │  preprocessing.py                           │
  │  1. PDF → PNG raster (PyMuPDF, if PDF)      │
  │  2. BGR → Grayscale                         │
  │  3. Hough-line deskew (up to ±10°)          │
  │  4. CLAHE contrast normalisation            │
  │  5. Quality assessment (Laplacian variance) │
  └──────────────────┬──────────────────────────┘
                     │ PNG bytes + quality label
                     ▼
  ┌─────────────────────────────────────────────┐
  │  extractor.py                               │
  │  6. Base64-encode image                     │
  │  7. Call Claude (system prompt + schema)    │
  │  8. Extract JSON from response              │
  │  9. Pydantic validation                     │
  │     ├─ Success → return Prescription        │
  │     └─ Failure → retry once with error msg  │
  │         ├─ Success → return Prescription    │
  │         └─ Failure → fallback (empty, review│
  └─────────────────────────────────────────────┘
```

---

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET`  | `/api/v1/health` | Liveness check |
| `POST` | `/api/v1/extract` | Extract one prescription (image or PDF) |
| `POST` | `/api/v1/extract/batch` | Extract up to 10 prescriptions concurrently |

### `/api/v1/extract` parameters

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `file` | `File` | ✅ | JPEG, PNG, TIFF, BMP, or PDF (max 20 MB) |
| `prescription_id` | `Form` | ❌ | Stable ID (UUID4 generated if omitted) |
| `pdf_page` | `Form` | ❌ | Zero-indexed page for PDF files (default: 0) |

---

## Running Tests

```bash
# Run all unit tests (no API key required — all Claude calls are mocked)
pytest tests/ -v

# Run with coverage report
pip install pytest-cov
pytest tests/ -v --cov=dosely --cov-report=term-missing

# Run a specific test file
pytest tests/test_models.py -v
pytest tests/test_preprocessing.py -v
pytest tests/test_extractor.py -v
```

> **No API key needed for tests.** All Claude API calls are intercepted by
> `unittest.mock.patch` fixtures defined in `tests/conftest.py`.

---

## Evaluation Script

### Offline mode (no API key, no images)

Validates all 40 test cases against the Pydantic schema and prints
field-level coverage statistics from the ground-truth data.

```bash
python -m eval.evaluate --mode offline
```

Sample output:
```
Loaded 40 test cases from data/test_cases.json

======================================================================
OFFLINE MODE — Schema compliance + ground-truth field coverage
======================================================================

Test cases parsed : 40
  Schema valid    : 40
  Schema errors   : 0

Total medicines   : 54

----------------------------------------------------------------------
Field                                         Present    Total Coverage
----------------------------------------------------------------------
medicine.confidence                                54       54   100.0%
medicine.drug_class                                54       54   100.0%
medicine.duration_days                             41       54    75.9%
medicine.food_instruction                          54       54   100.0%
medicine.form                                      54       54   100.0%
medicine.frequency_normalized                      44       54    81.5%
medicine.frequency_raw                             51       54    94.4%
medicine.name_generic                              39       54    72.2%
medicine.name_raw                                  54       54   100.0%
medicine.needs_review                              54       54   100.0%
medicine.strength                                  39       54    72.2%
prescription.image_quality                         40       40   100.0%
----------------------------------------------------------------------
```

### Online mode (real images, real API)

```bash
# Images must be named <prescription_id>.<ext>, e.g. rx-tc-001.jpg
python -m eval.evaluate --mode online --image-dir path/to/images/

# Evaluate a single test case
python -m eval.evaluate --mode online --image-dir path/to/images/ --filter TC-001
```

---

## "Never Guess" Rules

The system prompt enforces strict extraction-only behaviour. See
`never_guess_rules.md` (included in the project) for the full 14-rule
specification with severity matrix and implementation checklist.

Key rules:
1. **Unknown drug name** → `name_generic = null`, never substitute nearest match
2. **Missing strength** → `strength = null`, never default to common dose
3. **Unknown frequency abbreviation** → `frequency_normalized = null`
4. **Missing duration** → `duration_days = null`, never assume a "typical" course
5. **Poor image** → whole prescription flagged `needs_human_review = true`

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | — | **Required.** Your Anthropic API key. |
| `CLAUDE_MODEL` | `claude-3-5-sonnet-20241022` | Claude model to use. |
| `CLAUDE_MAX_TOKENS` | `2048` | Max tokens in the model response. |

---

## Accepted File Types

| Format | MIME type |
|--------|-----------|
| JPEG | `image/jpeg` |
| PNG | `image/png` |
| TIFF | `image/tiff` |
| BMP | `image/bmp` |
| PDF | `application/pdf` |

Maximum file size: **20 MB**. For PDF files, only the first page is processed
by default (use `pdf_page` parameter to select a different page).

---

## Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feat/my-feature`
3. Run tests before committing: `pytest tests/ -v`
4. Open a pull request
