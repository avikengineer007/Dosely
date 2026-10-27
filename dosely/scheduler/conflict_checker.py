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
from dosely.synonyms import canonical


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


def _product_key(entry: ScheduleEntry) -> str:
    """Distinct product identifier (by prescription line_id if present, else drug name)."""
    if entry.line_id is not None:
        return f"line_{entry.line_id}"
    return entry.drug.strip().lower()


def _entry_ingredients(entry: ScheduleEntry) -> list[str]:
    """Canonical active ingredients present in this entry."""
    if entry.ingredients:
        return [canonical(i) for i in entry.ingredients]
    dc = (entry.drug_class or "").strip().lower()
    if dc and dc != "unknown":
        return [canonical(entry.drug)]
    return []


def _entry_classes(entry: ScheduleEntry) -> set[str]:
    """All therapeutic classes represented by this entry's ingredients or class."""
    classes = set()
    if entry.ingredient_classes:
        for c in entry.ingredient_classes.values():
            if c:
                classes.add(c.strip().lower())
    dc = (entry.drug_class or "").strip().lower()
    if dc:
        classes.add(dc)
    return classes


# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------

def check_conflicts(entries: list[ScheduleEntry]) -> list[ScheduleEntry]:
    """
    Check for conflicts at the ingredient level and annotate entries with warnings.

    Mutates ``entries`` in-place and also returns them for chaining.
    """
    # ------------------------------------------------------------------ #
    # 1. Antacid-spacing check (ingredient level)                         #
    # ------------------------------------------------------------------ #
    for antacid in entries:
        antacid_classes = _entry_classes(antacid)
        has_antacid = bool(antacid_classes & _ANTACID_CLASSES)
        if not has_antacid:
            continue

        for other in entries:
            # Skip doses of the same product
            if _product_key(antacid) == _product_key(other):
                continue

            other_classes = _entry_classes(other)
            has_non_antacid = bool(other_classes - _ANTACID_CLASSES) or not other_classes
            if not has_non_antacid:
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
    # 2. Duplicate drug-class check (ingredient level across products)    #
    # ------------------------------------------------------------------ #
    # Map class -> {product_key -> (display_name, list of entries)}
    class_products: dict[str, dict[str, tuple[str, list[ScheduleEntry]]]] = {}
    for entry in entries:
        pkey = _product_key(entry)
        classes = _entry_classes(entry)
        for dc in classes:
            if dc and dc not in ("other", "unknown"):
                prod_map = class_products.setdefault(dc, {})
                if pkey not in prod_map:
                    prod_map[pkey] = (entry.drug, [])
                prod_map[pkey][1].append(entry)

    for dc, prod_map in class_products.items():
        if len(prod_map) >= 2:
            names = ", ".join(name for name, _ in prod_map.values())
            warn = (
                f"Duplicate drug class '{dc}': {names} are all in the same class. "
                "Taking two medicines of the same class together may increase "
                "side-effect risk — please confirm with your doctor or pharmacist."
            )
            for _, entry_list in prod_map.values():
                for entry in entry_list:
                    if warn not in entry.warnings:
                        entry.warnings.append(warn)

    # ------------------------------------------------------------------ #
    # 3. Duplicate active ingredient check                               #
    # ------------------------------------------------------------------ #
    # Map ingredient -> {product_key -> (display_name, list of entries)}
    ingredient_products: dict[str, dict[str, tuple[str, list[ScheduleEntry]]]] = {}
    for entry in entries:
        pkey = _product_key(entry)
        ings = _entry_ingredients(entry)
        for ing in ings:
            prod_map = ingredient_products.setdefault(ing, {})
            if pkey not in prod_map:
                prod_map[pkey] = (entry.drug, [])
            prod_map[pkey][1].append(entry)

    for ing, prod_map in ingredient_products.items():
        if len(prod_map) >= 2:
            names = ", ".join(name for name, _ in prod_map.values())
            warn = (
                f"Duplicate ingredient '{ing}': {names} contain {ing}. "
                "Taking multiple medicines with the same active ingredient increases the risk "
                "of accidental overdose — please confirm with your doctor or pharmacist."
            )
            for _, entry_list in prod_map.values():
                for entry in entry_list:
                    if warn not in entry.warnings:
                        entry.warnings.append(warn)

    return entries

