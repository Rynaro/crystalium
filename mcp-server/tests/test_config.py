"""Tests for Config dataclass — YAML loading, env-var overrides, traversal guard.

Container-first: run via:
  docker compose run --rm crystalium pytest mcp-server/tests/test_config.py -v
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from crystalium.config import Config


# ---------------------------------------------------------------------------
# Default construction
# ---------------------------------------------------------------------------


class TestConfigDefaults:
    def test_default_transport(self, tmp_path: Path) -> None:
        cfg = Config(data_dir=tmp_path)
        assert cfg.transport == "stdio"

    def test_default_slots(self, tmp_path: Path) -> None:
        cfg = Config(data_dir=tmp_path)
        assert cfg.slots["executive"] == 300
        assert cfg.slots["procedural"] == 600
        assert cfg.slots["semantic"] == 800
        assert cfg.slots["episodic"] == 800
        assert cfg.slots["execution"] == 1000
        assert cfg.slots["buffer"] == 300
        assert cfg.total_cap == 3500

    def test_default_importance_weights(self, tmp_path: Path) -> None:
        cfg = Config(data_dir=tmp_path)
        assert cfg.importance_weights == (0.25, 0.30, 0.25, 0.20)
        assert cfg.importance_recency_halflife_days == 14.0

    def test_default_rate_limit(self, tmp_path: Path) -> None:
        cfg = Config(data_dir=tmp_path)
        assert cfg.rate_limit_per_minute == 200

    def test_default_human_confirm_window(self, tmp_path: Path) -> None:
        cfg = Config(data_dir=tmp_path)
        assert cfg.human_confirm_default_window_days == 30

    def test_data_dir_created(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "crystalium" / "test-proj"
        cfg = Config(data_dir=data_dir)
        assert cfg.data_dir.exists()


# ---------------------------------------------------------------------------
# YAML loading
# ---------------------------------------------------------------------------


class TestConfigFromYaml:
    def test_from_yaml_overrides_transport(self, tmp_path: Path) -> None:
        yaml_file = tmp_path / "crystalium.yaml"
        yaml_file.write_text("transport: http\n")
        cfg = Config.from_yaml(yaml_file)
        assert cfg.transport == "http"

    def test_from_yaml_overrides_slots(self, tmp_path: Path) -> None:
        yaml_file = tmp_path / "crystalium.yaml"
        yaml_file.write_text(
            "slots:\n"
            "  executive: 500\n"
            "  procedural: 600\n"
            "  semantic: 800\n"
            "  episodic: 800\n"
            "  execution: 1000\n"
            "  buffer: 300\n"
        )
        cfg = Config.from_yaml(yaml_file)
        assert cfg.slots["executive"] == 500

    def test_from_yaml_overrides_k_corroboration(self, tmp_path: Path) -> None:
        yaml_file = tmp_path / "crystalium.yaml"
        yaml_file.write_text("k_corroboration: 5\n")
        cfg = Config.from_yaml(yaml_file)
        assert cfg.k_corroboration == 5

    def test_from_yaml_unknown_keys_ignored(self, tmp_path: Path) -> None:
        yaml_file = tmp_path / "crystalium.yaml"
        yaml_file.write_text("unknown_key: surprise\ntransport: stdio\n")
        # Should not raise
        cfg = Config.from_yaml(yaml_file)
        assert cfg.transport == "stdio"


# ---------------------------------------------------------------------------
# Env-var overrides
# ---------------------------------------------------------------------------


class TestConfigFromEnv:
    def test_from_env_reads_transport(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CRYSTALIUM_TRANSPORT", "http")
        cfg = Config.from_env()
        assert cfg.transport == "http"

    def test_from_env_reads_rate_limit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CRYSTALIUM_RATE_LIMIT_PER_MINUTE", "50")
        cfg = Config.from_env()
        assert cfg.rate_limit_per_minute == 50

    def test_from_env_reads_halflife(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CRYSTALIUM_RECENCY_HALFLIFE_DAYS", "7.0")
        cfg = Config.from_env()
        assert cfg.importance_recency_halflife_days == 7.0

    def test_from_env_defaults_when_not_set(self) -> None:
        # Ensure env vars are not set (they may not be in test env)
        for k in (
            "CRYSTALIUM_TRANSPORT",
            "CRYSTALIUM_RATE_LIMIT_PER_MINUTE",
            "CRYSTALIUM_RECENCY_HALFLIFE_DAYS",
        ):
            os.environ.pop(k, None)
        cfg = Config.from_env()
        assert cfg.transport == "stdio"
        assert cfg.rate_limit_per_minute == 200
        assert cfg.importance_recency_halflife_days == 14.0


# ---------------------------------------------------------------------------
# is_in_repo traversal guard
# ---------------------------------------------------------------------------


class TestIsInRepo:
    def test_valid_path_inside_repo(self, tmp_path: Path) -> None:
        repo = tmp_path / "myrepo"
        repo.mkdir()
        subdir = repo / "src"
        subdir.mkdir()
        (subdir / "foo.py").write_text("# test")
        cfg = Config(data_dir=tmp_path / "data", repo_root=repo)
        assert cfg.is_in_repo(subdir / "foo.py") is True

    def test_path_traversal_rejected(self, tmp_path: Path) -> None:
        repo = tmp_path / "myrepo"
        repo.mkdir()
        (repo / "file.py").write_text("x")
        cfg = Config(data_dir=tmp_path / "data", repo_root=repo)
        # Absolute path outside repo
        assert cfg.is_in_repo(tmp_path / "etc" / "passwd") is False

    def test_dotdot_traversal_rejected(self, tmp_path: Path) -> None:
        repo = tmp_path / "myrepo"
        repo.mkdir()
        cfg = Config(data_dir=tmp_path / "data", repo_root=repo)
        # ../something attempts to escape
        assert cfg.is_in_repo(repo / ".." / "secret") is False

    def test_nonexistent_path_rejected(self, tmp_path: Path) -> None:
        """resolve(strict=True) raises OSError for non-existent paths."""
        repo = tmp_path / "myrepo"
        repo.mkdir()
        cfg = Config(data_dir=tmp_path / "data", repo_root=repo)
        nonexistent = repo / "does_not_exist" / "file.py"
        # strict=True means OSError → is_in_repo returns False
        assert cfg.is_in_repo(nonexistent) is False

    def test_symlink_escape_rejected(self, tmp_path: Path) -> None:
        """Symlink that points outside the repo is rejected by resolve(strict=True)."""
        repo = tmp_path / "myrepo"
        repo.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("secret")
        # Create symlink inside repo pointing outside
        symlink = repo / "escape_link"
        symlink.symlink_to(outside)
        cfg = Config(data_dir=tmp_path / "data", repo_root=repo)
        assert cfg.is_in_repo(symlink / "secret.txt") is False

    def test_no_repo_root_allows_all(self, tmp_path: Path) -> None:
        """When repo_root is None, is_in_repo returns True (dev/test mode)."""
        cfg = Config(data_dir=tmp_path, repo_root=None)
        assert cfg.is_in_repo(Path("/etc/passwd")) is True


# ---------------------------------------------------------------------------
# human_confirm_active
# ---------------------------------------------------------------------------


class TestHumanConfirmActive:
    def test_active_within_30_days(self, tmp_path: Path) -> None:
        install_ts = datetime(2026, 5, 1, tzinfo=timezone.utc)
        cfg = Config(data_dir=tmp_path, install_ts=install_ts)
        now = datetime(2026, 5, 20, tzinfo=timezone.utc)  # 19 days later
        assert cfg.human_confirm_active(now=now) is True

    def test_inactive_after_30_days(self, tmp_path: Path) -> None:
        install_ts = datetime(2026, 4, 1, tzinfo=timezone.utc)
        cfg = Config(data_dir=tmp_path, install_ts=install_ts)
        now = datetime(2026, 5, 28, tzinfo=timezone.utc)  # 57 days later
        assert cfg.human_confirm_active(now=now) is False

    def test_inactive_without_install_ts(self, tmp_path: Path) -> None:
        cfg = Config(data_dir=tmp_path, install_ts=None)
        assert cfg.human_confirm_active() is False

    def test_install_ts_loaded_from_file(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        # Write a recent install.ts
        ts = datetime.now(timezone.utc)
        (data_dir / "install.ts").write_text(ts.isoformat())
        cfg = Config(data_dir=data_dir)
        assert cfg.install_ts is not None
        assert cfg.human_confirm_active() is True

    def test_window_boundary_exact(self, tmp_path: Path) -> None:
        install_ts = datetime(2026, 5, 1, tzinfo=timezone.utc)
        cfg = Config(data_dir=tmp_path, install_ts=install_ts)
        # Exactly 30 days later — should be inactive (< not <=)
        now = install_ts + timedelta(days=30)
        assert cfg.human_confirm_active(now=now) is False
        # One second before boundary — should be active
        almost = now - timedelta(seconds=1)
        assert cfg.human_confirm_active(now=almost) is True


class TestEvbFlag:
    """W2: evb_enabled flag + EVB proxy weights (EARNED ON in T2 — the
    discriminating evb_gate shows EVB strictly improves retained-set purity with no
    high-value-retention regression; the original promotion/retention criterion
    saturated and could not discriminate)."""

    def test_evb_enabled_by_default(self, tmp_path: Path) -> None:
        cfg = Config(data_dir=tmp_path)
        assert cfg.evb_enabled is True
        assert len(cfg.evb_gain_weights) == 3
        assert len(cfg.evb_need_weights) == 3

    def test_evb_enabled_from_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CRYSTALIUM_EVB_ENABLED", "true")
        monkeypatch.setenv("CRYSTALIUM_DATA_DIR", str(tmp_path / "d"))
        cfg = Config.from_env()
        assert cfg.evb_enabled is True

    def test_evb_from_dict(self) -> None:
        cfg = Config._from_dict({
            "evb_enabled": True,
            "evb_gain_weights": [0.4, 0.4, 0.2],
        })
        assert cfg.evb_enabled is True
        assert cfg.evb_gain_weights == (0.4, 0.4, 0.2)


class TestDreamFlags:
    """W3 Dream-intelligence flags (default OFF — ablation-or-revert)."""

    def test_dream_flags_off_by_default(self, tmp_path: Path) -> None:
        cfg = Config(data_dir=tmp_path)
        assert cfg.dream_replay_evb is False
        assert cfg.dream_interleave is False
        assert cfg.dream_stc is False
        assert cfg.dream_interleave_ratio == 0.5
        assert cfg.stc_threshold == 0.5
        assert cfg.stc_window_s == 3600

    def test_dream_flags_from_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CRYSTALIUM_DREAM_REPLAY_EVB", "true")
        monkeypatch.setenv("CRYSTALIUM_DREAM_INTERLEAVE", "true")
        monkeypatch.setenv("CRYSTALIUM_DREAM_INTERLEAVE_RATIO", "0.25")
        monkeypatch.setenv("CRYSTALIUM_DREAM_STC", "true")
        monkeypatch.setenv("CRYSTALIUM_STC_WINDOW_S", "120")
        monkeypatch.setenv("CRYSTALIUM_DATA_DIR", str(tmp_path / "d"))
        cfg = Config.from_env()
        assert cfg.dream_replay_evb is True
        assert cfg.dream_interleave is True
        assert cfg.dream_interleave_ratio == 0.25
        assert cfg.dream_stc is True
        assert cfg.stc_window_s == 120

    def test_dream_flags_from_dict(self) -> None:
        cfg = Config._from_dict({
            "dream_replay_evb": True,
            "dream_interleave_ratio": 0.1,
            "stc_threshold": 0.7,
            "stc_window_s": 60,
        })
        assert cfg.dream_replay_evb is True
        assert cfg.dream_interleave_ratio == 0.1
        assert cfg.stc_threshold == 0.7
        assert cfg.stc_window_s == 60


class TestForgettingFlags:
    """W4 forgetting-faculty flags (default OFF — ablation-or-revert)."""

    def test_forgetting_off_by_default(self, tmp_path: Path) -> None:
        cfg = Config(data_dir=tmp_path)
        assert cfg.forgetting_fsrs is False
        assert cfg.r_floor == 0.7
        assert cfg.evb_percentile == 0.5
        assert cfg.resurface_floor == 0.85

    def test_forgetting_from_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CRYSTALIUM_FORGETTING_FSRS", "true")
        monkeypatch.setenv("CRYSTALIUM_R_FLOOR", "0.6")
        monkeypatch.setenv("CRYSTALIUM_DATA_DIR", str(tmp_path / "d"))
        cfg = Config.from_env()
        assert cfg.forgetting_fsrs is True
        assert cfg.r_floor == 0.6

    def test_forgetting_from_dict(self) -> None:
        cfg = Config._from_dict({
            "forgetting_fsrs": True,
            "evb_percentile": 0.25,
            "fsrs_boost_factor": 2.0,
        })
        assert cfg.forgetting_fsrs is True
        assert cfg.evb_percentile == 0.25
        assert cfg.fsrs_boost_factor == 2.0


class TestRetrievalFlags:
    """W5 retrieval-intelligence flags (default OFF — ablation-or-revert)."""

    def test_retrieval_defaults(self, tmp_path: Path) -> None:
        # W5 ablation outcome (updated T2): dedup-merge + completion won their gates
        # confound-free -> ON by default; context (no rank lift) + prefetch (cache-
        # confounded) stay OFF. See evals/BENCH-NOTES.md "W5 retrieval-faculty gates".
        cfg = Config(data_dir=tmp_path)
        assert cfg.recall_completion is True      # EARNED ON (T2): multi-hop walk lifts F1
        assert cfg.recall_context_match is False  # no rank lift -> stays OFF
        assert cfg.write_dedup_merge is True
        assert cfg.recall_prefetch is False
        assert cfg.completion_max_hops == 2
        assert cfg.completion_decay == 0.5
        assert cfg.sep_threshold == 0.92

    def test_retrieval_from_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CRYSTALIUM_RECALL_COMPLETION", "true")
        monkeypatch.setenv("CRYSTALIUM_COMPLETION_MAX_HOPS", "3")
        monkeypatch.setenv("CRYSTALIUM_WRITE_DEDUP_MERGE", "true")
        monkeypatch.setenv("CRYSTALIUM_SEP_THRESHOLD", "0.88")
        monkeypatch.setenv("CRYSTALIUM_DATA_DIR", str(tmp_path / "d"))
        cfg = Config.from_env()
        assert cfg.recall_completion is True
        assert cfg.completion_max_hops == 3
        assert cfg.write_dedup_merge is True
        assert cfg.sep_threshold == 0.88

    def test_retrieval_from_dict(self) -> None:
        cfg = Config._from_dict({
            "recall_context_match": True,
            "recall_prefetch": True,
            "completion_decay": 0.7,
        })
        assert cfg.recall_context_match is True
        assert cfg.recall_prefetch is True
        assert cfg.completion_decay == 0.7


class TestRecallRelevancePrimary:
    """crystalium#36 / DP-2: relevance-primary composition flag. Default ON —
    earned by CORRECTNESS (a fresh crystal must be retrievable), not an
    ablation win, unlike every other W5/W6 flag above."""

    def test_default_true(self, tmp_path: Path) -> None:
        cfg = Config(data_dir=tmp_path)
        assert cfg.recall_relevance_primary is True

    def test_from_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CRYSTALIUM_RECALL_RELEVANCE_PRIMARY", "false")
        monkeypatch.setenv("CRYSTALIUM_DATA_DIR", str(tmp_path / "d"))
        cfg = Config.from_env()
        assert cfg.recall_relevance_primary is False

    def test_from_env_default_true_when_unset(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CRYSTALIUM_RECALL_RELEVANCE_PRIMARY", raising=False)
        monkeypatch.setenv("CRYSTALIUM_DATA_DIR", str(tmp_path / "d"))
        cfg = Config.from_env()
        assert cfg.recall_relevance_primary is True

    def test_from_dict(self) -> None:
        cfg = Config._from_dict({"recall_relevance_primary": False})
        assert cfg.recall_relevance_primary is False


class TestFusionConfig:
    """crystalium#38 (FORGE deliberation.md DP-1..DP-9): weighted RRF fusion
    config surface — AC-127, parameterised over all four new fields crossed
    with BOTH documented sources (env var + `crystalium.yaml` / `_from_dict`).
    The YAML/`_from_dict` half is the one that silently fails when a field is
    wired only into `from_env` and left out of the `bool_field`/`float_field`
    allowlists (spec.md §D6's G-4 finding)."""

    def test_defaults(self, tmp_path: Path) -> None:
        cfg = Config(data_dir=tmp_path)
        assert cfg.recall_weighted_fusion is True
        assert cfg.fusion_weight_dense == 1.0
        assert cfg.fusion_weight_derived == 1.0
        assert cfg.fusion_sparse_boost_alpha == 1.0

    def test_recall_weighted_fusion_from_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CRYSTALIUM_RECALL_WEIGHTED_FUSION", "false")
        monkeypatch.setenv("CRYSTALIUM_DATA_DIR", str(tmp_path / "d"))
        cfg = Config.from_env()
        assert cfg.recall_weighted_fusion is False

    def test_recall_weighted_fusion_from_dict(self) -> None:
        cfg = Config._from_dict({"recall_weighted_fusion": False})
        assert cfg.recall_weighted_fusion is False

    def test_recall_weighted_fusion_default_true_when_unset(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("CRYSTALIUM_RECALL_WEIGHTED_FUSION", raising=False)
        monkeypatch.setenv("CRYSTALIUM_DATA_DIR", str(tmp_path / "d"))
        cfg = Config.from_env()
        assert cfg.recall_weighted_fusion is True

    def test_fusion_weight_dense_from_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CRYSTALIUM_FUSION_WEIGHT_DENSE", "0.8")
        monkeypatch.setenv("CRYSTALIUM_DATA_DIR", str(tmp_path / "d"))
        cfg = Config.from_env()
        assert cfg.fusion_weight_dense == 0.8

    def test_fusion_weight_dense_from_dict(self) -> None:
        cfg = Config._from_dict({"fusion_weight_dense": 0.8})
        assert cfg.fusion_weight_dense == 0.8

    def test_fusion_weight_derived_from_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CRYSTALIUM_FUSION_WEIGHT_DERIVED", "0.95")
        monkeypatch.setenv("CRYSTALIUM_DATA_DIR", str(tmp_path / "d"))
        cfg = Config.from_env()
        assert cfg.fusion_weight_derived == 0.95

    def test_fusion_weight_derived_from_dict(self) -> None:
        # 0.95 stays LEGAL config (deliberation.md DP-2) even though it is
        # outside the documented/supported band — no validator, no clamp.
        cfg = Config._from_dict({"fusion_weight_derived": 0.95})
        assert cfg.fusion_weight_derived == 0.95

    def test_fusion_sparse_boost_alpha_from_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CRYSTALIUM_FUSION_SPARSE_BOOST_ALPHA", "0.0")
        monkeypatch.setenv("CRYSTALIUM_DATA_DIR", str(tmp_path / "d"))
        cfg = Config.from_env()
        assert cfg.fusion_sparse_boost_alpha == 0.0

    def test_fusion_sparse_boost_alpha_from_dict(self) -> None:
        cfg = Config._from_dict({"fusion_sparse_boost_alpha": 0.0})
        assert cfg.fusion_sparse_boost_alpha == 0.0

    def test_all_four_from_dict_together(self) -> None:
        """The `_from_dict` allowlist regression this AC exists to guard:
        every one of the four fields must round-trip through the SAME dict
        source `crystalium.yaml` loads through — a field present in the
        dataclass and in `from_env` but absent from `_from_dict`'s
        `bool_field`/`float_field` tuples is silently ignored from YAML."""
        cfg = Config._from_dict({
            "recall_weighted_fusion": False,
            "fusion_weight_dense": 0.5,
            "fusion_weight_derived": 0.5,
            "fusion_sparse_boost_alpha": 2.0,
        })
        assert cfg.recall_weighted_fusion is False
        assert cfg.fusion_weight_dense == 0.5
        assert cfg.fusion_weight_derived == 0.5
        assert cfg.fusion_sparse_boost_alpha == 2.0


class TestSecurityFlags:
    """W6 security & integrity hardening flags (default OFF — ablation-or-revert)."""

    def test_security_defaults(self, tmp_path: Path) -> None:
        # W6 ablation outcome: recall_active_only won its ASR gate (1.0->0.0) AND is
        # a correctness fix -> ON by default. drift_detect (detect-only; band needs
        # tuning) + write_conflict_detect (LWW inversion risk; not isolated by the
        # gate) stay OFF. See evals/BENCH-NOTES.md "W6 security & integrity gates".
        cfg = Config(data_dir=tmp_path)
        assert cfg.drift_detect is False
        assert cfg.write_conflict_detect is False
        assert cfg.recall_active_only is True
        assert cfg.drift_tau_lo == 0.80
        assert cfg.drift_tau_hi == 0.97
        assert cfg.conflict_tau_lo == 0.80

    def test_security_from_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CRYSTALIUM_DRIFT_DETECT", "true")
        monkeypatch.setenv("CRYSTALIUM_DRIFT_TAU_LO", "0.75")
        monkeypatch.setenv("CRYSTALIUM_WRITE_CONFLICT_DETECT", "true")
        monkeypatch.setenv("CRYSTALIUM_RECALL_ACTIVE_ONLY", "true")
        monkeypatch.setenv("CRYSTALIUM_DATA_DIR", str(tmp_path / "d"))
        cfg = Config.from_env()
        assert cfg.drift_detect is True
        assert cfg.drift_tau_lo == 0.75
        assert cfg.write_conflict_detect is True
        assert cfg.recall_active_only is True

    def test_security_from_dict(self) -> None:
        cfg = Config._from_dict({
            "drift_detect": True,
            "recall_active_only": True,
            "conflict_tau_lo": 0.85,
        })
        assert cfg.drift_detect is True
        assert cfg.recall_active_only is True
        assert cfg.conflict_tau_lo == 0.85


class TestDefaultParity:
    """W8: the dataclass default and the from_env default must agree for EVERY
    augment flag — otherwise an env-built config silently reverts an ablation flip
    (the W8 C1 bug). Regression guard against future drift."""

    _FLAGS = (
        "evb_enabled", "dream_replay_evb", "dream_interleave", "dream_stc",
        "forgetting_fsrs", "recall_completion", "recall_context_match",
        "write_dedup_merge", "recall_prefetch", "drift_detect",
        "write_conflict_detect", "recall_active_only",
        "recall_weighted_fusion",  # crystalium#38
        "recall_seed_derived_credit",  # crystalium#42
    )

    def test_dataclass_default_equals_from_env_default(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Clear every CRYSTALIUM_* env var so from_env sees pure defaults.
        for key in list(os.environ):
            if key.startswith("CRYSTALIUM_"):
                monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("CRYSTALIUM_DATA_DIR", str(tmp_path / "p"))
        dataclass_cfg = Config(data_dir=tmp_path / "d")
        env_cfg = Config.from_env()
        for flag in self._FLAGS:
            assert getattr(dataclass_cfg, flag) == getattr(env_cfg, flag), (
                f"default drift on {flag!r}: dataclass={getattr(dataclass_cfg, flag)} "
                f"!= from_env={getattr(env_cfg, flag)}"
            )

    def test_winning_flips_default_on(self, tmp_path: Path) -> None:
        # The two ablation winners must be ON by default in BOTH constructors.
        assert Config(data_dir=tmp_path / "a").write_dedup_merge is True
        assert Config(data_dir=tmp_path / "b").recall_active_only is True
