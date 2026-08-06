"""crystalium#47 -- corpus-scaling gate (W-G-CORPUS).

crystalium-residual-eight-plan (spec.md Sec 4 #47, Sec 5.1) -- **#47 is
already ruled NOT closable as specified.** This module builds the GATE
only: deterministic evidence that `candidate_k` per-layer fetch-width
truncation drops a planted ground-truth record once the corpus exceeds it.
It does NOT fix `retrieve.py`, and it does NOT write a scaling law into
`candidate_k` -- Sec 5.1 is binding: a synthetic fixture's ground truth is
STIPULATED, so it cannot adjudicate between `k*3`, `k*5`, `c*log(n)`, etc.
Any change here that hardcodes a candidate_k formula is out of scope; see
Sec 5.1's "Explicitly rejected" paragraph (the v1.11.0 failure this plan
exists to not repeat).

Deconfounding (spec.md Sec 0.2): `retrieve.py:521-548` is a SINGLE loop
that both (a) iterates layers in `_ALL_LAYERS` order and (b) truncates each
per-layer fetch at `candidate_k` -- so layer-major append (#45) and
`candidate_k` truncation (#47) produce the SAME observable symptom (a
ground-truth-relevant record missing from the fused head) via TWO different
causes. This gate pins the OTHER axis non-binding and ASSERTS it, per
R-CONF (`evals/_corpus_rig.py::resolve_verdict`):
  - single layer (`layers=["episodic"]`) -- makes layer-major ordering
    structurally impossible as a cause;
  - graph store edgeless (`len(all_edges()) == 0`) AND populated
    (`node_count() > 0` -- proves "genuinely edgeless", not "kuzu blew up"
    or "nothing was ever seeded", K-B16);
  - completion off.
If any of those checks fails, the gate returns `verdict: "confounded"` and
NO numbers (`assemble_result`) -- a gate that emits numbers with a live
confound is the defect, not the evidence.

FTS5 is implicit-AND (measured on the pinned build, sqlite 3.46.1; see
`relational.py::_fts5_query` -- each token is quoted and space-joined,
FTS5's bareword default is AND across terms). A crystal matching a STRICT
SUBSET of the query terms does not match AT ALL -- so record separation
cannot come from "some vs all" term membership (that mistake made the
original #52 fixture unbuildable, spec.md). Every crystal minted here
matches ALL FOUR query terms; rank separation comes from term FREQUENCY and
document LENGTH instead:
  - each "noise" competitor repeats the full query phrase `_NOISE_REPEATS`
    (2) times, then adds a short unique tail -- high term frequency, short
    document;
  - the ONE planted ground-truth record contains each query term exactly
    ONCE, inside a longer sentence -- low term frequency AND a length
    penalty.
Measured directly against the pinned FTS5/BM25 implementation (not assumed):
with `_NOISE_REPEATS = 2`, EVERY noise competitor outranks the planted
record, deterministically, for any noise-competitor count -- no
PYTHONHASHSEED dependence (SQLite's `bm25()` is a pure function of the
indexed content, unlike the graph arm's hash-order frontier walk other
gates in this campaign contend with).

Two corpus sizes, one shared fixture builder (`_build_fixture` /
`run_probe`):
  - `run()` -- AC-314: `M = 60` (59 noise + 1 planted) against `k=10`
    (`candidate_k = max(k*3, FETCH_WIDTH_FLOOR) = 30`, spec.md Sec 4's own
    worked example). `M > candidate_k`, so `bm25_search(..., k=candidate_k)`
    LIMITs the sparse arm to the top 30 -- the planted record, ranked last
    among all 60 by construction, is never even fetched. RED today
    (`planted_recovered: false`) -- this is the open #47 defect.
  - `run_small_corpus_control()` -- AC-315, the RED-CHECK (normative pytest
    node `test_small_corpus_control_recovers_planted`): `M = 5` (4 noise +
    1 planted), well under BOTH `candidate_k` (30) AND the final response
    cap `k` (10) -- so neither the arm-level LIMIT nor the post-fusion
    k-gate (`retrieve.py` seam 3/4b) can independently exclude the planted
    record, and the control isolates `candidate_k` truncation as the ONLY
    variable between the two runs. If this control is RED too, the gate is
    not measuring truncation -- STOP S-7, #47 closes WONTFIX (spec.md
    Sec 4, Sec 6) -- report immediately, do not tune the fixture to force a
    pass.

`FETCH_WIDTH_FLOOR` is read, never written (AC-316; spec.md Sec 4's own
constraint: "a corpus-dependent floor makes the ranking universe drift with
unrelated commits"). No monkeypatch, no `try/finally` restore dance here --
there is nothing to restore.
"""

from __future__ import annotations

import os
import uuid
from typing import Any

from evals._corpus_rig import (
    arm_liveness,
    assemble_result,
    build_aetheryte,
    crystal,
    graph_liveness,
    new_stores,
    resolve_verdict,
    seed_graph,
    seed_relational,
    stamp_sequence,
    stub_vector_store,
)

_PROJECT = "corpus-scaling-gate"
_AGENT_CLASS = "backend"
_QUERY = "zelqorin vastheme cindrala outpost"
_LAYERS = ["episodic"]  # single layer -- asserted below, per spec.md Sec 0.2

# Repeats of the full query phrase inside each noise competitor's summary.
# Measured (not assumed) against the pinned FTS5/BM25 build: with REPS=2,
# every noise competitor's bm25() score beats the planted record's (which
# contains each term exactly once) regardless of corpus size -- see module
# docstring. REPS=1 already wins on document-length normalisation alone in
# a 6-crystal probe, but REPS=2 gives a larger, more robust margin at the
# 60-crystal scale AC-314 uses.
_NOISE_REPEATS = 2

_PLANTED_ID = "planted"

# AC-314 (spec.md Sec 4's own worked example): at k=10, candidate_k =
# max(10*3, FETCH_WIDTH_FLOOR) = 30, so M ~= 60 already reaches the "M >
# candidate_k" regime. 59 noise competitors + 1 planted record = 60.
_LARGE_NOISE_COUNT = 59

# AC-315 red-check: well under BOTH candidate_k (30) and the final response
# cap k (10) -- see module docstring for why the second bound matters.
_SMALL_NOISE_COUNT = 4


def _build_fixture(relational: Any, graph: Any, *, noise_count: int) -> tuple[str, int]:
    """Mint one planted ground-truth crystal plus `noise_count` noise
    competitors into `relational` (real RelationalStore, real FTS5/BM25)
    and `graph` (real GraphStore, node-seeded only -- no edges, so the
    fixture is provably edgeless per K-B16 rather than merely unpopulated).

    Every crystal matches all four `_QUERY` terms (FTS5 implicit-AND, see
    module docstring) -- the planted record is never excluded by MEMBERSHIP,
    only by BM25 rank once the corpus exceeds `candidate_k`.

    Returns `(planted_id, total_crystal_count)`.
    """
    ts = stamp_sequence()

    planted_summary = (
        f"{_QUERY} planted ground truth record retained for the corpus scaling gate"
    )
    crystals = [
        crystal(
            _PLANTED_ID, "episodic", planted_summary,
            project=_PROJECT, agent_class=_AGENT_CLASS, created_at=next(ts),
        )
    ]
    for i in range(1, noise_count + 1):
        summary = (
            (" ".join([_QUERY] * _NOISE_REPEATS))
            + f" noise competitor filler entry number {i}"
        )
        crystals.append(
            crystal(
                f"N{i}", "episodic", summary,
                project=_PROJECT, agent_class=_AGENT_CLASS, created_at=next(ts),
            )
        )

    seed_relational(relational, crystals)
    seed_graph(graph, nodes=[(c["id"], c["layer"]) for c in crystals])

    return _PLANTED_ID, len(crystals)


def run_probe(
    *, noise_count: int, k: int = 10, data_root: str = "/tmp/crystalium-corpus-scaling-gate"
) -> dict[str, Any]:
    """One arm: mint a fixture of `noise_count + 1` crystals, run the REAL
    `Aetheryte.recall()` pipeline (real RelationalStore + real GraphStore +
    a deterministic, EMPTY stub dense arm -- the planted record is invisible
    there too, by construction, never merely by omission), and report
    whether the planted record survived to the recalled ids.

    `candidate_k` is computed with the SAME formula `retrieve.py` uses
    (`max(k*3, FETCH_WIDTH_FLOOR)`, `FETCH_WIDTH_FLOOR` READ off the live
    module, never a local constant -- AC-316 asserts elsewhere that this
    gate never writes to it). The sparse arm is ALSO queried directly here
    (`relational.bm25_search(_QUERY, layer_filter="episodic", k=candidate_k)`)
    -- with a single layer this is byte-for-byte the same call
    `retrieve.py`'s per-layer loop makes on its one iteration, so this is
    not a second, divergent measurement -- it is the SAME mechanism, made
    observable to the gate (the `recall()` pipeline itself has no seam that
    would let a caller read the intermediate per-layer `sparse_ranking`
    back out).
    """
    from crystalium.aetheryte import retrieve as retrieve_mod
    from crystalium.schemas import Scope
    from crystalium.trust import Tier

    os.makedirs(data_root, exist_ok=True)
    tag = f"cg-{noise_count}-{uuid.uuid4().hex[:8]}"
    stores = new_stores(data_root, tag)

    planted_id, corpus_size = _build_fixture(
        stores.relational, stores.graph, noise_count=noise_count
    )

    candidate_k = max(k * 3, retrieve_mod.FETCH_WIDTH_FLOOR)

    # Direct sparse-arm observation -- mirrors retrieve.py:521-530's single-
    # layer loop body exactly (same query, same layer_filter, same
    # candidate_k). This is what proves "the fetch really was censored"
    # (VP-M3) independent of anything downstream (fusion/composition).
    bm25_hits = stores.relational.bm25_search(_QUERY, layer_filter="episodic", k=candidate_k)
    sparse_ranking = [h["id"] for h in bm25_hits]

    # Dense arm pinned EMPTY -- the planted record has no dense presence at
    # all (deliberately, mirrors fusion_gate.py's `target` design): this
    # keeps the dense arm from ever being able to rescue it, so a green
    # `planted_recovered` can only mean the sparse arm carried it through.
    vector_store = stub_vector_store([])

    aetheryte = build_aetheryte(
        cfg=stores.cfg,
        relational=stores.relational,
        vector_store=vector_store,
        graph_store=stores.graph,
        completion=False,
        completion_max_hops=2,
        completion_decay=0.5,
        recall_active_only=True,
        recall_relevance_primary=True,
        recall_weighted_fusion=True,
        fusion_weight_dense=1.0,
        fusion_weight_derived=1.0,
        fusion_sparse_boost_alpha=1.0,
    )
    result = aetheryte.recall(
        Scope(project=_PROJECT, agent_class_visibility=_AGENT_CLASS),
        _QUERY, k, list(_LAYERS), Tier.T1,
    )
    recalled_ids = [r.id for r in result.records]
    planted_recovered = planted_id in recalled_ids

    gl = graph_liveness(stores.graph, expected_node_count=corpus_size, expected_edge_count=0)
    al = arm_liveness(
        dense_ranking=[], expected_dense=0,
        sparse_ranking=sparse_ranking, expected_sparse=min(candidate_k, corpus_size),
    )

    pinned = {
        "single_layer": list(_LAYERS) == ["episodic"],
        "graph_edgeless_and_populated": gl["verdict"] == "measured",
        "sparse_arm_matches_expected_width": al["verdict"] == "measured",
    }
    verdict = resolve_verdict(pinned=pinned)

    liveness = {
        "corpus_size": corpus_size,
        "candidate_k": candidate_k,
        "sparse_arm_size": len(sparse_ranking),
        "layers": list(_LAYERS),
        "edge_count": gl["liveness"]["edge_count"],
        "node_count": gl["liveness"]["node_count"],
        "completion": False,
    }

    return assemble_result(
        verdict=verdict,
        liveness=liveness,
        axes={"planted_recovered": planted_recovered, "recalled_ids": recalled_ids},
        planted_recovered=planted_recovered,
    )


def run(
    *, k: int = 10, data_root: str = "/tmp/crystalium-corpus-scaling-gate"
) -> dict[str, Any]:
    """AC-314 -- the gate itself. `M = 60` (`_LARGE_NOISE_COUNT + 1`) against
    the default `k=10` (`candidate_k = 30`): `M > candidate_k`, so the
    planted record -- ranked last among all 60 by construction -- is
    excluded from the sparse arm's LIMIT-30 fetch and never recalled.
    RED today: `planted_recovered` is `False`. This documents the open #47
    defect; it does not fix it (spec.md Sec 5.1 -- the fix is out of scope
    for this gate and deliberately unspecified)."""
    return run_probe(noise_count=_LARGE_NOISE_COUNT, k=k, data_root=data_root)


def run_small_corpus_control(
    *, k: int = 10, data_root: str = "/tmp/crystalium-corpus-scaling-gate-small"
) -> dict[str, Any]:
    """AC-315 -- the red-check. `M = 5` (`_SMALL_NOISE_COUNT + 1`), well
    under both `candidate_k` (30) and the final response cap `k` (10): no
    truncation anywhere in the pipeline, so the planted record -- last-
    ranked among the 5 by the SAME construction `run()` uses -- still
    surfaces in `result.records` because there is nothing to truncate it
    out. GREEN here is what proves `run()`'s RED is attributable to
    `candidate_k`, not to the fixture being unrecoverable by design.

    Per spec.md Sec 4 / Sec 6 (S-7): if this stays RED, the gate does not
    measure truncation and #47 closes WONTFIX -- report immediately rather
    than widening the corpus to force a pass."""
    return run_probe(noise_count=_SMALL_NOISE_COUNT, k=k, data_root=data_root)


def emit(result: dict[str, Any], out: str) -> None:
    """Rule (g) artifact-freshness wrapper -- delegates to the shared rig's
    `emit()` (stamps `run_nonce`/`tree_sha` by direct subscript against
    `CRYSTALIUM_GATE_NONCE`/`CRYSTALIUM_TREE_SHA`, `KeyError` if either is
    absent; writes ONLY the file, prints NOTHING -- `evals/_corpus_rig.py`
    for the full rationale)."""
    from evals._corpus_rig import emit as _rig_emit

    _rig_emit(result, out)
