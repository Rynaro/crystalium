"""SWE-Bench-CL continual-learning metric axes (W1 Objective 5).

Pure, deterministic functions over an *accuracy matrix* R, where

    R[i][j] = accuracy on task j after the agent has learned tasks 0..i

(the lower-triangular-and-beyond convention from GEM / continual-learning
literature; SWE-Bench-CL adapts it to a task *sequence*). All axes are computed
from R (+ optional tool-use counts) so they are reproducible on the committed
fixture repo. W1 only *measures* — no axis reads the new (unpopulated) schema
fields and nothing here changes an algorithm.

Axes
----
- average_accuracy   : mean of the final row  mean_j R[N-1][j]
- forgetting         : mean_j<N-1 ( max_{i<N-1} R[i][j] - R[N-1][j] )
- backward_transfer  : mean_{j<N-1} ( R[N-1][j] - R[j][j] )      (BWT)
- forward_transfer   : mean_{j>0}  ( R[j-1][j] - baseline[j] )   (FWT)
- tool_use_efficiency: successes / tool_calls                    (separate input)
"""

from __future__ import annotations

from collections.abc import Sequence

Matrix = Sequence[Sequence[float]]


def _validate(R: Matrix) -> int:
    n = len(R)
    if n == 0:
        raise ValueError("accuracy matrix must be non-empty")
    for row in R:
        if len(row) != n:
            raise ValueError(f"accuracy matrix must be square; got row of len {len(row)} for n={n}")
    return n


def average_accuracy(R: Matrix) -> float:
    """Mean accuracy across all tasks after the full sequence (final row mean)."""
    n = _validate(R)
    return sum(R[n - 1][j] for j in range(n)) / n


def forgetting(R: Matrix) -> float:
    """Average forgetting: how much peak accuracy on earlier tasks decayed by the end.

    0.0 for a single-task sequence (nothing to forget). Higher = more forgetting.
    """
    n = _validate(R)
    if n == 1:
        return 0.0
    total = 0.0
    for j in range(n - 1):
        peak = max(R[i][j] for i in range(n - 1))
        total += peak - R[n - 1][j]
    return total / (n - 1)


def backward_transfer(R: Matrix) -> float:
    """BWT: mean influence that learning later tasks had on earlier ones.

    Negative BWT indicates forgetting; positive indicates beneficial backward
    transfer. 0.0 for a single-task sequence.
    """
    n = _validate(R)
    if n == 1:
        return 0.0
    return sum(R[n - 1][j] - R[j][j] for j in range(n - 1)) / (n - 1)


def forward_transfer(R: Matrix, baseline: Sequence[float] | None = None) -> float:
    """FWT: mean accuracy on task j *before* learning it, vs a baseline.

    Uses R[j-1][j] (accuracy on j after learning up to j-1). baseline defaults to
    zeros (a cold agent). 0.0 for a single-task sequence.
    """
    n = _validate(R)
    if n == 1:
        return 0.0
    if baseline is None:
        baseline = [0.0] * n
    if len(baseline) != n:
        raise ValueError("baseline length must equal matrix dimension")
    return sum(R[j - 1][j] - baseline[j] for j in range(1, n)) / (n - 1)


def tool_use_efficiency(successes: float, tool_calls: float) -> float | None:
    """Successful outcomes per tool call. None when no tool calls were made."""
    if tool_calls <= 0:
        return None
    return successes / tool_calls


# ---------------------------------------------------------------------------
# W2 ablation-gate axes (store-snapshot based)
# ---------------------------------------------------------------------------


def promotion_precision(
    promotions: list[dict],
    crystals_by_id: dict[str, dict],
    *,
    outcome_threshold: float = 0.5,
) -> float | None:
    """Fraction of promoted crystals later recalled *usefully*.

    promotions: ledger rows ({"crystal_id": ...}). crystals_by_id maps id -> a
    snapshot dict with access_count + outcome_success_score. "Usefully recalled"
    = recalled at least once (access_count > 0) AND a positive outcome
    (outcome_success_score > threshold). Returns None when nothing was promoted
    (precision undefined — a documented sentinel, not 0.0).
    """
    promoted = {p["crystal_id"] for p in promotions}
    if not promoted:
        return None
    useful = 0
    for cid in promoted:
        c = crystals_by_id.get(cid)
        if not c:
            continue
        oss = c.get("outcome_success_score")
        if c.get("access_count", 0) > 0 and oss is not None and oss > outcome_threshold:
            useful += 1
    return useful / len(promoted)


def high_value_retention(
    crystals: list[dict],
    *,
    evb_threshold: float | None = None,
    high_value_ids: set[str] | None = None,
) -> float | None:
    """Fraction of high-value crystals still active (survived Dream eviction).

    "High value" is identified one of two ways:
      - high_value_ids: a GROUND-TRUTH set (scorer-independent) — the fair A/B
        form, defined identically in both arms. Preferred for the gate.
      - evb_threshold: high = persisted evb >= threshold. Only defined in the EVB
        arm (legacy never persists evb), so NOT a fair cross-arm comparison; kept
        for diagnostics.
    Returns None when there are no high-value members (undefined).
    """
    if high_value_ids is not None:
        members = [c for c in crystals if c.get("id") in high_value_ids]
    elif evb_threshold is not None:
        members = [c for c in crystals if c.get("evb") is not None and c["evb"] >= evb_threshold]
    else:
        raise ValueError("high_value_retention needs high_value_ids or evb_threshold")
    if not members:
        return None
    return sum(1 for c in members if c.get("status") == "active") / len(members)


# ---------------------------------------------------------------------------
# W3 Dream-intelligence gate axes (store-snapshot / consolidation based)
# ---------------------------------------------------------------------------


def consolidation_gain(semantic_active_before: int, semantic_active_after: int) -> int:
    """Net new admitted semantic crystals over a Dream cycle (row-growth, D3).

    The promotions ledger reads ~0 from Dream (consolidation routes to pending
    unless >=k independent witnesses), so gain is measured as the growth in
    count(layer='semantic' AND status='active').
    """
    return int(semantic_active_after) - int(semantic_active_before)


def semantic_drift(consolidations: list[dict]) -> float | None:
    """Fraction of consolidations whose summary does NOT preserve its ground-truth.

    Each item: {"summary": str, "expected_tokens": [str, ...]}. A consolidation
    "drifts" if any expected token is absent from the summary (case-insensitive) —
    a labeled-fact fidelity check. Lower is better. None when no consolidations.
    """
    if not consolidations:
        return None
    drifted = 0
    for c in consolidations:
        summary = (c.get("summary") or "").lower()
        expected = [t.lower() for t in c.get("expected_tokens", [])]
        if not all(tok in summary for tok in expected):
            drifted += 1
    return drifted / len(consolidations)


def useful_context_retention(
    crystals: list[dict],
    key_event_neighbor_ids: set[str],
) -> float | None:
    """Fraction of near-key-event context crystals still active (STC).

    Structurally identical to high_value_retention with a ground-truth id set —
    the context worth keeping around a salient (key) event.
    """
    return high_value_retention(crystals, high_value_ids=key_event_neighbor_ids)


def compression_ratio(source_sizes: list[int], summary_size: int) -> float | None:
    """summary_size / sum(source_sizes). None when there is no source content.

    Ratio ~1 means the consolidation didn't generalize (D5 abstraction diagnostic).
    """
    total = sum(source_sizes)
    if total <= 0:
        return None
    return summary_size / total


def swe_bench_cl_axes(
    R: Matrix,
    *,
    baseline: Sequence[float] | None = None,
    successes: float | None = None,
    tool_calls: float | None = None,
) -> dict[str, float | None]:
    """Compute all SWE-Bench-CL axes from an accuracy matrix (+ optional tool counts)."""
    axes: dict[str, float | None] = {
        "average_accuracy": average_accuracy(R),
        "forgetting": forgetting(R),
        "backward_transfer": backward_transfer(R),
        "forward_transfer": forward_transfer(R, baseline),
    }
    if successes is not None and tool_calls is not None:
        axes["tool_use_efficiency"] = tool_use_efficiency(successes, tool_calls)
    return axes
