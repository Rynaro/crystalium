"""Task-clustered paired statistics and conservative claim labels."""

from __future__ import annotations

import hashlib
import json
import math
import random
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

METRICS = ("resolve_rate", "injected_tokens", "distraction_rate", "steps", "total_tokens")


def claim_status(ci: list[float] | None) -> str:
    """Refuse an inference when a finite, ordered 95% interval is missing."""
    if ci is None or len(ci) != 2 or not all(math.isfinite(x) for x in ci) or ci[0] > ci[1]:
        return "no claim"
    return "synthetic-task calibration only; no product claim"


def check_claims(report: dict) -> None:
    """Machine-readable P0-1 guard: reject any claim lacking its metric's CI."""
    for arms in report["strata"].values():
        for result in arms.values():
            for metric in METRICS:
                ci = result.get("delta_resolve_rate_ci95") if metric == "resolve_rate" else None
                expected = claim_status(ci)
                if result["claims"].get(metric) != expected:
                    raise ValueError("unsupported claim for " + metric)


def bootstrap(values: list[float], resamples: int, rng_seed: int) -> list[float] | None:
    """Resample whole task-level paired means, preserving all within-task seeds."""
    if resamples < 1000:
        raise ValueError("at least 1000 bootstrap resamples required")
    if not values:
        return None
    rng = random.Random(rng_seed)
    samples = sorted(mean(rng.choices(values, k=len(values))) for _ in range(resamples))
    return [samples[int((resamples - 1) * 0.025)], samples[int((resamples - 1) * 0.975)]]


def summarize(rows: list[dict], tasks: list[dict], resamples: int, rng_seed: int) -> dict:
    """Report paired seed outcomes, task headroom and anchor intersections."""
    by_arm: dict[str, dict[tuple[str, int], dict]] = defaultdict(dict)
    for row in rows:
        key = (row["task"], row["seed"])
        if key in by_arm[row["arm"]]:
            raise ValueError("duplicate paired sample")
        by_arm[row["arm"]][key] = row
    baseline = by_arm["off"]
    if any(set(samples) != set(baseline) for samples in by_arm.values()):
        raise ValueError("unpaired task/seed samples")
    labels = {}
    strata: dict[str, set[str]] = {"all": {task["id"] for task in tasks}}
    for task in tasks:
        off = [r for (name, _), r in baseline.items() if name == task["id"]]
        successes = sum(r["resolved"] for r in off)
        n = len(off)
        if not n:
            raise ValueError("missing baseline task")
        headroom = "0/4" if successes == 0 else "4/4" if successes == n else "1-3/4"
        # Label the actual denominator for custom seed counts; default is four.
        if n != 4:
            headroom = "0/N" if successes == 0 else "N/N" if successes == n else "partial/N"
        anchor = "anchor" if task["anchor"] else "no_anchor"
        labels[task["id"]] = {
            "off_successes": successes,
            "seeds": n,
            "headroom": headroom,
            "anchor": task["anchor"],
        }
        for group in (headroom, anchor, headroom + ":" + anchor):
            strata.setdefault(group, set()).add(task["id"])
    summaries = {}
    for group, members in sorted(strata.items()):
        arms = {}
        for arm, samples in sorted(by_arm.items()):
            selected = [r for (task, _), r in samples.items() if task in members]
            metrics = {
                "resolve_rate": mean(r["resolved"] for r in selected),
                "injected_tokens": mean(r["injected_tokens"] for r in selected),
                # Fraction of tasks/seeds solved by off but failed by arm; the
                # denominator is all paired samples, not only off successes.
                "distraction_rate": mean(
                    mean(
                        baseline[(r["task"], r["seed"])]["resolved"] and not r["resolved"]
                        for r in selected
                        if r["task"] == task
                    )
                    for task in sorted(members)
                ),
                "steps": mean(r["steps"] for r in selected),
                "total_tokens": (
                    mean(r["total_tokens"] for r in selected)
                    if all(r["total_tokens"] is not None for r in selected)
                    else None
                ),
            }
            deltas = [
                mean(
                    float(r["resolved"]) - float(baseline[(task, seed)]["resolved"])
                    for (name, seed), r in samples.items()
                    if name == task
                )
                for task in sorted(members)
            ]
            ci = bootstrap(deltas, resamples, rng_seed)
            arms[arm] = {
                "task_count": len(members),
                "sample_count": len(selected),
                "metrics": metrics,
                "delta_resolve_rate": mean(deltas),
                "delta_resolve_rate_ci95": ci,
                "claims": {
                    metric: claim_status(ci if metric == "resolve_rate" else None)
                    for metric in METRICS
                },
            }
        summaries[group] = arms
    return {"headroom": labels, "strata": summaries}


def emit(report: dict[str, Any], destination: Path) -> str:
    """Write canonical JSON and readable Markdown; hash excludes wall-clock data."""
    check_claims(report)
    destination.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    digest = hashlib.sha256(payload.encode()).hexdigest()
    (destination / "report.json").write_text(payload)
    lines = [
        "# Coding memory A/B fixture calibration",
        "",
        "Synthetic, planted fixtures with executable tests. No product/default claim.",
        "Solver mode: " + report["mode"] + ".",
        "",
        "Related: [#71](https://github.com/Rynaro/crystalium/issues/71), "
        "[#72](https://github.com/Rynaro/crystalium/issues/72), "
        "[#73](https://github.com/Rynaro/crystalium/issues/73), "
        "[#74](https://github.com/Rynaro/crystalium/issues/74), "
        "[eidolons#635](https://github.com/Rynaro/eidolons/issues/635).",
        "",
        "JSON SHA-256: `" + digest + "`",
        "",
        "Paired task bootstrap: "
        + str(report["bootstrap_resamples"])
        + " resamples; fixed RNG seed "
        + str(report["bootstrap_seed"])
        + ".",
        "",
        "Token cost and steps are descriptive: **no claim** without their own CIs. "
        "Total solver tokens are unavailable in fixture mode. "
        "Distraction = paired off success and arm failure / all paired samples.",
    ]
    for stratum, arms in report["strata"].items():
        lines += [
            "",
            "## " + stratum,
            "",
            "| Arm | Resolved | Δ vs off [95% CI] | Injected tokens "
            "| Distraction | Steps | Total tokens |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
        for arm, result in arms.items():
            m = result["metrics"]
            ci = result["delta_resolve_rate_ci95"]
            interval = "no claim" if ci is None else "[{:.3f}, {:.3f}]".format(*ci)
            lines.append(
                "| {} | {:.3f} | {:.3f} {} | {:.1f} | {:.3f} | {:.1f} | {} |".format(
                    arm,
                    m["resolve_rate"],
                    result["delta_resolve_rate"],
                    interval,
                    m["injected_tokens"],
                    m["distraction_rate"],
                    m["steps"],
                    "unavailable" if m["total_tokens"] is None else str(m["total_tokens"]),
                )
            )
    (destination / "report.md").write_text("\n".join(lines) + "\n")
    return digest
