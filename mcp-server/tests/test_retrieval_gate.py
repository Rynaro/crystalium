"""W5(i) — retrieval pattern-completion / context-match gate (evals/retrieval_gate.py).

crystalium#43 / #54 (W3): the fixture is now deconfounded — `link_cooccurrence` is
pinned OFF in every arm (only the explicit hub->spoke1->spoke2 chain remains) and
`created_at` is strictly increasing across the 31 commits. `TestResolveVerdict` and
`TestEmbeddingsUnavailable` exercise the isolation self-check and the N-5 honesty
path; both are UNMARKED and set `CRYSTALIUM_SKIP_SLOW` themselves (design
constraint D-1, spec.md §2.5.4(b)/correction S-6) so they are real gates under
`make test-fast`, not tests the fast suite silently skips.

REINSTATEMENT (crystalium#43 + #41 — lift DOES survive deconfounding, post-#41):
an earlier version of this file asserted `multihop_f1.completion ==
multihop_f1.flat` ("no lift survives deconfounding"). That was measured on a
worktree that still carried the pre-crystalium#41 `neighbor_expand`
first-seed-abort bug, so the derived-family multi-hop walk from the top
`fetch_width` seeds could not reliably reach the seeded hub->spoke1->spoke2
chain even on the deconfounded (link_cooccurrence-OFF) fixture — the "no
lift" result was an artifact of the graph-arm bug, not a property of the
completion arm itself. With #41 fixed and merged, the release orchestrator's
7-seed remeasurement (PYTHONHASHSEED 0-5 + unset, 2 runs each = 14 runs; see
eval-baseline-deconfounded.json, AC-247) found a single distinct value across
all 14 runs: `multihop_f1.flat == 0.30769230769230765` and
`multihop_f1.completion == 0.4615384615384615` — completion strictly and
deterministically exceeds flat, and `completion_pass` is `True` 14/14 (the
verdict is never "confounded" or "inconclusive"). `recall_completion=True`
is therefore EARNED-ON, not merely a pre-#41 default carried by FORGE
pre-ruling. `recall_context_match` still shows no rank lift, so it stays OFF
(unchanged).

CI GAP (fix/gate-tests-require-embeddings, PR #56): `.github/workflows/ci.yml`
runs `pytest tests/ -v` with `CRYSTALIUM_SKIP_SLOW=1` but WITHOUT
`-m "not slow"`, so the two @pytest.mark.slow tests above still get collected
there — with the dense arm unreachable, `run()` correctly short-circuits to
verdict "inconclusive" (crystalium#54) and both tests fail asserting real
numbers against an all-`None` response. `_skip_unless_embeddings_available()`
probes the same precondition `run()` itself checks and SKIPs (never weakens
the assertions) when it is unmet. `TestResolveVerdict` and
`TestEmbeddingsUnavailable` are deliberately NOT given this guard — they are
the tests that prove the inconclusive short-circuit itself works, and must
keep running whenever CRYSTALIUM_SKIP_SLOW is in effect.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from evals.retrieval_gate import _embeddings_available, resolve_verdict, run, run_arm


def _skip_unless_embeddings_available(data_root: str) -> None:
    """Skip (never fail) a test that asserts real measured numbers when the
    dense-embedding backend cannot be probed.

    Uses `_embeddings_available()` — the exact same probe `run()` itself
    calls before deciding whether to short-circuit to verdict "inconclusive"
    (crystalium#54, N-5) — rather than sniffing `CRYSTALIUM_SKIP_SLOW`
    directly, so this precondition can never drift from the gate's own
    definition of "the dense arm works".

    CI's `pytest tests/ -v` runs with `CRYSTALIUM_SKIP_SLOW=1` but WITHOUT
    `-m "not slow"`, so these @pytest.mark.slow tests still get collected
    and executed there. With embeddings unreachable, `run()` correctly
    returns no numbers at all (every axis `None`) — asserting real measured
    values against that response is not a false negative in the test, it's
    a missing precondition, so SKIP rather than weaken the assertions
    (crystalium#43: a gate that cannot fail on the confound it names is not
    a gate — the fix here is the same principle applied to the tests that
    exercise the gate).
    """
    if not _embeddings_available(data_root):
        pytest.skip(
            "requires a working dense arm; embeddings unavailable "
            "(CRYSTALIUM_SKIP_SLOW) — the gate correctly returns "
            "inconclusive, see crystalium#54"
        )


@pytest.mark.slow
def test_completion_lifts_multihop_f1_when_deconfounded(tmp_path: Path) -> None:
    """crystalium#43 + #41: completion lift IS real on the deconfounded gate.

    Was `test_completion_shows_no_measured_lift_when_deconfounded`, asserting
    `f1["completion"] == approx(f1["flat"])`. That was measured on a worktree
    that still carried the pre-crystalium#41 `neighbor_expand`
    first-seed-abort bug: even with `link_cooccurrence` pinned OFF in every
    arm (no confound) and `created_at` strictly increasing (no tie), the
    graph-arm bug kept the derived-family multi-hop walk from reliably
    reaching the seeded hub->spoke1->spoke2 chain, masking the completion
    arm's real advantage. That "no lift" result was an artifact of the graph
    bug, not a property of the completion arm.

    With #41 fixed and merged (this worktree), the release orchestrator's
    7-seed remeasurement (PYTHONHASHSEED 0-5 + unset, x2 runs = 14 runs; see
    eval-baseline-deconfounded.json, AC-247) found a single distinct value
    across all 14 runs for each arm:
        multihop_f1.flat       == 0.30769230769230765
        multihop_f1.completion == 0.4615384615384615
    with `completion_pass` True and `context_pass` False on all 14, and the
    verdict never "confounded" or "inconclusive". The robust, meaningful
    claim is the strict inequality (completion > flat) — asserting exact
    equality to either float as the primary check would be reproducing a
    single measurement rather than the deconfounded relationship it embodies.
    """
    _skip_unless_embeddings_available(str(tmp_path))
    r = run(data_root=str(tmp_path))
    assert r["verdict"] != "confounded"
    assert r["graph_ok"] is True  # real kuzu graph, not the null stub
    f1 = r["axes"]["multihop_f1"]
    # Deconfounded, post-#41 measurement: completion strictly beats flat.
    assert f1["completion"] > f1["flat"]
    assert f1["flat"] == pytest.approx(0.30769230769230765)
    assert r["completion_pass"] is True


@pytest.mark.slow
def test_context_match_shows_no_rank_lift_stays_off(tmp_path: Path) -> None:
    _skip_unless_embeddings_available(str(tmp_path))
    r = run(data_root=str(tmp_path))
    assert r["verdict"] != "confounded"
    # The context-matching crystal already ranks first in both arms — no lift.
    assert r["context_pass"] is False


# ---------------------------------------------------------------------------
# TestResolveVerdict — pure classifier (design constraint D-1). No I/O, no
# model, no container state. UNMARKED so `make test-fast` (`-m "not slow"`)
# actually collects these — the two pre-existing tests above are both
# @pytest.mark.slow, so without this the fast suite collected zero tests
# from this file (spec.md §2.5.4(b), correction S-6).
# ---------------------------------------------------------------------------


class TestResolveVerdict:
    def test_resolve_verdict_honest_when_isolated(self) -> None:
        verdict = resolve_verdict(
            edge_counts={"flat": 2, "comp": 2, "ctx": 2, "both": 2},
            expected_edges=2,
            embeddings_ok=True,
        )
        assert verdict == "isolated"
        assert verdict not in ("confounded", "inconclusive")

    def test_resolve_verdict_confounded_when_edges_exceed_expected(self) -> None:
        # Measured pre-fix shape (BENCH-NOTES): flat/ctx stay at 2, comp/both
        # balloon to ~142 co-occurrence edges. A single drifting arm is enough.
        verdict = resolve_verdict(
            edge_counts={"flat": 2, "comp": 142, "ctx": 2, "both": 142},
            expected_edges=2,
            embeddings_ok=True,
        )
        assert verdict == "confounded"

    def test_resolve_verdict_inconclusive_when_embeddings_unavailable(self) -> None:
        # embeddings_ok takes precedence over the edge-count check even when
        # every arm is (incidentally) isolated — N-5 outranks the self-check.
        verdict = resolve_verdict(
            edge_counts={"flat": 2, "comp": 2, "ctx": 2, "both": 2},
            expected_edges=2,
            embeddings_ok=False,
        )
        assert verdict == "inconclusive"


# ---------------------------------------------------------------------------
# TestEmbeddingsUnavailable — N-5 (crystalium#54) honesty path through the
# real run(). UNMARKED; sets CRYSTALIUM_SKIP_SLOW itself via monkeypatch
# rather than inheriting it from the Makefile, so this is a real gate under
# both `make test-fast` (where it is already set) and plain `pytest`/`make
# test` (where it is not, and the test must still force the condition).
# ---------------------------------------------------------------------------


class TestEmbeddingsUnavailable:
    def test_run_emits_no_numbers_when_inconclusive(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("CRYSTALIUM_SKIP_SLOW", "1")
        r = run(data_root=str(tmp_path))
        assert r["verdict"] == "inconclusive"
        assert r["axes"] is None
        assert r["graph_ok"] is None
        assert r["completion_pass"] is None
        assert r["context_pass"] is None
        assert r["gate_pass"] is None
        assert "reason" in r


# ---------------------------------------------------------------------------
# TestIsolation — real GraphStore/RelationalStore checks (crystalium#43).
# Marked slow: these commit the full 31-crystal corpus through the real
# `_build_components` pipeline, same shape as the two gate tests above.
# ---------------------------------------------------------------------------


@pytest.mark.slow
class TestIsolation:
    def test_gate_reports_two_edges_in_every_arm(self, tmp_path: Path) -> None:
        for completion, context_match in ((False, False), (True, False), (False, True), (True, True)):
            arm = run_arm(completion=completion, context_match=context_match, data_root=str(tmp_path))
            assert arm["edge_count"] == 2

    def test_created_at_strictly_increases_across_commits(self, tmp_path: Path) -> None:
        arm = run_arm(completion=False, context_match=False, data_root=str(tmp_path))
        stamps = arm["created_at_values"]
        assert len(stamps) == 31
        assert len(set(stamps)) == 31  # no _T0-style total tie
        assert all(a < b for a, b in zip(stamps, stamps[1:]))
