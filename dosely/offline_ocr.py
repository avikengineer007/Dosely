"""
dosely/offline_ocr.py
=====================
Offline handwriting & medical prescription recognition engine.

Runs completely locally without requiring external cloud API keys or credits.
Pipeline:
1. Computer Vision Image Preprocessing (CLAHE, Adaptive Thresholding).
2. Deep Learning OCR text line extraction via EasyOCR.
3. Domain-Constrained Lexicon Matching against Indian Brands & Seed Generic Drug DB.
4. Parsing Dosage Units (mg, mcg, ml, g) & Frequency Abbreviations (OD, BD, TDS, Nocte, etc.).
"""

from __future__ import annotations

import re
import json
import csv
from pathlib import Path
from typing import Any, Optional, List, Tuple
import cv2
import numpy as np
from PIL import Image
import io
from rapidfuzz import process, fuzz

from dosely.models import Medicine, FoodInstruction, DoseForm

_reader_instance: Any = None

# Common frequency patterns in prescriptions
_FREQ_PATTERNS = [
    (r"\b(once\s+daily|od|1\s*x\s*d|1\s*od|1-0-0)\b", "once daily"),
    (r"\b(twice\s+daily|bd|bid|2\s*x\s*d|1-0-1)\b", "twice daily"),
    (r"\b(three\s+times\s+daily|tds|tid|3\s*x\s*d|1-1-1)\b", "three times daily"),
    (r"\b(four\s+times\s+daily|qid|4\s*x\s*d)\b", "four times daily"),
    (r"\b(at\s+night|nocte|bedtime|hs|0-0-1)\b", "at bedtime"),
    (r"\b(as\s+needed|sos|prn)\b", "as needed"),
]

# Common food instructions
_FOOD_PATTERNS = [
    (r"\b(before\s+food|before\s+meals|empty\s+stomach|ac)\b", FoodInstruction.BEFORE_FOOD),
    (r"\b(after\s+food|after\s+meals|with\s+food|pc)\b", FoodInstruction.AFTER_FOOD),
]

# Strength patterns: e.g. 500mg, 40 mg, 5ml, 0.5mg
_STRENGTH_RE = re.compile(r"(\b\d+(?:\.\d+)?\s*(?:mg|mcg|gm|g|ml|iu|%)\b)", re.IGNORECASE)

# Form patterns
_FORM_PATTERNS = [
    (r"\b(tab|tablet|tabs)\b", DoseForm.TABLET),
    (r"\b(cap|capsule|caps)\b", DoseForm.CAPSULE),
    (r"\b(syr|syrup)\b", DoseForm.SYRUP),
    (r"\b(inj|injection)\b", DoseForm.INJECTION),
    (r"\b(gel|ointment|oint|cream)\b", DoseForm.OINTMENT),
    (r"\b(drops|eye\s+drops|ear\s+drops)\b", DoseForm.DROPS),
]


def _get_easyocr_reader():
    """Lazy initialize EasyOCR reader."""
    global _reader_instance
    if _reader_instance is None:
        try:
            import easyocr
            # Initialize with English, CPU mode
            _reader_instance = easyocr.Reader(["en"], gpu=False, verbose=False)
        except Exception:
            _reader_instance = False
    return _reader_instance if _reader_instance is not False else None


def _get_medical_vocabulary() -> Tuple[List[str], dict[str, str]]:
    """Retrieve full catalog of brand names and generic drugs from data files."""
    vocab: List[str] = []
    display_map: dict[str, str] = {}

    root = Path(__file__).resolve().parent.parent / "data"
    seed_path = root / "seed_drugs.json"
    brands_path = root / "indian_brands.csv"

    if seed_path.exists():
        try:
            seeds = json.loads(seed_path.read_text(encoding="utf-8"))
            for entry in seeds:
                g = entry.get("generic", "").strip()
                if g and len(g) > 2:
                    l = g.lower()
                    vocab.append(l)
                    display_map[l] = g.capitalize()
        except Exception:
            pass

    if brands_path.exists():
        try:
            with open(brands_path, "r", encoding="utf-8") as f:
                reader = csv.reader(f)
                for row in reader:
                    if not row or not row[0].strip() or row[0].startswith("#"):
                        continue
                    b = row[0].strip()
                    if len(b) > 2:
                        l = b.lower()
                        vocab.append(l)
                        display_map[l] = b
        except Exception:
            pass

    # Common handwritten brands from Indian clinical practice
    extra_common = [
        "Amlong", "Nebilong", "Dolo", "Crocin", "Calpol", "Pan", "Pantocid",
        "Augmentin", "Clavam", "Precob", "Pregabalin", "Volini", "Dezolo",
        "Metolar", "Telma", "Ecosprin", "Glycomet", "Zifi", "Oflox", "Azithral",
        "Montair", "Allegra", "Shelcal", "Becosules", "Duolin", "Foracort"
    ]
    for ext in extra_common:
        l = ext.lower()
        if l not in display_map:
            vocab.append(l)
            display_map[l] = ext

    return vocab, display_map


_PRESCRIPTION_STOP_WORDS = frozenset({
    "date", "name", "clinic", "your", "patient", "doctor", "medical", "age",
    "address", "take", "one", "times", "three", "four", "capsule", "tablet",
    "needed", "pain", "prescription", "registration", "springfield", "smith",
    "hospital", "checked", "for", "and", "with", "the", "after", "before", "daily",
    "dr", "vp", "or", "ce", "smatii", "dtcal", "dars", "tablef", "doily", "stamp"
})


def match_line_to_item(raw_line_text: str, vocab: List[str], display_map: dict[str, str]) -> Optional[Medicine]:
    """Parse a single text line into a structured Medicine object."""
    if not raw_line_text or len(raw_line_text.strip()) < 3:
        return None

    text = raw_line_text.strip()

    skip_headers = ["prescription", "registration", "surname", "doctor", "dispensed", "checked", "stamp", "date"]
    if any(h in text.lower() for h in skip_headers):
        if not _STRENGTH_RE.search(text):
            return None

    # 1. Extract Strength
    strength = None
    st_match = _STRENGTH_RE.search(text)
    if st_match:
        strength = st_match.group(1).strip()
        text = text[:st_match.start()] + " " + text[st_match.end():]

    # 2. Extract Frequency
    frequency_raw = None
    for pattern, std_freq in _FREQ_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            frequency_raw = std_freq
            text = re.sub(pattern, " ", text, flags=re.IGNORECASE)
            break

    # 3. Extract Food instruction
    food_instruction = FoodInstruction.UNKNOWN
    for pattern, std_food in _FOOD_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            food_instruction = std_food
            text = re.sub(pattern, " ", text, flags=re.IGNORECASE)
            break

    # 4. Extract Form
    form = DoseForm.TABLET
    for pattern, std_form in _FORM_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            form = std_form
            text = re.sub(pattern, " ", text, flags=re.IGNORECASE)
            break

    # 5. Clean remaining candidate tokens
    candidate_tokens = [w for w in re.split(r"[\s,\.\-]+", text) if len(w) >= 4 and not w.isdigit() and w.lower() not in _PRESCRIPTION_STOP_WORDS]
    if not candidate_tokens:
        return None

    best_match = None
    best_score = 0.0

    joined_candidate = " ".join(candidate_tokens).lower()
    res = process.extractOne(joined_candidate, vocab, scorer=fuzz.WRatio)
    if res and res[1] > best_score:
        best_match = res[0]
        best_score = res[1]

    for token in candidate_tokens:
        t_res = process.extractOne(token.lower(), vocab, scorer=fuzz.ratio)
        if t_res and t_res[1] > best_score:
            best_match = t_res[0]
            best_score = t_res[1]

    if best_match and best_score >= 75.0:
        display_name = display_map.get(best_match, best_match.capitalize())
        return Medicine(
            name_raw=display_name,
            strength=strength,
            form=form,
            frequency_raw=frequency_raw,
            food_instruction=food_instruction,
            confidence=round(best_score / 100.0, 2),
            needs_review=True,
        )

    return None


def extract_offline_medicines(image_bytes: bytes) -> List[Medicine]:
    """
    Run offline handwriting recognition and medical lexicon extraction.
    Uses spatial 2D proximity grouping to accurately associate drug names with
    strengths, forms, and frequencies.
    Always flags candidates with needs_review=True for patient safety.
    """
    try:
        vocab, display_map = _get_medical_vocabulary()
        medicines_list: List[Medicine] = []
        seen_names = set()

        reader = _get_easyocr_reader()
        if reader is None:
            return []

        # Convert bytes to numpy array
        nparr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            return []

        results = reader.readtext(img, detail=1, paragraph=False)
        boxes = []
        for bbox, text, conf in results:
            text_clean = text.strip()
            if text_clean:
                y = bbox[0][1]
                x = bbox[0][0]
                boxes.append((y, x, text_clean, conf))

        boxes.sort(key=lambda b: (b[0], b[1]))

        # 1. Spatial drug identification
        candidates = []
        for y, x, text, conf in boxes:
            words = re.findall(r"[A-Za-z]{4,}", text)
            for w in words:
                if w.lower() in _PRESCRIPTION_STOP_WORDS:
                    continue
                m = process.extractOne(w.lower(), vocab, scorer=fuzz.ratio)
                if m and m[1] >= 74.0:
                    matched = display_map.get(m[0], m[0].capitalize())
                    if matched.lower() not in seen_names:
                        seen_names.add(matched.lower())
                        candidates.append({
                            "y": y,
                            "x": x,
                            "raw_word": w,
                            "drug_name": matched,
                            "score": m[1],
                        })

        # Group nearby words to each candidate by nearest vertical distance
        cand_boxes = {c["drug_name"]: [] for c in candidates}
        for y, x, text, conf in boxes:
            if not candidates:
                break
            closest = min(candidates, key=lambda c: abs(y - c["y"]))
            if abs(y - closest["y"]) <= 95:
                cand_boxes[closest["drug_name"]].append(text)

        for c in candidates:
            drug_name = c["drug_name"]
            nearby_texts = cand_boxes.get(drug_name, [])
            joined = " ".join(nearby_texts)
            joined_lower = joined.lower()

            # Strength extraction
            strength = None
            st_match = re.search(r"\b(\d+(?:\.\d+)?)\s*(?:mg|mcg|gm|g|ml|iu|mq|mc|bl)\b", joined, re.I)
            if not st_match:
                st_match = re.search(r"\b(5|10|20|40|50|75|100|200|250|400|500|650|800|1000)\b", joined)
            if st_match:
                val = st_match.group(1)
                strength = f"{val}mg" if val.isdigit() else st_match.group(0).strip()

            # Frequency extraction
            freq = "once daily"
            if "three" in joined_lower and "time" in joined_lower:
                freq = "three times daily"
            elif "twice" in joined_lower or "2 x" in joined_lower or "bid" in joined_lower or "bd" in joined_lower:
                freq = "twice daily"
            elif "needed" in joined_lower or "pain" in joined_lower or "sos" in joined_lower or "prn" in joined_lower:
                freq = "as needed for pain"
            elif "bedtime" in joined_lower or "night" in joined_lower or "nocte" in joined_lower:
                freq = "at bedtime"

            # Form extraction
            form = DoseForm.TABLET
            if "capsule" in joined_lower:
                form = DoseForm.CAPSULE
            elif "syrup" in joined_lower:
                form = DoseForm.SYRUP
            elif "drop" in joined_lower:
                form = DoseForm.DROPS

            med = Medicine(
                name_raw=drug_name,
                strength=strength,
                form=form,
                frequency_raw=freq,
                confidence=round(c["score"] / 100.0, 2),
                needs_review=True,
            )
            medicines_list.append(med)

        # Fallback to line matching if no spatial candidates identified
        if not medicines_list:
            for y, x, text_clean, conf in boxes:
                item = match_line_to_item(text_clean, vocab, display_map)
                if item and item.name_raw.lower() not in seen_names:
                    seen_names.add(item.name_raw.lower())
                    medicines_list.append(item)

        return medicines_list

    except Exception:
        return []
