"""Deterministic W5 pattern-separation (dedup-merge) ablation gate.

Commits a base fact plus several near-duplicate paraphrases of it and a few
genuinely distinct facts, in two arms — blind-append (write_dedup_merge off) vs
dedup-merge (on). Measures:

  write_amplification — stored rows / logical commits (merges drive this DOWN)
  precision_held      — the canonical fact is still recalled in BOTH arms

Honest ablation (D6.4-iii): dedup flips on only if write amplification drops AND
recall precision is not regressed (the merged crystal is still found). Requires a
real embedder (paraphrase similarity > sep_threshold); reports "inconclusive"
when the vector store is the null stub. Template = forgetting_gate.py.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from evals.metrics import write_amplification

_T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
_PROJECT = "dedup-gate"

# A base fact + paraphrases (near-duplicates) + distinct facts.
_BASE = "authentication uses argon2id for password hashing"
_PARAPHRASES = [
    "passwords are hashed with argon2id during authentication",
    "we hash passwords using argon2id in the auth flow",
    "argon2id is the password hashing algorithm for auth",
]
_DISTINCT = [
    "the billing service runs nightly reconciliation jobs",
    "frontend uses a virtualized list for the inbox",
]


def _commit(layer, summary: str, tier) -> dict[str, Any]:
    return layer.commit(
        payload={"summary": summary, "scope": {"project": _PROJECT}},
        provenance={"source": "verified_agent", "author_agent": "dg",
                    "created_at": _T0.isoformat()},
        caller_tier=tier,
    )


def run_arm(*, dedup_merge: bool, data_root: str) -> dict[str, Any]:
    from pathlib import Path

    from crystalium.config import Config
    from crystalium.schemas import Scope
    from crystalium.server import _build_components
    from crystalium.trust import Tier

    cfg = Config(
        data_dir=Path(data_root) / f"dg-{int(dedup_merge)}-{uuid.uuid4().hex[:8]}",
        write_dedup_merge=dedup_merge,
        sep_threshold=0.92,
        rate_limit_per_minute=1_000_000,
    )
    (_enf, aetheryte, episodic, _sem, _proc, _exec, _gate, _sched, relational) = _build_components(cfg)

    # Detect a usable embedder up front (null stub / SKIP_SLOW -> no dedup possible).
    try:
        embedder_ok = bool(aetheryte.vector_store.embed("probe"))
    except Exception:
        embedder_ok = False

    logical = 0
    merged = 0
    _commit(episodic, _BASE, Tier.T1); logical += 1
    for p in _PARAPHRASES:
        res = _commit(episodic, p, Tier.T1); logical += 1
        if res.get("status") == "merged":
            merged += 1
    for d in _DISTINCT:
        _commit(episodic, d, Tier.T1); logical += 1

    rows = sum(1 for c in relational.list_crystals_with_dynamics()
               if c.get("layer") == "episodic" and c["status"] == "active")

    # Precision-held proxy: the canonical fact is still recallable.
    try:
        result = aetheryte.recall(
            Scope(project=_PROJECT), "argon2id password hashing", 10, None, Tier.T1,
        )
        found = len(result.records) > 0
    except Exception:
        found = False

    return {
        "write_amplification": write_amplification(rows, logical),
        "rows": rows,
        "logical": logical,
        "merged": merged,
        "precision_held": found,
        "embedder_ok": embedder_ok,
    }


def run(*, data_root: str = "/tmp/crystalium-dedup-gate") -> dict[str, Any]:
    import os

    os.makedirs(data_root, exist_ok=True)
    off = run_arm(dedup_merge=False, data_root=data_root)
    on = run_arm(dedup_merge=True, data_root=data_root)

    embedder_ok = off["embedder_ok"] and on["embedder_ok"]
    wa_off = off["write_amplification"]
    wa_on = on["write_amplification"]
    amp_down = wa_off is not None and wa_on is not None and wa_on < wa_off
    precision_held = on["precision_held"] and (on["precision_held"] == off["precision_held"]
                                               or off["precision_held"])
    gate_pass = embedder_ok and amp_down and precision_held
    return {
        "axes": {
            "write_amplification": {"on": wa_on, "off": wa_off},
            "merged_on": on["merged"],
            "precision_held": {"on": on["precision_held"], "off": off["precision_held"]},
        },
        "embedder_ok": embedder_ok,
        "gate_pass": gate_pass,
        "verdict": (
            "INCONCLUSIVE — no usable embedder (null/SKIP_SLOW); write_dedup_merge stays OFF"
            if not embedder_ok else
            "dedup-merge lowers write amplification with precision held — flip write_dedup_merge ON"
            if gate_pass else
            "dedup-merge does NOT lower amplification with precision held — write_dedup_merge stays OFF"
        ),
    }
