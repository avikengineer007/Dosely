"""
eval/render_test_images.py
==========================
Renders each of the 40 test cases from data/test_cases.json into synthetic
prescription images using Pillow (PIL), with multiple font choices,
mild noise, and rotation variants.

Outputs:
  - data/test_images/<prescription_id>.png for each test case
  - data/test_images/ground_truth.json recording metadata and ground truth per image
"""

from __future__ import annotations

import json
import math
import os
import random
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
TEST_CASES_PATH = DATA_DIR / "test_cases.json"
OUTPUT_DIR = DATA_DIR / "test_images"


def _get_font(font_name: str, size: int) -> ImageFont.ImageFont:
    """Attempt to load a TrueType font, falling back to default."""
    candidates = [
        Path(f"C:/Windows/Fonts/{font_name}"),
        Path(f"/usr/share/fonts/truetype/{font_name}"),
        Path(f"/System/Library/Fonts/{font_name}"),
    ]
    for p in candidates:
        if p.exists():
            try:
                return ImageFont.truetype(str(p), size)
            except Exception:
                pass
    try:
        return ImageFont.truetype(font_name, size)
    except Exception:
        return ImageFont.load_default()


def render_prescription_image(
    test_case: dict[str, Any],
    variant_idx: int = 0,
) -> Image.Image:
    """Render a single prescription test case to a PIL Image."""
    rx = test_case.get("prescription", {})
    prescriber = rx.get("prescriber") or {}
    medicines = rx.get("medicines", [])
    quality = rx.get("image_quality", "good")

    # Font families rotated per variant
    font_choices = [
        ("arial.ttf", "calibri.ttf"),
        ("times.ttf", "times.ttf"),
        ("cour.ttf", "cour.ttf"),
        ("calibri.ttf", "arial.ttf"),
    ]
    primary_font_name, body_font_name = font_choices[variant_idx % len(font_choices)]

    title_font = _get_font(primary_font_name, 26)
    sub_font = _get_font(primary_font_name, 16)
    body_bold = _get_font(primary_font_name, 20)
    body_font = _get_font(body_font_name, 17)
    small_font = _get_font(body_font_name, 14)

    # Base canvas: 850 x 1100 (standard US Letter / A4 aspect)
    width, height = 850, 1150
    # Slightly aged / off-white paper
    paper_color = (
        random.randint(250, 255),
        random.randint(248, 254),
        random.randint(245, 252),
    )
    img = Image.new("RGB", (width, height), color=paper_color)
    draw = ImageDraw.Draw(img)

    # Header / Clinic block
    clinic_name = prescriber.get("clinic_or_hospital") or "City Medical Center & Clinic"
    doc_name = prescriber.get("name") or "Dr. S. K. Mukherjee, MBBS, MD"
    reg_no = prescriber.get("registration_number") or "REG-2024-8841"

    draw.text((60, 50), clinic_name.upper(), fill=(20, 40, 80), font=title_font)
    draw.text((60, 85), f"Consultant Physician: {doc_name}", fill=(50, 50, 50), font=sub_font)
    draw.text((60, 108), f"Reg. No: {reg_no}  |  Phone: +91 98765 43210", fill=(90, 90, 90), font=small_font)

    # Divider line
    draw.line([(50, 135), (width - 50, 135)], fill=(30, 60, 120), width=3)
    draw.line([(50, 139), (width - 50, 139)], fill=(180, 180, 180), width=1)

    # Patient & Date Meta
    rx_date = rx.get("prescription_date") or "2026-09-15"
    rx_id = rx.get("prescription_id", "rx-000")
    draw.text((60, 155), f"Patient: General OPD (Adult)", fill=(40, 40, 40), font=body_font)
    draw.text((580, 155), f"Date: {rx_date}", fill=(40, 40, 40), font=body_font)
    draw.text((60, 180), f"Prescription ID: {rx_id}", fill=(100, 100, 100), font=small_font)

    # Rx Symbol
    rx_symbol_font = _get_font(primary_font_name, 42)
    draw.text((60, 220), "Rx", fill=(180, 30, 30), font=rx_symbol_font)
    draw.line([(60, 275), (200, 275)], fill=(200, 200, 200), width=1)

    # Medicines list
    y = 300
    for idx, med in enumerate(medicines, 1):
        name_raw = med.get("name_raw") or "Medication"
        freq = med.get("frequency_raw") or ""
        food = med.get("food_instruction") or ""
        duration = med.get("duration_days")
        dur_str = f"for {duration} days" if duration else ""

        # Medicine line
        med_line = f"{idx}.  {name_raw}"
        draw.text((60, y), med_line, fill=(20, 20, 20), font=body_bold)
        y += 28

        # Instructions / timing line
        details = []
        if freq:
            details.append(f"Dose: {freq}")
        if food and food != "unspecified":
            details.append(f"({food.replace('_', ' ')})")
        if dur_str:
            details.append(dur_str)

        if details:
            instr_line = "      " + "  —  ".join(details)
            draw.text((60, y), instr_line, fill=(60, 60, 60), font=body_font)
            y += 26

        y += 18  # Spacing between medicines

    # Notes or special instructions
    notes = rx.get("notes_or_special_instructions")
    if notes:
        y += 20
        draw.text((60, y), "Instructions / Advice:", fill=(50, 50, 50), font=body_bold)
        y += 26
        draw.text((80, y), f"• {notes}", fill=(70, 70, 70), font=body_font)
        y += 30

    # Signature block at bottom
    sig_y = height - 160
    draw.line([(width - 280, sig_y), (width - 60, sig_y)], fill=(100, 100, 100), width=1)
    draw.text((width - 250, sig_y + 10), "Doctor's Signature", fill=(80, 80, 80), font=sub_font)
    draw.text((width - 270, sig_y + 35), prescriber.get("name", "Dr. In-Charge"), fill=(120, 120, 120), font=small_font)

    # Noise and quality degradation
    if quality in ("fair", "poor"):
        # Add light salt-and-pepper noise
        pixels = img.load()
        noise_density = 0.015 if quality == "fair" else 0.04
        num_noisy_pixels = int(width * height * noise_density)
        for _ in range(num_noisy_pixels):
            nx = random.randint(0, width - 1)
            ny = random.randint(0, height - 1)
            val = random.choice([40, 220])
            pixels[nx, ny] = (val, val, val)

        if quality == "poor":
            img = img.filter(ImageFilter.GaussianBlur(radius=0.7))

    # Mild rotation variant (-1.5 to +1.5 degrees, or up to 3 deg for poor)
    max_angle = 1.5 if quality == "good" else (2.5 if quality == "fair" else 3.5)
    angle = random.uniform(-max_angle, max_angle)
    img = img.rotate(angle, resample=Image.Resampling.BICUBIC, expand=False, fillcolor=paper_color)

    return img


def render_all_test_cases(
    test_cases_file: Path = TEST_CASES_PATH,
    output_dir: Path = OUTPUT_DIR,
) -> list[dict[str, Any]]:
    """Render all test cases and save images + ground truth manifest."""
    output_dir.mkdir(parents=True, exist_ok=True)

    with open(test_cases_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    test_cases = data if isinstance(data, list) else data.get("test_cases", [])
    ground_truth_records: list[dict[str, Any]] = []

    print(f"Rendering {len(test_cases)} test cases to {output_dir}...")

    for i, tc in enumerate(test_cases):
        rx = tc.get("prescription", {})
        rx_id = rx.get("prescription_id", f"rx-tc-{i+1:03d}")
        image_filename = f"{rx_id}.png"
        image_path = output_dir / image_filename

        rendered = render_prescription_image(tc, variant_idx=i)
        rendered.save(str(image_path), format="PNG", optimize=True)

        record = {
            "test_case_id": tc.get("id"),
            "prescription_id": rx_id,
            "label": tc.get("label"),
            "image_filename": image_filename,
            "image_path": str(image_path.relative_to(PROJECT_ROOT)),
            "image_quality": rx.get("image_quality", "good"),
            "prescription_date": rx.get("prescription_date"),
            "prescriber": rx.get("prescriber"),
            "medicines": rx.get("medicines", []),
        }
        ground_truth_records.append(record)

    # Save manifest
    manifest_path = output_dir / "ground_truth.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "total_images": len(ground_truth_records),
                "generated_from": str(test_cases_file.name),
                "records": ground_truth_records,
            },
            f,
            indent=2,
        )

    print(f"Rendered {len(ground_truth_records)} images successfully.")
    print(f"Manifest written to {manifest_path}")
    return ground_truth_records


DEGRADATION_TYPES = ["tilt", "perspective", "blur", "glare", "noise", "low_res", "shadow"]


def apply_degradation(
    img: Image.Image,
    degradation_type: str,
    severity: float = 1.0,
) -> Image.Image:
    """
    Apply synthetic real-world smartphone camera degradations:
      - tilt: in-plane rotation (e.g. 8.0 * severity)
      - perspective: 4-corner trapezoid perspective warp
      - blur: camera defocus Gaussian blur
      - glare: localized specular white reflection hotspot
      - noise: high ISO sensor Gaussian noise
      - low_res: downscaling then upscaling (loss of high frequencies)
      - shadow: uneven lighting / shadow gradient across page
    """
    import cv2
    import numpy as np

    arr = np.array(img)
    h, w = arr.shape[:2]

    deg = degradation_type.lower()
    if deg == "tilt":
        angle = 8.0 * severity
        M = cv2.getRotationMatrix2D((w // 2, h // 2), angle, 1.0)
        warped = cv2.warpAffine(arr, M, (w, h), borderValue=(245, 245, 245))
        return Image.fromarray(warped)

    elif deg == "perspective":
        offset = int(min(w, h) * 0.12 * severity)
        pts1 = np.float32([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]])
        pts2 = np.float32([
            [offset, offset // 2],
            [w - 1 - offset // 2, 0],
            [w - 1, h - 1 - offset],
            [0, h - 1],
        ])
        M = cv2.getPerspectiveTransform(pts1, pts2)
        warped = cv2.warpPerspective(arr, M, (w, h), borderValue=(245, 245, 245))
        return Image.fromarray(warped)

    elif deg == "blur":
        sigma = 2.5 * severity
        ksize = int(math.ceil(sigma * 3)) * 2 + 1
        blurred = cv2.GaussianBlur(arr, (ksize, ksize), sigmaX=sigma)
        return Image.fromarray(blurred)

    elif deg == "glare":
        cx, cy = w // 2, h // 3
        r = int(min(w, h) * 0.18 * severity)
        mask = np.zeros((h, w), dtype=np.uint8)
        cv2.circle(mask, (cx, cy), r, 255, -1)
        mask_blur = cv2.GaussianBlur(mask, (31, 31), 0).astype(np.float32) / 255.0
        if arr.ndim == 3:
            mask_blur = np.expand_dims(mask_blur, axis=2)
        blended = np.clip(arr.astype(np.float32) * (1.0 - mask_blur) + 255.0 * mask_blur, 0, 255).astype(np.uint8)
        return Image.fromarray(blended)

    elif deg == "noise":
        rng = np.random.default_rng(42)
        noise = rng.normal(0, 18.0 * severity, arr.shape).astype(np.float32)
        noisy = np.clip(arr.astype(np.float32) + noise, 0, 255).astype(np.uint8)
        return Image.fromarray(noisy)

    elif deg == "low_res":
        factor = max(0.2, 0.45 / severity)
        small = cv2.resize(arr, (int(w * factor), int(h * factor)), interpolation=cv2.INTER_AREA)
        upscaled = cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)
        return Image.fromarray(upscaled)

    elif deg == "shadow":
        grad = np.tile(np.linspace(max(0.2, 1.0 - 0.65 * severity), 1.0, w, dtype=np.float32), (h, 1))
        if arr.ndim == 3:
            grad = np.expand_dims(grad, axis=2)
        shadowed = np.clip(arr.astype(np.float32) * grad, 0, 255).astype(np.uint8)
        return Image.fromarray(shadowed)

    return img


def render_degradation_matrix(
    input_dir: Path = OUTPUT_DIR,
    output_dir: Path = DATA_DIR / "degraded_test_images",
    num_samples: int = 5,
) -> dict[str, list[str]]:
    """
    Renders degraded image pairs for benchmarking pipeline resilience.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    images = sorted(list(input_dir.glob("rx-tc-*.png")))[:num_samples]
    matrix_index: dict[str, list[str]] = {}

    for deg in DEGRADATION_TYPES:
        matrix_index[deg] = []
        deg_dir = output_dir / deg
        deg_dir.mkdir(parents=True, exist_ok=True)
        for img_path in images:
            with Image.open(img_path) as im:
                degraded = apply_degradation(im, degradation_type=deg, severity=1.0)
                out_path = deg_dir / img_path.name
                degraded.save(out_path, format="PNG", optimize=True)
                matrix_index[deg].append(str(out_path.relative_to(PROJECT_ROOT)))

    index_file = output_dir / "matrix_index.json"
    with open(index_file, "w", encoding="utf-8") as f:
        json.dump(matrix_index, f, indent=2)

    print(f"Rendered degradation matrix for {len(images)} images across {len(DEGRADATION_TYPES)} types to {output_dir}")
    return matrix_index


if __name__ == "__main__":
    render_all_test_cases()
    render_degradation_matrix()
