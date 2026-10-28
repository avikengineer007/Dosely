"""
tests/test_geometric_recovery.py
================================
Unit tests verifying recovery of synthetic geometric degradations with known ground truth:
  1. Orientation stage recovers 90-degree rotations (reports angle error).
  2. Deskew stage recovers known tilt angles within <= 0.5 degree error.
  3. Document perspective stage recovers known quadrilateral corners within corner error.
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from dosely.preprocessing import (
    DeskewStage,
    DocumentPerspectiveStage,
    OrientationStage,
    _estimate_skew_angle,
    _rotate_image,
)


def _make_oriented_test_page() -> np.ndarray:
    """Create a 800x500 portrait page with horizontal text lines."""
    img = np.full((800, 500), 250, dtype=np.uint8)
    for y in range(80, 720, 30):
        cv2.line(img, (40, y), (460, y), 20, 2)
    return img


def _make_tilted_lines_page() -> np.ndarray:
    """Create a page with clear horizontal text baseline structure."""
    img = np.full((600, 600), 255, dtype=np.uint8)
    for y in range(80, 520, 35):
        cv2.line(img, (60, y), (540, y), 0, 2)
    return img


class TestGeometricRecovery:
    def test_orientation_stage_recovers_90_degree_rotations(self):
        """OrientationStage detects and recovers 90-degree rotations with 0.0 deg error."""
        upright = _make_oriented_test_page()
        # Rotate 90 degrees clockwise -> landscape (500x800)
        rotated_90 = cv2.rotate(upright, cv2.ROTATE_90_CLOCKWISE)
        assert rotated_90.shape == (500, 800)

        stage = OrientationStage()
        recovered, report = stage.process(rotated_90)

        assert report.applied is True
        assert report.parameters.get("angle_fixed") == 90
        assert recovered.shape == (800, 500)
        # Angle error is 0.0
        angle_error = abs(report.parameters.get("angle_fixed", 0) - 90)
        assert angle_error == 0.0

    @pytest.mark.parametrize("tilt_angle", [-5.5, -3.0, 2.5, 4.5, 6.0])
    def test_deskew_stage_recovers_known_tilt_angles(self, tilt_angle: float):
        """DeskewStage recovers known tilt angles with residual angle error <= 0.5 deg."""
        clean = _make_tilted_lines_page()
        tilted = _rotate_image(clean, tilt_angle)

        stage = DeskewStage()
        deskewed, report = stage.process(tilted)

        assert report.applied is True
        residual_angle = _estimate_skew_angle(deskewed)
        angle_error = abs(residual_angle)
        assert angle_error <= 0.5, f"Residual angle error {angle_error:.3f} deg exceeds 0.5 deg tolerance"

    def test_perspective_stage_recovers_known_quadrilateral_corners(self):
        """DocumentPerspectiveStage recovers known 4 corners with mean corner error <= 3.0 px."""
        canvas = np.full((800, 800), 40, dtype=np.uint8)
        gt_corners = np.float32([
            [150.0, 120.0],
            [650.0, 100.0],
            [680.0, 680.0],
            [130.0, 650.0],
        ])
        cv2.fillPoly(canvas, [gt_corners.astype(np.int32)], 240)

        stage = DocumentPerspectiveStage()
        warped, report = stage.process(canvas)

        assert report.applied is True
        detected_corners = np.array(report.parameters.get("corners", []))
        assert len(detected_corners) == 4

        corner_errors = np.linalg.norm(detected_corners - gt_corners, axis=1)
        mean_corner_error = float(np.mean(corner_errors))
        assert mean_corner_error <= 3.0, f"Mean corner error {mean_corner_error:.2f}px exceeds 3.0px limit"
