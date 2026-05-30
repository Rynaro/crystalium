"""W6 C6 — quarantine triage CLI (operator T0, audited, soft-deprecate on reject).

Container-first:
  docker compose run --rm crystalium pytest mcp-server/tests/test_quarantine_cli.py -v
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from click.testing import CliRunner

from crystalium.config import Config
from crystalium.storage.relational import RelationalStore

_NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)


def _quarantined(cid: str, *, vstate="quarantined", status="active") -> dict:
    return {
        "id": cid, "layer": "episodic", "trust_tier": "T3",
        "validation_state": vstate, "status": status, "summary": f"untrusted note {cid}",
        "content_ref": "a" * 64, "scope": {"project": "p"},
        "provenance": {"source": "unverified_agent", "created_at": _NOW.isoformat()},
        "utility": {"importance": 0.0}, "temporal": {"t_valid_from": _NOW.isoformat()},
    }


def _store(monkeypatch, tmp_path) -> tuple[Config, RelationalStore]:
    monkeypatch.setenv("CRYSTALIUM_DATA_DIR", str(tmp_path / "data"))
    cfg = Config.from_env()
    return cfg, RelationalStore(db_path=cfg.sqlite_path)


def _cli():
    from crystalium.__main__ import cli
    return cli


def test_list_shows_only_quarantined(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg, store = _store(monkeypatch, tmp_path)
    store.insert_crystal(_quarantined("q1"))
    store.insert_crystal(_quarantined("u1", vstate="unverified"))
    res = CliRunner().invoke(_cli(), ["quarantine", "list"], catch_exceptions=False)
    assert res.exit_code == 0, res.output
    assert "q1" in res.output and "u1" not in res.output


def test_accept_clears_quarantine_and_audits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg, store = _store(monkeypatch, tmp_path)
    store.insert_crystal(_quarantined("q1"))
    res = CliRunner().invoke(
        _cli(), ["quarantine", "review", "q1", "--accept", "--reason", "vetted", "--yes"],
        catch_exceptions=False,
    )
    assert res.exit_code == 0, res.output
    fresh = RelationalStore(db_path=cfg.sqlite_path)
    c = fresh.get_crystal("q1")
    assert c["validation_state"] == "unverified" and c["status"] == "active"
    audit = fresh.list_quarantine_audit()
    assert audit[0]["action"] == "accept" and audit[0]["reason"] == "vetted"


def test_reject_soft_deprecates_not_deleted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg, store = _store(monkeypatch, tmp_path)
    store.insert_crystal(_quarantined("q2"))
    res = CliRunner().invoke(
        _cli(), ["quarantine", "review", "q2", "--reject", "--reason", "poison", "--yes"],
        catch_exceptions=False,
    )
    assert res.exit_code == 0, res.output
    fresh = RelationalStore(db_path=cfg.sqlite_path)
    c = fresh.get_crystal("q2")
    assert c is not None and c["status"] == "deprecated"   # soft-delete: row survives
    assert fresh.list_quarantine_audit()[0]["action"] == "reject"
    assert fresh.list_forget_audit() == []                 # NOT a hard tombstone


def test_reason_required(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg, store = _store(monkeypatch, tmp_path)
    store.insert_crystal(_quarantined("q3"))
    res = CliRunner().invoke(_cli(), ["quarantine", "review", "q3", "--accept", "--yes"])
    assert res.exit_code != 0                               # missing --reason -> usage error
    assert RelationalStore(db_path=cfg.sqlite_path).get_crystal("q3")["validation_state"] == "quarantined"


def test_non_quarantined_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg, store = _store(monkeypatch, tmp_path)
    store.insert_crystal(_quarantined("v1", vstate="validated"))
    res = CliRunner().invoke(
        _cli(), ["quarantine", "review", "v1", "--accept", "--reason", "x", "--yes"],
    )
    assert res.exit_code != 0
    assert "not quarantined" in res.output


def test_missing_id_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _store(monkeypatch, tmp_path)
    res = CliRunner().invoke(
        _cli(), ["quarantine", "review", "nope", "--accept", "--reason", "x", "--yes"],
    )
    assert res.exit_code != 0 and "not found" in res.output.lower()
