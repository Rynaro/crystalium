"""W5(i) — retrieval pattern-completion / context-match gate (evals/retrieval_gate.py).

crystalium#43 / #54 (W3): the fixture is now deconfounded — `link_cooccurrence` is
pinned OFF in every arm (only the explicit hub->spoke1->spoke2 chain remains) and
`created_at` is strictly increasing across the 31 commits. `TestResolveVerdict` and
`TestEmbeddingsUnavailable` exercise the isolation self-check and the N-5 honesty
path; both are UNMARKED and set `CRYSTALIUM_SKIP_SLOW` themselves (design
constraint D-1, spec.md §2.5.4(b)/correction S-6) so they are real gates under
`make test-fast`, not tests the fast suite silently skips.

RETRACTION (crystalium#43, honesty branch — lift did NOT survive deconfounding):
the previous docstring here claimed "the larger corpus ... completion lifts
multi-hop recall/F1, so recall_completion is ON". That claim was measured on
the CONFOUNDED gate (completion arm carried ~150 extra co-occurrence edges the
flat arm did not — see config.py:211's retraction comment). MEASURED on this
worktree, deconfounded: multihop_f1 completion == flat == 0.30769230769230765
— no lift survives. `recall_completion` stays `True` by FORGE's pre-ruling
(flipping a shipped default is release-coupled and out of #43's scope, and
this worktree still carries the pre-#41 neighbor_expand first-seed-abort bug
a separate campaign unit fixes) — but the justification this file asserted is
gone. A follow-up issue reassessing this default once #41 lands must be filed.
The formal 7-seed post-#41 remeasurement is eval-baseline-deconfounded.json
(AC-247), owned by the release orchestrator, and may revise this finding.
`recall_context_match` still shows no rank lift, so it stays OFF (unchanged).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from evals.retrieval_gate import resolve_verdict, run, run_arm


@pytest.mark.slow
def test_completion_shows_no_measured_lift_when_deconfounded(tmp_path: Path) -> None:
    """crystalium#43 honesty branch: lift did NOT survive deconfounding.

    Was `test_completion_lifts_multihop_recall_and_f1`, asserting
    `f1["completion"] > f1["flat"]`. Once `link_cooccurrence` is pinned OFF in
    every arm (killing the ~150-edge confound) and the `created_at` tie is
    broken, that assertion is FALSE on this worktree — MEASURED, not assumed:
    `multihop_f1.completion == multihop_f1.flat == 0.30769230769230765`. This
    worktree still carries the pre-crystalium#41 `neighbor_expand`
    first-seed-abort bug (a separate campaign unit fixes it), so the
    derived-family multi-hop walk from the top `fetch_width` seeds does not
    reliably reach the seeded hub->spoke1->spoke2 chain. Per FORGE's pre-ruled
    honesty branch, this asserts the measured relationship — not a fabricated
    pass — and does NOT flip the shipped `recall_completion=True` default.
    """
    r = run(data_root=str(tmp_path))
    assert r["verdict"] != "confounded"
    assert r["graph_ok"] is True  # real kuzu graph, not the null stub
    f1 = r["axes"]["multihop_f1"]
    # Deconfounded measurement (this worktree): no lift, and no regression
    # either — completion never scores WORSE than flat.
    assert f1["completion"] == pytest.approx(f1["flat"])
    assert r["completion_pass"] is False


@pytest.mark.slow
def test_context_match_shows_no_rank_lift_stays_off(tmp_path: Path) -> None:
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
