"""crystalium#47 -- pytest wrapper for the corpus-scaling eval gate
(AC-314, AC-315, AC-316; W-G-CORPUS).

Template: test_fusion_gate.py. The eval itself (`evals/corpus_scaling_gate.py`)
is runnable standalone (`python -c "import evals.corpus_scaling_gate as m; ..."`,
see spec.criteria.amend-03.md AC-314/AC-316); this file pins its verdict as a
pytest node so `make test` / CI catches a regression the same way
`test_fusion_gate.py` pins `evals/fusion_gate.py`.

**#47 is already ruled NOT closable as specified** (spec.md Sec 5.1). This
file is a GATE, not a fix -- it documents that `candidate_k` truncation
drops a planted ground-truth record once the corpus exceeds it (RED, by
design, on this tree) and that the gate is not measuring something else
(the AC-315 red-check, GREEN below `candidate_k`). No test in this file
asserts the defect is fixed.

Container-first: run via
  docker compose run --rm crystalium pytest mcp-server/tests/test_corpus_scaling_gate.py -v
"""

from __future__ import annotations

from pathlib import Path

from evals.corpus_scaling_gate import run, run_small_corpus_control


class TestCorpusScalingGate:
    def test_large_corpus_drops_planted(self, tmp_path: Path) -> None:
        """AC-314: single-layer corpus of M=60 crystals (M > candidate_k=30
        at the default k=10) -- the planted ground-truth record, ranked
        last among all 60 by construction, is absent from the recalled ids.
        RED today: this documents the open #47 defect, it does not fix it.
        """
        result = run(data_root=str(tmp_path / "corpus-scaling-gate"))

        assert result["verdict"] == "measured", result
        assert result["planted_recovered"] is False, result

        liveness = result["liveness"]
        assert liveness["layers"] == ["episodic"], liveness  # deconfounding: single layer
        assert liveness["edge_count"] == 0, liveness  # deconfounding: edgeless
        assert liveness["node_count"] > 0, liveness  # ... and genuinely populated, not dead
        assert liveness["completion"] is False, liveness
        assert liveness["corpus_size"] > liveness["candidate_k"], liveness
        # VP-M3: sparse_arm_size == candidate_k EXACTLY proves the fetch was
        # really censored -- anything else means this gate measures
        # something other than truncation.
        assert liveness["sparse_arm_size"] == liveness["candidate_k"], liveness

    def test_small_corpus_control_recovers_planted(self, tmp_path: Path) -> None:
        """AC-315 -- the red-check (normative node name, spec.criteria
        .amend-01.md). Shrink the corpus to M=5, well under candidate_k=30
        (and under the final response cap k=10 too, so the post-fusion
        k-gate cannot independently exclude the record either) -- no
        truncation anywhere in the pipeline, so the SAME last-ranked
        planted record must now be recovered.

        Per spec.md Sec 4 / Sec 6 (S-7): if this assertion is RED instead
        of the gate above, the gate is not measuring truncation and #47
        must close WONTFIX -- that is a STOP condition for the campaign,
        not something this test should be tuned to avoid.
        """
        result = run_small_corpus_control(data_root=str(tmp_path / "corpus-scaling-gate-small"))

        assert result["verdict"] == "measured", result
        assert result["planted_recovered"] is True, result

        liveness = result["liveness"]
        assert liveness["layers"] == ["episodic"], liveness
        assert liveness["edge_count"] == 0, liveness
        assert liveness["node_count"] > 0, liveness
        assert liveness["completion"] is False, liveness
        # The control's whole point: corpus fits entirely inside candidate_k,
        # so nothing is arm-truncated.
        assert liveness["corpus_size"] < liveness["candidate_k"], liveness
        assert liveness["sparse_arm_size"] == liveness["corpus_size"], liveness


class TestFetchWidthFloorUnchanged:
    def test_floor_not_touched_by_the_gate(self, tmp_path: Path) -> None:
        """AC-316: FETCH_WIDTH_FLOOR is READ (to compute candidate_k the
        same way retrieve.py does), never WRITTEN. Unlike fusion_gate.py's
        `run_floor_probe` (which monkeypatches it under try/finally), this
        gate has no patch/restore dance to get right in the first place --
        this test proves that in the same process, not merely by
        inspection: FETCH_WIDTH_FLOOR reads 10 before AND after the gate
        runs at both corpus sizes."""
        import crystalium.aetheryte.retrieve as retrieve_mod

        before = retrieve_mod.FETCH_WIDTH_FLOOR
        assert before == 10, before

        run(data_root=str(tmp_path / "corpus-scaling-gate-floor-large"))
        run_small_corpus_control(data_root=str(tmp_path / "corpus-scaling-gate-floor-small"))

        after = retrieve_mod.FETCH_WIDTH_FLOOR
        assert after == 10, ("leaked monkeypatch", before, after)
