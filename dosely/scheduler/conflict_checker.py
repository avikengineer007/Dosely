"""
dosely/scheduler/conflict_checker.py
======================================
Post-scheduling conflict detection.

Checks performed
----------------
1. Antacid spacing:
   Any antacid_ppi drug scheduled within 2 hours (±120 min) of any other
   medicine emits a warning on BOTH affected ScheduleEntries.

2. Duplicate drug class:
   If two or more drugs share the same drug_class (excluding "other" and
   "unknown"), a warning is added to each duplicate entry.

Public surface
--------------
    check_conflicts(entries: list[ScheduleEntry]) -> list[ScheduleEntry]
        Returns the same list with warnings populated in-place.
        (Entries are mutated; the caller may also discard the return value.)
"""

from __future__ import annotations

from datetime import datetime, time
from typing import Sequence

from dosely.scheduler.models import ScheduleEntry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_time(t_str: str) -> datetime | None:
    """Parse 'HH:MM' to a comparable datetime on an arbitrary fixed date."""
    try:
        return datetime.strptime(t_str, "%H:%M").replace(year=2000, month=1, day=1)
    except ValueError:
        return None   # "as needed" or unknown


def _minutes_between(a: str, b: str) -> float | None:
    """
    Return the absolute difference in minutes between two 'HH:MM' strings.
    Returns None if either is unparseable (e.g. as-needed).
    """
    ta = _parse_time(a)
    tb = _parse_time(b)
    if ta is None or tb is None:
        return None
    return abs((ta - tb).total_seconds() / 60.0)


ANTACID_WINDOW_MINUTES: int = 120   # 2-hour window

_ANTACID_CLASSES: frozenset[str] = frozenset({
    "antacid_ppi",
    "antacid",
    "ppi",
})



# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------

def check_conflicts(entries: list[ScheduleEntry]) -> list[ScheduleEntry]:
    """
    Check for conflicts and annotate entries with warnings.

    Mutates ``entries`` in-place and also returns them for chaining.

    Parameters
    ----------
    entries : list[ScheduleEntry]
        The raw daily timeline produced by MedicationScheduler.

    Returns
    -------
    Same list with `.warnings` populated where conflicts exist.
    """
    # ------------------------------------------------------------------ #
    # 1. Antacid-spacing check                                            #
    # ------------------------------------------------------------------ #
    antacid_entries = [e for e in entries if (e.drug_class or "").lower() in _ANTACID_CLASSES]
    non_antacid_entries = [e for e in entries if (e.drug_class or "").lower() not in _ANTACID_CLASSES]

    for antacid in antacid_entries:
        for other in non_antacid_entries:
            if antacid.drug.strip().lower() == other.drug.strip().lower():
                continue
            gap = _minutes_between(antacid.time, other.time)
            if gap is None:
                continue
            if gap <= ANTACID_WINDOW_MINUTES:
                msg = (
                    f"Antacid interaction: '{antacid.drug}' ({antacid.time}) "
                    f"is within 2 hours of '{other.drug}' ({other.time}). "
                    "Antacids may reduce absorption of other medicines — "
                    "consider spacing them ≥2 hours apart."
                )
                if msg not in antacid.warnings:
                    antacid.warnings.append(msg)
                other_msg = (
                    f"Antacid interaction: '{other.drug}' ({other.time}) "
                    f"is within 2 hours of antacid '{antacid.drug}' ({antacid.time}). "
                    "Absorption may be reduced — space ≥2 hours from antacid if possible."
                )
                if other_msg not in other.warnings:
                    other.warnings.append(other_msg)

    # ------------------------------------------------------------------ #
    # 2. Duplicate drug-class check                                       #
    # ------------------------------------------------------------------ #
    class_to_entries: dict[str, list[ScheduleEntry]] = {}
    for entry in entries:
        dc = (entry.drug_class or "").strip().lower()
        if dc and dc not in ("other", "unknown"):
            class_to_entries.setdefault(dc, []).append(entry)

    for dc, group in class_to_entries.items():
        # Deduplicate by drug name to count distinct drugs (not slots)
        distinct_drugs: dict[str, str] = {}
        for e in group:
            distinct_drugs.setdefault(e.drug.strip().lower(), e.drug)

        if len(distinct_drugs) >= 2:
            names = ", ".join(distinct_drugs.values())
            for entry in group:
                warn = (
                    f"Duplicate drug class '{dc}': {names} are all in the same class. "
                    "Taking two medicines of the same class together may increase "
                    "side-effect risk — please confirm with your doctor or pharmacist."
                )
                if warn not in entry.warnings:
                    entry.warnings.append(warn)

    return entries

