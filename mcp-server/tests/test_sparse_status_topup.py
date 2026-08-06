"""crystalium#44 -- status-blind sparse candidate-set top-up (W-44).

D6 (`CHANGE/forge-rulings.md`): this is a REAL production defect, not a
fixture artefact. `_is_active` (`retrieve.py`) returns `True` unconditionally
when `Config.recall_active_only` is `False`, and the `Aetheryte` CONSTRUCTOR
default is `False` -- so a fixture copied blind from the eval template would
exercise dead code. Production wires it `True`: `config.py:347` defaults
`recall_active_only=True` and `server.py:600` / `__main__.py:351` pass it
through unchanged. The `False` at the constructor binds only for DIRECT
construction (evals/tests). So deprecated rows really do consume fetch slots
and starve active hits at default deployment, silently -- every fixture
below therefore pins `recall_active_only=True` and asserts the pin by
READ-BACK off the `Aetheryte` instance (`aetheryte.recall_active_only is
True`), never off the kwargs dict a fixture just wrote (that assertion is a
tautology and cannot fail -- spec.amend-01.md Sec B.4.3 item 2).

D7 (two-commit shape, `forge-rulings.md`): this file lands in two commits.
Commit 1 (this state) adds ONLY `test_prefix_baseline_starves_active_hits`
(AC-345) and `test_topup_recovers_active_hits` (AC-346), no xfail markers,
on the PRE-#44 tree (`retrieve.py` untouched) -- AC-345 is GREEN here
(starvation reproduces) and AC-346 is RED (no top-up exists yet to recover
it). Commit 2 lands the `retrieve.py` fix AND the
`@pytest.mark.xfail(strict=True, ...)` marker on AC-345's node in the SAME
commit -- post-fix AC-345 must report XFAIL (not PASS: XPASS would mean the
starvation regressed and must fail the suite; not FAIL: the characterisation
itself is unchanged, only its relevance is).

Fence: `retrieve.py:605-615` / `:241-242` (`CHANGE/fence-amend.md`, verdict
ALLOW) -- the top-up is caller-side only, reuses the ONE existing
`_is_active` predicate, and never adds a parameter, a new storage method, or
a status predicate to `bm25_search`. This file, plus `retrieve.py`, are the
ONLY files W-44 may touch (`fence-amend.json.authorised_changes`).

Container-first:
  docker compose run --rm crystalium pytest mcp-server/tests/test_sparse_status_topup.py -v
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from evals._corpus_rig import (
    build_aetheryte,
    crystal,
    new_stores,
    seed_relational,
    stamp_sequence,
    stub_vector_store,
)

from crystalium.schemas import Scope
from crystalium.trust import Tier

_PROJECT = "sparse-status-topup"
_AGENT_CLASS = "backend"

# Four fresh nonce terms -- zero lexical overlap with any other gate/test
# module's own corpus in this campaign (separate data root, separate project
# scope in any case; see the grep sweep this file's own maker ran across
# evals/*.py and mcp-server/tests/*.py before picking these).
_QUERY_TERMS = ["obrenthal", "kyzmerith", "duskvane", "ferrolance"]
_QUERY = " ".join(_QUERY_TERMS)

_K = 5
_CANDIDATE_K = max(_K * 3, 10)  # 15, mirrors retrieve.py's FETCH_WIDTH_FLOOR=10
_LAYER = "semantic"
_TARGET_ID = "active-target"


def _summary(label: str, tf: int, pad: int) -> str:
    """All four `_QUERY_TERMS` repeated `tf` times, padded with `label`-
    prefixed non-query nonce tokens to add `pad` extra tokens -- the same
    TF/document-length BM25-rank-separation mechanism D1 measured
    (`CHANGE/kb1-fts5-measurement.txt`) and W-45 already relies on
    (`test_retrieve_layer_merge.py::_summary`)."""
    terms = _QUERY_TERMS * tf
    padding = [f"{label}pad{i:03d}" for i in range(pad)]
    return " ".join([*terms, *padding])


def _build_starved_status_fixture(relational: Any) -> dict[str, Any]:
    """AC-345/346's shared corpus (single-layer path, the simplest fetch
    shape D6's regime needs): `candidate_k` DEPRECATED rows at `tf=6` (no
    padding, the same shape that dominated the K-N12 fixture's episodic
    rows), all strictly better in BM25 than ONE active target at `tf=1`
    (padded to 20 tokens, the same shape as that fixture's semantic
    fillers). `candidate_k + 1` total matching rows -> a plain `k=candidate_k`
    fetch is CENSORED (`len(hits) == candidate_k`) and returns the
    deprecated rows exclusively: the target is starved OUT of the fetch
    entirely, not merely re-ranked."""
    stamps = stamp_sequence()
    crystals: list[dict[str, Any]] = []
    deprecated_ids: list[str] = []
    for i in range(_CANDIDATE_K):
        cid = f"dep{i:02d}"
        deprecated_ids.append(cid)
        crystals.append(
            crystal(
                cid, _LAYER, _summary(cid, 6, 0),
                project=_PROJECT, agent_class=_AGENT_CLASS, created_at=next(stamps),
                status="deprecated",
            )
        )
    crystals.append(
        crystal(
            _TARGET_ID, _LAYER, _summary("target", 1, 16),
            project=_PROJECT, agent_class=_AGENT_CLASS, created_at=next(stamps),
            status="active",
        )
    )
    seed_relational(relational, crystals)
    return {"deprecated_ids": deprecated_ids, "target_id": _TARGET_ID}


def _build_aetheryte_for(stores: Any, *, recall_active_only: bool) -> Any:
    """Every retrieval flag stated explicitly (the rig's own `build_aetheryte`
    contract) -- dense arm pinned EMPTY (`stub_vector_store([])`) so the
    sparse-fetch starvation is the ONLY channel by which the target could
    reach `result.records`, never a dense-arm confound (single-axis
    discipline, spec.md Sec 0.2)."""
    return build_aetheryte(
        cfg=stores.cfg,
        relational=stores.relational,
        vector_store=stub_vector_store([]),
        graph_store=MagicMock(**{"neighbor_expand.return_value": set()}),
        completion=False,
        completion_max_hops=1,
        completion_decay=0.5,
        recall_active_only=recall_active_only,
        recall_relevance_primary=True,
        recall_weighted_fusion=True,
        fusion_weight_dense=1.0,
        fusion_weight_derived=1.0,
        fusion_sparse_boost_alpha=1.0,
    )


# ---------------------------------------------------------------------------
# AC-345 -- REPLACED (K-B5/K-C-N4/K-C-N7/K-C-N8; FORGE D7). Node name
# NORMATIVE (module-level, deliberately NOT class-nested -- see
# `CHANGE/spec.criteria.amend-04.md`'s node-selector sweep).
# ---------------------------------------------------------------------------


def test_prefix_baseline_starves_active_hits(tmp_path: Path) -> None:
    """Commit-1 characterisation: on the PRE-#44 tree, the top
    `candidate_k` BM25 hits are entirely deprecated and the active target
    NEVER reaches `result.records` -- silent starvation, D6's production
    defect. GREEN here (commit 1); XFAIL-marked in commit 2 once the fix
    lands, so this node's own continued PASS becomes the self-enforcing
    regression signal (XPASS -> strict -> suite RED)."""
    stores = new_stores(str(tmp_path / "topup-baseline"), "baseline")
    fixture = _build_starved_status_fixture(stores.relational)
    aetheryte = _build_aetheryte_for(stores, recall_active_only=True)
    # VP-M4's rule (spec.amend-01.md Sec B.4.3 item 2): read the pin back off
    # the INSTANCE, never off the kwargs dict just written above.
    assert aetheryte.recall_active_only is True

    result = aetheryte.recall(
        Scope(project=_PROJECT, agent_class_visibility=_AGENT_CLASS),
        _QUERY, _K, [_LAYER], Tier.T1,
        explain=True,
    )
    retrieved = [r.id for r in result.records]
    assert fixture["target_id"] not in retrieved, retrieved

    # Positive-capability check (rule (f) discipline): prove the fetch was
    # genuinely censored, not merely absent-by-accident -- LIMIT candidate_k
    # over candidate_k+1 total matches returns EXACTLY candidate_k rows.
    fusion = (result.explain or {}).get("fusion", {})
    assert fusion.get("raw_n_sparse") == _CANDIDATE_K, fusion
    assert fusion.get("n_sparse_cap") == _CANDIDATE_K, fusion


# ---------------------------------------------------------------------------
# AC-346 -- UNCHANGED-BUT-RE-ANCHORED (FORGE D6). Node name NORMATIVE.
# ---------------------------------------------------------------------------


def test_topup_recovers_active_hits(tmp_path: Path) -> None:
    """Post-#44, the SAME corpus recovers the active target into
    `result.records`. RED on the pre-fix tree (this commit); GREEN once the
    `retrieve.py` top-up lands in commit 2."""
    stores = new_stores(str(tmp_path / "topup-recover"), "recover")
    fixture = _build_starved_status_fixture(stores.relational)
    aetheryte = _build_aetheryte_for(stores, recall_active_only=True)
    assert aetheryte.recall_active_only is True

    result = aetheryte.recall(
        Scope(project=_PROJECT, agent_class_visibility=_AGENT_CLASS),
        _QUERY, _K, [_LAYER], Tier.T1,
        explain=True,
    )
    retrieved = [r.id for r in result.records]
    assert fixture["target_id"] in retrieved, retrieved
