"""crystalium-residual-eight-plan (W-G-FLOOR / crystalium#48) -- the floor-
sensitivity gate, and FORGE D5's VP-M1 derived-membership probe.

Two DISTINCT measurements live in this module (verification-plan.amend-03.md
Sec 1-3; do not conflate them):

  VP-M1 (`vp_m1_probe` / `vp_m1_pair`) -- answers "does #41's all-seed
  `neighbor_expand` expansion (`graph.py:215-230`, `:305`) still leave the
  SHIPPED `evals/fusion_gate.py` fixture a floor-sensitive derived-arm
  MEMBERSHIP channel?" `evals/fusion_gate.py` stays byte-untouched (Sec 3.1
  freeze) -- imported here, read-only, never edited, never extended.

  VP-M6 (`run_seed` / `aggregate_seeds` / `classify_disjoint`) -- AC-322's
  OWN measurement, on THIS module's own tie-break-neutral, distinct-phantom
  fixture (`_build_gate_fixture`), asking a narrower question: can a NEW
  fixture be built where the floor changes the FUSED RANK deterministically,
  post-#41?

FINDING (measured this pass; `CHANGE/issue-48-mechanism-note.md`,
`CHANGE/vp-m1-interpretation.md`): the shipped fixture's derived-membership
channel is STRUCTURALLY ABSENT post-#41 -- `evals/fusion_gate.py:175-182`
points all three edge-bearing competitors (N1/N2/N3) at the SAME phantom
(Z), and `fetch_width = max(k, FETCH_WIDTH_FLOOR)` (`retrieve.py:562`)
always admits `N1` (`dense_ranking[0]`) at every floor >= 1. Measured (this
module, `shipped_fixture_topology`): `DERIVED_UNION == ['Z']` at floors
2, 10 AND 1000, identically, at every sampled `PYTHONHASHSEED` (0-12,
unset -- 14/14 points, `vp_m1_pair(fixture="shipped")`). This is a property
of the fixture's TOPOLOGY (one phantom, always reached), not of any seed.
AC-138/AC-139, as worded against the SHIPPED fixture, are therefore
UNOBTAINABLE there -- STOP S-5, FORGE D9 class (c): retired with this
mechanism note, not discharged (see the closing comment this unit records).

That finding does NOT mean no floor-sensitive fixture can exist post-#41.
`_build_gate_fixture` builds one with a DISTINCT phantom, placed at a dense
rank BETWEEN the two floors (rank 11, 0-indexed -- outside `[:10]`, inside
`[:15]`) -- so `floor=10` never seeds the edge-bearing competitor and its
phantom is never discovered, while `floor=1000` does. Measured (AC-322,
`run_seed`/`aggregate_seeds`, the 7-point C-2 protocol -- `PYTHONHASHSEED`
0-5 plus one run omitting `-e` entirely): `floor=10` -> `target_rank == 0`
at every one of the 7 seeds; `floor=1000` -> `target_rank == 1` at every
one of the 7 seeds -- DISJOINT (`classify_disjoint` and the criterion's own
independent recomputation agree), and deterministic, because the union
that changes between floors has exactly ONE member (`sorted()` over a 0-
or 1-element set cannot be seed-order-sensitive by construction, not
merely by observation). AC-322 is therefore DISCHARGED, not retired -- the
mechanism gate that was unbuildable on the SHIPPED fixture (S-5) is
buildable on a fixture built for this axis specifically.

FORGE D5's probe construction (spec.amend-01.md Sec B.7.2, extended by
verification-plan.amend-03.md Sec 2.0's `fixture` keyword): the probe wraps
the real `GraphStore` in `_GraphStoreSpy`, a thin recording proxy that
delegates every method and records the return values of `neighbor_expand`/
`decaying_walk` -- where the derived arm's MEMBERSHIP is actually produced.
This NEVER reads `RecallResult.explain`, which carries only
`arm_sizes` (`retrieve.py:1098-1104`) -- SIZES, not MEMBERSHIP. A
self-check (`vp_m1_probe`, fixture="shipped" only) re-runs
`evals.fusion_gate.run_floor_probe` on a fresh data dir and asserts its own
`{target_rank, retrieved}` match -- so a drift in `fusion_gate`'s recall
path invalidates the probe LOUDLY (`self_check_ok: false`) instead of
silently measuring a divergent construction (D5's reversal condition).

Container-first:
  docker compose run --rm crystalium pytest mcp-server/tests/test_floor_sensitivity_gate.py -v
"""

from __future__ import annotations

import os
import uuid
from typing import Any

from evals._corpus_rig import (
    build_aetheryte,
    crystal,
    emit,
    new_stores,
    seed_graph,
    seed_relational,
    stamp_sequence,
    stub_vector_store,
)

_PROJECT = "floor-sensitivity-gate"
_AGENT_CLASS = "backend"
_QUERY = "zqlarnix threxil vandomere gate probe"
_DATA_ROOT = "/tmp/crystalium-floor-sensitivity-gate"

# `_build_gate_fixture`'s layout (spec.md Sec 4 #48's Approach; VP-M6):
# 11 head fillers (dense ranks 0-10) keep the edge-bearing competitor OUTSIDE
# `dense_ranking[:10]` (the shipped `FETCH_WIDTH_FLOOR` default); 3 tail
# fillers pad the fixture out to dense rank 14, i.e. INSIDE `[:15]`, mirroring
# `evals/fusion_gate.py`'s own "[:15] but outside [:10]" placement idiom
# without sharing a single id, phantom, or project scope with it (spec.md
# Sec 0.2's disjoint-fixture rule).
_GATE_HEAD_COUNT = 11
_GATE_TAIL_COUNT = 3


# ---------------------------------------------------------------------------
# The recording proxy (FORGE D5; spec.amend-01.md Sec B.7.2 point 3)
# ---------------------------------------------------------------------------


class _GraphStoreSpy:
    """Thin recording proxy around a real `GraphStore`. Delegates every
    method via `__getattr__`; `neighbor_expand`/`decaying_walk` are
    overridden to RECORD their return values before returning them
    UNCHANGED -- this is where the derived arm's membership is actually
    produced (`retrieve.py:664-717`), so this is where D5 rules the probe
    must look, never `explain` (sizes only, `retrieve.py:1098-1104`).

    Never mutates behaviour: every call is forwarded to the wrapped store
    with identical arguments and its real return value is passed straight
    through. `vp_m1_probe`'s self-check (fixture="shipped") is what proves
    this empirically rather than by assertion (D5's reversal condition).
    """

    def __init__(self, graph: Any) -> None:
        self._graph = graph
        self.neighbor_expand_results: list[set[str]] = []
        self.decaying_walk_results: list[dict[str, float]] = []

    def neighbor_expand(
        self, seed_ids: list[str], depth: int = 1, rel_filter: str | None = None
    ) -> set[str]:
        result = self._graph.neighbor_expand(seed_ids, depth=depth, rel_filter=rel_filter)
        self.neighbor_expand_results.append(set(result))
        return result

    def decaying_walk(
        self, seed_ids: list[str], max_hops: int = 2, decay: float = 0.5
    ) -> dict[str, float]:
        result = self._graph.decaying_walk(seed_ids, max_hops=max_hops, decay=decay)
        self.decaying_walk_results.append(dict(result))
        return result

    def __getattr__(self, name: str) -> Any:
        return getattr(self._graph, name)

    @property
    def derived_union(self) -> list[str]:
        """The derived arm's MEMBERSHIP -- the sorted union of every id the
        walk actually returned, across BOTH `neighbor_expand` and
        `decaying_walk` (spec.amend-01.md Sec B.7.2 point 3's
        `floorN_derived` definition). This is the quantity the mechanism
        note and AC-321/AC-374 are about; it is never read off `explain`."""
        ids: set[str] = set()
        for r in self.neighbor_expand_results:
            ids |= r
        for r in self.decaying_walk_results:
            ids |= set(r.keys())
        return sorted(ids)


# ---------------------------------------------------------------------------
# Fixtures (spec.md Sec 0.2's disjoint-fixture rule: distinct ids, distinct
# phantoms, distinct project scope from every other gate AND from each other)
# ---------------------------------------------------------------------------


def _build_distinct_phantom_fixture(relational: Any, graph: Any) -> tuple[list[str], str]:
    """VP-M1's positive control fixture (AC-357; verification-plan.amend-03.md
    Sec 2.1). ONE phantom PER edge-bearing competitor -- `c1 -> z1`,
    `c2 -> z2`, `c3 -> z3` -- with `c1` inside the low control floor's slice
    (`floor_low=2`) and `c2`/`c3` outside it (a filler sits at dense rank 1
    so `[:2]` reaches ONLY `c1`). The derived union therefore differs across
    the floor boundary BY CONSTRUCTION, independent of hash order: `[:2]`
    reaches only `z1`; `[:1000]` reaches all three. This is what makes the
    control buildable where the SHIPPED fixture's control (floor=2 vs
    floor=1000 on `evals/fusion_gate.py`'s own fixture) was not --
    `_build_fixture:172-182` there points every competitor at the SAME
    phantom, so no floor pair can ever emit `channel_live == true`
    (`CHANGE/issue-48-mechanism-note.md`).
    """
    stamps = stamp_sequence()
    target = crystal(
        "target", "episodic", f"{_QUERY} episodic runbook notes",
        project=_PROJECT, agent_class=_AGENT_CLASS, created_at=next(stamps),
    )
    c1 = crystal(
        "zzc1", "episodic", "dense competitor one, distinct-phantom control",
        project=_PROJECT, agent_class=_AGENT_CLASS, created_at=next(stamps),
    )
    filler = crystal(
        "zzfiller", "episodic", "filler between c1 and c2, distinct-phantom control",
        project=_PROJECT, agent_class=_AGENT_CLASS, created_at=next(stamps),
    )
    c2 = crystal(
        "zzc2", "episodic", "dense competitor two, distinct-phantom control",
        project=_PROJECT, agent_class=_AGENT_CLASS, created_at=next(stamps),
    )
    c3 = crystal(
        "zzc3", "episodic", "dense competitor three, distinct-phantom control",
        project=_PROJECT, agent_class=_AGENT_CLASS, created_at=next(stamps),
    )
    z1 = crystal(
        "zzz1", "episodic", "phantom target one, graph-only, distinct-phantom control",
        project=_PROJECT, agent_class=_AGENT_CLASS, created_at=next(stamps),
    )
    z2 = crystal(
        "zzz2", "episodic", "phantom target two, graph-only, distinct-phantom control",
        project=_PROJECT, agent_class=_AGENT_CLASS, created_at=next(stamps),
    )
    z3 = crystal(
        "zzz3", "episodic", "phantom target three, graph-only, distinct-phantom control",
        project=_PROJECT, agent_class=_AGENT_CLASS, created_at=next(stamps),
    )

    seed_relational(relational, [target, c1, filler, c2, c3, z1, z2, z3])
    seed_graph(
        graph,
        nodes=[("zzc1", "episodic"), ("zzc2", "episodic"), ("zzc3", "episodic"),
               ("zzz1", "episodic"), ("zzz2", "episodic"), ("zzz3", "episodic")],
        edges=[("zzc1", "zzz1", "LINKS_TO"), ("zzc2", "zzz2", "LINKS_TO"),
               ("zzc3", "zzz3", "LINKS_TO")],
    )

    dense_hits = ["zzc1", "zzfiller", "zzc2", "zzc3"]
    return dense_hits, "target"


def _build_gate_fixture(relational: Any, graph: Any) -> tuple[list[str], str]:
    """AC-322's own fixture (spec.md Sec 4 #48's Approach; VP-M6). Ids
    renamed so every competitor sorts AFTER "target" (tie-break-neutral --
    safe: this is a new file, `evals/fusion_gate.py`'s AC-125 fixture is
    untouched, Sec 3.1). ONE distinct phantom (never shared with
    `_build_distinct_phantom_fixture` or the shipped fixture). Single layer
    only (episodic) -- pins layer-major append order out of the picture
    (spec.md Sec 0.2's G-FLOOR row). The edge-bearing competitor sits at
    dense rank 11 (0-indexed) -- OUTSIDE `dense_ranking[:10]` (the shipped
    `FETCH_WIDTH_FLOOR` default) but INSIDE `dense_ranking[:15]`.

    Measured (AC-322, `run_seed`/`aggregate_seeds`, `PYTHONHASHSEED` 0-5
    plus one run omitting `-e`): `floor=10` seeds only the
    11 filler competitors -- no graph edge exists among them, so the
    derived arm is EMPTY and `target` (the sole sparse hit) wins every tie
    against the tied dense-rank-0 filler by sparse-arm insertion order
    (`retrieve.py:82-86`'s `rrf_merge_scored`) -- `target_rank == 0`.
    `floor=1000` additionally seeds the edge-bearing competitor; its
    phantom is discovered via BOTH `neighbor_expand` (graph arm) AND
    `decaying_walk` (completion arm) -- two SEPARATE arms in the UNWEIGHTED
    fusion (`retrieve.py:749-756`; D2's `derived_family_merge` collapse is
    gated behind `recall_weighted_fusion`, off here) -- so the phantom
    scores `2/61`, exceeding `target`'s single-arm `1/61`, and demotes
    `target` to `target_rank == 1`. Both outcomes are deterministic at
    every sampled seed because the union that changes between floors has
    exactly ONE member: `sorted()` over a 0- or 1-element set cannot be
    seed-order-sensitive BY CONSTRUCTION, not merely by observation --
    unlike the pre-#41 "abort channel" (`CHANGE/issue-48-mechanism-note.md`),
    there is no lottery left to sample.
    """
    stamps = stamp_sequence()
    target = crystal(
        "target", "episodic", f"{_QUERY} episodic runbook notes",
        project=_PROJECT, agent_class=_AGENT_CLASS, created_at=next(stamps),
    )
    head = [
        crystal(
            f"zzc{i:02d}", "episodic", f"unrelated dense filler {i}, gate fixture",
            project=_PROJECT, agent_class=_AGENT_CLASS, created_at=next(stamps),
        )
        for i in range(1, _GATE_HEAD_COUNT + 1)
    ]
    edge_comp = crystal(
        "zzedgecomp", "episodic", "dense competitor with a graph edge, gate fixture",
        project=_PROJECT, agent_class=_AGENT_CLASS, created_at=next(stamps),
    )
    tail = [
        crystal(
            f"zztail{i}", "episodic", f"tail filler {i}, gate fixture",
            project=_PROJECT, agent_class=_AGENT_CLASS, created_at=next(stamps),
        )
        for i in range(1, _GATE_TAIL_COUNT + 1)
    ]
    phantom = crystal(
        "zzphantom", "episodic",
        "phantom node, graph-only, no lexical or dense presence, gate fixture",
        project=_PROJECT, agent_class=_AGENT_CLASS, created_at=next(stamps),
    )

    seed_relational(relational, [target, *head, edge_comp, *tail, phantom])
    seed_graph(
        graph,
        nodes=[("zzedgecomp", "episodic"), ("zzphantom", "episodic")],
        edges=[("zzedgecomp", "zzphantom", "LINKS_TO")],
    )

    dense_hits = [c["id"] for c in head] + ["zzedgecomp"] + [c["id"] for c in tail]
    return dense_hits, "target"


# ---------------------------------------------------------------------------
# VP-M1 -- FORGE D5's ruled probe (verification-plan.amend-03.md Sec 2)
# ---------------------------------------------------------------------------


def vp_m1_probe(
    *, floor: int, fixture: str = "shipped", data_root: str = _DATA_ROOT
) -> dict[str, Any]:
    """FORGE D5's ruled probe symbol (spec.amend-01.md Sec B.7.2), extended
    by the `fixture` keyword (verification-plan.amend-03.md Sec 2.0) --
    default-preserving, so every D5-era call is unchanged. Always the
    UNWEIGHTED arm (`recall_weighted_fusion=False`) -- AC-138/AC-139's
    literal scope is "the reverted build"
    (`evals/fusion_gate.py::TestFetchWidthFloorInflation`'s own framing).

    `fixture`:
      "shipped"          -- imports `evals.fusion_gate._build_fixture`
                             (read-only; Sec 3.1's freeze), all four layers,
                             and self-checks against
                             `evals.fusion_gate.run_floor_probe`.
      "distinct_phantom"  -- `_build_distinct_phantom_fixture` (AC-357's
                             control), single layer, no external duplicate
                             to self-check against (`self_check_ok` is
                             trivially `True` -- documented, not silently
                             assumed).

    Returns `{floor, floor_applied_readback, derived, retrieved,
    target_rank, self_check_ok, fixture, fixture_phantom_targets}`
    (verification-plan.amend-03.md Sec 2.0).
    """
    from crystalium.aetheryte import retrieve as retrieve_mod
    from crystalium.schemas import Scope
    from crystalium.trust import Tier

    if fixture not in ("shipped", "distinct_phantom"):
        raise ValueError(f"vp_m1_probe: unknown fixture {fixture!r}")

    os.makedirs(data_root, exist_ok=True)
    tag = f"vpm1-{fixture}-{floor}-{uuid.uuid4().hex[:8]}"
    stores = new_stores(data_root, tag)

    if fixture == "shipped":
        from evals.fusion_gate import _build_fixture as _shipped_build_fixture

        dense_hits, target_id = _shipped_build_fixture(stores.relational, stores.graph)
        layers: list[str] | None = None
        from evals.fusion_gate import _PROJECT as _shipped_project
        from evals.fusion_gate import _AGENT_CLASS as _shipped_agent_class
        from evals.fusion_gate import _QUERY as _shipped_query
        scope = Scope(project=_shipped_project, agent_class_visibility=_shipped_agent_class)
        query = _shipped_query
    else:
        dense_hits, target_id = _build_distinct_phantom_fixture(stores.relational, stores.graph)
        layers = ["episodic"]
        scope = Scope(project=_PROJECT, agent_class_visibility=_AGENT_CLASS)
        query = _QUERY

    fixture_phantom_targets = sorted({dst for _src, dst, _rel in stores.graph.all_edges()})

    vector_store = stub_vector_store(dense_hits)
    spy = _GraphStoreSpy(stores.graph)

    original_floor = retrieve_mod.FETCH_WIDTH_FLOOR
    retrieve_mod.FETCH_WIDTH_FLOOR = floor
    try:
        floor_applied_readback = retrieve_mod.FETCH_WIDTH_FLOOR
        aetheryte = build_aetheryte(
            cfg=stores.cfg,
            relational=stores.relational,
            vector_store=vector_store,
            graph_store=spy,
            completion=True,
            completion_max_hops=1,
            completion_decay=0.5,
            recall_active_only=False,
            recall_relevance_primary=True,
            recall_weighted_fusion=False,
            fusion_weight_dense=stores.cfg.fusion_weight_dense,
            fusion_weight_derived=stores.cfg.fusion_weight_derived,
            fusion_sparse_boost_alpha=stores.cfg.fusion_sparse_boost_alpha,
        )
        result = aetheryte.recall(scope, query, 2, layers, Tier.T1)
        retrieved = [r.id for r in result.records]
        target_rank = retrieved.index(target_id) if target_id in retrieved else -1
    finally:
        retrieve_mod.FETCH_WIDTH_FLOOR = original_floor

    if fixture == "shipped":
        from evals.fusion_gate import run_floor_probe as _fusion_run_floor_probe

        dup = _fusion_run_floor_probe(floor=floor, weighted=False, data_root=data_root)
        self_check_ok = (dup["target_rank"] == target_rank) and (dup["retrieved"] == retrieved)
    else:
        # No external duplicate exists for a fixture `fusion_gate.py` does
        # not know how to build (Sec 3.1's freeze) -- documented `True`,
        # never silently omitted (D5's self-check is a "shipped"-fixture
        # concept; see this function's docstring).
        self_check_ok = True

    return {
        "floor": floor,
        "floor_applied_readback": floor_applied_readback,
        "derived": spy.derived_union,
        "retrieved": retrieved,
        "target_rank": target_rank,
        "self_check_ok": self_check_ok,
        "fixture": fixture,
        "fixture_phantom_targets": fixture_phantom_targets,
    }


def vp_m1_pair(
    *, seed_label: str, floor_low: int, floor_high: int,
    fixture: str = "shipped", data_root: str = _DATA_ROOT,
) -> dict[str, Any]:
    """FORGE D5's ruled pair-caller (verification-plan.amend-03.md Sec 2.0)
    -- the ONLY caller of `vp_m1_probe`. Runs both floors of ONE seed
    WITHIN one process: with `PYTHONHASHSEED` unset (mandatory under rule
    (d)'s 14th/7th row), hash randomisation differs PER PROCESS, so a
    cross-process pair for the `unset` row would compare two different
    randomisations, not a measurement (bounded, recorded extension of D5,
    not absorbed silently).
    """
    low = vp_m1_probe(floor=floor_low, fixture=fixture, data_root=data_root)
    high = vp_m1_probe(floor=floor_high, fixture=fixture, data_root=data_root)
    return {
        "seed": seed_label,
        "floor_low": floor_low,
        "floor_high": floor_high,
        "fixture": fixture,
        "fixture_phantom_targets": low["fixture_phantom_targets"],
        "probe_symbol": "vp_m1_probe",
        "probe_calls": 2,
        "low": low,
        "high": high,
        "channel_live": low["derived"] != high["derived"],
    }


def shipped_fixture_topology(
    floors: list[int], data_root: str = _DATA_ROOT
) -> dict[str, Any]:
    """AC-374 -- the mechanism note AS AN ASSERTION
    (verification-plan.amend-03.md Sec 2.2). Imports
    `evals.fusion_gate._build_fixture` (read-only; Sec 3.1's freeze), reads
    its edge set OFF THE GRAPH (`topology_source == "graph.all_edges"`,
    never a literal), and captures the derived-arm union at each floor in
    `floors` through `vp_m1_probe(fixture="shipped")` -- the SAME
    construction/self-check VP-M1's own 14-seed measurement uses, so this
    artifact and that measurement can never silently diverge in how the
    shipped fixture is built.
    """
    from evals.fusion_gate import _build_fixture as _shipped_build_fixture

    os.makedirs(data_root, exist_ok=True)
    stores = new_stores(data_root, f"topo-{uuid.uuid4().hex[:8]}")
    _shipped_build_fixture(stores.relational, stores.graph)
    edges = stores.graph.all_edges()

    derived_union_by_floor: dict[str, list[str]] = {}
    for floor in floors:
        probe = vp_m1_probe(floor=floor, fixture="shipped", data_root=data_root)
        derived_union_by_floor[str(floor)] = probe["derived"]

    edge_sources = sorted({src for src, _dst, _rel in edges})
    phantom_targets = [dst for _src, dst, _rel in edges]

    return {
        "fixture": "shipped",
        "topology_source": "graph.all_edges",
        "edge_sources": edge_sources,
        "phantom_targets": phantom_targets,
        "distinct_phantom_count": len(set(phantom_targets)),
        "derived_union_by_floor": derived_union_by_floor,
    }


# ---------------------------------------------------------------------------
# VP-M6 -- the gate's OWN measurement (verification-plan.amend-03.md Sec 3;
# AC-322/AC-358), distinct from VP-M1.
# ---------------------------------------------------------------------------


def classify_disjoint(low_ranks: list[int], high_ranks: list[int]) -> bool:
    """AC-358's pure disjointness classifier -- no I/O, every branch reached
    from plain in-memory inputs. `aggregate_seeds` calls this SAME symbol
    (K-C5's binding: two independent computations, `aggregate_seeds`'s own
    call and AC-322's `jq` recomputation from the raw rows, must agree).

    Rejects the vacuous empty case explicitly (K-B3): `[] - [] | length ==
    0 == 0` reads as trivially "disjoint" under a naive length check, but an
    empty comparison decided NOTHING -- `classify_disjoint([], [])` is
    `False`, never `True`.
    """
    low_set = set(low_ranks)
    high_set = set(high_ranks)
    if not low_set or not high_set:
        return False
    return len(low_set - high_set) == len(low_set)


def run_seed(
    *, seed_label: str, floor_low: int = 10, floor_high: int = 1000,
    data_root: str = _DATA_ROOT,
) -> dict[str, Any]:
    """VP-M6 step 1 (verification-plan.amend-03.md Sec 3) / AC-322: one
    seed's pair of target-rank measurements on `_build_gate_fixture`, WITHIN
    one process (same cross-process-unsound rationale as `vp_m1_pair`).
    Field names `floor10_target_rank`/`floor1000_target_rank` are LITERAL
    (AC-322's `jq` predicate names them exactly), independent of the actual
    `floor_low`/`floor_high` values passed (always 10/1000 in this
    campaign's own chain).
    """
    from crystalium.aetheryte import retrieve as retrieve_mod
    from crystalium.schemas import Scope
    from crystalium.trust import Tier

    def _run_gate_arm(floor: int) -> dict[str, Any]:
        os.makedirs(data_root, exist_ok=True)
        tag = f"gateseed-{seed_label}-{floor}-{uuid.uuid4().hex[:8]}"
        stores = new_stores(data_root, tag)
        dense_hits, target_id = _build_gate_fixture(stores.relational, stores.graph)
        vector_store = stub_vector_store(dense_hits)

        original_floor = retrieve_mod.FETCH_WIDTH_FLOOR
        retrieve_mod.FETCH_WIDTH_FLOOR = floor
        try:
            aetheryte = build_aetheryte(
                cfg=stores.cfg,
                relational=stores.relational,
                vector_store=vector_store,
                graph_store=stores.graph,
                completion=True,
                completion_max_hops=1,
                completion_decay=0.5,
                recall_active_only=False,
                recall_relevance_primary=True,
                recall_weighted_fusion=False,
                fusion_weight_dense=stores.cfg.fusion_weight_dense,
                fusion_weight_derived=stores.cfg.fusion_weight_derived,
                fusion_sparse_boost_alpha=stores.cfg.fusion_sparse_boost_alpha,
            )
            scope = Scope(project=_PROJECT, agent_class_visibility=_AGENT_CLASS)
            result = aetheryte.recall(scope, _QUERY, 2, ["episodic"], Tier.T1)
            retrieved = [r.id for r in result.records]
            target_rank = retrieved.index(target_id) if target_id in retrieved else -1
        finally:
            retrieve_mod.FETCH_WIDTH_FLOOR = original_floor
        return {"target_rank": target_rank, "retrieved": retrieved}

    low = _run_gate_arm(floor_low)
    high = _run_gate_arm(floor_high)

    return {
        "seed": seed_label,
        "floor_low": floor_low,
        "floor_high": floor_high,
        "floor10_target_rank": low["target_rank"],
        "floor1000_target_rank": high["target_rank"],
        "floor10_retrieved": low["retrieved"],
        "floor1000_retrieved": high["retrieved"],
    }


def aggregate_seeds(seed_labels: list[str]) -> dict[str, Any]:
    """VP-M6 step 2 -- rule (i)'s named producing step for
    `evals/results/floor-seed-aggregate.json` (verification-plan.amend-03.md
    Sec 3; AC-322). Reads each `evals/results/floor-seed-<label>.json`
    ALREADY WRITTEN by an earlier `run_seed` + `emit()` call IN THE SAME
    chain (rule (g): these are the chain's own prior steps, never a stale
    read). Calls `classify_disjoint` -- AC-358's own symbol -- rather than
    reimplementing the predicate inline, so this aggregate and a caller
    testing the classifier directly can never silently diverge (K-C5).
    """
    import json
    from pathlib import Path

    seeds = []
    for label in seed_labels:
        path = Path("evals/results") / f"floor-seed-{label}.json"
        seeds.append(json.loads(path.read_text()))

    seed_set = [s["seed"] for s in seeds]
    low_ranks = [s["floor10_target_rank"] for s in seeds]
    high_ranks = [s["floor1000_target_rank"] for s in seeds]
    disjoint = classify_disjoint(low_ranks, high_ranks)

    return {
        "seeds": seeds,
        "seed_set": seed_set,
        "seed_set_covers_divergent_point": "8" in seed_set,
        "divergent_point_known": "8",
        "classifier": {"symbol": "classify_disjoint", "disjoint": disjoint},
    }


# ---------------------------------------------------------------------------
# CLI convention (rule (a)/(b); W-CLI's own registration, `evals/__main__.py`,
# is a separate unit -- this module only exposes the symbols that convention
# names). The 7/14-seed multi-process protocols cannot run from a single
# `run()` call (rule (d): PYTHONHASHSEED is read once per process) -- `run()`
# is therefore the single-process-safe structural measurement (AC-374),
# never a substitute for the spawned-process chain AC-321/AC-322 require.
# ---------------------------------------------------------------------------


def run(*, data_root: str = _DATA_ROOT) -> dict[str, Any]:
    """Single-process CLI smoke entry point: the AC-374 structural
    measurement only (`shipped_fixture_topology` at floors {2, 10, 1000}).
    """
    return shipped_fixture_topology([2, 10, 1000], data_root=data_root)
