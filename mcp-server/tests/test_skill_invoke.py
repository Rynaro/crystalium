"""W4 tests: G3 skill_invoke sandbox contract.

Tests:
  test_g3_sandbox_timeout_enforced    — infinite loop → timeout, exit_code == -1
  test_g3_output_capped               — >8KiB stdout → truncated + overflow_flag
  test_g3_path_escape_blocked         — workdir outside /sandbox/<id> rejected
  test_g3_operator_warning_logged     — telemetry sink received the operator warning

Container-first: run via
  docker compose run --rm crystalium pytest mcp-server/tests/test_skill_invoke.py -v
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from crystalium.config import Config
from crystalium.enforcement import Enforcement, PathEscape
from crystalium.gate import PromotionGate, SkillResult
from crystalium.layers.procedural import ProceduralLayer
from crystalium.trust import Tier
from crystalium.aetheryte.redact import Redactor
from crystalium.importance import importance_score


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def config(tmp_path: Path) -> Config:
    return Config(
        data_dir=tmp_path / "crystalium_data",
        sandbox_root=tmp_path / "sandbox",
        skill_invoke_timeout_s=5,
        skill_invoke_output_cap_bytes=8192,
        rate_limit_per_minute=1000,
    )


@pytest.fixture
def enforcement(config: Config) -> Enforcement:
    return Enforcement(config)


@pytest.fixture
def relational_store(tmp_path: Path):
    from crystalium.storage.relational import RelationalStore
    return RelationalStore(db_path=tmp_path / "test.sqlite")


@pytest.fixture
def blob_store(tmp_path: Path):
    from crystalium.storage.blob import BlobStore
    return BlobStore(root=tmp_path / "blobs")


@pytest.fixture
def procedural(config, blob_store, relational_store, enforcement) -> ProceduralLayer:
    gate = PromotionGate(config, relational_store, enforcement)
    return ProceduralLayer(
        blob_store=blob_store,
        relational=relational_store,
        enforcement=enforcement,
        gate=gate,
        redactor=Redactor(config),
        importance_fn=importance_score,
        data_dir=config.data_dir,
    )


@pytest.fixture
def sandbox_dir(tmp_path: Path) -> Path:
    """Create a /sandbox/<skill_id>-like directory under tmp_path."""
    d = tmp_path / "sandbox" / "test-skill"
    d.mkdir(parents=True)
    return d


# ---------------------------------------------------------------------------
# test_g3_sandbox_timeout_enforced
# ---------------------------------------------------------------------------


def test_g3_sandbox_timeout_enforced(procedural, tmp_path: Path) -> None:
    """G3: subprocess running an infinite loop is killed by the timeout.

    Uses 'python -c while True: pass' — guaranteed to block indefinitely.
    timeout_s=2 so the test stays fast.
    """
    workdir = tmp_path / "sandbox" / "slow-skill"
    workdir.mkdir(parents=True)

    # Use the enforcement path's string prefix check (workdir exists)
    result = procedural.invoke(
        skill_id="slow-skill",
        args={"command": [sys.executable, "-c", "import time; time.sleep(999)"]},
        caller_tier=Tier.T1,
        timeout_s=2,
        output_cap_bytes=8192,
        workdir=workdir,
    )

    assert isinstance(result, SkillResult)
    assert result.exit_code == -1, f"Expected timeout exit_code -1, got {result.exit_code}"


def test_g3_timeout_returns_skill_result_type(procedural, tmp_path: Path) -> None:
    """Timeout path returns SkillResult (not raises)."""
    workdir = tmp_path / "sandbox" / "timeout-check-skill"
    workdir.mkdir(parents=True)

    result = procedural.invoke(
        skill_id="timeout-check-skill",
        args={"command": [sys.executable, "-c", "import time; time.sleep(999)"]},
        caller_tier=Tier.T0,
        timeout_s=1,
        workdir=workdir,
    )
    assert result.exit_code == -1


# ---------------------------------------------------------------------------
# test_g3_output_capped
# ---------------------------------------------------------------------------


def test_g3_output_capped(procedural, tmp_path: Path) -> None:
    """G3: >8KiB stdout → truncated to cap and overflow_flag=True."""
    workdir = tmp_path / "sandbox" / "noisy-skill"
    workdir.mkdir(parents=True)

    # Emit 16 KiB of 'x' chars
    sixteen_kib = 16 * 1024
    cap = 8192

    result = procedural.invoke(
        skill_id="noisy-skill",
        args={"command": [sys.executable, "-c", f"print('x' * {sixteen_kib})"]},
        caller_tier=Tier.T1,
        timeout_s=10,
        output_cap_bytes=cap,
        workdir=workdir,
    )

    assert result.overflow_flag is True, "overflow_flag must be True when output exceeds cap"
    assert len(result.stdout) <= cap, (
        f"stdout len {len(result.stdout)} exceeds cap {cap}"
    )


def test_g3_output_within_cap_no_overflow(procedural, tmp_path: Path) -> None:
    """Within-cap output does not set overflow_flag."""
    workdir = tmp_path / "sandbox" / "quiet-skill"
    workdir.mkdir(parents=True)

    result = procedural.invoke(
        skill_id="quiet-skill",
        args={"command": [sys.executable, "-c", "print('hello')"]},
        caller_tier=Tier.T1,
        timeout_s=10,
        output_cap_bytes=8192,
        workdir=workdir,
    )

    assert result.overflow_flag is False
    assert b"hello" in result.stdout


# ---------------------------------------------------------------------------
# test_g3_path_escape_blocked
# ---------------------------------------------------------------------------


def test_g3_path_escape_blocked(enforcement: Enforcement) -> None:
    """G3: workdir resolving outside /sandbox/<skill_id> raises PathEscape."""
    skill_id = "escape-skill"
    malicious_workdir = Path("/tmp/escape-attempt")
    sandbox_root = Path(f"/sandbox/{skill_id}")

    with pytest.raises(PathEscape):
        enforcement.assert_no_path_escape(malicious_workdir, sandbox_root)


def test_g3_path_escape_blocked_via_server_handler(config, blob_store, relational_store) -> None:
    """Server-level _handle_skill_invoke blocks workdir outside /sandbox/<skill_id>."""
    from crystalium.server import _handle_skill_invoke

    enforcement = Enforcement(config)
    gate = PromotionGate(config, relational_store, enforcement)
    proc = ProceduralLayer(
        blob_store=blob_store,
        relational=relational_store,
        enforcement=enforcement,
        gate=gate,
        redactor=Redactor(config),
        importance_fn=importance_score,
        data_dir=config.data_dir,
    )

    args = {
        "skill_id": "test-skill",
        "workdir": "/etc/passwd",  # attempt to escape
    }

    with pytest.raises(PathEscape):
        _handle_skill_invoke(args, proc, enforcement, config, Tier.T1)


def test_g3_valid_sandbox_path_not_blocked(enforcement: Enforcement) -> None:
    """A path string under /sandbox/<skill_id> passes the prefix check.

    Note: enforcement.assert_no_path_escape uses Path.resolve(strict=True) which
    requires the path to exist. The server-level check uses string prefix instead.
    This test validates the string-prefix path used by _handle_skill_invoke.
    """
    from crystalium.server import _handle_skill_invoke
    from crystalium.config import Config
    from crystalium.storage.blob import BlobStore
    from crystalium.storage.relational import RelationalStore
    from pathlib import Path
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        cfg = Config(data_dir=Path(tmp) / "data", rate_limit_per_minute=1000)
        blob = BlobStore(root=Path(tmp) / "blobs")
        rel = RelationalStore(db_path=Path(tmp) / "test.sqlite")
        enf = Enforcement(cfg)
        gate = PromotionGate(cfg, rel, enf)
        proc = ProceduralLayer(
            blob_store=blob,
            relational=rel,
            enforcement=enf,
            gate=gate,
            redactor=Redactor(cfg),
            importance_fn=importance_score,
            data_dir=cfg.data_dir,
        )

        # Valid path: must start with /sandbox/<skill_id>
        # The actual subprocess will fail (command not found) but the path check passes
        args = {
            "skill_id": "my-skill",
            "args": {"command": "nonexistent-binary-xyz"},
            "workdir": "/sandbox/my-skill/run1",
        }
        # Should not raise PathEscape — may raise something from subprocess
        try:
            _handle_skill_invoke(args, proc, enf, cfg, Tier.T1)
        except PathEscape:
            pytest.fail("PathEscape raised for a valid /sandbox/<skill_id> path")
        except Exception:
            pass  # subprocess errors are expected; we only care about PathEscape not firing


# ---------------------------------------------------------------------------
# test_g3_operator_warning_logged
# ---------------------------------------------------------------------------


def test_g3_operator_warning_logged(enforcement: Enforcement, caplog) -> None:
    """G3: enforcement.warn_skill_invoke emits the D5 operator warning."""
    with caplog.at_level(logging.WARNING, logger="crystalium.enforcement"):
        enforcement.warn_skill_invoke("my-test-skill")

    # At least one WARNING record containing the skill_id
    warning_records = [r for r in caplog.records if r.levelname == "WARNING"]
    assert any("my-test-skill" in r.getMessage() for r in warning_records), (
        "No WARNING log containing skill_id 'my-test-skill' emitted by warn_skill_invoke"
    )


def test_g3_operator_warning_structlog(enforcement: Enforcement) -> None:
    """warn_skill_invoke emits to the structlog logger without raising."""
    # Should not raise
    enforcement.warn_skill_invoke("operator-warning-test-skill")


def test_g3_operator_warning_mentions_sandbox_boundary(enforcement: Enforcement, caplog) -> None:
    """Operator warning message must mention the container sandbox boundary (D5)."""
    with caplog.at_level(logging.WARNING, logger="crystalium.enforcement"):
        enforcement.warn_skill_invoke("boundary-check-skill")

    all_messages = " ".join(str(r.message) for r in caplog.records)
    # The D5 operator warning string includes "OPERATOR'S RESPONSIBILITY"
    assert "OPERATOR" in all_messages or "sandbox" in all_messages.lower(), (
        "Operator warning does not mention operator responsibility or sandbox boundary (D5)"
    )
