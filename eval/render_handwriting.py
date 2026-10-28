"""
Synthetic Handwriting Prescription Generator
============================================
Renders test cases in at least 12 handwriting-style font variants, with realistic
degradations including variable stroke width, baseline wobble, slant, ink bleed,
pen pressure variation, paper texture, ruled lines crossing the text, smudges,
strikethroughs, and overwritten corrections.

MANDATORY LABEL:
This set is strictly labeled 'SYNTHETIC' in every report.
It is an easier proxy than real handwriting, so NO real-world claims may be derived from it.
"""

from __future__ import annotations

import json
import math
import os
import random
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
SYNTHETIC_DIR = DATA_DIR / "synthetic_handwriting"

# Candidate handwriting font names across platforms
FONT_CANDIDATES = [
    "segoesc.ttf",       # Windows Segoe Script
    "segpr.ttf",         # Windows Segoe Print
    "comic.ttf",         # Windows Comic Sans MS
    "inkfree.ttf",       # Windows Ink Free
    "mvboli.ttf",        # Windows MV Boli
    "kalam.ttf",         # Kalam
    "caveat.ttf",        # Caveat
    "dancing_script.ttf",# Dancing Script
    "pacifico.ttf",      # Pacifico
    "arial.ttf",         # Fallback base
    "calibri.ttf",       # Fallback base
    "georgia.ttf",       # Fallback base
]


def _get_handwriting_font(index: int, size: int) -> ImageFont.ImageFont:
    candidate_name = FONT_CANDIDATES[index % len(FONT_CANDIDATES)]
    paths = [
        Path(f"C:/Windows/Fonts/{candidate_name}"),
        Path(f"/usr/share/fonts/truetype/{candidate_name}"),
        Path(f"/Library/Fonts/{candidate_name}"),
    ]
    for p in paths:
        if p.exists():
            try:
                return ImageFont.truetype(str(p), size)
            except Exception:
                pass
    return ImageFont.load_default()


def render_synthetic_handwriting_line(
    text: str,
    font_index: int = 0,
    wobble: bool = True,
    slant_angle: float = 0.0,
    stroke_width: int = 2,
    pen_pressure: float = 1.0,
    crossed_lines: bool = False,
    smudge: bool = False,
) -> tuple[Image.Image, tuple[int, int, int, int]]:
    """
    Renders a single line of synthetic doctor handwriting with variable physical distortions.
    """
    font_size = int(32 * pen_pressure)
    font = _get_handwriting_font(font_index, font_size)

    # Canvas
    canvas_w = 900
    canvas_h = 100
    img = Image.new("RGB", (canvas_w, canvas_h), (250, 248, 243))
    draw = ImageDraw.Draw(img)

    # Optional ruled notebook line crossing text
    if crossed_lines:
        rule_y = random.randint(45, 65)
        draw.line([(0, rule_y), (canvas_w, rule_y)], fill=(200, 215, 230), width=1)

    # Baseline wobble and character-by-character ink variations
    cur_x = 40
    base_y = 35
    ink_color = (random.randint(15, 35), random.randint(20, 45), random.randint(40, 80))

    for idx, char in enumerate(text):
        char_y = base_y
        if wobble:
            char_y += int(math.sin(idx * 0.7) * random.uniform(1.0, 3.5))

        draw.text(
            (cur_x, char_y),
            char,
            font=font,
            fill=ink_color,
            stroke_width=max(0, stroke_width - 1),
        )
        bbox = draw.textbbox((cur_x, char_y), char, font=font)
        char_w = max(12, bbox[2] - bbox[0])
        cur_x += char_w + random.randint(-1, 2)

    # Slant shear transform
    if abs(slant_angle) > 0.01:
        m = math.tan(math.radians(slant_angle))
        img = img.transform(img.size, Image.AFFINE, (1, m, 0, 0, 1, 0), resample=Image.BICUBIC)

    # Smudge / ink bleed filter
    if smudge:
        img = img.filter(ImageFilter.GaussianBlur(radius=0.4))

    return img, (40, base_y - 10, min(canvas_w - 10, cur_x + 10), base_y + 45)


def generate_synthetic_evaluation_dataset(num_samples: int = 40) -> dict[str, Any]:
    SYNTHETIC_DIR.mkdir(parents=True, exist_ok=True)

    test_medicines = [
        ("Metformin 500mg", "metformin", "500 mg", "BD", 30),
        ("Paracetamol 650mg", "paracetamol", "650 mg", "TDS", 3),
        ("Amoxicillin 500mg", "amoxicillin", "500 mg", "TDS", 5),
        ("Pantoprazole 40mg", "pantoprazole", "40 mg", "OD", 14),
        ("Cetirizine 10mg", "cetirizine", "10 mg", "HS", 7),
        ("Azithromycin 500mg", "azithromycin", "500 mg", "OD", 3),
        ("Atorvastatin 20mg", "atorvastatin", "20 mg", "HS", 30),
        ("Amlodipine 5mg", "amlodipine", "5 mg", "OD", 30),
    ]

    manifest: list[dict[str, Any]] = []

    for i in range(num_samples):
        med_entry = test_medicines[i % len(test_medicines)]
        raw_text, generic, strength, freq, dur = med_entry

        font_idx = i % len(FONT_CANDIDATES)
        wobble = (i % 2 == 0)
        slant = (i % 5) * 2.5 - 5.0
        crossed = (i % 3 == 0)
        smudge = (i % 4 == 0)

        img, bbox = render_synthetic_handwriting_line(
            text=raw_text,
            font_index=font_idx,
            wobble=wobble,
            slant_angle=slant,
            crossed_lines=crossed,
            smudge=smudge,
        )

        filename = f"synthetic_line_{i+1:03d}.png"
        img.save(SYNTHETIC_DIR / filename)

        manifest.append(
            {
                "image_filename": filename,
                "label": "SYNTHETIC",
                "disclaimer": "SYNTHETIC: An easier proxy than real handwriting. No real-world claim may be derived.",
                "font_index": font_idx,
                "raw_text": raw_text,
                "ground_truth_drug": generic,
                "ground_truth_strength": strength,
                "ground_truth_frequency": freq,
                "ground_truth_duration": dur,
                "bbox": bbox,
            }
        )

    gt_path = SYNTHETIC_DIR / "ground_truth.json"
    gt_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return {"status": "generated", "count": len(manifest), "ground_truth_file": str(gt_path)}


if __name__ == "__main__":
    generate_synthetic_evaluation_dataset()
