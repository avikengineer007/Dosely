"""
dosely/scheduler
================
Deterministic medication scheduling engine (zero LLM calls).

Public entry-point::

    from dosely.scheduler import MedicationScheduler, SchedulerConfig
    from dosely.scheduler.models import ScheduleEntry, DrugInput

Submodules
----------
models          — Pure data classes (no I/O, no external deps)
frequency_parser — Normalise raw frequency tokens → list[DoseSlot]
timing_rules    — Resolve DoseSlots → concrete clock times
conflict_checker — Antacid and duplicate-class warnings
scheduler       — Orchestration layer; returns the daily timeline
"""

from dosely.scheduler.models import (
    FOOD_AFTER,
    FOOD_ANY,
    FOOD_BEFORE,
    FOOD_EMPTY,
    FOOD_WITH,
    DoseSlot,
    DrugInput,
    ScheduleEntry,
    SchedulerConfig,
)
from dosely.scheduler.frequency_parser import FrequencyResult, parse_frequency
from dosely.scheduler.timing_rules import apply_default_rule, resolve_slots
from dosely.scheduler.conflict_checker import check_conflicts
from dosely.scheduler.scheduler import MedicationScheduler

__all__ = [
    "FOOD_AFTER",
    "FOOD_ANY",
    "FOOD_BEFORE",
    "FOOD_EMPTY",
    "FOOD_WITH",
    "DoseSlot",
    "DrugInput",
    "ScheduleEntry",
    "SchedulerConfig",
    "FrequencyResult",
    "parse_frequency",
    "apply_default_rule",
    "resolve_slots",
    "check_conflicts",
    "MedicationScheduler",
]

