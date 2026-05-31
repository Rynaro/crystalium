"""W7 C2 — generic envelope → crystal.v1 adapter mapping.

Container-first:
  docker compose run --rm crystalium pytest mcp-server/tests/test_adapter_mapping.py -v
"""

from __future__ import annotations

import json

from crystalium.ingest_adapter import (
    layer_for_kind,
    map_envelope_to_crystal,
    resolve_caller_tier,
    source_for_tier,
)
from crystalium.trust import Tier


def _envelope(kind="scout-report", eidolon="atlas", version="1.0") -> dict:
    return {
        "envelope_version": version,
        "message_id": "msg-1", "thread_id": "thread-42", "parent_id": None,
        "from": {"eidolon": eidolon, "version": "1.0.0"},
        "to": {"eidolon": "spectra", "version": "1.0.0"},
        "performative": "INFORM", "objective": "map the auth subsystem",
        "artifact": {"kind": kind, "schema_version": "1.0", "path": "stdin",
                     "sha256": "a" * 64, "size_bytes": 10},
        "integrity": {"method": "sha256", "value": "a" * 64},
        "trace": {"ts": "2026-01-01T00:00:00Z", "host": "h", "model": "m", "tier": "T1"},
    }


def test_field_mapping():
    layer, payload = map_envelope_to_crystal(_envelope(), {"findings": [1, 2]}, Tier.T1)
    assert layer == "episodic"                                   # default layer
    assert payload["content_ref"] == "a" * 64                    # artifact.sha256
    assert payload["scope"]["project"] == "thread-42"            # thread_id groups chain
    assert payload["provenance"]["author_agent"] == "atlas"      # from.eidolon
    assert payload["provenance"]["source"] == "verified_agent"   # T1 -> verified_agent
    assert payload["provenance"]["task_id"] == "thread-42"
    assert payload["provenance"]["created_at"] == "2026-01-01T00:00:00Z"
    assert "map the auth subsystem" in payload["summary"]        # objective is FTS-searchable
    assert "scout-report" in payload["summary"]
    assert "from:atlas" in payload["tags"]
    assert "env:1.0" in payload["tags"]


def test_native_artifact_preserved_verbatim():
    artifact = {"findings": [{"id": "F1", "sev": "high"}], "gaps": ["G1"]}
    _layer, payload = map_envelope_to_crystal(_envelope(), artifact, Tier.T1)
    assert payload["encoding_context"]["native_artifact"] == artifact
    assert payload["encoding_context"]["artifact_kind"] == "scout-report"
    assert payload["encoding_context"]["envelope_version"] == "1.0"
    assert payload["encoding_context"]["source_message_id"] == "msg-1"


def test_payload_json_string_decoded():
    artifact = {"k": "v"}
    _layer, payload = map_envelope_to_crystal(
        _envelope(), json.dumps(artifact), Tier.T1, payload_encoding="json")
    assert payload["encoding_context"]["native_artifact"] == artifact


def test_default_layer_episodic():
    assert layer_for_kind("anything-unknown") == "episodic"


def test_source_for_tier_proxy_table():
    assert source_for_tier(Tier.T0) == "human"
    assert source_for_tier(Tier.T1) == "verified_agent"
    assert source_for_tier(Tier.T3) == "unverified_agent"


def test_summary_never_empty_without_objective():
    env = _envelope(); env["objective"] = ""
    _layer, payload = map_envelope_to_crystal(env, {}, Tier.T2)
    assert payload["summary"] == "scout-report"                  # falls back to kind
    assert payload["provenance"]["source"] == "unverified_agent"  # T2


def test_resolve_tier_explicit_token_wins():
    env = _envelope(); env["trace"]["tier"] = "T0"
    assert resolve_caller_tier(env) == Tier.T0


def test_resolve_tier_falls_back_to_roster_default():
    # v1.0 envelopes carry non-tier trace.tier (e.g. "standard") -> per-eidolon default
    env = _envelope(eidolon="atlas"); env["trace"]["tier"] = "standard"
    assert resolve_caller_tier(env) == Tier.T1


def test_resolve_tier_host_is_t0():
    env = _envelope(eidolon="host"); env["trace"]["tier"] = "trance"
    assert resolve_caller_tier(env) == Tier.T0


def test_resolve_tier_unknown_source_is_t3():
    env = _envelope(eidolon="some-random-tool"); env["trace"]["tier"] = "standard"
    assert resolve_caller_tier(env) == Tier.T3


def test_resolve_tier_absent_trace():
    env = _envelope(eidolon="spectra"); env["trace"] = {}
    assert resolve_caller_tier(env) == Tier.T1


def test_output_is_valid_crystal_payload_shape():
    # crystal.v1 commit payload requires summary, scope.project, provenance, content_ref
    # (episodic). Assert the mapping produces all of them.
    _layer, payload = map_envelope_to_crystal(_envelope(), {}, Tier.T1)
    assert payload["summary"] and payload["scope"]["project"]
    assert payload["provenance"]["source"] in {
        "human", "verified_agent", "unverified_agent", "environment"}
    assert len(payload["content_ref"]) == 64
