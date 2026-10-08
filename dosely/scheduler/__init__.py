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

from dosely.scheduler.models import DrugInput, ScheduleEntry, SchedulerConfig
from dosely.scheduler.scheduler import MedicationScheduler

__all__ = [
    "DrugInput",
    "ScheduleEntry",
    "SchedulerConfig",
    "MedicationScheduler",
]
