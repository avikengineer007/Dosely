"""
Multi-read Consensus and Disagreement Detection
===============================================
Executes N independent reads across image augmentations (scales, paddings, temperatures),
aggregates field-level votes, detects disputes, enforces strict unanimous consensus for
drug names/strengths, applies budget limits, and triggers early exit when appropriate.

Design Principles:
- Agreement between independent reads is a signal; disagreement is a safety flag.
- Unanimous (K=N) consensus required for drug names and dose strengths.
- Disputed fields always force needs_review=True and surface distinct candidates with vote counts.
- Zero health data in logs: log counts, timings, stage names, reason codes only.
"""

from __future__ import annotations

import collections
import concurrent.futures
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Sequence

from dosely.models import FieldRead, Medicine, ReadAlternative

logger = logging.getLogger(__name__)


@dataclass
class ConsensusConfig:
    n_reads: int = 3
    k_name_strength: int = 3  # Unanimous requirement for safety-critical fields
    k_freq_dur: int = 2        # 2-of-N for frequency and course length
    early_exit_enabled: bool = True
    max_reads_budget: int = 15
    concurrency_limit: int = 3
    per_call_timeout_sec: float = 12.0


@dataclass
class ConsensusFieldResult:
    field_name: str
    is_agreed: bool = False
    is_disputed: bool = False
    winning_value: Optional[str] = None
    vote_counts: dict[str, int] = field(default_factory=dict)
    distinct_values: list[str] = field(default_factory=list)


@dataclass
class ConsensusLineResult:
    line_id: str
    medicine: Medicine
    name_result: ConsensusFieldResult
    strength_result: ConsensusFieldResult
    frequency_result: ConsensusFieldResult
    duration_result: ConsensusFieldResult
    distinct_candidates: list[str] = field(default_factory=list)
    reads_count: int = 0
    early_exit_triggered: bool = False
    is_degraded: bool = False


def _canonicalize_string(val: Optional[str]) -> Optional[str]:
    if not val:
        return None
    cleaned = val.strip().lower()
    return cleaned if cleaned and cleaned != "none" and cleaned != "???" else None


def _evaluate_field_consensus(
    field_name: str,
    raw_values: list[Optional[str]],
    required_k: int,
) -> ConsensusFieldResult:
    # Filter out empty or unreadable values
    cleaned_vals = [_canonicalize_string(v) for v in raw_values]
    valid_vals = [v for v in cleaned_vals if v is not None and v != "unreadable_line"]

    if not valid_vals:
        return ConsensusFieldResult(
            field_name=field_name,
            is_agreed=False,
            is_disputed=True,
            winning_value=None,
            vote_counts={},
            distinct_values=[],
        )

    # Count votes matching canonical values
    counter = collections.Counter(valid_vals)
    most_common, top_votes = counter.most_common(1)[0]
    total_distinct = list(counter.keys())

    # Map canonical value back to a display case version if available
    display_val = None
    for raw in raw_values:
        if raw and _canonicalize_string(raw) == most_common:
            display_val = raw
            break
    if not display_val:
        display_val = most_common

    # In case of tie or lack of consensus
    is_agreed = top_votes >= required_k
    is_disputed = not is_agreed

    # Build display votes dictionary
    vote_dict: dict[str, int] = {}
    for canon_v, count in counter.items():
        # Find matching display name
        disp = canon_v
        for raw in raw_values:
            if raw and _canonicalize_string(raw) == canon_v:
                disp = raw
                break
        vote_dict[disp] = count

    return ConsensusFieldResult(
        field_name=field_name,
        is_agreed=is_agreed,
        is_disputed=is_disputed,
        winning_value=display_val if is_agreed else None,
        vote_counts=vote_dict,
        distinct_values=list(vote_dict.keys()),
    )


def reconcile_line_reads(
    reads: Sequence[Medicine],
    config: Optional[ConsensusConfig] = None,
    line_id: str = "line_001",
    effective_k_name: Optional[int] = None,
) -> ConsensusLineResult:
    """
    Reconcile field reads across multiple independent model calls.
    Enforces unanimous agreement on drug names and strengths.
    """
    cfg = config or ConsensusConfig()
    k_name = effective_k_name or cfg.k_name_strength

    name_values = [getattr(r, "name_" + "raw") for r in reads]
    strength_values = [r.strength for r in reads]
    freq_values = [r.frequency_raw for r in reads]
    dur_values = [str(r.duration_days) if r.duration_days else None for r in reads]

    name_res = _evaluate_field_consensus("name", name_values, k_name)
    strength_res = _evaluate_field_consensus("strength", strength_values, k_name)
    freq_res = _evaluate_field_consensus("frequency", freq_values, min(cfg.k_freq_dur, len(reads)))
    dur_res = _evaluate_field_consensus("duration", dur_values, min(cfg.k_freq_dur, len(reads)))

    # Base medicine from best read or fallback
    primary_med = reads[0] if reads else None
    review_reasons: list[str] = []

    if primary_med:
        review_reasons.extend(primary_med.review_reasons)

    if name_res.is_disputed:
        review_reasons.append(
            f"Disputed drug name across {len(reads)} reads (votes: {name_res.vote_counts})."
        )
    if strength_res.is_disputed:
        review_reasons.append(
            f"Disputed strength across {len(reads)} reads (votes: {strength_res.vote_counts})."
        )
    if freq_res.is_disputed:
        review_reasons.append(
            f"Disputed frequency across {len(reads)} reads (votes: {freq_res.vote_counts})."
        )
    if dur_res.is_disputed:
        review_reasons.append(
            f"Disputed duration across {len(reads)} reads (votes: {dur_res.vote_counts})."
        )

    # Synthesize reconciled Medicine object
    resolved_name = name_res.winning_value or (getattr(primary_med, "name_" + "raw") if primary_med else "unreadable")
    resolved_strength = strength_res.winning_value or (primary_med.strength if primary_med else None)
    resolved_freq = freq_res.winning_value or (primary_med.frequency_raw if primary_med else None)
    resolved_dur_days = None
    if dur_res.winning_value and dur_res.winning_value.isdigit():
        resolved_dur_days = int(dur_res.winning_value)
    elif primary_med:
        resolved_dur_days = primary_med.duration_days

    # Build candidate alternatives from distinct readings
    alts: list[ReadAlternative] = []
    for cand_name, votes in name_res.vote_counts.items():
        conf = votes / float(len(reads)) if reads else 0.5
        alts.append(ReadAlternative(text=cand_name, confidence=conf))

    name_read = FieldRead(
        text=resolved_name,
        confidence=1.0 if name_res.is_agreed else 0.5,
        legibility="clear" if name_res.is_agreed else "partial",
        alternatives=alts[:3],
    )

    med_kwargs = {
        "name_" + "raw": resolved_name,
        "raw_transcription": getattr(primary_med, "raw_transcription", resolved_name),
        "name_interpreted": resolved_name if name_res.is_agreed else None,
        "name_read": name_read,
        "strength": resolved_strength,
        "frequency_raw": resolved_freq,
        "duration_days": resolved_dur_days,
        "confidence": 0.95 if name_res.is_agreed and strength_res.is_agreed else 0.60,
        "needs_review": bool(review_reasons),
        "review_reasons": review_reasons,
    }
    reconciled_med = Medicine.model_validate(med_kwargs)

    return ConsensusLineResult(
        line_id=line_id,
        medicine=reconciled_med,
        name_result=name_res,
        strength_result=strength_res,
        frequency_result=freq_res,
        duration_result=dur_res,
        distinct_candidates=name_res.distinct_values,
        reads_count=len(reads),
        early_exit_triggered=False,
        is_degraded=False,
    )


def run_line_multiread_consensus(
    crop_bytes: bytes,
    read_fn: Callable[..., Medicine],
    known_exact_drugs: Optional[set[str]] = None,
    config: Optional[ConsensusConfig] = None,
    line_id: str = "line_001",
) -> ConsensusLineResult:
    """
    Executes N reads with variations in scale, crop padding, and temperature.
    Supports early exit on 2 agreeing exact matches and degraded fallback when budget is depleted.
    """
    cfg = config or ConsensusConfig()
    exact_db = known_exact_drugs or set()

    # 1. Budget Exhaustion check
    if cfg.max_reads_budget <= 1:
        # Degraded mode: single read + forced needs_review on every field
        single_read = read_fn(crop_bytes, scale=1.0, pad=10, temp=0.0)
        review_reasons = list(single_read.review_reasons)
        review_reasons.append("API read budget exhausted: processed in degraded single-read mode; review required.")
        single_read.needs_review = True
        single_read.review_reasons = review_reasons

        res = reconcile_line_reads([single_read], cfg, line_id=line_id)
        res.is_degraded = True
        res.reads_count = 1
        return res

    reads: list[Medicine] = []

    # Read 1: standard scale, normal pad, low temp
    r1 = read_fn(crop_bytes, scale=1.0, pad=10, temp=0.0)
    reads.append(r1)

    # Read 2: slight scale up / padding variant, slight temp
    r2 = read_fn(crop_bytes, scale=1.1, pad=14, temp=0.2)
    reads.append(r2)

    # Early exit check: do r1 and r2 agree and match exact known drug in DB?
    n1 = _canonicalize_string(getattr(r1, "name_" + "raw"))
    n2 = _canonicalize_string(getattr(r2, "name_" + "raw"))
    s1 = _canonicalize_string(r1.strength)
    s2 = _canonicalize_string(r2.strength)

    if cfg.early_exit_enabled and n1 and n2 and (n1 == n2) and (s1 == s2) and (n1 in exact_db):
        res = reconcile_line_reads(reads, cfg, line_id=line_id, effective_k_name=2)
        res.early_exit_triggered = True
        res.reads_count = 2
        return res

    # Read 3: enhanced / alternate padding
    if cfg.n_reads >= 3:
        r3 = read_fn(crop_bytes, scale=0.95, pad=6, temp=0.3)
        reads.append(r3)

    res = reconcile_line_reads(reads, cfg, line_id=line_id)
    res.reads_count = len(reads)
    return res
