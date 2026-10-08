"""
dosely/scheduler/models.py
==========================
Pure data classes used throughout the scheduling engine.

No external I/O, no side-effects — safe to import anywhere.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time
from typing import Optional


# ---------------------------------------------------------------------------
# Enumerations (plain strings kept for JSON-friendliness)
# ---------------------------------------------------------------------------

# Recognised frequency codes (used as string literals, not an Enum, so that
# the parser can produce them without a circular import).
FREQ_OD   = "OD"    # once daily
FREQ_BD   = "BD"    # twice daily
FREQ_TDS  = "TDS"   # three times daily
FREQ_QID  = "QID"   # four times daily
FREQ_HS   = "HS"    # at bedtime
FREQ_SOS  = "SOS"   # as needed / pro re nata
FREQ_EVERY_N_HOURS = "EVERY_N_HOURS"  # e.g. "every 6 hours"
FREQ_PATTERN = "PATTERN"              # e.g. "1-0-1"
FREQ_UNKNOWN = "UNKNOWN"

# Food-relation constants
FOOD_BEFORE = "before_food"
FOOD_AFTER  = "after_food"
FOOD_WITH   = "with_food"
FOOD_EMPTY  = "empty_stomach"
FOOD_ANY    = "any_time"


# ---------------------------------------------------------------------------
# DoseSlot — one administration event within a day
# ---------------------------------------------------------------------------

@dataclass
class DoseSlot:
    """
    A single administration event produced by the frequency parser.

    ``anchor`` names the meal/period the time is relative to.
    Possible values: "morning" | "breakfast" | "lunch" | "dinner" |
                     "bedtime" | "as_needed" | "absolute".

    ``offset_minutes`` is applied to the anchor time to get the final time.
    Positive = after the anchor, negative = before.

    ``absolute_time`` is set when the slot is not relative to any anchor
    (e.g. "every 8 hours" starting from wake time).
    """
    anchor: str                             # see docstring above
    offset_minutes: int = 0                # relative to anchor
    absolute_time: Optional[time] = None   # set when anchor == "absolute"
    label: str = ""                        # human-readable label, e.g. "morning"

    def __repr__(self) -> str:
        if self.anchor == "absolute" and self.absolute_time:
            return f"DoseSlot(absolute={self.absolute_time.strftime('%H:%M')})"
        return f"DoseSlot(anchor={self.anchor!r}, offset={self.offset_minutes:+d}min)"


# ---------------------------------------------------------------------------
# SchedulerConfig — user's daily timetable
# ---------------------------------------------------------------------------

@dataclass
class SchedulerConfig:
    """
    User's personal daily timetable.  All fields are ``datetime.time`` objects.

    Defaults approximate a typical South-Asian day.
    """
    wake_time:      time = field(default_factory=lambda: time(6, 30))
    breakfast_time: time = field(default_factory=lambda: time(8, 0))
    lunch_time:     time = field(default_factory=lambda: time(13, 0))
    dinner_time:    time = field(default_factory=lambda: time(20, 0))
    sleep_time:     time = field(default_factory=lambda: time(22, 30))

    def __post_init__(self) -> None:
        # Validate ordering (wrap-around sleep past midnight not supported here)
        pairs = [
            ("wake_time",      "breakfast_time"),
            ("breakfast_time", "lunch_time"),
            ("lunch_time",     "dinner_time"),
            ("dinner_time",    "sleep_time"),
        ]
        for earlier, later in pairs:
            t_e: time = getattr(self, earlier)
            t_l: time = getattr(self, later)
            if t_e >= t_l:
                raise ValueError(
                    f"SchedulerConfig: {earlier} ({t_e}) must be before "
                    f"{later} ({t_l})."
                )


# ---------------------------------------------------------------------------
# DrugInput — caller-supplied prescription line
# ---------------------------------------------------------------------------

@dataclass
class DrugInput:
    """
    A single drug entry supplied by the caller.

    ``drug_class`` must be one of the canonical class strings produced by
    DrugMatcher: "analgesic_nsaid" | "antacid_ppi" | "antibiotic" |
    "antihistamine" | "other" | "unknown".

    ``food_relation`` accepts the FOOD_* constants or None.
    ``frequency_raw`` accepts the raw prescription text (e.g. "BD", "1-0-1").

    When ``frequency_raw`` is None *and* drug_class is one of the four
    classes that have built-in defaults, the scheduler applies the default
    rule and tags the entry with ``source="default"`` and ``suggested=True``.
    """
    name: str
    drug_class: str
    frequency_raw: Optional[str] = None
    food_relation: Optional[str] = None     # FOOD_* constant or None
    # Arbitrary extra context the caller may pass through
    extra: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# ScheduleEntry — one row in the returned daily timeline
# ---------------------------------------------------------------------------

@dataclass
class ScheduleEntry:
    """
    A single row in the daily medication timeline returned by the scheduler.

    ``time``        : resolved administration time as HH:MM string.
    ``drug``        : canonical drug name.
    ``instruction`` : human-readable sentence (food relation + timing).
    ``source``      : "prescription" | "default".
    ``suggested``   : True only when source == "default".
    ``warnings``    : list of warning strings (may be empty).
    ``drug_class``  : propagated from DrugInput for conflict checking.
    """
    time:        str          # "HH:MM"
    drug:        str
    instruction: str
    source:      str          # "prescription" | "default"
    suggested:   bool
    warnings:    list[str] = field(default_factory=list)
    drug_class:  str = ""

    def to_dict(self) -> dict:
        return {
            "time":        self.time,
            "drug":        self.drug,
            "instruction": self.instruction,
            "source":      self.source,
            "suggested":   self.suggested,
            "warnings":    list(self.warnings),
            "drug_class":  self.drug_class,
        }

    def __getitem__(self, key: str):
        if hasattr(self, key):
            return getattr(self, key)
        raise KeyError(key)

    def get(self, key: str, default=None):
        return getattr(self, key, default)

    def keys(self):
        return ["time", "drug", "instruction", "source", "suggested", "warnings", "drug_class"]
