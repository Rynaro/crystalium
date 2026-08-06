"""crystalium-residual-eight-plan (W-RIG) -- shared parameterised corpus rig.

The mechanism, not the measurement (spec.md Sec 0.1): every Wave-1 gate in this
campaign (G-XL / #52, G-CORPUS / #47, G-WD / #55, G-FLOOR / #48) needs the same
plumbing -- deterministic crystal minting, a real `RelationalStore` + real
`GraphStore` seeded with a fixture, a deterministic STUB dense arm, an
`Aetheryte` built with every retrieval flag stated explicitly, an
arm-liveness self-check that can tell "genuinely edgeless" apart from "kuzu
blew up", and a pure verdict classifier that enforces R-CONF (a confounded
axis emits verdict "confounded" and NEVER numbers). None of that plumbing
measures anything on its own -- no arm participates in it -- so sharing it
cannot confound a gate. What the four gates must NOT share is spelled out in
spec.md Sec 0.2 and lives entirely in each gate's OWN fixture module: this
rig has no opinion about what a "candidate_k" or a "corpus_per_layer" is.

Template = `evals/fusion_gate.py` (real RelationalStore + real GraphStore +
deterministic stub vector arm) -- NOT `evals/retrieval_gate.py`, which uses a
REAL VectorStore and therefore returns `verdict: "inconclusive"` (no numbers)
under `CRYSTALIUM_SKIP_SLOW=1`. `make test-ci` runs SKIP_SLOW **with** slow
tests still selected (Makefile, .github/workflows/ci.yml) -- a gate built on
the retrieval-gate template would protect CI not at all (spec.md Sec 0.3).
Every function here is import-safe and none of it is `@pytest.mark.slow`.

`evals/fusion_gate.py` itself is FROZEN (spec.md Sec 3.1) and is read here only
as a template -- nothing in this module imports it, and nothing in this
module may be imported back into it.

K-B16 (spec.amend-01.md Sec A.3), restated as CODE rather than left as prose a
gate could restate wrong: `GraphStore.all_edges()` returns a `list`, and its
own docstring says "Returns [] on any kuzu error" (graph.py:320-332) -- so
`all_edges() == 0` is `[] == 0`, which is `False` in Python ALWAYS. Every
liveness check in this module therefore asserts `len(all_edges()) ==
expected_edge_count` **and** `node_count() == expected_node_count` together;
neither alone can distinguish "genuinely edgeless" from "the store never
came up" or "nothing was ever seeded".

Rule (g) -- artifact freshness (spec.criteria.amend-03.md Sec 0.1, K-C6): the
`emit()` helper below is the ONE place every Wave-1 gate stamps `run_nonce`
and `tree_sha` onto its result, by DIRECT SUBSCRIPT against
`os.environ["CRYSTALIUM_GATE_NONCE"]` / `os.environ["CRYSTALIUM_TREE_SHA"]` --
a missing variable raises `KeyError` and the emit fails loudly. No default,
no `.get`, no fallback. `emit()` also never prints: structlog writes to
stdout ahead of any JSON a gate might otherwise print (K-B15,
`kb15-stdout-contamination.md`), so every criterion in this campaign reads
the artifact file, never a pipe.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator
from unittest.mock import MagicMock

_T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# 1. Deterministic crystal minting (spec.md Sec 0.1, bullet 1)
# ---------------------------------------------------------------------------


def stamp_sequence(
    start: datetime = _T0, step: timedelta = timedelta(seconds=1)
) -> Iterator[str]:
    """Infinite, strictly-increasing ISO-8601 `created_at` generator
    (retrieval_gate.py:74-88 precedent, generalised for reuse across gates).

    `relational.py`'s `recent_crystal_ids` orders `created_at DESC` with NO
    tiebreak and no index (crystalium#43) -- a corpus minted off a single
    shared timestamp (e.g. `datetime.now()` called once) makes that order an
    artefact of SQLite's scan order, not a real recency window. Call this
    once per gate/arm to get a fresh, independent sequence -- generator
    instances are never shared across the disjoint fixtures in spec.md
    Sec 0.2, only the *shape* (this function) is shared.
    """
    t = start
    while True:
        yield t.isoformat()
        t += step


def crystal(
    id: str,
    layer: str,
    summary: str,
    *,
    project: str,
    agent_class: str,
    created_at: str,
    trust_tier: str = "T1",
    status: str = "active",
) -> dict[str, Any]:
    """Deterministic crystal-payload minting (spec.md Sec 0.1's `_crystal(id,
    layer, summary, ...)`; renamed without the leading underscore here since
    this module -- already private by its own `_corpus_rig` filename -- is
    meant to be imported by all four Wave-1 gate modules).

    `created_at` is REQUIRED and caller-supplied -- pull it from
    `stamp_sequence()`, never compute it internally with `datetime.now()`,
    so every corpus this rig seeds carries a strictly-increasing,
    reproducible timestamp order (see `stamp_sequence` docstring).

    `project`/`agent_class` are REQUIRED (no shared module-level default):
    each of the four gates mints crystals under its OWN project scope
    (`fusion-gate`-style isolation, spec.amend-01.md Sec B.2.1 -- "separate
    data root, separate project scope") so two gates' fixtures can never
    cross-pollinate via `Scope` filtering even if they happened to share a
    data root.
    """
    return {
        "id": id,
        "layer": layer,
        "summary": summary,
        "trust_tier": trust_tier,
        "validation_state": "validated",
        "status": status,
        "content_ref": hashlib.sha256(id.encode()).hexdigest(),
        "embedding_ref": None,
        "scope": {
            "project": project,
            "agent_class_visibility": agent_class,
            "sensitivity_tag": "none",
        },
        "provenance": {
            "source": "verified_agent",
            "author_agent": "corpus-rig",
            "task_id": None,
            "created_at": created_at,
        },
        "temporal": {"t_valid_from": created_at, "t_valid_to": None, "superseded_by": None},
        "utility": {
            "access_count": 0,
            "last_access": created_at,
            "outcome_success_score": None,
            "importance": 0.5,
            "novelty_at_write": 0.5,
        },
    }


# ---------------------------------------------------------------------------
# 2. RelationalStore insert loop + GraphStore node/edge seeding
#    (spec.md Sec 0.1, bullet 2)
# ---------------------------------------------------------------------------


def seed_relational(relational: Any, crystals: Iterable[dict[str, Any]]) -> None:
    """`RelationalStore.insert_crystal` loop -- fixture construction, no arm
    participates, so sharing this loop cannot confound a gate (spec.md
    Sec 0.1)."""
    for c in crystals:
        relational.insert_crystal(c)


def seed_graph(
    graph: Any,
    *,
    nodes: Iterable[tuple[str, str]] = (),
    edges: Iterable[tuple[str, str, str]] = (),
) -> None:
    """`GraphStore` node/edge seeding. `nodes` is `(crystal_id, layer)`
    pairs; `edges` is `(src, dst, relation)` triples. Both default to empty
    so an edgeless-graph fixture (G-XL, G-CORPUS per spec.md Sec 0.2) can
    call `seed_graph(graph, nodes=[...])` with no `edges=` argument at all --
    the resulting absence of edges is then PROVEN by `graph_liveness`
    (below), never merely assumed.
    """
    for crystal_id, layer in nodes:
        graph.add_node(crystal_id=crystal_id, layer=layer)
    for src, dst, rel in edges:
        graph.add_edge(src, dst, rel)


# ---------------------------------------------------------------------------
# 3. The stub dense arm (spec.md Sec 0.1, bullet 3 / Sec 0.3)
# ---------------------------------------------------------------------------


def stub_vector_store(
    dense_hits: list[str], *, embed_vec: tuple[float, ...] = (0.1, 0.2, 0.3)
) -> MagicMock:
    """`MagicMock.dense_search` pinned to a caller-supplied id list -- this
    is what removes the CI-invisibility trap (spec.md Sec 0.3): a REAL
    `VectorStore` goes `verdict: "inconclusive"` under
    `CRYSTALIUM_SKIP_SLOW=1` (retrieval_gate.py's N-5 branch), and
    `make test-ci` runs SKIP_SLOW **with** slow tests still selected -- so a
    gate built on a real embedder protects CI not at all.

    `embed.return_value` is still a real 3-vector (not `None`/`[]`) so
    `query_vec` stays truthy at the dense-arm gate check inside
    `Aetheryte.recall` (mirrors `fusion_gate.py:224`); only `dense_search`'s
    RESULT is pinned, so the query-vs-store round trip through the dense
    arm's call sites is still exercised, only its answer is a stated,
    caller-controlled ranking rather than a real embedding's.
    """
    vector_store = MagicMock()
    vector_store.embed.return_value = list(embed_vec)
    vector_store.dense_search.return_value = [{"id": cid} for cid in dense_hits]
    return vector_store


# ---------------------------------------------------------------------------
# 4. The Aetheryte construction harness -- all flags explicit, never
#    defaulted (spec.md Sec 0.1, bullet 4)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Stores:
    """Bundle returned by `new_stores` -- everything a gate needs to seed a
    fixture before it stubs the dense arm and builds an `Aetheryte`."""

    cfg: Any
    relational: Any
    graph: Any


def new_stores(data_root: str, tag: str, **config_overrides: Any) -> Stores:
    """`Config` + a real `RelationalStore` + a real `GraphStore` for ONE gate
    arm (spec.md Sec 0.1 -- SAFE to share; Sec 0.2's mandatory disjointness
    lives in what each gate SEEDS into these stores, not in how they are
    built).

    `tag` MUST be unique per arm within a `data_root` (mirrors
    `fusion_gate.py`'s `f"{int(weighted)}-{fetch_width_floor or 'd'}-
    {uuid.uuid4().hex[:8]}"` convention) -- two arms that reuse a `tag`
    silently share one sqlite/kuzu directory, which is a confound of exactly
    the crystalium#43 species this campaign exists to close. This function
    does not mint a `tag` itself (unlike `fusion_gate.py`'s inline
    convention) so a gate's own naming stays visible in its own fixture code
    -- callers are expected to include a `uuid`/seed/weight component of
    their own where collision is a real risk.

    `rate_limit_per_minute` defaults to 1_000_000 (never let a gate's own
    seeding loop trip the rate limiter); pass `rate_limit_per_minute=...` in
    `config_overrides` to override it. Every other `Config` field not named
    in `config_overrides` is `Config`'s own default -- this function does
    NOT silently pin any retrieval-affecting flag (`recall_weighted_fusion`,
    `fusion_weight_derived`, etc.); those are stated explicitly by
    `build_aetheryte` below, never inherited from `Config` construction.
    """
    from crystalium.config import Config
    from crystalium.storage.graph import GraphStore
    from crystalium.storage.relational import RelationalStore

    data_dir = Path(data_root) / tag
    overrides: dict[str, Any] = {"rate_limit_per_minute": 1_000_000, **config_overrides}
    cfg = Config(data_dir=data_dir, **overrides)
    relational = RelationalStore(db_path=cfg.sqlite_path)
    graph = GraphStore(kuzu_dir=cfg.kuzu_path)
    return Stores(cfg=cfg, relational=relational, graph=graph)


def build_aetheryte(
    *,
    cfg: Any,
    relational: Any,
    vector_store: Any,
    graph_store: Any,
    completion: bool,
    completion_max_hops: int,
    completion_decay: float,
    recall_active_only: bool,
    recall_relevance_primary: bool,
    recall_weighted_fusion: bool,
    fusion_weight_dense: float,
    fusion_weight_derived: float,
    fusion_sparse_boost_alpha: float,
) -> Any:
    """The `Aetheryte` construction harness (spec.md Sec 0.1, bullet 4):
    every flag that can change a fused rank is a REQUIRED keyword argument
    with NO default -- a gate that forgets to pin one gets a `TypeError` at
    construction time, not a silently-inherited default. This is what made
    `fusion_gate.py` auditable (spec.md Sec 0.1's own rationale), generalised
    here so every one of the four Wave-1 gates states its own axis values in
    its own fixture code and never inherits this rig's opinion of what
    "off" means.

    `Composer`'s `recall_relevance_primary` is built from the SAME value
    passed for the `Aetheryte`'s own `recall_relevance_primary` (never read
    back off `cfg` separately) -- `Aetheryte.recall`'s docstring documents
    seam 3 (top-k truncation) as gated together with the Composer's own
    seams 4/5 (relevance-primary eviction key + ordering); constructing them
    from two different sources would silently desynchronise a flag this rig
    exists to keep explicit.
    """
    from crystalium.aetheryte.redact import Redactor
    from crystalium.aetheryte.retrieve import Aetheryte
    from crystalium.composer import Composer
    from crystalium.enforcement import Enforcement
    from crystalium.importance import importance_score

    enforcement = Enforcement(cfg)
    redactor = Redactor(cfg)
    composer = Composer(cfg, recall_relevance_primary=recall_relevance_primary)

    return Aetheryte(
        relational=relational,
        vector_store=vector_store,
        graph_store=graph_store,
        enforcement=enforcement,
        redactor=redactor,
        importance_fn=importance_score,
        composer=composer,
        completion=completion,
        completion_max_hops=completion_max_hops,
        completion_decay=completion_decay,
        recall_active_only=recall_active_only,
        recall_relevance_primary=recall_relevance_primary,
        recall_weighted_fusion=recall_weighted_fusion,
        fusion_weight_dense=fusion_weight_dense,
        fusion_weight_derived=fusion_weight_derived,
        fusion_sparse_boost_alpha=fusion_sparse_boost_alpha,
    )


# ---------------------------------------------------------------------------
# 5. Arm-liveness self-checks (spec.md Sec 0.1, bullet 5 / AC-305 / K-B16)
# ---------------------------------------------------------------------------


def graph_liveness(
    graph: Any, *, expected_node_count: int, expected_edge_count: int
) -> dict[str, Any]:
    """The rig's graph-store liveness self-check (AC-305; K-B16 fix).

    `GraphStore.all_edges()` returns a LIST and "Returns [] on any kuzu
    error" (graph.py:320-332) -- so `all_edges() == 0` is `[] == 0`, which is
    `False` ALWAYS in Python, and a rig written that way can never conclude
    "edgeless". `GraphStore._node_count()` has the identical shape (catches
    `Exception` and returns `0`, graph.py:108-109) -- so a bare `edge_count
    == 0` is ambiguous between three states that must NOT be conflated:
    "genuinely edgeless", "kuzu blew up", and "nothing was ever seeded".

    The normative form (spec.amend-01.md Sec A.3), stated here as CODE
    rather than left as a paragraph a downstream gate could restate wrong::

        edge_count = len(graph.all_edges())        # 0 == edgeless OR error
        node_count = graph.node_count()             # a real COUNT query
        node_live  = node_count == expected_node_count   # proves the store
                                                          # is live AND
                                                          # populated exactly
                                                          # as seeded
        edge_live  = edge_count == expected_edge_count

    `verdict` is `"measured"` only when BOTH hold. Callers pass
    `expected_edge_count=0` for the edgeless gates (G-XL, G-CORPUS per
    spec.md Sec 0.2); a gate whose fixture seeds real edges (e.g. a
    derived-arm topology) passes its own non-zero expectation and gets the
    identical discipline -- this is the general form of
    `retrieval_gate.py::resolve_verdict`'s `expected_edges` check
    (crystalium#43), not a variant restricted to zero.

    Returns `{"verdict": "measured" | "confounded", "liveness": {...}}`;
    `liveness` always carries the raw counts and expectations (diagnostics
    are never withheld) -- only the higher-level "axes" a gate reports are
    ever suppressed on a confounded verdict (see `assemble_result`).
    """
    node_count = graph.node_count()
    edge_count = len(graph.all_edges())
    node_live = node_count == expected_node_count
    edge_live = edge_count == expected_edge_count
    liveness = {
        "node_count": node_count,
        "edge_count": edge_count,
        "expected_node_count": expected_node_count,
        "expected_edge_count": expected_edge_count,
    }
    return {
        "verdict": "measured" if (node_live and edge_live) else "confounded",
        "liveness": liveness,
    }


def arm_liveness(
    *,
    dense_ranking: list[Any],
    expected_dense: int,
    sparse_ranking: list[Any],
    expected_sparse: int,
) -> dict[str, Any]:
    """Sparse/dense arm cardinality self-check (spec.md Sec 0.1, bullet 5):
    each gate asserts the arm it pins actually carries the length it thinks
    it does, so a truncated stub list (an off-by-one in a fixture's
    `dense_hits=[...]`, or a `bm25_search` call whose `k=` silently clipped
    the corpus) reads as `"confounded"`, not as a silently-smaller
    measurement nobody notices.

    Returns `{"verdict": "measured" | "confounded", "liveness": {...}}`,
    mirroring `graph_liveness`'s shape.
    """
    dense_live = len(dense_ranking) == expected_dense
    sparse_live = len(sparse_ranking) == expected_sparse
    liveness = {
        "dense_count": len(dense_ranking),
        "expected_dense": expected_dense,
        "sparse_count": len(sparse_ranking),
        "expected_sparse": expected_sparse,
    }
    return {
        "verdict": "measured" if (dense_live and sparse_live) else "confounded",
        "liveness": liveness,
    }


# ---------------------------------------------------------------------------
# 6. The pure verdict classifier (spec.md Sec 0.1, bullet 6 / R-CONF)
# ---------------------------------------------------------------------------


def resolve_verdict(*, pinned: dict[str, bool]) -> str:
    """Pure verdict classifier -- no I/O, no model, no container state,
    every branch reachable from plain in-memory booleans (D-1 design
    constraint; `retrieval_gate.py::resolve_verdict`, :91-114, generalised).
    This is what keeps a gate's honesty branch actually falsifiable rather
    than merely asserted.

    `pinned` maps an axis name -> `True` when that axis is PROVEN
    NON-BINDING (safe: cannot be the cause of the measured effect), `False`
    when it is BINDING (a live confound, R-CONF). This function does not
    know what a "candidate_k" or a "corpus_per_layer" IS -- each of the four
    Wave-1 gates supplies its own axis dict, typically folding in this
    module's own `graph_liveness`/`arm_liveness` verdicts alongside its own
    gate-specific pinned-axis booleans, e.g.::

        resolve_verdict(pinned={
            "graph_edgeless": graph_liveness(...)["verdict"] == "measured",
            "dense_sparse_arms": arm_liveness(...)["verdict"] == "measured",
            "corpus_per_layer_lt_candidate_k": corpus_per_layer < candidate_k,
        })

    Precedence is fixed and total:
      1. ANY axis `False` -> `"confounded"` (R-CONF: never numbers).
      2. ALL axes `True`  -> `"measured"`.

    Raises `ValueError` on an empty `pinned` mapping -- a verdict resolved
    over zero named axes is not a verdict, it is a default masquerading as
    one.
    """
    if not pinned:
        raise ValueError("resolve_verdict requires at least one named pinned axis")
    if any(not ok for ok in pinned.values()):
        return "confounded"
    return "measured"


def assemble_result(
    *,
    verdict: str,
    liveness: dict[str, Any] | None = None,
    axes: dict[str, Any] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """R-CONF, enforced ONCE here rather than re-implemented (and
    potentially re-broken) independently in each of the four gates that
    build on this rig (`retrieval_gate.py:301-310` precedent -- "a gate that
    emits numbers with a live confound is the defect, not the evidence").

    If `verdict == "confounded"`, `axes` is forced to `None` regardless of
    what the caller passes -- a gate cannot accidentally leak a numeric axis
    through a confounded return path, even via `**extra`, which is dropped
    entirely on that branch. `liveness` (diagnostics) is never suppressed --
    only "axes" (the gate's actual measurement) is.
    """
    if verdict not in ("measured", "confounded"):
        raise ValueError(f"unknown verdict: {verdict!r}")
    result: dict[str, Any] = {"verdict": verdict}
    if liveness is not None:
        result["liveness"] = liveness
    if verdict == "confounded":
        result["axes"] = None
        return result
    result["axes"] = axes
    result.update(extra)
    return result


# ---------------------------------------------------------------------------
# 7. Artifact emission -- rule (g), artifact freshness
#    (spec.criteria.amend-03.md Sec 0.1, K-C6)
# ---------------------------------------------------------------------------


def emit(result: dict[str, Any], out: str) -> None:
    """Rule (g)'s artifact-freshness contract, binding on every gate this
    campaign ships (spec.criteria.amend-03.md Sec 0.1, K-C6).

    Stamps `run_nonce`/`tree_sha` onto a COPY of `result` by DIRECT
    SUBSCRIPT -- `os.environ["CRYSTALIUM_GATE_NONCE"]` /
    `os.environ["CRYSTALIUM_TREE_SHA"]` -- so a missing variable raises
    `KeyError` and the emit fails LOUDLY. No default, no `.get`, no
    fallback: a silently-unstamped artifact is indistinguishable from a
    stale one left over from a previous run, which is exactly the K-C6
    defect rule (g) exists to close (a gate that crashes AFTER a previous
    green run, before ever calling `emit`, must not leave a green-looking
    artifact behind for the next reader to trust).

    Writes ONLY the file and prints NOTHING -- structlog writes to stdout
    ahead of any JSON a gate might otherwise print (amend-01 Sec A.4 /
    K-B15), so every criterion in this campaign reads the artifact file,
    never a pipe over this process's stdout. Creates parent directories.
    OVERWRITES, never appends. The caller (the shell chain named by every
    criterion in this campaign, per rule (g)'s canonical form) is
    responsible for `rm -f`ing the target FIRST -- `emit` itself never
    deletes anything; it only ever writes the one file it is given, once,
    with today's stamped values.
    """
    stamped = dict(result)
    stamped["run_nonce"] = os.environ["CRYSTALIUM_GATE_NONCE"]
    stamped["tree_sha"] = os.environ["CRYSTALIUM_TREE_SHA"]
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(stamped, indent=1, sort_keys=True))
