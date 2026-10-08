"""
dosely/scheduler/frequency_parser.py
=====================================
Deterministic frequency parser: raw string → list[DoseSlot].

Supported input forms
---------------------
Abbreviations (case-insensitive):
    OD / QD / QHS / MANE / NOCTE   → 1 dose/day
    BD / BID / BDS / Q12H           → 2 doses/day
    TDS / TID / Q8H                 → 3 doses/day
    QID / Q6H                       → 4 doses/day
    HS                              → 1 dose at bedtime
    SOS / PRN / STAT                → as-needed (single as_needed slot)

Numeric pattern ("1-0-1" style):
    Digits separated by dashes or slashes; each position = a meal slot.
    Positions: morning (1), afternoon (2), evening/dinner (3), bedtime (4).
    A '0' in a position means no dose at that time.
    Examples:  "1-0-1" → morning + evening
               "1-1-1" → morning + afternoon + evening
               "1-1-1-1" → morning + afternoon + evening + bedtime

"Every N hours" / "every N hrs":
    Evenly-spaced slots starting from wake time across waking hours.
    "every 4 hours" → 6 doses (wake → sleep window / 4 h).
    "every 8 hours" → up to 3 doses.
    The number of doses is capped at 24 // N.
    Slots use anchor "absolute" with absolute_time pre-computed assuming
    a default 06:30 wake time; timing_rules.py will override with the
    real wake time from SchedulerConfig.

Unknown / unrecognised strings:
    Returns [DoseSlot(anchor="as_needed", label="UNKNOWN")] so the
    caller can always inspect slots[0].label for the raw input.

Public surface
--------------
    parse_frequency(raw: str) -> FrequencyResult
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Optional

from dosely.scheduler.models import (
    FREQ_BD, FREQ_EVERY_N_HOURS, FREQ_HS, FREQ_OD,
    FREQ_PATTERN, FREQ_QID, FREQ_SOS, FREQ_TDS, FREQ_UNKNOWN,
    DoseSlot,
)


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass
class FrequencyResult:
    """Output of parse_frequency()."""
    code: str                   # one of the FREQ_* constants
    slots: list[DoseSlot]       # ordered list of DoseSlots for the day
    doses_per_day: int          # 0 for SOS/unknown
    raw: str                    # original input, preserved verbatim
    is_known: bool = True       # False when input was unrecognised
    every_n_hours: Optional[int] = None   # set only for FREQ_EVERY_N_HOURS
    pattern: Optional[list[int]] = None   # set only for FREQ_PATTERN


# ---------------------------------------------------------------------------
# Regex helpers
# ---------------------------------------------------------------------------

_EVERY_N_HOURS_RE = re.compile(
    r"every\s+(\d+)\s*(?:hours?|hrs?)",
    re.IGNORECASE,
)

# Pattern: digits (including multi-digit) separated by - or /
_NUMERIC_PATTERN_RE = re.compile(
    r"^(\d+)[-/](\d+)(?:[-/](\d+))?(?:[-/](\d+))?$",
    re.IGNORECASE,
)

# Simple abbreviation table (lower-cased key → FREQ_* code)
_ABBREV_MAP: dict[str, str] = {
    # OD variants
    "od": FREQ_OD, "qd": FREQ_OD, "qhs": FREQ_HS,
    "mane": FREQ_OD, "q24h": FREQ_OD,
    # BD variants
    "bd": FREQ_BD, "bid": FREQ_BD, "bds": FREQ_BD, "q12h": FREQ_BD,
    # TDS variants
    "tds": FREQ_TDS, "tid": FREQ_TDS, "q8h": FREQ_TDS,
    # QID variants
    "qid": FREQ_QID, "q6h": FREQ_QID,
    # Bedtime
    "hs": FREQ_HS, "nocte": FREQ_HS,
    # SOS/PRN
    "sos": FREQ_SOS, "prn": FREQ_PRN if False else FREQ_SOS, "stat": FREQ_SOS,
}

# Slot factory helpers -------------------------------------------------------

def _morning() -> DoseSlot:
    return DoseSlot(anchor="morning", label="morning")

def _lunch() -> DoseSlot:
    return DoseSlot(anchor="lunch", label="afternoon")

def _dinner() -> DoseSlot:
    return DoseSlot(anchor="dinner", label="evening")

def _bedtime() -> DoseSlot:
    return DoseSlot(anchor="bedtime", label="bedtime")

def _as_needed(label: str = "as needed") -> DoseSlot:
    return DoseSlot(anchor="as_needed", label=label)


# ---------------------------------------------------------------------------
# Slot lists for each frequency code
# ---------------------------------------------------------------------------

def _slots_for_code(code: str) -> list[DoseSlot]:
    return {
        FREQ_OD:  [_morning()],
        FREQ_BD:  [_morning(), _dinner()],
        FREQ_TDS: [_morning(), _lunch(), _dinner()],
        FREQ_QID: [_morning(), _lunch(), _dinner(), _bedtime()],
        FREQ_HS:  [_bedtime()],
        FREQ_SOS: [_as_needed()],
    }[code]


# ---------------------------------------------------------------------------
# "every N hours" parser
# ---------------------------------------------------------------------------

_DEFAULT_WAKE = time(6, 30)
_DEFAULT_SLEEP = time(22, 30)


def _parse_every_n_hours(n: int) -> list[DoseSlot]:
    """
    Generate evenly-spaced absolute DoseSlots across waking hours.

    Slots are computed with default wake/sleep (06:30–22:30).
    timing_rules.py recomputes them with the user's real SchedulerConfig.
    """
    if n <= 0:
        return [_as_needed("every_? hours")]
    wake_dt  = datetime(2000, 1, 1, _DEFAULT_WAKE.hour,  _DEFAULT_WAKE.minute)
    sleep_dt = datetime(2000, 1, 1, _DEFAULT_SLEEP.hour, _DEFAULT_SLEEP.minute)
    waking_minutes = int((sleep_dt - wake_dt).total_seconds() / 60)
    dose_count = max(1, waking_minutes // (n * 60))   # cap by waking window

    slots = []
    for i in range(dose_count):
        t = (wake_dt + timedelta(hours=n * i)).time()
        slots.append(DoseSlot(anchor="absolute", absolute_time=t,
                              label=f"dose {i + 1}"))
    return slots


# ---------------------------------------------------------------------------
# Numeric pattern parser
# ---------------------------------------------------------------------------

_PATTERN_ANCHORS = ["morning", "lunch", "dinner", "bedtime"]

def _parse_numeric_pattern(digits: list[int]) -> list[DoseSlot]:
    """
    Convert a list of 0/1 digit values to DoseSlots.

    Position 0 → morning, 1 → lunch, 2 → dinner, 3 → bedtime.
    Doses > 1 in a slot are represented as a single slot (pharmacist notation
    sometimes uses 2-0-2 for "two tablets twice daily"; we produce one slot
    and note the count in the label).
    """
    slots: list[DoseSlot] = []
    for pos, count in enumerate(digits):
        if count == 0:
            continue
        anchor = _PATTERN_ANCHORS[pos] if pos < len(_PATTERN_ANCHORS) else "bedtime"
        label  = anchor if count == 1 else f"{anchor} ×{count}"
        slots.append(DoseSlot(anchor=anchor, label=label))
    return slots


# ---------------------------------------------------------------------------
# Main public function
# ---------------------------------------------------------------------------

def parse_frequency(raw: str) -> FrequencyResult:
    """
    Parse a raw frequency string into a FrequencyResult.

    Parameters
    ----------
    raw : str
        The frequency text exactly as written (e.g. "BD", "1-0-1",
        "every 8 hours", "TDS").

    Returns
    -------
    FrequencyResult
        Always returns a valid result; never raises.
        ``is_known=False`` when input is unrecognised.
    """
    if not raw or not raw.strip():
        return FrequencyResult(
            code=FREQ_UNKNOWN,
            slots=[_as_needed("UNKNOWN")],
            doses_per_day=0,
            raw=raw or "",
            is_known=False,
        )

    stripped = raw.strip()

    # ---- 1. Abbreviation lookup (whole token) ----------------------------
    key = stripped.lower()
    if key in _ABBREV_MAP:
        code  = _ABBREV_MAP[key]
        slots = _slots_for_code(code)
        dpd   = len(slots) if code != FREQ_SOS else 0
        return FrequencyResult(code=code, slots=slots,
                               doses_per_day=dpd, raw=raw)

    # ---- 2. "every N hours" ---------------------------------------------
    m = _EVERY_N_HOURS_RE.search(stripped)
    if m:
        n = int(m.group(1))
        slots = _parse_every_n_hours(n)
        return FrequencyResult(
            code=FREQ_EVERY_N_HOURS,
            slots=slots,
            doses_per_day=len(slots),
            raw=raw,
            every_n_hours=n,
        )

    # ---- 3. Numeric pattern (e.g. "1-0-1", "1-1-1-1") ------------------
    pm = _NUMERIC_PATTERN_RE.match(stripped)
    if pm:
        digits = [int(g) for g in pm.groups() if g is not None]
        slots  = _parse_numeric_pattern(digits)
        dpd    = sum(1 for d in digits if d > 0)
        return FrequencyResult(
            code=FREQ_PATTERN,
            slots=slots,
            doses_per_day=dpd,
            raw=raw,
            pattern=digits,
        )

    # ---- 4. Unknown --------------------------------------------------------
    return FrequencyResult(
        code=FREQ_UNKNOWN,
        slots=[_as_needed(f"UNKNOWN:{stripped}")],
        doses_per_day=0,
        raw=raw,
        is_known=False,
    )
