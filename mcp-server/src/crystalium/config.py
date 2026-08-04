"""Configuration for the CRYSTALIUM MCP server.

Loaded from crystalium.yaml (sibling to the repo root) with env-var overrides.
All config defaults are traceable to FORGE decisions (D1..D9) or MISSION P0 anchors.

Pattern mirrors atlas-aci/mcp-server/src/atlas_aci/config.py (FINDING-001).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_yaml(path: Path) -> dict[str, Any]:
    """Load a YAML file using PyYAML or raise ImportError with a helpful message."""
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError as exc:
        raise ImportError(
            "PyYAML is required to load crystalium.yaml. "
            "Install it: pip install pyyaml"
        ) from exc
    with path.open() as fh:
        return yaml.safe_load(fh) or {}


def _env(key: str, default: str | None = None) -> str | None:
    return os.environ.get(key, default)


def _env_int(key: str, default: int) -> int:
    val = os.environ.get(key)
    return int(val) if val is not None else default


def _env_float(key: str, default: float) -> float:
    val = os.environ.get(key)
    return float(val) if val is not None else default


def _env_bool(key: str, default: bool) -> bool:
    val = os.environ.get(key)
    if val is None:
        return default
    return val.lower() not in ("0", "false", "no", "")


def _env_bool_optional(key: str) -> bool | None:
    """Tri-state env lookup: unset -> None ("follow the linked flag"); set -> bool.

    Used for `link_cooccurrence` (crystalium#43): `None` is not "false", it is
    "no opinion — inherit today's `recall_completion`-derived wiring".
    """
    val = os.environ.get(key)
    if val is None:
        return None
    return val.lower() not in ("0", "false", "no", "")


# ---------------------------------------------------------------------------
# Caller identity env vars (v1.2.0) — process-level identity for non-ingest
# MCP calls. These drive server._extract_caller_identity(), not Config fields,
# so they are documented here for discoverability alongside the other
# CRYSTALIUM_* vars but are NOT surfaced as Config fields.
#
#   CRYSTALIUM_CALLER_EIDOLON  — Eidolon name (e.g. "atlas", "spectra").
#       Mapped through the roster sets in ingest_adapter to T0/T1/T3.
#       Absent → identity contributes no constraint (env-tier drives result).
#
#   CRYSTALIUM_CALLER_TIER  — Explicit trust tier override: "T0", "T1", "T2",
#       or "T3" (case-insensitive). Parsed via Tier.from_str().
#       MIN-trust rule: final tier = max(declared, identity_tier).
#       Absent → identity tier drives result.
#
#   If neither is set → T2 default (D4 conservative; backward-compatible).
# ---------------------------------------------------------------------------
# _env("CRYSTALIUM_CALLER_EIDOLON")  — see server._extract_caller_identity()
# _env("CRYSTALIUM_CALLER_TIER")     — see server._extract_caller_identity()


# ---------------------------------------------------------------------------
# Config dataclass
# ---------------------------------------------------------------------------


@dataclass
class Config:
    """Server-wide configuration. One instance per server process.

    Default values trace to FORGE decisions:
    - transport, idle_threshold_s, min_dream_gap_s, dream_tick_s: D2, D3
    - ecl_version: D4
    - skill_invoke_*: D5
    - importance_weights, importance_recency_halflife_days: D6 (FROZEN)
    - k_corroboration, human_confirm_default_window_days: D8
    - slots, total_cap: D9 / MISSION.md §Bounded slotted working set
    - rate_limit_per_minute: MISSION P0-7 (atlas-aci pattern)
    """

    # Transport (D2) — "stdio" (default) or "http"/"streamable-http" (v0.2, Streamable-HTTP).
    transport: str = "stdio"

    # Streamable-HTTP transport binding (used only when transport != "stdio").
    http_host: str = "127.0.0.1"
    http_port: int = 8848
    http_path: str = "/mcp"
    # json_response=True returns plain JSON (no SSE) — simpler for single request/response
    # clients and smoke tests. stateless=True serves each request in its own transient
    # session (no server-side session continuity required).
    http_json_response: bool = True
    http_stateless: bool = True

    # Dream scheduler (D3)
    idle_threshold_s: int = 300
    min_dream_gap_s: int = 1800
    dream_tick_s: int = 60

    # ECL (D4)
    ecl_version: str = "2.0"

    # skill_invoke sandbox (D5)
    skill_invoke_timeout_s: int = 30
    skill_invoke_output_cap_bytes: int = 8192

    # Importance weights (D6) — FROZEN SIGNATURE; only WEIGHTS tuple is the D11 swap point.
    # [UNVERIFIED] that halflife=14 is appropriate for long-lived workspaces (OQ-4).
    importance_weights: tuple[float, float, float, float] = (0.25, 0.30, 0.25, 0.20)
    importance_recency_halflife_days: float = 14.0

    # Corroboration / promotion gate (D8)
    k_corroboration: int = 3
    human_confirm_default_window_days: int = 30

    # Working-set slot caps (D9, MISSION §Bounded slotted working set)
    slots: dict[str, int] = field(
        default_factory=lambda: {
            "executive": 300,
            "procedural": 600,
            "semantic": 800,
            "episodic": 800,
            "execution": 1000,
            "buffer": 300,
        }
    )
    total_cap: int = 3500

    # Storage paths — resolved relative to data_dir at __post_init__
    data_dir: Path = field(default_factory=lambda: Path("~/.crystalium/default/").expanduser())

    # Embedding backend — sentence-transformers by default; ollama raises NotImplementedError
    embed_backend: str = "sentence-transformers"

    # Reranker (W4 TODO per spec.yaml §config_defaults; D9 / MISSION.md:106)
    # Enabled when k_gt exceeds this threshold; false by default to keep hot path lean.
    reranker_enabled: bool = False

    # EVB importance (W2, Mattar & Daw 2018). When True, evb_score replaces the
    # legacy importance_score as the single source of truth for eviction + the
    # composer (the roadmap's "importance.mode: legacy|evb"). EARNED ON (T2): the
    # discriminating evb_gate shows EVB strictly improves retained-set purity
    # (retention_precision 1.0 vs legacy 0.33) with NO high-value-retention
    # regression — the original promotion/high-value criterion saturated at 1.0 in
    # both arms (non-discriminating). See DESIGN-RATIONALE §D6.1 / BENCH-NOTES W2.
    evb_enabled: bool = True
    # Hand-tuned EVB proxy weights (SFMA-style). gain over
    # (outcome_success, novelty, corroboration_potential); need over
    # (recency, access_frequency, predicted_next_task_match). Sum within each
    # need not be 1.0 (evb is unbounded). The D11-style swap point for W2.
    evb_gain_weights: tuple[float, float, float] = (0.5, 0.3, 0.2)
    evb_need_weights: tuple[float, float, float] = (0.5, 0.3, 0.2)

    # Dream intelligence (W3) — each augment behind its own flag, default OFF
    # (ablation-or-revert). The Dream still only PROPOSES through the gate.
    # dream_replay_evb realizes the roadmap's "dream.replay=evb": order the
    # consolidation queue by EVB (reverse replay of high-Gain episodes).
    dream_replay_evb: bool = False
    # Interleaved replay (Complementary Learning Systems): mix a sampled ratio of
    # existing semantic facts into each consolidation batch to resist interference.
    dream_interleave: bool = False
    dream_interleave_ratio: float = 0.5
    # Synaptic tagging & capture: a salient (EVB >= stc_threshold) episode lowers
    # the promotion threshold (k_override) for episodes within stc_window_s.
    dream_stc: bool = False
    stc_threshold: float = 0.5
    stc_window_s: int = 3600

    # Forgetting faculty (W4, FSRS/DSR). When True, FSRS decay drives a
    # value-aware eviction (R < r_floor AND EVB below percentile — never age
    # alone), recall boosts stability, and aging-but-valuable crystals are
    # re-surfaced. Default OFF (ablation-or-revert). Realizes "forgetting.mode=fsrs".
    forgetting_fsrs: bool = False
    r_floor: float = 0.7              # evict only when retrievability dips below this
    resurface_floor: float = 0.85    # re-surface aging crystals before R crosses r_floor
    evb_percentile: float = 0.5      # evict only when EVB below this percentile of active set
    fsrs_initial_stability: float = 2.0   # days; R=0.9 after this elapsed at first
    fsrs_initial_difficulty: float = 0.3
    fsrs_boost_factor: float = 1.5   # stability multiplier on successful recall (reconsolidation)
    fsrs_lapse_stability: float = 0.5  # stability reset on a lapse (R<r_floor at access)

    # Retrieval intelligence (W5, Aetheryte II) — each faculty behind its own flag,
    # default OFF (ablation-or-revert). The recall pipeline + chokepoint are unchanged
    # when all are off.
    # crystalium#43 (2026-08-04): the "EARNED-ON" claim this field used to
    # carry here (retrieval_gate F1 0.12->0.18, recall 0.67->1.0) was measured
    # on a CONFOUNDED gate — evals/retrieval_gate.py pre-fix wired
    # link_cooccurrence=recall_completion, so the completion arm silently
    # carried ~150 extra co-occurrence LINKS_TO edges the flat arm did not;
    # "completion vs flat" was two different graphs, not one faculty
    # ablation. Deconfounded (edges pinned OFF in every arm via the new
    # Config.link_cooccurrence above), the measured lift DOES NOT SURVIVE on
    # this worktree: multihop_f1 completion == flat == 0.30769230769230765
    # (test_retrieval_gate.py). Note this worktree still carries the
    # pre-crystalium#41 neighbor_expand first-seed-abort bug (fixed in a
    # separate campaign unit) — the formal post-#41, 7-seed remeasurement is
    # eval-baseline-deconfounded.json (crystalium#43 AC-247), owned by the
    # release orchestrator, and may revise this finding.
    # RETRACTED: the EARNED-ON claim above no longer holds as measured.
    # FORGE's pre-ruling stands regardless: the shipped default stays True
    # this campaign — flipping it is release-coupled and out of #43's scope.
    # A follow-up issue re-assessing this default once #41 lands must be
    # filed (see test_retrieval_gate.py's retraction and BENCH-NOTES §W5(i)).
    recall_completion: bool = True         # default retained per FORGE ruling; earned-ON claim RETRACTED (crystalium#43) — see comment above
    completion_max_hops: int = 2
    completion_decay: float = 0.5
    # crystalium#43 (retrieval-gate deconfound): decouples the write-time
    # co-occurrence LINKS_TO edge flag from the recall_completion faculty
    # flag under test. None (default) means "follow recall_completion" —
    # i.e. today's wiring at server.py::_build_components — so this default
    # is BEHAVIOUR-NEUTRAL for every existing caller; only an explicit
    # True/False overrides it. Exists so evals/retrieval_gate.py can pin
    # graph writes OFF in every arm without also disabling the recall-time
    # completion faculty it is trying to isolate.
    link_cooccurrence: bool | None = None
    recall_context_match: bool = False     # encoding-specificity: post-RRF context re-rank — stays OFF (T2: no rank lift in the discriminating gate)
    write_dedup_merge: bool = True         # W5 gate PASS (confound-free): merge near-dups at write
    sep_threshold: float = 0.92            # cosine above which a commit is a near-duplicate
    recall_prefetch: bool = False          # protention: pre-warm recall cache on plan_checkpoint
    # crystalium#36 fix (v1.9.0): relevance is the primary composition signal (the
    # RRF-fused query relevance gates + orders the working set), importance is
    # retained as the secondary/tiebreak signal. Gates seams 3+4+5 (top-k truncation,
    # relevance-primary eviction key, descending-relevance ordering) as ONE unit —
    # everything else in the fix (score, budget, k clamp, cold-start importance) is
    # ungated. Default ON: earned by CORRECTNESS (a fresh crystal must be retrievable
    # at all), not by an ablation win — unlike every other W5/W6 flag above, this one
    # is not an optional faculty. Set False for a one-line revert to pre-1.9.0
    # composition ordering, k behaviour and result sets (R-1).
    recall_relevance_primary: bool = True

    # crystalium#38 (FORGE deliberation.md DP-1..DP-9): weighted RRF fusion +
    # derived-family (graph/completion) min-rank merge, replacing unweighted
    # summation on the gated path. SUBSUMED under recall_relevance_primary
    # (D7): the weighted path activates only when BOTH are True —
    # recall_relevance_primary=False stays contractually byte-identical to
    # pre-1.9.0 (#36 AC-008/AC-009, frozen), so this flag is deliberately NOT
    # independent of it. Default ON: measured live on the real retrieval
    # gate (deliberation.md DP-4) to correct a live P1 inversion (a record
    # with ZERO lexical match outranking the exact BM25+dense match) with no
    # measured non-regression cost. Ship contingency (AC-136's frozen six —
    # AC-121, 122, 123, 124, 125, 133): any red on the implementation branch
    # flips this to False automatically and returns the change to FORGE.
    recall_weighted_fusion: bool = True
    # Per-arm fusion weights (D6). Deliberately NO fusion_weight_sparse: RRF
    # ordering is invariant to a global positive scale factor (score values
    # scale, the sort does not), so pinning the sparse arm's base weight at
    # 1.0 removes a redundant degree of freedom — `fusion_sparse_boost_alpha`
    # below is the sparse arm's only dial, and it is query-conditional
    # rather than a static weight.
    fusion_weight_dense: float = 1.0
    # Graph+completion derived-family weight (D2's min-rank merge).
    # MEASURED cliff (deliberation.md DP-2, real fusion-gate runs, 7 hash
    # seeds): 0.90 fails the gate deterministically; 0.95 is a FLAKE — green
    # at seeds 0-4/unset, RED at PYTHONHASHSEED=5, on a 1.0% score margin
    # against a completion-arm rank that is hash-nondeterministic because of
    # the OPEN anomaly-A bug in `neighbor_expand` (crystalium#38 follow-up
    # F-A; NOT fixed by this change). 1.00 passed 7/7 runs and carries the
    # §D2 identity property MEASURED BITWISE (20/20 in-process comparisons,
    # exact 0.0 score diff) — no other value has this property. Values
    # BELOW 1.0 remain LEGAL config (AC-127 must stay trivially satisfiable,
    # no validator/clamp) but are OUTSIDE the documented/supported band: they
    # forfeit the identity property and are flaky on the shipped gate. Values
    # ABOVE 1.0 re-create P1 (a derived-only record at w/61 can then outrank
    # a two-base-arm record at 2/61). No test, doc, or CHANGELOG line may
    # present a sub-1.0 value as a supported "precision dial" (deliberation.md
    # C-9).
    fusion_weight_derived: float = 1.0
    # Sparse-arm selectivity boost (D3): w_sparse resolves to
    # `1.0 + fusion_sparse_boost_alpha * selectivity`, selectivity in [0, 1]
    # (a search-space-local measure of how far the lexical arm narrowed the
    # corpus for THIS query — see aetheryte/retrieve.py::resolve_sparse_weight).
    # alpha=1.0 is the measured default (deliberation.md DP-3): it is MARGIN,
    # not the cure — D2's derived-family merge is what fixes the measured
    # inversion; the sparse boost only widens the margin on selective
    # queries. Do not raise it chasing headroom the measured fixture does
    # not need — every increment moves surfaced `score` magnitudes (DP-7).
    fusion_sparse_boost_alpha: float = 1.0

    # Security & integrity hardening (W6) — each defense behind its own flag,
    # default OFF (ablation-or-revert; the poisoning ASR gate is the arbiter).
    # All OFF -> recall pipeline, Dream, and semantic commit are byte-identical to W5.
    drift_detect: bool = False             # A-MemGuard belief-drift: flag same-subject lower-trust divergence
    drift_tau_lo: float = 0.80             # cosine floor of the "same-subject but divergent" band
    drift_tau_hi: float = 0.97             # cosine ceiling of that band (>=hi is a near-duplicate, not drift)
    write_conflict_detect: bool = False    # multi-agent write conflict: LWW supersede + record both lineages
    conflict_tau_lo: float = 0.80          # cosine floor for "same-subject" conflict candidacy
    recall_active_only: bool = True        # W6 gate PASS + correctness: exclude deprecated/superseded from recall

    # Rate limiting (P0-7, atlas-aci pattern)
    rate_limit_per_minute: int = 200

    # install_ts — written by install.sh; used by human_confirm_active()
    install_ts: datetime | None = None

    # Sandbox root — base directory for skill_invoke sandboxes (D5).
    # Production default: /sandbox. Tests override via Config(sandbox_root=tmp_path/"sandbox").
    sandbox_root: Path = field(default_factory=lambda: Path("/sandbox"))

    # Repo root — used by is_in_repo() traversal guard
    repo_root: Path | None = None

    def __post_init__(self) -> None:
        self.data_dir = self.data_dir.expanduser().resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # Load install_ts from <data_dir>/install.ts if present and not already set
        if self.install_ts is None:
            ts_file = self.data_dir / "install.ts"
            if ts_file.exists():
                try:
                    raw = ts_file.read_text().strip()
                    self.install_ts = datetime.fromisoformat(raw)
                    if self.install_ts.tzinfo is None:
                        self.install_ts = self.install_ts.replace(tzinfo=timezone.utc)
                except ValueError:
                    pass  # malformed timestamp; leave as None

        if self.repo_root is not None:
            self.repo_root = self.repo_root.resolve()

    # ------------------------------------------------------------------
    # Factory methods
    # ------------------------------------------------------------------

    @classmethod
    def from_yaml(cls, path: Path) -> "Config":
        """Load config from a YAML file at *path*."""
        data = _load_yaml(path)
        return cls._from_dict(data)

    @classmethod
    def from_env(cls) -> "Config":
        """Build config purely from environment variables (useful for tests)."""
        return cls(
            transport=_env("CRYSTALIUM_TRANSPORT", "stdio") or "stdio",
            http_host=_env("CRYSTALIUM_HTTP_HOST", "127.0.0.1") or "127.0.0.1",
            http_port=_env_int("CRYSTALIUM_HTTP_PORT", 8848),
            http_path=_env("CRYSTALIUM_HTTP_PATH", "/mcp") or "/mcp",
            http_json_response=_env_bool("CRYSTALIUM_HTTP_JSON_RESPONSE", True),
            http_stateless=_env_bool("CRYSTALIUM_HTTP_STATELESS", True),
            idle_threshold_s=_env_int("CRYSTALIUM_IDLE_THRESHOLD_S", 300),
            min_dream_gap_s=_env_int("CRYSTALIUM_MIN_DREAM_GAP_S", 1800),
            dream_tick_s=_env_int("CRYSTALIUM_DREAM_TICK_S", 60),
            ecl_version=_env("CRYSTALIUM_ECL_VERSION", "2.0") or "2.0",
            skill_invoke_timeout_s=_env_int("CRYSTALIUM_SKILL_INVOKE_TIMEOUT_S", 30),
            skill_invoke_output_cap_bytes=_env_int(
                "CRYSTALIUM_SKILL_INVOKE_OUTPUT_CAP_BYTES", 8192
            ),
            importance_recency_halflife_days=_env_float(
                "CRYSTALIUM_RECENCY_HALFLIFE_DAYS", 14.0
            ),
            k_corroboration=_env_int("CRYSTALIUM_K_CORROBORATION", 3),
            human_confirm_default_window_days=_env_int(
                "CRYSTALIUM_HUMAN_CONFIRM_WINDOW_DAYS", 30
            ),
            data_dir=Path(
                _env("CRYSTALIUM_DATA_DIR", "~/.crystalium/default/") or "~/.crystalium/default/"
            ).expanduser(),
            embed_backend=_env("CRYSTALIUM_EMBED_BACKEND", "sentence-transformers")
            or "sentence-transformers",
            rate_limit_per_minute=_env_int("CRYSTALIUM_RATE_LIMIT_PER_MINUTE", 200),
            evb_enabled=_env_bool("CRYSTALIUM_EVB_ENABLED", True),  # W2 earned ON (T2)
            dream_replay_evb=_env_bool("CRYSTALIUM_DREAM_REPLAY_EVB", False),
            dream_interleave=_env_bool("CRYSTALIUM_DREAM_INTERLEAVE", False),
            dream_interleave_ratio=_env_float("CRYSTALIUM_DREAM_INTERLEAVE_RATIO", 0.5),
            dream_stc=_env_bool("CRYSTALIUM_DREAM_STC", False),
            stc_threshold=_env_float("CRYSTALIUM_STC_THRESHOLD", 0.5),
            stc_window_s=_env_int("CRYSTALIUM_STC_WINDOW_S", 3600),
            forgetting_fsrs=_env_bool("CRYSTALIUM_FORGETTING_FSRS", False),
            r_floor=_env_float("CRYSTALIUM_R_FLOOR", 0.7),
            resurface_floor=_env_float("CRYSTALIUM_RESURFACE_FLOOR", 0.85),
            evb_percentile=_env_float("CRYSTALIUM_EVB_PERCENTILE", 0.5),
            recall_completion=_env_bool("CRYSTALIUM_RECALL_COMPLETION", True),  # default retained (crystalium#43 retracted the earned-ON claim; see config.py:211 comment)
            completion_max_hops=_env_int("CRYSTALIUM_COMPLETION_MAX_HOPS", 2),
            completion_decay=_env_float("CRYSTALIUM_COMPLETION_DECAY", 0.5),
            link_cooccurrence=_env_bool_optional("CRYSTALIUM_LINK_COOCCURRENCE"),  # crystalium#43; None = follow recall_completion
            recall_context_match=_env_bool("CRYSTALIUM_RECALL_CONTEXT_MATCH", False),
            write_dedup_merge=_env_bool("CRYSTALIUM_WRITE_DEDUP_MERGE", True),
            sep_threshold=_env_float("CRYSTALIUM_SEP_THRESHOLD", 0.92),
            recall_prefetch=_env_bool("CRYSTALIUM_RECALL_PREFETCH", False),
            recall_relevance_primary=_env_bool("CRYSTALIUM_RECALL_RELEVANCE_PRIMARY", True),
            recall_weighted_fusion=_env_bool("CRYSTALIUM_RECALL_WEIGHTED_FUSION", True),
            fusion_weight_dense=_env_float("CRYSTALIUM_FUSION_WEIGHT_DENSE", 1.0),
            fusion_weight_derived=_env_float("CRYSTALIUM_FUSION_WEIGHT_DERIVED", 1.0),
            fusion_sparse_boost_alpha=_env_float("CRYSTALIUM_FUSION_SPARSE_BOOST_ALPHA", 1.0),
            drift_detect=_env_bool("CRYSTALIUM_DRIFT_DETECT", False),
            drift_tau_lo=_env_float("CRYSTALIUM_DRIFT_TAU_LO", 0.80),
            drift_tau_hi=_env_float("CRYSTALIUM_DRIFT_TAU_HI", 0.97),
            write_conflict_detect=_env_bool("CRYSTALIUM_WRITE_CONFLICT_DETECT", False),
            conflict_tau_lo=_env_float("CRYSTALIUM_CONFLICT_TAU_LO", 0.80),
            recall_active_only=_env_bool("CRYSTALIUM_RECALL_ACTIVE_ONLY", True),
        )

    @classmethod
    def _from_dict(cls, data: dict[str, Any]) -> "Config":
        """Construct from a dict (e.g. from yaml.safe_load). Unknown keys are silently ignored."""
        kwargs: dict[str, Any] = {}

        for simple in (
            "transport",
            "ecl_version",
            "embed_backend",
            "http_host",
            "http_path",
        ):
            if simple in data:
                kwargs[simple] = data[simple]

        for bool_field in (
            "http_json_response", "http_stateless", "evb_enabled",
            "dream_replay_evb", "dream_interleave", "dream_stc",
            "forgetting_fsrs",
            "recall_completion", "recall_context_match", "write_dedup_merge", "recall_prefetch",
            "drift_detect", "write_conflict_detect", "recall_active_only",
            "recall_relevance_primary", "recall_weighted_fusion",
        ):
            if bool_field in data:
                kwargs[bool_field] = bool(data[bool_field])

        for weight_field in ("evb_gain_weights", "evb_need_weights"):
            if weight_field in data:
                kwargs[weight_field] = tuple(float(x) for x in data[weight_field])

        if "http_port" in data:
            kwargs["http_port"] = int(data["http_port"])

        for int_field in (
            "idle_threshold_s",
            "min_dream_gap_s",
            "dream_tick_s",
            "skill_invoke_timeout_s",
            "skill_invoke_output_cap_bytes",
            "k_corroboration",
            "human_confirm_default_window_days",
            "rate_limit_per_minute",
            "total_cap",
            "stc_window_s",
            "completion_max_hops",
        ):
            if int_field in data:
                kwargs[int_field] = int(data[int_field])

        for float_field in (
            "importance_recency_halflife_days",
            "dream_interleave_ratio",
            "stc_threshold",
            "r_floor",
            "resurface_floor",
            "evb_percentile",
            "fsrs_initial_stability",
            "fsrs_initial_difficulty",
            "fsrs_boost_factor",
            "fsrs_lapse_stability",
            "completion_decay",
            "sep_threshold",
            "drift_tau_lo",
            "drift_tau_hi",
            "conflict_tau_lo",
            "fusion_weight_dense",
            "fusion_weight_derived",
            "fusion_sparse_boost_alpha",
        ):
            if float_field in data:
                kwargs[float_field] = float(data[float_field])

        if "importance_weights" in data:
            w = data["importance_weights"]
            kwargs["importance_weights"] = tuple(float(x) for x in w)  # type: ignore[assignment]

        if "slots" in data:
            kwargs["slots"] = {k: int(v) for k, v in data["slots"].items()}

        if "data_dir" in data:
            kwargs["data_dir"] = Path(data["data_dir"]).expanduser()

        if "repo_root" in data:
            kwargs["repo_root"] = Path(data["repo_root"]).expanduser()

        if "install_ts" in data:
            ts = data["install_ts"]
            if isinstance(ts, str):
                dt = datetime.fromisoformat(ts)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                kwargs["install_ts"] = dt
            elif isinstance(ts, datetime):
                kwargs["install_ts"] = ts

        return cls(**kwargs)

    # ------------------------------------------------------------------
    # Guards and predicates
    # ------------------------------------------------------------------

    def is_in_repo(self, p: Path) -> bool:
        """Path-traversal guard.

        Resolves symlinks strictly (strict=True raises OSError if path does not exist).
        Rejects anything outside self.repo_root.

        Mirrors atlas-aci/config.py::Config.is_in_repo (FINDING-001).
        """
        if self.repo_root is None:
            # No repo root configured — treat all paths as safe (test / dev scenario).
            return True
        try:
            if not p.is_absolute():
                p = self.repo_root / p
            resolved = p.resolve(strict=True)
            resolved.relative_to(self.repo_root)
        except (ValueError, OSError):
            return False
        return True

    def human_confirm_active(self, now: datetime | None = None) -> bool:
        """Return True if the human-confirm window is active.

        Active when: install_ts is known AND (now - install_ts) < human_confirm_default_window_days.
        After the window, defaults to False unless explicitly configured.

        CRYSTALIUM_AUTO_CONFIRM=1 bypasses (test-only) — callers must check this env var
        separately and emit a WARN log on every bypass (G5, D8).
        """
        if self.install_ts is None:
            # No install.ts found — treat as outside window (conservative default for dev)
            return False
        if now is None:
            now = datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        install_ts = self.install_ts
        if install_ts.tzinfo is None:
            install_ts = install_ts.replace(tzinfo=timezone.utc)
        window = timedelta(days=self.human_confirm_default_window_days)
        return (now - install_ts) < window

    # ------------------------------------------------------------------
    # Storage path helpers
    # ------------------------------------------------------------------

    @property
    def blob_root(self) -> Path:
        return self.data_dir / "blobs"

    @property
    def sqlite_path(self) -> Path:
        return self.data_dir / "index.sqlite"

    @property
    def lance_path(self) -> Path:
        return self.data_dir / "vectors.lance"

    @property
    def kuzu_path(self) -> Path:
        return self.data_dir / "graph.kuzu"
