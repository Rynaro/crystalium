"""W4 tests: CLI subcommands (doctor + promote list + index + export).

Tests:
  test_doctor_healthy_exits_0             — healthy env → exit code 0
  test_doctor_readonly_data_dir_nonzero   — read-only data_dir → non-zero exit
  test_promote_list_returns_pending_rows  — mock RelationalStore → rows printed
  test_index_redactor_receives_config     — Redactor constructed with config kwarg (regression:
                                           bare Redactor() crashed with TypeError)
  test_index_single_file_exits_0          — index a single .md file against a real tmp data dir
  TestExportCli (W-GE4)                   — export subcommand: valid JSON, flags, --output, exit codes

Container-first: run via
  docker compose run --rm crystalium pytest mcp-server/tests/test_cli.py -v
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from crystalium.__main__ import cli


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _invoke(args: list[str], env: dict | None = None):
    """Invoke CLI via CliRunner and return Result."""
    runner = CliRunner()
    return runner.invoke(cli, args, env=env, catch_exceptions=False)


# ---------------------------------------------------------------------------
# doctor — healthy exit 0
# ---------------------------------------------------------------------------


def test_doctor_healthy_exits_0(tmp_path: Path) -> None:
    """doctor with a writable data_dir exits 0 (all P0 checks pass)."""
    data_dir = tmp_path / "crystalium_data"
    data_dir.mkdir()

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["doctor"],
        env={"CRYSTALIUM_DATA_DIR": str(data_dir)},
        catch_exceptions=False,
    )

    assert result.exit_code == 0, (
        f"doctor exited with {result.exit_code}.\nOutput:\n{result.output}"
    )
    assert "P0 checks passed" in result.output


def test_doctor_reports_all_ok(tmp_path: Path) -> None:
    """doctor output contains '[OK]' for all P0 checks in a healthy environment."""
    data_dir = tmp_path / "crystalium_data"
    data_dir.mkdir()

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["doctor"],
        env={"CRYSTALIUM_DATA_DIR": str(data_dir)},
        catch_exceptions=False,
    )

    # Count how many [OK] marks appear
    ok_count = result.output.count("[OK]")
    assert ok_count >= 4, (
        f"Expected at least 4 [OK] marks for P0 checks, found {ok_count}.\n{result.output}"
    )


def test_doctor_outputs_crystalium_header(tmp_path: Path) -> None:
    """doctor output starts with 'CRYSTALIUM doctor'."""
    data_dir = tmp_path / "crystalium_data"
    data_dir.mkdir()

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["doctor"],
        env={"CRYSTALIUM_DATA_DIR": str(data_dir)},
        catch_exceptions=False,
    )
    assert "CRYSTALIUM doctor" in result.output


# ---------------------------------------------------------------------------
# doctor — read-only data_dir → non-zero
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    os.getuid() == 0,
    reason="Running as root; chmod 0o444 does not prevent writes for root",
)
def test_doctor_readonly_data_dir_nonzero(tmp_path: Path) -> None:
    """doctor exits non-zero when data_dir is not writable (P0-4 fails)."""
    data_dir = tmp_path / "ro_data"
    data_dir.mkdir()
    # Make it read-only
    data_dir.chmod(0o444)

    try:
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["doctor"],
            env={"CRYSTALIUM_DATA_DIR": str(data_dir)},
            catch_exceptions=True,  # allow sys.exit to propagate
        )
        assert result.exit_code != 0, (
            f"Expected non-zero exit for read-only data_dir, got {result.exit_code}.\n"
            f"Output:\n{result.output}"
        )
    finally:
        # Restore perms so tmp_path cleanup doesn't fail
        data_dir.chmod(0o755)


def test_doctor_fail_shows_fail_marker(tmp_path: Path) -> None:
    """When a P0 check fails, output contains '[FAIL]'."""
    data_dir = tmp_path / "ro_data_fail"
    data_dir.mkdir()
    data_dir.chmod(0o444)

    try:
        runner = CliRunner()
        # catch_exceptions=True to allow sys.exit
        result = runner.invoke(
            cli,
            ["doctor"],
            env={"CRYSTALIUM_DATA_DIR": str(data_dir)},
            catch_exceptions=True,
        )

        combined = (result.output or "") + (result.stderr or "")

        if os.getuid() == 0:
            pytest.skip("Root can write to read-only dirs; skip FAIL assertion")

        assert "[FAIL]" in combined or result.exit_code != 0, (
            "Expected either [FAIL] in output or non-zero exit code"
        )
    finally:
        data_dir.chmod(0o755)


# ---------------------------------------------------------------------------
# canary — dispatches to the evals bench (no longer a W5 stub)
# ---------------------------------------------------------------------------


def test_canary_dispatches_to_bench_and_prints_headline() -> None:
    """crystalium canary calls evals.run_all and prints the headline (no W5 stub)."""
    runner = CliRunner()
    fake = {"headline": {"delta": 0.8, "headline_pass": True}, "mode": "both"}
    with patch("evals.ab_memory_onoff.run_all", return_value=fake) as run_all:
        result = runner.invoke(cli, ["canary", "--mode", "off_only", "--no-write"],
                               catch_exceptions=False)
    assert result.exit_code == 0, result.output
    run_all.assert_called_once_with(mode="off_only", write_results=False)
    assert "headline_pass" in result.output


# ---------------------------------------------------------------------------
# promote list — returns pending rows (mock RelationalStore)
# ---------------------------------------------------------------------------


def test_promote_list_returns_pending_rows(tmp_path: Path) -> None:
    """promote list prints pending promotion rows from RelationalStore."""
    import uuid as _uuid
    from datetime import datetime, timezone

    data_dir = tmp_path / "crystalium_data"
    data_dir.mkdir()

    fake_rows = [
        {
            "promotion_id": str(_uuid.uuid4()),
            "crystal_id": str(_uuid.uuid4()),
            "target_layer": "semantic",
            "proposed_at": datetime(2026, 5, 28, 12, 0, 0, tzinfo=timezone.utc).isoformat(),
        },
        {
            "promotion_id": str(_uuid.uuid4()),
            "crystal_id": str(_uuid.uuid4()),
            "target_layer": "procedural",
            "proposed_at": datetime(2026, 5, 28, 13, 0, 0, tzinfo=timezone.utc).isoformat(),
        },
    ]

    with patch("crystalium.__main__.RelationalStore") as MockRelationalStore:
        mock_instance = MagicMock()
        mock_instance.list_pending_promotions.return_value = fake_rows
        MockRelationalStore.return_value = mock_instance

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["promote", "list"],
            env={"CRYSTALIUM_DATA_DIR": str(data_dir)},
            catch_exceptions=False,
        )

    assert result.exit_code == 0, f"promote list failed:\n{result.output}"

    # Both promotion IDs appear in output
    for row in fake_rows:
        assert row["promotion_id"] in result.output, (
            f"promotion_id {row['promotion_id']!r} not found in output"
        )


def test_promote_list_no_pending_rows(tmp_path: Path) -> None:
    """promote list prints 'No pending promotions.' when queue is empty."""
    data_dir = tmp_path / "crystalium_data"
    data_dir.mkdir()

    with patch("crystalium.__main__.RelationalStore") as MockRelationalStore:
        mock_instance = MagicMock()
        mock_instance.list_pending_promotions.return_value = []
        MockRelationalStore.return_value = mock_instance

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["promote", "list"],
            env={"CRYSTALIUM_DATA_DIR": str(data_dir)},
            catch_exceptions=False,
        )

    assert result.exit_code == 0
    assert "No pending promotions" in result.output


def test_promote_list_layer_filter_passed_to_store(tmp_path: Path) -> None:
    """promote list --layer semantic passes layer_filter to RelationalStore."""
    data_dir = tmp_path / "crystalium_data"
    data_dir.mkdir()

    with patch("crystalium.__main__.RelationalStore") as MockRelationalStore:
        mock_instance = MagicMock()
        mock_instance.list_pending_promotions.return_value = []
        MockRelationalStore.return_value = mock_instance

        runner = CliRunner()
        runner.invoke(
            cli,
            ["promote", "list", "--layer", "semantic"],
            env={"CRYSTALIUM_DATA_DIR": str(data_dir)},
            catch_exceptions=False,
        )

        mock_instance.list_pending_promotions.assert_called_once_with(layer_filter="semantic")


# ---------------------------------------------------------------------------
# promote review — calls gate.process_pending
# ---------------------------------------------------------------------------


def test_promote_review_accept_calls_gate(tmp_path: Path) -> None:
    """promote review <id> --accept calls gate.process_pending(id, 'accept')."""
    import uuid as _uuid
    data_dir = tmp_path / "crystalium_data"
    data_dir.mkdir()

    promo_id = str(_uuid.uuid4())

    with patch("crystalium.__main__.PromotionGate") as MockGate:
        mock_gate_instance = MagicMock()
        MockGate.return_value = mock_gate_instance

        with patch("crystalium.__main__.RelationalStore"):
            with patch("crystalium.__main__.Enforcement"):
                runner = CliRunner()
                result = runner.invoke(
                    cli,
                    ["promote", "review", promo_id, "--accept"],
                    env={"CRYSTALIUM_DATA_DIR": str(data_dir)},
                    catch_exceptions=False,
                )

        mock_gate_instance.process_pending.assert_called_once_with(promo_id, "accept")
    assert result.exit_code == 0


def test_promote_review_reject_calls_gate(tmp_path: Path) -> None:
    """promote review <id> --reject calls gate.process_pending(id, 'reject')."""
    import uuid as _uuid
    data_dir = tmp_path / "crystalium_data"
    data_dir.mkdir()

    promo_id = str(_uuid.uuid4())

    with patch("crystalium.__main__.PromotionGate") as MockGate:
        mock_gate_instance = MagicMock()
        MockGate.return_value = mock_gate_instance

        with patch("crystalium.__main__.RelationalStore"):
            with patch("crystalium.__main__.Enforcement"):
                runner = CliRunner()
                result = runner.invoke(
                    cli,
                    ["promote", "review", promo_id, "--reject"],
                    env={"CRYSTALIUM_DATA_DIR": str(data_dir)},
                    catch_exceptions=False,
                )

        mock_gate_instance.process_pending.assert_called_once_with(promo_id, "reject")
    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# index — Redactor config-kwarg regression (pre-existing bug: bare Redactor() crashed)
# ---------------------------------------------------------------------------


def test_index_redactor_receives_config(tmp_path: Path) -> None:
    """Regression: index command must pass config=<Config> to Redactor, not Redactor().

    Before the fix, `Redactor()` in the index command raised:
      TypeError: Redactor.__init__() missing 1 required positional argument: 'config'

    This test patches Redactor at the module level and asserts the constructor was
    called with the `config` keyword argument (not bare).
    """
    data_dir = tmp_path / "crystalium_data"
    data_dir.mkdir()
    md_file = tmp_path / "note.md"
    md_file.write_text("# Hello\nThis is a test document.")

    # Redactor is a lazy local import inside index(), so patch it at the source module.
    with patch("crystalium.aetheryte.redact.Redactor") as MockRedactor:
        mock_redactor_instance = MagicMock()
        MockRedactor.return_value = mock_redactor_instance

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["index", str(tmp_path)],
            env={"CRYSTALIUM_DATA_DIR": str(data_dir)},
            catch_exceptions=False,
        )

    # Redactor must have been constructed with a config keyword argument — never bare.
    assert MockRedactor.called, "Redactor was not instantiated at all"
    call_kwargs = MockRedactor.call_args
    assert call_kwargs.kwargs.get("config") is not None, (
        "Redactor() was called without config= kwarg — "
        "this is the regression that causes TypeError at runtime"
    )


def test_index_single_file_exits_0(tmp_path: Path) -> None:
    """index against a real tmp data dir with a single .md file exits 0 and reports 1 indexed."""
    data_dir = tmp_path / "crystalium_data"
    data_dir.mkdir()
    md_file = tmp_path / "note.md"
    md_file.write_text("# Hello\nThis is a test document for crystalium index.")

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["index", str(tmp_path), "--ext", ".md"],
        env={"CRYSTALIUM_DATA_DIR": str(data_dir)},
        catch_exceptions=False,
    )

    assert result.exit_code == 0, (
        f"index exited {result.exit_code}.\nOutput:\n{result.output}"
    )
    assert "1 indexed" in result.output, (
        f"Expected '1 indexed' in output.\nOutput:\n{result.output}"
    )


# ---------------------------------------------------------------------------
# export (W-GE4) — CLI export subcommand
# ---------------------------------------------------------------------------


class TestExportCli:
    """W-GE4: crystalium export subcommand tests.

    Uses a real (tmp) RelationalStore + _NullGraphStore so no kuzu/lance deps needed.
    Mirrors spec §8.1 and §10 W-GE4 acceptance gate.
    """

    def _invoke_export(
        self,
        tmp_path: Path,
        extra_args: list[str] | None = None,
        env_overrides: dict | None = None,
    ):
        """Helper: set up a minimal data dir and invoke the export command."""
        data_dir = tmp_path / "crystalium_data"
        data_dir.mkdir(exist_ok=True)

        env = {"CRYSTALIUM_DATA_DIR": str(data_dir)}
        if env_overrides:
            env.update(env_overrides)

        args = ["export", "--scope-project", "cli-test-project"]
        if extra_args:
            args.extend(extra_args)

        runner = CliRunner()
        return runner.invoke(cli, args, env=env, catch_exceptions=False)

    def test_export_emits_valid_canonical_json(self, tmp_path: Path) -> None:
        """export emits valid canonical JSON on stdout (G-GE4 CLI arm)."""
        import json as _json
        result = self._invoke_export(tmp_path)
        assert result.exit_code == 0, (
            f"export exited {result.exit_code}.\nOutput:\n{result.output}"
        )
        payload = _json.loads(result.output)
        assert payload["schema_version"] == "graph-export.v1"
        assert "generated_from" in payload
        assert "counts" in payload
        assert "truncated" in payload
        assert "nodes" in payload
        assert "edges" in payload

    def test_export_stdout_is_pure_json(self, tmp_path: Path) -> None:
        """export stdout contains ONLY the JSON payload (structlog routed to stderr)."""
        import json as _json
        result = self._invoke_export(tmp_path)
        assert result.exit_code == 0, result.output
        # Must parse as JSON without error
        _json.loads(result.output.strip())

    def test_export_generated_from_project_matches(self, tmp_path: Path) -> None:
        """generated_from.project matches --scope-project."""
        import json as _json
        result = self._invoke_export(tmp_path)
        assert result.exit_code == 0, result.output
        payload = _json.loads(result.output)
        assert payload["generated_from"]["project"] == "cli-test-project"

    def test_export_scope_visibility_flag(self, tmp_path: Path) -> None:
        """--scope-visibility sets agent_class_visibility in generated_from."""
        import json as _json
        result = self._invoke_export(tmp_path, extra_args=["--scope-visibility", "spectra"])
        assert result.exit_code == 0, result.output
        payload = _json.loads(result.output)
        assert payload["generated_from"]["agent_class_visibility"] == "spectra"

    def test_export_limit_flag_wired(self, tmp_path: Path) -> None:
        """--limit is wired: counts.nodes <= limit."""
        import json as _json
        result = self._invoke_export(tmp_path, extra_args=["--limit", "3"])
        assert result.exit_code == 0, result.output
        payload = _json.loads(result.output)
        assert payload["counts"]["nodes"] <= 3

    def test_export_output_writes_file(self, tmp_path: Path) -> None:
        """--output writes the payload to a file and prints the path on stdout."""
        import json as _json
        out_file = tmp_path / "export_out.json"
        result = self._invoke_export(tmp_path, extra_args=["--output", str(out_file)])
        assert result.exit_code == 0, result.output
        assert out_file.exists(), f"--output file not created: {out_file}"
        payload = _json.loads(out_file.read_text())
        assert payload["schema_version"] == "graph-export.v1"
        # stdout should echo the file path
        assert str(out_file) in result.output

    def test_export_exit_0_on_success(self, tmp_path: Path) -> None:
        """export exits 0 on success."""
        result = self._invoke_export(tmp_path)
        assert result.exit_code == 0, (
            f"Expected exit 0, got {result.exit_code}.\nOutput:\n{result.output}"
        )

    def test_export_missing_scope_project_exits_nonzero(self, tmp_path: Path) -> None:
        """export without --scope-project exits non-zero (Click required option)."""
        data_dir = tmp_path / "crystalium_data"
        data_dir.mkdir(exist_ok=True)
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["export"],
            env={"CRYSTALIUM_DATA_DIR": str(data_dir)},
            catch_exceptions=True,
        )
        assert result.exit_code != 0, (
            "Expected non-zero exit when --scope-project is missing"
        )

    def test_export_truncated_flag_set_when_limit_exceeded(self, tmp_path: Path) -> None:
        """truncated:true when we insert more crystals than --limit."""
        import json as _json
        import uuid as _uuid
        from crystalium.storage.relational import RelationalStore as _RS
        from datetime import datetime, timezone

        data_dir = tmp_path / "crystalium_data"
        data_dir.mkdir(exist_ok=True)

        # Directly populate a real RelationalStore in the data dir.
        # CRYSTALIUM_DATA_DIR sets config.data_dir; Config derives sqlite_path = data_dir / "index.sqlite".
        sqlite_path = data_dir / "index.sqlite"
        rel = _RS(db_path=sqlite_path)
        now = datetime.now(timezone.utc).isoformat()
        for i in range(5):
            cid = f"cli-trunc-{i:03d}"
            c = {
                "id": cid,
                "layer": "semantic",
                "summary": f"crystal {i}",
                "provenance": {"source": "verified_agent", "author_agent": "test", "task_id": None, "created_at": now},
                "trust_tier": "T1",
                "validation_state": "unverified",
                "scope": {"project": "cli-test-project", "agent_class_visibility": None, "sensitivity_tag": "none"},
                "temporal": {"t_valid_from": now, "t_valid_to": None, "superseded_by": None},
                "utility": {"access_count": 1, "last_access": now, "outcome_success_score": None, "importance": 0.5, "novelty_at_write": 0.5},
                "status": "active",
            }
            rel.insert_crystal(c)

        result = self._invoke_export(
            tmp_path,
            extra_args=["--limit", "3"],
            env_overrides={"CRYSTALIUM_DATA_DIR": str(data_dir)},
        )
        assert result.exit_code == 0, result.output
        payload = _json.loads(result.output)
        assert payload["truncated"] is True
        assert len(payload["nodes"]) == 3

    def test_export_include_flags_accepted(self, tmp_path: Path) -> None:
        """All include flags are accepted without error."""
        result = self._invoke_export(
            tmp_path,
            extra_args=[
                "--include-quarantined",
                "--include-deprecated",
                "--include-superseded",
                "--all-visibility",
                "--include-content-ref",
                "--include-drift",
                "--synthesize-links",
                "--dangling-policy", "keep",
            ],
        )
        assert result.exit_code == 0, (
            f"export with all include flags failed:\n{result.output}"
        )

    def test_export_format_json_default(self, tmp_path: Path) -> None:
        """--format json (default) produces a JSON object."""
        import json as _json
        result = self._invoke_export(tmp_path, extra_args=["--format", "json"])
        assert result.exit_code == 0, result.output
        payload = _json.loads(result.output)
        assert isinstance(payload, dict)

    def test_export_invalid_layer_exits_nonzero(self, tmp_path: Path) -> None:
        """--layers with an invalid layer name exits non-zero."""
        data_dir = tmp_path / "crystalium_data"
        data_dir.mkdir(exist_ok=True)
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["export", "--scope-project", "p", "--layers", "episodic,bogus_layer"],
            env={"CRYSTALIUM_DATA_DIR": str(data_dir)},
            catch_exceptions=True,
        )
        assert result.exit_code != 0, (
            "Expected non-zero exit for invalid layer"
        )

    def test_export_calls_graph_exporter_core(self, tmp_path: Path) -> None:
        """export calls GraphExporter.export() — the shared core (G-GE6 structural parity)."""
        import json as _json
        from unittest.mock import MagicMock

        data_dir = tmp_path / "crystalium_data"
        data_dir.mkdir(exist_ok=True)

        fake_result = {
            "schema_version": "graph-export.v1",
            "generated_from": {
                "project": "cli-test-project",
                "agent_class_visibility": None,
                "layers": None,
                "generated_at": "2026-06-22T12:00:00+00:00",
                "caller_tier": None,
            },
            "counts": {"nodes": 0, "edges": 0, "nodes_total_estimate": 0,
                       "edges_dropped_dangling": 0, "edges_deduped": 0},
            "truncated": False,
            "nodes": [],
            "edges": [],
        }

        mock_exporter_instance = MagicMock()
        mock_exporter_instance.export.return_value = fake_result
        mock_exporter_cls = MagicMock(return_value=mock_exporter_instance)

        with patch("crystalium.__main__.GraphExporter", mock_exporter_cls):
            runner = CliRunner()
            result = runner.invoke(
                cli,
                ["export", "--scope-project", "cli-test-project"],
                env={"CRYSTALIUM_DATA_DIR": str(data_dir)},
                catch_exceptions=False,
            )

        assert result.exit_code == 0, result.output
        assert mock_exporter_instance.export.called, "GraphExporter.export was not called"
        payload = _json.loads(result.output)
        assert payload["schema_version"] == "graph-export.v1"
