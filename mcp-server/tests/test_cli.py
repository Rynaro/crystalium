"""W4 tests: CLI subcommands (doctor + promote list).

Tests:
  test_doctor_healthy_exits_0             — healthy env → exit code 0
  test_doctor_readonly_data_dir_nonzero   — read-only data_dir → non-zero exit
  test_promote_list_returns_pending_rows  — mock RelationalStore → rows printed

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
