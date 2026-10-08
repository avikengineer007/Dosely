"""
dosely/scheduler/scheduler.py
==============================
Orchestration layer: combines frequency_parser, timing_rules, and
conflict_checker to produce a daily medication timeline.

Usage
-----
    from dosely.scheduler import MedicationScheduler, SchedulerConfig
    from dosely.scheduler.models import DrugInput, FOOD_AFTER

    config = SchedulerConfig(
        wake_time=time(7, 0),
        breakfast_time=time(8, 0),
        lunch_time=time(13, 0),
        dinner_time=time(19, 30),
        sleep_time=time(22, 0),
    )
    scheduler = MedicationScheduler(config)

    drugs = [
        DrugInput("Omeprazole", "antacid_ppi", frequency_raw="OD", food_relation="before_food"),
        DrugInput("Amoxicillin", "antibiotic",  frequency_raw="TDS", food_relation="after_food"),
    ]
    timeline = scheduler.schedule(drugs)
    # → [ScheduleEntry(time="07:30", drug="Omeprazole", ...), ...]

Timeline shape
--------------
    list[ScheduleEntry]  sorted by time (ascending), as-needed slots last.

Source tagging
--------------
    source="prescription" when frequency_raw was supplied.
    source="default"      when frequency_raw was None (default rule applied).
    suggested=True        only when source="default".
"""

from __future__ import annotations

import logging
from datetime import time
from typing import Optional

from dosely.scheduler.conflict_checker import check_conflicts
from dosely.scheduler.frequency_parser import FrequencyResult, parse_frequency
from dosely.scheduler.models import (
    FREQ_SOS, FREQ_UNKNOWN,
    DrugInput, ScheduleEntry, SchedulerConfig,
)
from dosely.scheduler.timing_rules import apply_default_rule, resolve_slots

logger = logging.getLogger(__name__)


class MedicationScheduler:
    """
    Deterministic medication scheduling engine.

    Parameters
    ----------
    config : SchedulerConfig
        User's daily timetable (wake, breakfast, lunch, dinner, sleep times).
    """

    def __init__(self, config: Optional[SchedulerConfig] = None) -> None:
        self._config = config or SchedulerConfig()

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def schedule(self, drugs: list[DrugInput]) -> list[ScheduleEntry]:
        """
        Build the daily medication timeline for the given list of drugs.

        Parameters
        ----------
        drugs : list[DrugInput]
            Each entry represents one prescription line.

        Returns
        -------
        list[ScheduleEntry]
            Sorted by time (ascending), as-needed entries appended at the end.
            Warnings are populated by conflict_checker.
        """
        all_entries: list[ScheduleEntry] = []

        for drug in drugs:
            if drug.frequency_raw is not None:
                entries = self._from_prescription(drug)
            else:
                entries = self._from_default(drug)
            all_entries.extend(entries)

        # Sort: timed entries by HH:MM, as-needed at the end
        timed     = [e for e in all_entries if e.time != "as needed"]
        as_needed = [e for e in all_entries if e.time == "as needed"]
        timed.sort(key=lambda e: e.time)  # lexicographic works for HH:MM

        sorted_entries = timed + as_needed

        # Run conflict checks (mutates in-place)
        check_conflicts(sorted_entries)

        return sorted_entries

    # ------------------------------------------------------------------
    # Private: prescription-driven scheduling
    # ------------------------------------------------------------------

    def _from_prescription(self, drug: DrugInput) -> list[ScheduleEntry]:
        """Schedule a drug whose frequency_raw is present."""
        freq: FrequencyResult = parse_frequency(drug.frequency_raw)  # type: ignore[arg-type]

        if freq.code in (FREQ_SOS, FREQ_UNKNOWN):
            return [ScheduleEntry(
                time="as needed",
                drug=drug.name,
                instruction="Take as needed" if freq.code == FREQ_SOS
                            else f"Frequency '{drug.frequency_raw}' unrecognised — consult prescriber",
                source="prescription",
                suggested=False,
                drug_class=drug.drug_class,
            )]

        time_instrs = resolve_slots(
            freq.slots,
            self._config,
            drug.food_relation,
            every_n_hours=freq.every_n_hours,
        )

        return [
            ScheduleEntry(
                time=self._fmt(t),
                drug=drug.name,
                instruction=instr,
                source="prescription",
                suggested=False,
                drug_class=drug.drug_class,
            )
            for t, instr in time_instrs
        ]

    # ------------------------------------------------------------------
    # Private: default-rule scheduling
    # ------------------------------------------------------------------

    def _from_default(self, drug: DrugInput) -> list[ScheduleEntry]:
        """
        Schedule a drug using built-in class-based default rules.

        All entries produced here carry source="default", suggested=True.
        """
        time_instrs = apply_default_rule(
            drug.drug_class,
            drug.name,
            self._config,
        )

        entries = []
        for t, instr in time_instrs:
            entries.append(ScheduleEntry(
                time=self._fmt(t),
                drug=drug.name,
                instruction=instr,
                source="default",
                suggested=True,
                drug_class=drug.drug_class,
            ))

        if not entries:
            # Should never happen; safety net
            entries.append(ScheduleEntry(
                time=self._fmt(self._config.wake_time),
                drug=drug.name,
                instruction="Take as directed by your doctor",
                source="default",
                suggested=True,
                drug_class=drug.drug_class,
            ))

        return entries

    # ------------------------------------------------------------------
    # Helper
    # ------------------------------------------------------------------

    @staticmethod
    def _fmt(t: time) -> str:
        return t.strftime("%H:%M")
