"""Tests for JSON Schema validity and Pydantic model round-trips.

Container-first: run via:
  docker compose run --rm crystalium pytest mcp-server/tests/test_schemas.py -v

Tests:
  - All 6 JSON schemas parse as valid JSON
  - Well-formed examples validate against each schema
  - Known-bad examples are rejected by each schema
  - Pydantic model round-trips (Crystal, Skill, RecallRequest, etc.)
  - Cross-field constraint: Crystal.content_ref required when layer='episodic'
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Schema loading helpers
# ---------------------------------------------------------------------------

def _find_schemas_dir() -> Path:
    """Locate the schemas/ directory robustly regardless of container layout.

    Tries candidate paths in order:
      1. Two parents up from tests/ (host layout: mcp-server/tests → mcp-server → schemas/)
      2. Three parents up (host layout: mcp-server/tests → mcp-server → crystalium → schemas/)
      3. /app/schemas (container layout: app is the project root)
      4. /schemas (fallback)
    Raises AssertionError if none exist.
    """
    here = Path(__file__).parent
    candidates = [
        here.parent.parent / "schemas",        # container: /app/tests/../schemas
        here.parent.parent.parent / "schemas",  # host: mcp-server/tests/../../schemas
        Path("/app/schemas"),
        Path("/schemas"),
    ]
    for p in candidates:
        if p.exists() and p.is_dir():
            return p
    raise AssertionError(
        f"schemas/ directory not found. Tried: {[str(c) for c in candidates]}"
    )


SCHEMAS_DIR = _find_schemas_dir()


def load_schema(name: str) -> dict:
    """Load a JSON schema file by name."""
    path = SCHEMAS_DIR / name
    assert path.exists(), f"Schema file not found: {path}"
    with path.open() as f:
        return json.load(f)


def validate(schema: dict, instance: dict) -> None:
    """Validate *instance* against *schema* using jsonschema (Draft 2020-12)."""
    try:
        import jsonschema
        from jsonschema import Draft202012Validator
    except ImportError:
        pytest.skip("jsonschema not installed (add to dev deps)")

    validator = Draft202012Validator(schema)
    errors = list(validator.iter_errors(instance))
    if errors:
        messages = "\n".join(str(e.message) for e in errors)
        raise AssertionError(f"JSON schema validation failed:\n{messages}")


def assert_invalid(schema: dict, instance: dict, reason: str = "") -> None:
    """Assert that *instance* does NOT validate against *schema*."""
    try:
        import jsonschema
        from jsonschema import Draft202012Validator
    except ImportError:
        pytest.skip("jsonschema not installed")

    validator = Draft202012Validator(schema)
    errors = list(validator.iter_errors(instance))
    assert errors, (
        f"Expected validation to FAIL but it PASSED. Reason: {reason}\n"
        f"Instance: {json.dumps(instance, indent=2)}"
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 5, 28, 12, 0, 0, tzinfo=timezone.utc).isoformat()
_SHA256 = hashlib.sha256(b"test content").hexdigest()


def _good_crystal(layer: str = "semantic") -> dict:
    base = {
        "id": "abc123",
        "layer": layer,
        "summary": "Project uses Conventional Commits for all PRs.",
        "provenance": {
            "source": "verified_agent",
            "author_agent": "atlas",
            "task_id": "task-001",
            "created_at": _NOW,
        },
        "trust_tier": "T1",
        "validation_state": "validated",
        "scope": {
            "project": "eidolons",
            "agent_class_visibility": "all",
            "sensitivity_tag": "public",
        },
        "temporal": {
            "t_valid_from": _NOW,
            "t_valid_to": None,
            "superseded_by": None,
        },
        "utility": {
            "access_count": 5,
            "last_access": _NOW,
            "outcome_success_score": 0.9,
            "importance": 0.75,
            "novelty_at_write": 0.6,
        },
        "status": "active",
    }
    if layer == "episodic":
        base["content_ref"] = _SHA256
    return base


# ---------------------------------------------------------------------------
# Schema file tests
# ---------------------------------------------------------------------------


class TestSchemaFilesAreValidJson:
    """All 6 schemas (+ install.manifest) must parse as valid JSON with required meta-fields."""

    SCHEMA_FILES = [
        "crystal.v1.json",
        "skill.v1.json",
        "recall-request.v1.json",
        "recall-result.v1.json",
        "commit-request.v1.json",
        "commit-result.v1.json",
        "install.manifest.v1.json",
    ]

    @pytest.mark.parametrize("filename", SCHEMA_FILES)
    def test_schema_parses_as_json(self, filename: str) -> None:
        schema = load_schema(filename)
        assert isinstance(schema, dict), f"{filename} should be a JSON object"

    @pytest.mark.parametrize("filename", SCHEMA_FILES)
    def test_schema_has_required_meta_fields(self, filename: str) -> None:
        schema = load_schema(filename)
        assert "$schema" in schema, f"{filename} missing $schema"
        assert "$id" in schema, f"{filename} missing $id"
        assert "title" in schema, f"{filename} missing title"
        assert "description" in schema, f"{filename} missing description"
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"


# ---------------------------------------------------------------------------
# crystal.v1.json
# ---------------------------------------------------------------------------


class TestCrystalSchema:
    def setup_method(self) -> None:
        self.schema = load_schema("crystal.v1.json")

    def test_valid_semantic_crystal(self) -> None:
        validate(self.schema, _good_crystal("semantic"))

    def test_valid_episodic_crystal_with_content_ref(self) -> None:
        validate(self.schema, _good_crystal("episodic"))

    def test_episodic_missing_content_ref_is_invalid(self) -> None:
        bad = _good_crystal("episodic")
        del bad["content_ref"]
        assert_invalid(self.schema, bad, "episodic crystal must have content_ref")

    def test_invalid_layer_rejected(self) -> None:
        bad = _good_crystal("semantic")
        bad["layer"] = "working_memory"
        assert_invalid(self.schema, bad, "unknown layer")

    def test_invalid_trust_tier_rejected(self) -> None:
        bad = _good_crystal("semantic")
        bad["trust_tier"] = "T5"
        assert_invalid(self.schema, bad, "T5 is not a valid tier")

    def test_missing_required_field_rejected(self) -> None:
        bad = _good_crystal("semantic")
        del bad["summary"]
        assert_invalid(self.schema, bad, "summary is required")

    def test_importance_out_of_range_rejected(self) -> None:
        bad = _good_crystal("semantic")
        bad["utility"]["importance"] = 1.5
        assert_invalid(self.schema, bad, "importance > 1.0 should be rejected")

    def test_additional_properties_rejected(self) -> None:
        bad = _good_crystal("semantic")
        bad["unknown_field"] = "surprise"
        assert_invalid(self.schema, bad, "additionalProperties = false")


# ---------------------------------------------------------------------------
# skill.v1.json
# ---------------------------------------------------------------------------


class TestSkillSchema:
    def setup_method(self) -> None:
        self.schema = load_schema("skill.v1.json")

    def _good_skill(self) -> dict:
        return {
            "name": "generate_changelog",
            "capability_class": "code-generation",
            "description": "Generates a CHANGELOG entry from git log.",
            "language": "python",
            "inputs": [
                {"name": "repo_path", "type": "str", "required": True},
                {"name": "version", "type": "str", "required": True},
            ],
            "verifier": "tests/test_generate_changelog.py",
            "provenance": {
                "source": "verified_agent",
                "author_agent": "spectra",
                "created_at": _NOW,
            },
            "trust_tier": "T1",
            "success_count": 12,
            "last_used": _NOW,
            "status": "shared",
        }

    def test_valid_skill(self) -> None:
        validate(self.schema, self._good_skill())

    def test_invalid_language_rejected(self) -> None:
        bad = self._good_skill()
        bad["language"] = "ruby"
        assert_invalid(self.schema, bad, "ruby not in language enum")

    def test_invalid_status_rejected(self) -> None:
        bad = self._good_skill()
        bad["status"] = "approved"
        assert_invalid(self.schema, bad, "approved not in status enum")

    def test_missing_verifier_rejected(self) -> None:
        bad = self._good_skill()
        del bad["verifier"]
        assert_invalid(self.schema, bad, "verifier is required")


# ---------------------------------------------------------------------------
# recall-request.v1.json
# ---------------------------------------------------------------------------


class TestRecallRequestSchema:
    def setup_method(self) -> None:
        self.schema = load_schema("recall-request.v1.json")

    def test_valid_request(self) -> None:
        validate(
            self.schema,
            {
                "scope": {"project": "eidolons"},
                "query": "what conventions does this project use?",
                "k": 10,
                "layers": ["semantic", "procedural"],
            },
        )

    def test_k_out_of_range_rejected(self) -> None:
        assert_invalid(
            self.schema,
            {"scope": {"project": "p"}, "query": "q", "k": 0},
            "k=0 below minimum",
        )
        assert_invalid(
            self.schema,
            {"scope": {"project": "p"}, "query": "q", "k": 101},
            "k=101 above maximum",
        )

    def test_missing_scope_rejected(self) -> None:
        assert_invalid(
            self.schema,
            {"query": "q", "k": 5},
            "scope is required",
        )


# ---------------------------------------------------------------------------
# commit-request.v1.json
# ---------------------------------------------------------------------------


class TestCommitRequestSchema:
    def setup_method(self) -> None:
        self.schema = load_schema("commit-request.v1.json")

    def test_valid_request(self) -> None:
        validate(
            self.schema,
            {
                "layer": "semantic",
                "payload": {"summary": "test", "trust_tier": "T1"},
                "provenance": {"source": "verified_agent", "created_at": _NOW},
                "caller_tier": "T1",
            },
        )

    def test_invalid_caller_tier_rejected(self) -> None:
        assert_invalid(
            self.schema,
            {
                "layer": "semantic",
                "payload": {},
                "provenance": {"source": "human", "created_at": _NOW},
                "caller_tier": "T9",
            },
            "T9 is not a valid tier",
        )


# ---------------------------------------------------------------------------
# commit-result.v1.json
# ---------------------------------------------------------------------------


class TestCommitResultSchema:
    def setup_method(self) -> None:
        self.schema = load_schema("commit-result.v1.json")

    def test_valid_committed_result(self) -> None:
        validate(
            self.schema,
            {
                "status": "committed",
                "id": "abc123",
                "layer": "semantic",
                "validation_state": "validated",
                "importance": 0.8,
            },
        )

    def test_valid_rejected_result(self) -> None:
        validate(
            self.schema,
            {
                "status": "rejected",
                "reason_code": "TIER_VIOLATION",
                "detail": "T3 cannot commit above episodic layer.",
            },
        )

    def test_invalid_reason_code_rejected(self) -> None:
        assert_invalid(
            self.schema,
            {"status": "rejected", "reason_code": "UNKNOWN_ERROR", "detail": "oops"},
            "UNKNOWN_ERROR not in reason_code enum",
        )


# ---------------------------------------------------------------------------
# Pydantic round-trips
# ---------------------------------------------------------------------------


class TestPydanticRoundTrips:
    """Pydantic v2 model validation + serialization round-trips."""

    def test_crystal_round_trip(self) -> None:
        from crystalium.schemas import Crystal

        data = _good_crystal("semantic")
        crystal = Crystal.model_validate(data)
        assert crystal.layer == "semantic"
        assert crystal.trust_tier == "T1"
        dumped = crystal.model_dump(mode="json")
        crystal2 = Crystal.model_validate(dumped)
        assert crystal2.id == crystal.id

    def test_crystal_episodic_requires_content_ref(self) -> None:
        from pydantic import ValidationError

        from crystalium.schemas import Crystal

        bad = _good_crystal("episodic")
        del bad["content_ref"]
        with pytest.raises(ValidationError, match="content_ref is required"):
            Crystal.model_validate(bad)

    def test_crystal_episodic_with_content_ref_passes(self) -> None:
        from crystalium.schemas import Crystal

        data = _good_crystal("episodic")
        crystal = Crystal.model_validate(data)
        assert crystal.content_ref == _SHA256

    def test_crystal_non_episodic_content_ref_optional(self) -> None:
        from crystalium.schemas import Crystal

        data = _good_crystal("semantic")
        # semantic crystals do NOT require content_ref
        assert "content_ref" not in data or data.get("content_ref") is None
        crystal = Crystal.model_validate(data)
        assert crystal.content_ref is None

    def test_recall_request_round_trip(self) -> None:
        from crystalium.schemas import RecallRequest

        data = {
            "scope": {"project": "eidolons"},
            "query": "project conventions",
            "k": 5,
            "layers": ["semantic"],
        }
        req = RecallRequest.model_validate(data)
        assert req.k == 5
        assert req.layers == ["semantic"]

    def test_commit_result_committed(self) -> None:
        from crystalium.schemas import CommitResultCommitted

        data = {
            "status": "committed",
            "id": "xyz",
            "layer": "episodic",
            "validation_state": "quarantined",
            "importance": 0.3,
        }
        result = CommitResultCommitted.model_validate(data)
        assert result.status == "committed"
        assert result.validation_state == "quarantined"

    def test_commit_result_rejected(self) -> None:
        from crystalium.schemas import CommitResultRejected

        data = {
            "status": "rejected",
            "reason_code": "TIER_VIOLATION",
            "detail": "T3 cannot commit to Semantic.",
        }
        result = CommitResultRejected.model_validate(data)
        assert result.reason_code == "TIER_VIOLATION"

    def test_crystal_invalid_content_ref_pattern(self) -> None:
        from pydantic import ValidationError

        from crystalium.schemas import Crystal

        bad = _good_crystal("episodic")
        bad["content_ref"] = "not-a-valid-sha256"
        with pytest.raises(ValidationError):
            Crystal.model_validate(bad)


# ---------------------------------------------------------------------------
# v0.2.0 schema-first migration (DECISION-1) — memory_dynamics + tags/protected/
# encoding_context. Fields are UNPOPULATED in v0.2.0: a v0.1 crystal omitting them
# must still validate, and the new fields must round-trip when present-but-null.
# ---------------------------------------------------------------------------


class TestCrystalV2Fields:
    def setup_method(self) -> None:
        self.schema = load_schema("crystal.v1.json")

    def test_v01_crystal_without_new_fields_still_valid(self) -> None:
        # Backward compatibility: the new fields are optional.
        validate(self.schema, _good_crystal("semantic"))

    def test_crystal_with_nulled_new_fields_valid(self) -> None:
        c = _good_crystal("semantic")
        c["memory_dynamics"] = {
            "stability": None,
            "retrievability": None,
            "difficulty": None,
            "evb": None,
            "prediction_error": None,
        }
        c["tags"] = []
        c["protected"] = False
        c["encoding_context"] = None
        validate(self.schema, c)

    def test_crystal_with_populated_new_fields_valid(self) -> None:
        # Schema permits population (later waves); v0.2.0 simply never does it.
        c = _good_crystal("semantic")
        c["memory_dynamics"] = {"stability": 12.5, "retrievability": 0.9, "evb": -0.2}
        c["tags"] = ["auth", "bcrypt"]
        c["protected"] = True
        c["encoding_context"] = {"branch": "main"}
        validate(self.schema, c)

    def test_memory_dynamics_unknown_subfield_rejected(self) -> None:
        c = _good_crystal("semantic")
        c["memory_dynamics"] = {"stability": 1.0, "bogus": 2}
        assert_invalid(self.schema, c, "memory_dynamics additionalProperties=false")

    def test_pydantic_defaults_when_new_fields_omitted(self) -> None:
        from crystalium.schemas import Crystal

        crystal = Crystal.model_validate(_good_crystal("semantic"))
        assert crystal.memory_dynamics is None
        assert crystal.tags == []
        assert crystal.protected is False
        assert crystal.encoding_context is None

    def test_pydantic_round_trip_with_new_fields(self) -> None:
        from crystalium.schemas import Crystal

        data = _good_crystal("semantic")
        data["memory_dynamics"] = {"stability": 3.0, "evb": 0.5}
        data["tags"] = ["x"]
        data["protected"] = True
        data["encoding_context"] = {"k": "v"}
        crystal = Crystal.model_validate(data)
        assert crystal.memory_dynamics is not None
        assert crystal.memory_dynamics.stability == 3.0
        assert crystal.memory_dynamics.retrievability is None
        dumped = crystal.model_dump(mode="json")
        crystal2 = Crystal.model_validate(dumped)
        assert crystal2.tags == ["x"]
        assert crystal2.protected is True
        assert crystal2.encoding_context == {"k": "v"}


# ---------------------------------------------------------------------------
# graph-export.v1.json (W-GE3)
# ---------------------------------------------------------------------------


def _good_graph_export() -> dict:
    """Minimal valid graph-export payload."""
    import datetime
    now = datetime.datetime(2026, 6, 22, 12, 0, 0, tzinfo=datetime.timezone.utc).isoformat()
    return {
        "schema_version": "graph-export.v1",
        "generated_from": {
            "project": "test-project",
            "agent_class_visibility": None,
            "layers": None,
            "generated_at": now,
            "caller_tier": None,
        },
        "counts": {
            "nodes": 1,
            "edges": 1,
            "nodes_total_estimate": 1,
            "edges_dropped_dangling": 0,
            "edges_deduped": 0,
        },
        "truncated": False,
        "nodes": [
            {
                "id": "crystal-abc",
                "layer": "semantic",
                "summary": "Test crystal summary.",
                "trust_tier": "T1",
                "validation_state": "unverified",
                "status": "active",
                "importance": 0.5,
                "tags": [],
                "protected": False,
                "scope_project": "test-project",
            }
        ],
        "edges": [
            {
                "from": "crystal-abc",
                "to": "crystal-def",
                "type": "LINKS_TO",
                "source": "kuzu",
                "weight": 1.0,
                "metadata": {},
            }
        ],
    }


class TestGraphExportSchema:
    def setup_method(self) -> None:
        self.schema = load_schema("graph-export.v1.json")

    def test_schema_parses_as_json(self) -> None:
        assert isinstance(self.schema, dict)

    def test_schema_has_required_meta_fields(self) -> None:
        assert "$schema" in self.schema
        assert "$id" in self.schema
        assert "title" in self.schema
        assert "description" in self.schema
        assert self.schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"

    def test_valid_export_validates(self) -> None:
        validate(self.schema, _good_graph_export())

    def test_missing_schema_version_rejected(self) -> None:
        bad = _good_graph_export()
        del bad["schema_version"]
        assert_invalid(self.schema, bad, "schema_version is required")

    def test_wrong_schema_version_rejected(self) -> None:
        bad = _good_graph_export()
        bad["schema_version"] = "graph-export.v2"
        assert_invalid(self.schema, bad, "schema_version const must be graph-export.v1")

    def test_missing_truncated_rejected(self) -> None:
        bad = _good_graph_export()
        del bad["truncated"]
        assert_invalid(self.schema, bad, "truncated is required")

    def test_missing_counts_rejected(self) -> None:
        bad = _good_graph_export()
        del bad["counts"]
        assert_invalid(self.schema, bad, "counts is required")

    def test_missing_nodes_rejected(self) -> None:
        bad = _good_graph_export()
        del bad["nodes"]
        assert_invalid(self.schema, bad, "nodes is required")

    def test_missing_edges_rejected(self) -> None:
        bad = _good_graph_export()
        del bad["edges"]
        assert_invalid(self.schema, bad, "edges is required")

    def test_node_missing_required_field_rejected(self) -> None:
        bad = _good_graph_export()
        del bad["nodes"][0]["summary"]
        assert_invalid(self.schema, bad, "node summary is required")

    def test_node_invalid_layer_rejected(self) -> None:
        bad = _good_graph_export()
        bad["nodes"][0]["layer"] = "working_memory"
        assert_invalid(self.schema, bad, "invalid layer enum")

    def test_edge_missing_source_rejected(self) -> None:
        bad = _good_graph_export()
        del bad["edges"][0]["source"]
        assert_invalid(self.schema, bad, "edge source is required")

    def test_edge_invalid_source_rejected(self) -> None:
        bad = _good_graph_export()
        bad["edges"][0]["source"] = "postgres"
        assert_invalid(self.schema, bad, "source must be kuzu or derived")

    def test_edge_invalid_type_rejected(self) -> None:
        bad = _good_graph_export()
        bad["edges"][0]["type"] = "INVENTED_REL"
        assert_invalid(self.schema, bad, "type must be one of the valid enum values")

    def test_empty_nodes_and_edges_valid(self) -> None:
        """An export with no nodes and no edges is valid (empty project)."""
        empty = _good_graph_export()
        empty["nodes"] = []
        empty["edges"] = []
        empty["counts"]["nodes"] = 0
        empty["counts"]["edges"] = 0
        validate(self.schema, empty)

    def test_additional_top_level_properties_rejected(self) -> None:
        bad = _good_graph_export()
        bad["unexpected_key"] = "surprise"
        assert_invalid(self.schema, bad, "additionalProperties: false at top level")
