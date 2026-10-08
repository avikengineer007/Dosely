"""
tests/test_scheduler.py
========================
Extensive unit tests for the deterministic scheduling engine.

Every rule has at least two unit tests, including boundary and edge cases:
1. Frequency parser (OD, BD, TDS, QID, HS, SOS, 1-0-1 style, every N hours, edge cases)
2. Timing rules & configuration (custom timetable, before/after/with food, empty stomach, ordering validation)
3. Default rules (PPI 30 min before breakfast, NSAID after food, antibiotic evenly spaced across waking hours, sedating antihistamine at bedtime, source="default", suggested=True)
4. Conflict checks (antacid within 2h of another medicine, safe spacing >= 2h, duplicate drug class, single drug with multiple doses)
5. Daily timeline shape and contract ([{time, drug, instruction, source, warnings}])
"""

import pytest
from datetime import time

from dosely.scheduler import (
    FOOD_AFTER,
    FOOD_ANY,
    FOOD_BEFORE,
    FOOD_EMPTY,
    FOOD_WITH,
    DrugInput,
    MedicationScheduler,
    ScheduleEntry,
    SchedulerConfig,
    apply_default_rule,
    check_conflicts,
    parse_frequency,
    resolve_slots,
)
from dosely.scheduler.models import (
    FREQ_BD,
    FREQ_EVERY_N_HOURS,
    FREQ_HS,
    FREQ_OD,
    FREQ_PATTERN,
    FREQ_QID,
    FREQ_SOS,
    FREQ_TDS,
    FREQ_UNKNOWN,
    DoseSlot,
)


# ===========================================================================
# 1. Frequency Parser Tests (Rule 1)
# ===========================================================================

class TestFrequencyParserOD:
    """Rule 1.1: OD normalisation."""

    def test_od_standard_uppercase(self):
        res = parse_frequency("OD")
        assert res.code == FREQ_OD
        assert res.doses_per_day == 1
        assert len(res.slots) == 1
        assert res.slots[0].anchor == "morning"

    def test_od_case_insensitive_and_variants(self):
        for raw in ["od", "Od", "O.D.", "o.d.", "QD", "qd", "MANE", "once daily", "daily"]:
            res = parse_frequency(raw)
            assert res.code == FREQ_OD, f"Failed for variant '{raw}'"
            assert res.doses_per_day == 1
            assert len(res.slots) == 1


class TestFrequencyParserBD:
    """Rule 1.2: BD normalisation."""

    def test_bd_standard(self):
        res = parse_frequency("BD")
        assert res.code == FREQ_BD
        assert res.doses_per_day == 2
        assert len(res.slots) == 2
        assert [s.anchor for s in res.slots] == ["morning", "dinner"]

    def test_bd_variants_and_dotted(self):
        for raw in ["bd", "b.d.", "B.D.", "BID", "bid", "bds", "q12h", "twice daily", "2 times daily"]:
            res = parse_frequency(raw)
            assert res.code == FREQ_BD, f"Failed for variant '{raw}'"
            assert res.doses_per_day == 2
            assert len(res.slots) == 2


class TestFrequencyParserTDS:
    """Rule 1.3: TDS normalisation."""

    def test_tds_standard(self):
        res = parse_frequency("TDS")
        assert res.code == FREQ_TDS
        assert res.doses_per_day == 3
        assert len(res.slots) == 3
        assert [s.anchor for s in res.slots] == ["morning", "lunch", "dinner"]

    def test_tds_variants_and_dotted(self):
        for raw in ["tds", "t.d.s.", "T.D.S.", "TID", "tid", "q8h", "three times daily", "thrice daily"]:
            res = parse_frequency(raw)
            assert res.code == FREQ_TDS, f"Failed for variant '{raw}'"
            assert res.doses_per_day == 3
            assert len(res.slots) == 3


class TestFrequencyParserQID:
    """Rule 1.4: QID normalisation."""

    def test_qid_standard(self):
        res = parse_frequency("QID")
        assert res.code == FREQ_QID
        assert res.doses_per_day == 4
        assert len(res.slots) == 4
        assert [s.anchor for s in res.slots] == ["morning", "lunch", "dinner", "bedtime"]

    def test_qid_variants_and_dotted(self):
        for raw in ["qid", "q.i.d.", "Q.I.D.", "q6h", "four times daily", "4 times daily"]:
            res = parse_frequency(raw)
            assert res.code == FREQ_QID, f"Failed for variant '{raw}'"
            assert res.doses_per_day == 4
            assert len(res.slots) == 4


class TestFrequencyParserHS:
    """Rule 1.5: HS (bedtime) normalisation."""

    def test_hs_standard(self):
        res = parse_frequency("HS")
        assert res.code == FREQ_HS
        assert res.doses_per_day == 1
        assert len(res.slots) == 1
        assert res.slots[0].anchor == "bedtime"

    def test_hs_variants(self):
        for raw in ["hs", "h.s.", "H.S.", "nocte", "bedtime", "at bedtime"]:
            res = parse_frequency(raw)
            assert res.code == FREQ_HS, f"Failed for variant '{raw}'"
            assert res.slots[0].anchor == "bedtime"


class TestFrequencyParserSOS:
    """Rule 1.6: SOS / PRN normalisation."""

    def test_sos_standard(self):
        res = parse_frequency("SOS")
        assert res.code == FREQ_SOS
        assert res.doses_per_day == 0
        assert len(res.slots) == 1
        assert res.slots[0].anchor == "as_needed"

    def test_sos_variants(self):
        for raw in ["sos", "s.o.s.", "S.O.S.", "PRN", "prn", "stat", "as needed"]:
            res = parse_frequency(raw)
            assert res.code == FREQ_SOS, f"Failed for variant '{raw}'"
            assert res.slots[0].anchor == "as_needed"


class TestFrequencyParserPattern:
    """Rule 1.7: '1-0-1' style pattern normalisation."""

    def test_pattern_1_0_1(self):
        res = parse_frequency("1-0-1")
        assert res.code == FREQ_PATTERN
        assert res.doses_per_day == 2
        assert len(res.slots) == 2
        assert [s.anchor for s in res.slots] == ["morning", "dinner"]

    def test_pattern_1_1_1(self):
        res = parse_frequency("1-1-1")
        assert res.code == FREQ_PATTERN
        assert res.doses_per_day == 3
        assert len(res.slots) == 3
        assert [s.anchor for s in res.slots] == ["morning", "lunch", "dinner"]

    def test_pattern_whitespace_and_slashes(self):
        for raw in ["1 - 0 - 1", "1/0/1", "1 / 0 / 1", " 1-0-1 "]:
            res = parse_frequency(raw)
            assert res.code == FREQ_PATTERN, f"Failed for pattern '{raw}'"
            assert res.doses_per_day == 2
            assert [s.anchor for s in res.slots] == ["morning", "dinner"]

    def test_pattern_four_slots_and_single_slots(self):
        res4 = parse_frequency("1-1-1-1")
        assert res4.code == FREQ_PATTERN
        assert res4.doses_per_day == 4
        assert [s.anchor for s in res4.slots] == ["morning", "lunch", "dinner", "bedtime"]

        res_night = parse_frequency("0-0-1")
        assert res_night.doses_per_day == 1
        assert [s.anchor for s in res_night.slots] == ["dinner"]

        res_morn = parse_frequency("1-0-0")
        assert res_morn.doses_per_day == 1
        assert [s.anchor for s in res_morn.slots] == ["morning"]

        res_multi = parse_frequency("2-0-2")
        assert res_multi.doses_per_day == 2
        assert "×2" in res_multi.slots[0].label


class TestFrequencyParserEveryNHours:
    """Rule 1.8: 'every N hours' normalisation."""

    def test_every_8_hours(self):
        res = parse_frequency("every 8 hours")
        assert res.code == FREQ_EVERY_N_HOURS
        assert res.every_n_hours == 8
        assert len(res.slots) >= 2
        for slot in res.slots:
            assert slot.anchor == "absolute"
            assert slot.absolute_time is not None

    def test_every_4_hours_and_variants(self):
        for raw in ["every 4 hours", "every 4 hrs", "every 6 hours", "q4h"]:
            res = parse_frequency(raw)
            assert res.code == FREQ_EVERY_N_HOURS, f"Failed for '{raw}'"
            assert res.every_n_hours in (4, 6)
            assert len(res.slots) >= 2


class TestFrequencyParserEdgeCases:
    """Rule 1.9: Edge cases, empty, unknown inputs."""

    def test_empty_and_whitespace(self):
        for empty_val in ["", "   ", "\t\n"]:
            res = parse_frequency(empty_val)
            assert res.code == FREQ_UNKNOWN
            assert res.is_known is False
            assert res.doses_per_day == 0

    def test_unrecognised_frequency(self):
        for bogus in ["rubbish", "take 5 times maybe", "123-abc"]:
            res = parse_frequency(bogus)
            assert res.code == FREQ_UNKNOWN
            assert res.is_known is False
            assert res.slots[0].anchor == "as_needed"


# ===========================================================================
# 2. Timing Rules & User Configuration (Rule 2)
# ===========================================================================

class TestTimingRulesConfiguration:
    """Rule 2.1: Custom user timetable configuration."""

    def test_custom_user_timetable_shifts_slots(self):
        cfg_early = SchedulerConfig(
            wake_time=time(5, 30),
            breakfast_time=time(7, 0),
            lunch_time=time(12, 0),
            dinner_time=time(19, 0),
            sleep_time=time(21, 30),
        )
        slots = [DoseSlot(anchor="breakfast"), DoseSlot(anchor="dinner")]
        resolved = resolve_slots(slots, cfg_early, food_relation=None)
        times = [t for t, _ in resolved]
        assert times == [time(7, 0), time(19, 0)]

    def test_late_riser_timetable_shifts_slots(self):
        cfg_late = SchedulerConfig(
            wake_time=time(9, 0),
            breakfast_time=time(10, 30),
            lunch_time=time(14, 30),
            dinner_time=time(21, 0),
            sleep_time=time(23, 30),
        )
        slots = [DoseSlot(anchor="breakfast"), DoseSlot(anchor="lunch")]
        resolved = resolve_slots(slots, cfg_late, food_relation=None)
        times = [t for t, _ in resolved]
        assert times == [time(10, 30), time(14, 30)]

    def test_invalid_timetable_ordering_raises_value_error(self):
        with pytest.raises(ValueError, match="wake_time .* must be before breakfast_time"):
            SchedulerConfig(wake_time=time(9, 0), breakfast_time=time(8, 0))

        with pytest.raises(ValueError, match="dinner_time .* must be before sleep_time"):
            SchedulerConfig(dinner_time=time(23, 0), sleep_time=time(22, 0))


class TestTimingRulesFoodRelation:
    """Rule 2.2: Apply food relation (before/after/with food) from prescription."""

    @pytest.fixture
    def config(self):
        return SchedulerConfig(
            wake_time=time(7, 0),
            breakfast_time=time(8, 0),
            lunch_time=time(13, 0),
            dinner_time=time(20, 0),
            sleep_time=time(22, 30),
        )

    def test_before_food_offset_30min_prior(self, config):
        slots = [DoseSlot(anchor="morning"), DoseSlot(anchor="dinner")]
        resolved = resolve_slots(slots, config, food_relation=FOOD_BEFORE)
        times = [t for t, _ in resolved]
        # Morning meal (breakfast 08:00) - 30 min = 07:30
        # Dinner (20:00) - 30 min = 19:30
        assert times == [time(7, 30), time(19, 30)]
        assert "30 minutes before breakfast" in resolved[0][1]
        assert "30 minutes before dinner" in resolved[1][1]

    def test_after_food_offset_30min_post(self, config):
        slots = [DoseSlot(anchor="morning"), DoseSlot(anchor="lunch"), DoseSlot(anchor="dinner")]
        resolved = resolve_slots(slots, config, food_relation=FOOD_AFTER)
        times = [t for t, _ in resolved]
        # Breakfast (08:00) + 30 min = 08:30
        # Lunch (13:00) + 30 min = 13:30
        # Dinner (20:00) + 30 min = 20:30
        assert times == [time(8, 30), time(13, 30), time(20, 30)]
        assert "after breakfast" in resolved[0][1]
        assert "after lunch" in resolved[1][1]
        assert "after dinner" in resolved[2][1]

    def test_with_food_zero_offset(self, config):
        slots = [DoseSlot(anchor="morning"), DoseSlot(anchor="dinner")]
        resolved = resolve_slots(slots, config, food_relation=FOOD_WITH)
        times = [t for t, _ in resolved]
        assert times == [time(8, 0), time(20, 0)]
        assert "with breakfast" in resolved[0][1]
        assert "with dinner" in resolved[1][1]

    def test_empty_stomach_relation(self, config):
        slots = [DoseSlot(anchor="morning")]
        resolved = resolve_slots(slots, config, food_relation=FOOD_EMPTY)
        assert resolved[0][0] == time(7, 30)
        assert "empty stomach" in resolved[0][1]

    def test_any_time_or_none_relation(self, config):
        slots = [DoseSlot(anchor="lunch")]
        resolved_none = resolve_slots(slots, config, food_relation=None)
        resolved_any = resolve_slots(slots, config, food_relation=FOOD_ANY)
        assert resolved_none[0][0] == time(13, 0)
        assert resolved_any[0][0] == time(13, 0)


# ===========================================================================
# 3. Default Rules (Rule 3)
# ===========================================================================

class TestDefaultRulePPI:
    """Rule 3.1: PPIs default to 30 minutes before breakfast, source='default', suggested=True."""

    def test_ppi_default_standard_config(self):
        cfg = SchedulerConfig(breakfast_time=time(8, 0))
        scheduler = MedicationScheduler(cfg)
        drugs = [DrugInput(name="Omeprazole", drug_class="antacid_ppi")]
        timeline = scheduler.schedule(drugs)

        assert len(timeline) == 1
        entry = timeline[0]
        assert entry.time == "07:30"
        assert entry.drug == "Omeprazole"
        assert "30 minutes before breakfast" in entry.instruction
        assert entry.source == "default"
        assert entry.suggested is True

    def test_ppi_default_custom_breakfast(self):
        cfg = SchedulerConfig(
            wake_time=time(7, 30),
            breakfast_time=time(9, 15),
            lunch_time=time(13, 30),
            dinner_time=time(20, 0),
            sleep_time=time(23, 0),
        )
        scheduler = MedicationScheduler(cfg)
        drugs = [DrugInput(name="Pantoprazole", drug_class="antacid_ppi")]
        timeline = scheduler.schedule(drugs)

        assert len(timeline) == 1
        entry = timeline[0]
        assert entry.time == "08:45"
        assert entry.source == "default"
        assert entry.suggested is True


class TestDefaultRuleNSAID:
    """Rule 3.2: NSAIDs default to after food, source='default', suggested=True."""

    def test_nsaid_default_standard_config(self):
        cfg = SchedulerConfig(lunch_time=time(13, 0), dinner_time=time(20, 0))
        scheduler = MedicationScheduler(cfg)
        drugs = [DrugInput(name="Ibuprofen", drug_class="analgesic_nsaid")]
        timeline = scheduler.schedule(drugs)

        assert len(timeline) == 2
        assert [e.time for e in timeline] == ["13:30", "20:30"]
        for entry in timeline:
            assert "after" in entry.instruction.lower()
            assert entry.source == "default"
            assert entry.suggested is True

    def test_nsaid_default_custom_meals(self):
        cfg = SchedulerConfig(
            wake_time=time(6, 0),
            breakfast_time=time(7, 30),
            lunch_time=time(12, 15),
            dinner_time=time(19, 45),
            sleep_time=time(22, 0),
        )
        scheduler = MedicationScheduler(cfg)
        drugs = [DrugInput(name="Diclofenac", drug_class="analgesic_nsaid")]
        timeline = scheduler.schedule(drugs)

        assert len(timeline) == 2
        assert [e.time for e in timeline] == ["12:45", "20:15"]
        assert all(e.source == "default" and e.suggested is True for e in timeline)


class TestDefaultRuleAntibiotic:
    """Rule 3.3: Antibiotics default to evenly spaced intervals across waking hours."""

    def test_antibiotic_default_standard_waking_window(self):
        # 06:30 to 22:30 (16 waking hours) -> 06:30, 14:30, 22:30
        cfg = SchedulerConfig(wake_time=time(6, 30), sleep_time=time(22, 30))
        scheduler = MedicationScheduler(cfg)
        drugs = [DrugInput(name="Amoxicillin", drug_class="antibiotic")]
        timeline = scheduler.schedule(drugs)

        assert len(timeline) == 3
        assert [e.time for e in timeline] == ["06:30", "14:30", "22:30"]
        for entry in timeline:
            assert "evenly spaced" in entry.instruction.lower()
            assert entry.source == "default"
            assert entry.suggested is True

    def test_antibiotic_default_custom_waking_window(self):
        # 08:00 to 20:00 (12 waking hours) -> 08:00, 14:00, 20:00
        cfg = SchedulerConfig(
            wake_time=time(8, 0),
            breakfast_time=time(9, 0),
            lunch_time=time(13, 0),
            dinner_time=time(18, 30),
            sleep_time=time(20, 0),
        )
        scheduler = MedicationScheduler(cfg)
        drugs = [DrugInput(name="Azithromycin", drug_class="antibiotic")]
        timeline = scheduler.schedule(drugs)

        assert len(timeline) == 3
        assert [e.time for e in timeline] == ["08:00", "14:00", "20:00"]
        assert all(e.source == "default" and e.suggested is True for e in timeline)


class TestDefaultRuleAntihistamine:
    """Rule 3.4: Sedating antihistamines default to bedtime; non-sedating to morning."""

    def test_sedating_antihistamine_bedtime(self):
        cfg = SchedulerConfig(sleep_time=time(22, 15))
        scheduler = MedicationScheduler(cfg)
        for sedating_drug in ["Diphenhydramine", "Hydroxyzine", "Chlorpheniramine", "Promethazine"]:
            timeline = scheduler.schedule([DrugInput(name=sedating_drug, drug_class="antihistamine")])
            assert len(timeline) == 1
            entry = timeline[0]
            assert entry.time == "22:15"
            assert "bedtime" in entry.instruction.lower()
            assert entry.source == "default"
            assert entry.suggested is True

    def test_non_sedating_antihistamine_morning(self):
        cfg = SchedulerConfig(wake_time=time(6, 45))
        scheduler = MedicationScheduler(cfg)
        for non_sedating_drug in ["Cetirizine", "Loratadine", "Fexofenadine"]:
            timeline = scheduler.schedule([DrugInput(name=non_sedating_drug, drug_class="antihistamine")])
            assert len(timeline) == 1
            entry = timeline[0]
            assert entry.time == "06:45"
            assert "morning" in entry.instruction.lower()
            assert entry.source == "default"
            assert entry.suggested is True


class TestDefaultRuleExclusivity:
    """Rule 3.5: Default rules are applied ONLY when no frequency is provided."""

    def test_explicit_frequency_overrides_default_ppi(self):
        scheduler = MedicationScheduler()
        drug = DrugInput(name="Omeprazole", drug_class="antacid_ppi", frequency_raw="BD", food_relation=FOOD_BEFORE)
        timeline = scheduler.schedule([drug])

        assert len(timeline) == 2
        for entry in timeline:
            assert entry.source == "prescription"
            assert entry.suggested is False

    def test_explicit_frequency_overrides_default_antibiotic(self):
        scheduler = MedicationScheduler()
        drug = DrugInput(name="Amoxicillin", drug_class="antibiotic", frequency_raw="OD")
        timeline = scheduler.schedule([drug])

        assert len(timeline) == 1
        assert timeline[0].source == "prescription"
        assert timeline[0].suggested is False


# ===========================================================================
# 4. Conflict Checks (Rule 4)
# ===========================================================================

class TestConflictCheckerAntacidSpacing:
    """Rule 4.1 & 4.2: Warn if antacid is within 2 hours of another medicine; no warning if >= 2h."""

    def test_antacid_conflict_when_within_two_hours(self):
        # Antacid at 07:30, Antibiotic at 08:30 (gap = 60 minutes <= 120 minutes)
        entries = [
            ScheduleEntry(time="07:30", drug="Omeprazole", instruction="", source="default", suggested=True, drug_class="antacid_ppi"),
            ScheduleEntry(time="08:30", drug="Amoxicillin", instruction="", source="prescription", suggested=False, drug_class="antibiotic"),
        ]
        check_conflicts(entries)

        assert len(entries[0].warnings) >= 1
        assert len(entries[1].warnings) >= 1
        assert "Antacid interaction" in entries[0].warnings[0]
        assert "Antacid interaction" in entries[1].warnings[0]

    def test_antacid_conflict_exact_boundary_two_hours(self):
        # Gap = exactly 120 minutes (07:30 to 09:30)
        entries = [
            ScheduleEntry(time="07:30", drug="Gelusil", instruction="", source="prescription", suggested=False, drug_class="antacid"),
            ScheduleEntry(time="09:30", drug="Metformin", instruction="", source="prescription", suggested=False, drug_class="other"),
        ]
        check_conflicts(entries)
        assert any("within 2 hours" in w for w in entries[0].warnings)
        assert any("within 2 hours" in w for w in entries[1].warnings)

    def test_antacid_safe_spacing_no_warning(self):
        # Gap = 150 minutes (07:30 to 10:00) > 120 minutes
        entries = [
            ScheduleEntry(time="07:30", drug="Omeprazole", instruction="", source="default", suggested=True, drug_class="antacid_ppi"),
            ScheduleEntry(time="10:00", drug="Amoxicillin", instruction="", source="prescription", suggested=False, drug_class="antibiotic"),
        ]
        check_conflicts(entries)
        assert entries[0].warnings == []
        assert entries[1].warnings == []

    def test_antacid_distinct_drugs_no_self_conflict(self):
        # Same antacid taken twice daily (07:30 and 19:30) does not conflict with itself
        entries = [
            ScheduleEntry(time="07:30", drug="Omeprazole", instruction="", source="prescription", suggested=False, drug_class="antacid_ppi"),
            ScheduleEntry(time="19:30", drug="Omeprazole", instruction="", source="prescription", suggested=False, drug_class="antacid_ppi"),
        ]
        check_conflicts(entries)
        assert entries[0].warnings == []
        assert entries[1].warnings == []


class TestConflictCheckerDuplicateDrugClass:
    """Rule 4.3 & 4.4: Warn on duplicate drug classes across distinct drugs; no warning for same drug."""

    def test_duplicate_nsaid_class_warns_all_entries(self):
        entries = [
            ScheduleEntry(time="13:30", drug="Ibuprofen", instruction="", source="prescription", suggested=False, drug_class="analgesic_nsaid"),
            ScheduleEntry(time="20:30", drug="Diclofenac", instruction="", source="prescription", suggested=False, drug_class="analgesic_nsaid"),
        ]
        check_conflicts(entries)

        assert len(entries[0].warnings) >= 1
        assert len(entries[1].warnings) >= 1
        assert "Duplicate drug class 'analgesic_nsaid'" in entries[0].warnings[0]
        assert "Duplicate drug class 'analgesic_nsaid'" in entries[1].warnings[0]

    def test_duplicate_ppi_class_warns(self):
        entries = [
            ScheduleEntry(time="07:30", drug="Omeprazole", instruction="", source="default", suggested=True, drug_class="antacid_ppi"),
            ScheduleEntry(time="19:30", drug="Pantoprazole", instruction="", source="prescription", suggested=False, drug_class="antacid_ppi"),
        ]
        check_conflicts(entries)
        assert any("Duplicate drug class 'antacid_ppi'" in w for w in entries[0].warnings)
        assert any("Duplicate drug class 'antacid_ppi'" in w for w in entries[1].warnings)

    def test_same_drug_multiple_doses_does_not_warn(self):
        # A patient taking Amoxicillin BD (morning and evening) is ONE drug, NOT a duplicate class
        entries = [
            ScheduleEntry(time="08:30", drug="Amoxicillin", instruction="", source="prescription", suggested=False, drug_class="antibiotic"),
            ScheduleEntry(time="20:30", drug="Amoxicillin", instruction="", source="prescription", suggested=False, drug_class="antibiotic"),
        ]
        check_conflicts(entries)
        assert entries[0].warnings == []
        assert entries[1].warnings == []

    def test_same_drug_three_doses_does_not_warn(self):
        # Paracetamol TDS
        entries = [
            ScheduleEntry(time="08:30", drug="Paracetamol", instruction="", source="prescription", suggested=False, drug_class="analgesic_nsaid"),
            ScheduleEntry(time="13:30", drug="Paracetamol", instruction="", source="prescription", suggested=False, drug_class="analgesic_nsaid"),
            ScheduleEntry(time="20:30", drug="Paracetamol", instruction="", source="prescription", suggested=False, drug_class="analgesic_nsaid"),
        ]
        check_conflicts(entries)
        assert all(len(e.warnings) == 0 for e in entries)


# ===========================================================================
# 5. Daily Timeline Shape & Contract (Rule 5)
# ===========================================================================

class TestDailyTimelineContract:
    """Rule 5: Return daily timeline [{time, drug, instruction, source, warnings}]."""

    def test_timeline_chronological_order(self):
        scheduler = MedicationScheduler()
        drugs = [
            DrugInput(name="Zolpidem", drug_class="other", frequency_raw="HS"),
            DrugInput(name="Amoxicillin", drug_class="antibiotic", frequency_raw="1-0-1", food_relation=FOOD_AFTER),
            DrugInput(name="SOS Painkiller", drug_class="other", frequency_raw="SOS"),
        ]
        timeline = scheduler.schedule(drugs)

        times = [e.time for e in timeline]
        # Timed entries must be sorted in ascending order; as-needed must be at the end
        assert times[-1] == "as needed"
        timed_times = times[:-1]
        assert timed_times == sorted(timed_times)

    def test_timeline_dict_shape_and_keys(self):
        scheduler = MedicationScheduler()
        drugs = [DrugInput(name="Omeprazole", drug_class="antacid_ppi")]
        timeline_entries = scheduler.schedule(drugs)
        entry = timeline_entries[0]

        # Dataclass attribute access
        assert hasattr(entry, "time")
        assert hasattr(entry, "drug")
        assert hasattr(entry, "instruction")
        assert hasattr(entry, "source")
        assert hasattr(entry, "warnings")

        # Dict subscript access
        assert entry["time"] == "07:30"
        assert entry["drug"] == "Omeprazole"
        assert entry["source"] == "default"
        assert isinstance(entry["warnings"], list)

        # schedule_timeline pure dict output
        timeline_dicts = scheduler.schedule_timeline(drugs)
        assert isinstance(timeline_dicts, list)
        d = timeline_dicts[0]
        assert set(["time", "drug", "instruction", "source", "warnings"]).issubset(d.keys())

    def test_end_to_end_complex_prescription(self):
        cfg = SchedulerConfig(
            wake_time=time(6, 30),
            breakfast_time=time(8, 0),
            lunch_time=time(13, 0),
            dinner_time=time(20, 0),
            sleep_time=time(22, 30),
        )
        scheduler = MedicationScheduler(cfg)
        drugs = [
            # 1. PPI with no frequency -> default 30 min before breakfast (07:30)
            DrugInput(name="Pantoprazole", drug_class="antacid_ppi"),
            # 2. Antibiotic with explicit TDS after food -> 08:30, 13:30, 20:30
            DrugInput(name="Amoxicillin", drug_class="antibiotic", frequency_raw="TDS", food_relation=FOOD_AFTER),
            # 3. Sedating antihistamine with no frequency -> default bedtime (22:30)
            DrugInput(name="Diphenhydramine", drug_class="antihistamine"),
            # 4. As-needed drug -> "as needed"
            DrugInput(name="Paracetamol", drug_class="analgesic_nsaid", frequency_raw="SOS"),
        ]
        timeline = scheduler.schedule(drugs)

        assert len(timeline) == 6  # 1 (PPI) + 3 (Amoxicillin) + 1 (Diphenhydramine) + 1 (SOS)

        # Check times
        entry_times = [e.time for e in timeline]
        assert entry_times == ["07:30", "08:30", "13:30", "20:30", "22:30", "as needed"]

        # Conflict check verification:
        # Pantoprazole is at 07:30, Amoxicillin is at 08:30 (gap = 60m <= 120m)
        # Pantoprazole and Amoxicillin first doses should have antacid warnings
        panto_entry = next(e for e in timeline if e.drug == "Pantoprazole")
        amox_entry1 = timeline[1]  # 08:30 Amoxicillin
        assert any("Antacid interaction" in w for w in panto_entry.warnings)
        assert any("Antacid interaction" in w for w in amox_entry1.warnings)

        # Diphenhydramine must be suggested=True and source="default"
        diph_entry = next(e for e in timeline if e.drug == "Diphenhydramine")
        assert diph_entry.source == "default"
        assert diph_entry.suggested is True

        # Amoxicillin must be suggested=False and source="prescription"
        assert amox_entry1.source == "prescription"
        assert amox_entry1.suggested is False
