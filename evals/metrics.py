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
