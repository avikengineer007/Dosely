"""
Safe Telemetry Engine
=====================
Records aggregated operational metrics with ZERO health data, ZERO PII,
and ZERO content fields. Operates exclusively on counts and rates.
"""

from __future__ import annotations

import collections
import logging
from typing import Any

logger = logging.getLogger(__name__)


class SafeTelemetryRecorder:
    _instance: SafeTelemetryRecorder | None = None

    def __init__(self) -> None:
        self.total_lines: int = 0
        self.type_counts: dict[str, int] = collections.defaultdict(int)
        self.disputed_count: int = 0
        self.cant_tell_count: int = 0
        self.retake_count: int = 0

    @classmethod
    def get_instance(cls) -> SafeTelemetryRecorder:
        if cls._instance is None:
            cls._instance = SafeTelemetryRecorder()
        return cls._instance

    def record_line_event(
        self,
        region_type: str,
        is_disputed: bool = False,
        cant_tell: bool = False,
        retake_requested: bool = False,
    ) -> None:
        """
        Record a line event. All arguments are anonymous counts and booleans.
        No pixels, crops, text, drug names, patient IDs, or file paths are accepted.
        """
        self.total_lines += 1
        self.type_counts[region_type] += 1
        if is_disputed:
            self.disputed_count += 1
        if cant_tell:
            self.cant_tell_count += 1
        if retake_requested:
            self.retake_count += 1

    def get_daily_summary(self) -> dict[str, Any]:
        """Return aggregated rates and counts without content."""
        total = self.total_lines
        disputed_rate = (self.disputed_count / total) if total else 0.0
        cant_tell_rate = (self.cant_tell_count / total) if total else 0.0
        retake_rate = (self.retake_count / total) if total else 0.0

        return {
            "total_lines": total,
            "handwritten_lines": self.type_counts.get("handwritten", 0),
            "printed_lines": self.type_counts.get("printed", 0),
            "mixed_lines": self.type_counts.get("mixed", 0),
            "disputed_rate": disputed_rate,
            "cant_tell_rate": cant_tell_rate,
            "retake_rate": retake_rate,
        }
