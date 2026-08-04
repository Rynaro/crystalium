"""Tests for the working-set composer (G6 anchor).

Container-first: run via:
  docker compose run --rm crystalium pytest mcp-server/tests/test_composer.py -v

G6 anchor:
  test_g6_eviction_deterministic — eviction order matches (importance↑, last_access↑, record_id↑);
  final total_tokens ≤ slot.cap; same inputs → same kept set across two runs.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from crystalium.composer import Composer, ComposedSet, _eviction_key
from crystalium.config import Config


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_UTC = timezone.utc


def _ts(offset_hours: int = 0) -> datetime:
    # Use timedelta to avoid hour-overflow for large offsets (e.g. i=0..29 in TestTotalCap)
    return datetime(2026, 5, 28, 0, 0, 0, tzinfo=_UTC) + timedelta(hours=offset_hours)


class _Rec:
    """Minimal record stub matching the Composer's expected interface."""

    def __init__(
        self,
        id: str,
        layer: str,
        summary: str,
        importance: float,
        last_access: datetime,
        slot_override: str | None = None,
        relevance_score: float | None = None,
    ) -> None:
        self.id = id
        self.layer = layer
        self.summary = summary
        self.importance = importance
        self.last_access = last_access
        self.slot_override = slot_override
        # MemoryRecord stubs
        self.access_count = 0
        self.outcome_success: float | None = None
        self.novelty_at_write = 0.5
        self.trust_tier = "T1"
        self.validation_state = "validated"
        self.content_ref = None
        self.scope_sensitivity_tag = "none"
        # crystalium#36 / C-2: relevance_score is only SET when a test explicitly
        # passes it — every stub that omits it stays attribute-less, so
        # `_eviction_key`'s `getattr(rec, "relevance_score", 0.0)` degrade-safe
        # read is what makes it tie at 0.0 (not a value we set here to 0.0
        # ourselves), which is precisely what keeps the four
        # TestG6EvictionDeterministic tests meaningful as the equal-relevance case.
        if relevance_score is not None:
            self.relevance_score = relevance_score


def _make_config(
    episodic_cap: int = 800,
    semantic_cap: int = 800,
    procedural_cap: int = 600,
    execution_cap: int = 1000,
    executive_cap: int = 300,
    buffer_cap: int = 300,
    total_cap: int = 3500,
) -> Config:
    """Build a Config with custom slot caps."""
    cfg = Config.__new__(Config)
    cfg.transport = "stdio"
    cfg.idle_threshold_s = 300
    cfg.min_dream_gap_s = 1800
    cfg.dream_tick_s = 60
    cfg.ecl_version = "2.0"
    cfg.skill_invoke_timeout_s = 30
    cfg.skill_invoke_output_cap_bytes = 8192
    cfg.importance_weights = (0.25, 0.30, 0.25, 0.20)
    cfg.importance_recency_halflife_days = 14.0
    cfg.k_corroboration = 3
    cfg.human_confirm_default_window_days = 30
    cfg.slots = {
        "executive": executive_cap,
        "procedural": procedural_cap,
        "semantic": semantic_cap,
        "episodic": episodic_cap,
        "execution": execution_cap,
        "buffer": buffer_cap,
    }
    cfg.total_cap = total_cap
    cfg.data_dir = __import__("pathlib").Path("/tmp/crystalium_test")
    cfg.embed_backend = "sentence-transformers"
    cfg.rate_limit_per_minute = 200
    cfg.install_ts = None
    cfg.repo_root = None
    # crystalium#36 / C-8: the new Config field, assigned in the same commit
    # that adds it (R-3 — an unrelated test must never die with an opaque
    # AttributeError far from this change).
    cfg.recall_relevance_primary = True
    # crystalium#38 / S-5: the four new fusion config fields, same rationale.
    cfg.recall_weighted_fusion = True
    cfg.fusion_weight_dense = 1.0
    cfg.fusion_weight_derived = 1.0
    cfg.fusion_sparse_boost_alpha = 1.0
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    return cfg


def _word_tokenizer(text: str) -> int:
    """Deterministic word-count tokenizer for tests (no tiktoken dep)."""
    return len(text.split())


# ---------------------------------------------------------------------------
# G6: Deterministic eviction
# ---------------------------------------------------------------------------


class TestG6EvictionDeterministic:
    """G6 anchor: eviction order + slot cap + total cap invariants."""

    def test_g6_eviction_deterministic(self) -> None:
        """G6: build an episodic slot over cap; verify deterministic eviction order.

        Setup: cap=20 words; records exceed cap by design.
        Eviction rule: pop lowest (importance↑, last_access↑, record_id↑).
        Expected: lowest-importance record is evicted first; total ≤ cap.
        """
        cfg = _make_config(episodic_cap=20)
        composer = Composer(config=cfg, tokenizer=_word_tokenizer)

        # Three episodic records; each ~8 words → total ~24 words > cap=20
        # importance: 0.1 (lowest) → evicted first; then 0.5; then 0.9 (kept)
        records = [
            _Rec("rec-low",  "episodic", "alpha beta gamma delta epsilon zeta eta theta",
                 importance=0.1, last_access=_ts(0)),
            _Rec("rec-mid",  "episodic", "iota kappa lambda mu nu xi omicron pi",
                 importance=0.5, last_access=_ts(1)),
            _Rec("rec-high", "episodic", "rho sigma tau upsilon phi chi psi omega",
                 importance=0.9, last_access=_ts(2)),
        ]

        result_a = composer.compose(records)
        result_b = composer.compose(records)

        # Deterministic: same kept set across two runs
        ids_a = {r.id for r in result_a.records}
        ids_b = {r.id for r in result_b.records}
        assert ids_a == ids_b, "Composition is not deterministic across two runs"

        # Total tokens ≤ episodic cap = 20
        assert result_a.slot_tokens["episodic"] <= 20, (
            f"episodic slot exceeded cap: {result_a.slot_tokens['episodic']}"
        )

        # Total tokens ≤ total_cap
        assert result_a.total_tokens <= cfg.total_cap

        # rec-low (importance=0.1) must be evicted
        assert "rec-low" not in ids_a, "Lowest-importance record should have been evicted"

        # rec-high (importance=0.9) must be kept
        assert "rec-high" in ids_a, "Highest-importance record should be retained"

        # Evicted count ≥ 1
        assert result_a.evicted_count >= 1

    def test_eviction_tiebreak_last_access(self) -> None:
        """Tie on importance: older last_access is evicted first (last_access↑)."""
        cfg = _make_config(episodic_cap=8)  # small cap
        composer = Composer(config=cfg, tokenizer=_word_tokenizer)

        # Equal importance; older timestamp → evicted
        older = _Rec("old", "episodic", "word1 word2 word3 word4 word5",
                     importance=0.5, last_access=_ts(-10))  # older
        newer = _Rec("new", "episodic", "word1 word2 word3 word4 word5",
                     importance=0.5, last_access=_ts(0))   # newer

        result = composer.compose([older, newer])
        kept_ids = {r.id for r in result.records}

        # "new" should be kept (newer last_access → evict old first)
        # total 10 words > cap=8 → one must be evicted
        assert "old" not in kept_ids or "new" in kept_ids

    def test_eviction_tiebreak_record_id(self) -> None:
        """Tie on importance AND last_access: lexicographic record_id tiebreak."""
        cfg = _make_config(episodic_cap=5)
        composer = Composer(config=cfg, tokenizer=_word_tokenizer)

        ts = _ts(0)
        # Same importance, same last_access; lexicographic: "aaa" < "zzz" → "aaa" evicted
        rec_a = _Rec("aaa-id", "episodic", "one two three", importance=0.5, last_access=ts)
        rec_z = _Rec("zzz-id", "episodic", "one two three", importance=0.5, last_access=ts)

        result = composer.compose([rec_a, rec_z])
        kept_ids = {r.id for r in result.records}

        # "aaa-id" should be evicted first (lexicographically smaller)
        assert "aaa-id" not in kept_ids or len(kept_ids) == 2  # only evicted if over cap

    def test_no_eviction_when_within_cap(self) -> None:
        """No eviction when total tokens are within budget."""
        cfg = _make_config(episodic_cap=100, total_cap=3500)
        composer = Composer(config=cfg, tokenizer=_word_tokenizer)

        records = [
            _Rec(f"rec-{i}", "episodic", "short text", importance=0.5, last_access=_ts(0))
            for i in range(5)
        ]
        result = composer.compose(records)
        assert result.evicted_count == 0
        assert len(result.records) == 5


# ---------------------------------------------------------------------------
# crystalium#36 / FORGE DP-1 — relevance-primary eviction key (seam 4)
# ---------------------------------------------------------------------------


class TestRelevancePrimaryEviction:
    """S-4: _eviction_key is relevance-primary when recall_relevance_primary is
    True (the Composer default). C-2: getattr(rec, "relevance_score", 0.0) is
    what keeps every _Rec stub without the attribute (TestG6EvictionDeterministic
    above) meaningful as the equal-relevance case — these two tests are the
    UNEQUAL-relevance case."""

    def test_eviction_evicts_least_relevant_first(self) -> None:
        """A LOW-relevance, HIGH-importance record is evicted before a
        HIGH-relevance, LOW-importance one — relevance is primary, importance
        is the secondary/tiebreak signal now (crystalium#36 seam 4)."""
        cfg = _make_config(episodic_cap=4)  # 3+3=6 words > 4 -> forces exactly one eviction
        composer = Composer(config=cfg, tokenizer=_word_tokenizer)  # flag ON by default

        low_relevance_high_importance = _Rec(
            "low-rel", "episodic", "one two three", importance=0.9,
            last_access=_ts(0), relevance_score=0.01,
        )
        high_relevance_low_importance = _Rec(
            "high-rel", "episodic", "four five six", importance=0.1,
            last_access=_ts(0), relevance_score=0.9,
        )

        result = composer.compose([low_relevance_high_importance, high_relevance_low_importance])
        kept_ids = {r.id for r in result.records}

        assert "high-rel" in kept_ids, "high-relevance record should survive despite low importance"
        assert "low-rel" not in kept_ids, "low-relevance record should be evicted despite high importance"

    def test_importance_breaks_relevance_ties(self) -> None:
        """Equal relevance_score -> importance is the tiebreak (the pre-1.9.0
        rule, preserved as seam 4's secondary key)."""
        cfg = _make_config(episodic_cap=4)  # 3+3=6 words > 4 -> forces exactly one eviction
        composer = Composer(config=cfg, tokenizer=_word_tokenizer)

        tied_low_importance = _Rec(
            "tied-low", "episodic", "one two three", importance=0.1,
            last_access=_ts(0), relevance_score=0.5,
        )
        tied_high_importance = _Rec(
            "tied-high", "episodic", "four five six", importance=0.9,
            last_access=_ts(0), relevance_score=0.5,
        )

        result = composer.compose([tied_low_importance, tied_high_importance])
        kept_ids = {r.id for r in result.records}

        assert "tied-high" in kept_ids
        assert "tied-low" not in kept_ids


# ---------------------------------------------------------------------------
# Slot routing
# ---------------------------------------------------------------------------


class TestSlotRouting:
    def test_episodic_routes_to_episodic_slot(self) -> None:
        cfg = _make_config()
        composer = Composer(config=cfg, tokenizer=_word_tokenizer)
        rec = _Rec("r1", "episodic", "hello world", importance=0.5, last_access=_ts(0))
        result = composer.compose([rec])
        assert result.slot_tokens.get("episodic", 0) > 0
        assert result.slot_tokens.get("semantic", 0) == 0

    def test_semantic_routes_to_semantic_slot(self) -> None:
        cfg = _make_config()
        composer = Composer(config=cfg, tokenizer=_word_tokenizer)
        rec = _Rec("r1", "semantic", "semantic fact here", importance=0.5, last_access=_ts(0))
        result = composer.compose([rec])
        assert result.slot_tokens.get("semantic", 0) > 0

    def test_procedural_routes_to_procedural_slot(self) -> None:
        cfg = _make_config()
        composer = Composer(config=cfg, tokenizer=_word_tokenizer)
        rec = _Rec("r1", "procedural", "run test command", importance=0.5, last_access=_ts(0))
        result = composer.compose([rec])
        assert result.slot_tokens.get("procedural", 0) > 0

    def test_execution_routes_to_execution_slot(self) -> None:
        cfg = _make_config()
        composer = Composer(config=cfg, tokenizer=_word_tokenizer)
        rec = _Rec("r1", "execution", "plan state checkpoint", importance=0.5, last_access=_ts(0))
        result = composer.compose([rec])
        assert result.slot_tokens.get("execution", 0) > 0

    def test_executive_slot_override(self) -> None:
        """Records tagged slot_override='executive' route to executive slot."""
        cfg = _make_config()
        composer = Composer(config=cfg, tokenizer=_word_tokenizer)
        rec = _Rec(
            "exec-rec", "semantic", "mission spec persona",
            importance=0.9, last_access=_ts(0), slot_override="executive"
        )
        result = composer.compose([rec])
        assert result.slot_tokens.get("executive", 0) > 0
        assert result.slot_tokens.get("semantic", 0) == 0

    def test_unknown_layer_routes_to_buffer(self) -> None:
        """Unknown layer routes to buffer slot."""
        cfg = _make_config()
        composer = Composer(config=cfg, tokenizer=_word_tokenizer)
        rec = _Rec("r1", "unknown_layer", "some text", importance=0.5, last_access=_ts(0))
        result = composer.compose([rec])
        assert result.slot_tokens.get("buffer", 0) > 0


# ---------------------------------------------------------------------------
# Total cap assertion
# ---------------------------------------------------------------------------


class TestTotalCap:
    def test_g6_working_set_cap_is_literally_3500(self) -> None:
        """G6 invariant pin (W8): the working-set cap is exactly 3500 tokens. The
        1.0 conformance freeze pins the literal — changing the Config default fails
        here, not silently."""
        from crystalium.config import Config

        assert Config(data_dir=__import__("pathlib").Path("/tmp/crys-cap-pin")).total_cap == 3500

    def test_total_tokens_within_total_cap(self) -> None:
        """G6 over-subscription: slot caps sum to 3800 > total_cap 3500, so when
        every slot fills the GLOBAL cap pass must trim the working set to ≤3500.

        Regression for the battle-test HIGH: the old version only generated ~2400
        tokens (no slot ever hit its cap), so total never approached 3500 and the
        bug — per-slot eviction can't satisfy total_cap, the composer asserted and
        crashed recall — was invisible. This version floods ALL SIX slots well past
        their caps, so without the global pass total_tokens would be 3800."""
        cfg = _make_config()
        assert sum(cfg.slots.values()) > cfg.total_cap          # the over-subscription
        composer = Composer(config=cfg, tokenizer=_word_tokenizer)

        twenty_words = " ".join(f"word{j}" for j in range(20))  # 20 tokens each
        records = []
        # Drive every slot far past its cap: 4 native layers + executive override + buffer.
        slot_specs = [
            ("episodic", None), ("semantic", None), ("procedural", None),
            ("execution", None), ("episodic", "executive"), ("misc", None),  # misc -> buffer
        ]
        for layer, override in slot_specs:
            tag = override or layer
            for i in range(80):                                  # 80*20 = 1600 tokens / slot
                records.append(_Rec(
                    f"{tag}-{i}", layer, twenty_words,
                    importance=i / 80, last_access=_ts(i), slot_override=override,
                ))

        result = composer.compose(records)
        assert result.total_tokens <= cfg.total_cap              # the real G6 invariant
        assert result.total_tokens == sum(result.slot_tokens.values())
        assert result.evicted_count > 0                          # eviction actually ran
        # Packed near the ceiling — proves the global pass trimmed to the boundary,
        # not that the test stayed vacuously below it.
        assert result.total_tokens >= cfg.total_cap - 20

    def test_empty_records(self) -> None:
        """Empty input returns zero tokens and zero evictions."""
        cfg = _make_config()
        composer = Composer(config=cfg, tokenizer=_word_tokenizer)
        result = composer.compose([])
        assert result.total_tokens == 0
        assert result.evicted_count == 0
        assert result.records == []


# ---------------------------------------------------------------------------
# Tokenizer fallback
# ---------------------------------------------------------------------------


class TestTokenizer:
    def test_tokenize_non_empty(self) -> None:
        """tokenize() returns a positive int for non-empty text."""
        cfg = _make_config()
        composer = Composer(config=cfg)  # uses module-level tokenizer
        count = composer.tokenize("hello world this is a test")
        assert count > 0

    def test_tokenize_empty(self) -> None:
        """tokenize('') returns 0."""
        cfg = _make_config()
        composer = Composer(config=cfg)
        assert composer.tokenize("") == 0

    def test_compose_tolerates_tiktoken_special_token_in_summary(self) -> None:
        """A stored crystal whose summary embeds a cl100k_base special-token string
        (e.g. '<|endoftext|>') must NOT crash the composer/recall (issue #32)."""
        cfg = _make_config()
        composer = Composer(config=cfg)  # DEFAULT tokenizer → exercises the tiktoken path
        rec = _Rec(
            "c-special", "semantic",
            "note: GPT docs are joined with <|endoftext|> as a delimiter",
            importance=0.9, last_access=_ts(0),
        )
        out = composer.compose([rec])  # must not raise
        assert out.total_tokens > 0
        assert any(r.id == "c-special" for r in out.records)
