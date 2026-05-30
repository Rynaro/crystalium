"""CRYSTALIUM CLI — entry-point for all operator interactions.

Subcommands:
  serve     — start the MCP server (stdio transport, D2)
  index     — bulk-load a directory of .md/.txt files into Episodic (T0)
  dream     — manually trigger the Dream worker
  doctor    — health checks: imports, config, schemas, data_dir writable, optional deps
  canary    — run the canary suite (W5 stub in v0.1)
  promote   — manage pending promotion queue (list / review)

Usage (container-first):
  docker compose run --rm crystalium python -m crystalium <subcommand>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

import click

# Module-level imports make these names patchable in tests via
# `patch("crystalium.__main__.RelationalStore")` etc.
from crystalium.storage.relational import RelationalStore  # noqa: F401 — test-patchable
from crystalium.gate import PromotionGate  # noqa: F401 — test-patchable
from crystalium.enforcement import Enforcement  # noqa: F401 — test-patchable


def _select_importance_fn(config):
    """W2: pick EVB (Gain x Need) or legacy importance_score per config.evb_enabled.

    Mirrors server._build_components so CLI-built components honour the same flag.
    """
    from crystalium.importance import importance_score

    if config.evb_enabled:
        from crystalium.evb import make_evb_scorer

        return make_evb_scorer(config.evb_gain_weights, config.evb_need_weights)
    return importance_score


# ---------------------------------------------------------------------------
# CLI group
# ---------------------------------------------------------------------------


@click.group()
@click.version_option(prog_name="crystalium")
def cli() -> None:
    """CRYSTALIUM — portable memory harness for the Eidolons."""


# ---------------------------------------------------------------------------
# serve
# ---------------------------------------------------------------------------


@cli.command()
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=False, path_type=Path),
    default=None,
    help="Path to crystalium.yaml (default: crystalium.yaml in cwd or env vars).",
)
def serve(config_path: Optional[Path]) -> None:
    """Start the CRYSTALIUM MCP server over stdio (D2: stdio-only in v0.1)."""
    import asyncio

    from crystalium.config import Config
    from crystalium.server import run_stdio

    if config_path and config_path.exists():
        config = Config.from_yaml(config_path)
    else:
        config = Config.from_env()

    transport = (config.transport or "stdio").lower()
    if transport == "stdio":
        asyncio.run(run_stdio(config))
    elif transport in ("http", "streamable-http", "streamable_http"):
        from crystalium.server import run_http

        asyncio.run(run_http(config))
    else:
        raise click.ClickException(
            f"Transport {config.transport!r} is not supported. "
            "Use CRYSTALIUM_TRANSPORT=stdio (default) or http."
        )


# ---------------------------------------------------------------------------
# index
# ---------------------------------------------------------------------------


@cli.command()
@click.argument("path", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=False, path_type=Path),
    default=None,
)
@click.option(
    "--ext",
    "extensions",
    multiple=True,
    default=(".md", ".txt"),
    help="File extensions to index (default: .md .txt).",
    show_default=True,
)
def index(path: Path, config_path: Optional[Path], extensions: tuple[str, ...]) -> None:
    """Bulk-load a directory of files into Episodic (T0).

    PATH can be a directory (recursive) or a single file.
    """
    from datetime import datetime, timezone

    from crystalium.config import Config
    from crystalium.layers.episodic import EpisodicLayer
    from crystalium.aetheryte.redact import Redactor
    from crystalium.enforcement import Enforcement
    from crystalium.storage.blob import BlobStore
    from crystalium.storage.relational import RelationalStore
    from crystalium.schemas import Provenance
    from crystalium.trust import Tier

    if config_path and config_path.exists():
        config = Config.from_yaml(config_path)
    else:
        config = Config.from_env()

    blob_store = BlobStore(root=config.blob_root)
    relational = RelationalStore(db_path=config.sqlite_path)
    enforcement = Enforcement(config)
    redactor = Redactor()

    importance_fn = _select_importance_fn(config)
    episodic = EpisodicLayer(
        blob_store=blob_store,
        relational=relational,
        vector_store=None,
        graph_store=None,
        enforcement=enforcement,
        redactor=redactor,
        importance_fn=importance_fn,
    )

    p = path.resolve()
    files: list[Path]
    if p.is_file():
        files = [p]
    else:
        files = [f for f in p.rglob("*") if f.is_file() and f.suffix in extensions]

    if not files:
        click.echo(f"No files matching {extensions} found in {path}")
        return

    now = datetime.now(timezone.utc)
    provenance = Provenance(
        source="human",
        author_agent="crystalium-cli",
        task_id=None,
        created_at=now,
    )

    ok = 0
    errors = 0
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
            payload = {
                "summary": text[:256].strip() or f.name,
                "content": text,
                "scope": {"project": "default", "agent_class_visibility": "all"},
            }
            result = episodic.commit(payload=payload, provenance=provenance, caller_tier=Tier.T0)
            ok += 1
            click.echo(f"  indexed {f.name} → {result['id'][:8]}")
        except Exception as exc:
            errors += 1
            click.echo(f"  ERROR {f.name}: {exc}", err=True)

    click.echo(f"\nDone: {ok} indexed, {errors} errors.")


# ---------------------------------------------------------------------------
# forget — right-to-be-forgotten (W4): the ONE sanctioned hard-delete
# ---------------------------------------------------------------------------


@cli.command()
@click.argument("crystal_id")
@click.option("--reason", required=True, help="Audited reason for the erasure.")
@click.option("--config", "config_path", type=click.Path(exists=False, path_type=Path), default=None)
@click.option("--yes", is_flag=True, default=False, help="Skip the interactive confirmation.")
def forget(crystal_id: str, reason: str, config_path: Optional[Path], yes: bool) -> None:
    """Operator right-to-be-forgotten: hard-tombstone a crystal (T0, audited).

    The SOLE exception to never-hard-delete. Routes through the `forget`
    enforcement op (T0-only), writes a forget_audit row, then deletes the crystal
    row + its blob. Irreversible.
    """
    import datetime as _dt

    from crystalium.config import Config
    from crystalium.enforcement import Enforcement
    from crystalium.storage.blob import BlobStore
    from crystalium.trust import Tier

    config = Config.from_yaml(config_path) if (config_path and config_path.exists()) else Config.from_env()
    relational = RelationalStore(db_path=config.sqlite_path)
    enforcement = Enforcement(config)
    blob_store = BlobStore(root=config.blob_root)

    crystal = relational.get_crystal(crystal_id)
    if crystal is None:
        raise click.ClickException(f"Crystal not found: {crystal_id!r}")
    layer = crystal.get("layer", "episodic")

    # Operator gate THROUGH the chokepoint: forget op is T0-only.
    try:
        enforcement.assert_tier_allowed("crystalium.forget", layer, Tier.T0, "forget")
    except Exception as exc:
        raise click.ClickException(f"forget denied by enforcement: {exc}") from exc

    if not yes:
        click.confirm(
            f"Permanently erase crystal {crystal_id!r} ({layer})? This is irreversible.",
            abort=True,
        )

    now = _dt.datetime.now(_dt.timezone.utc)
    relational.tombstone(crystal_id, reason, actor_tier=str(Tier.T0), now=now)
    content_ref = crystal.get("content_ref")
    if content_ref:
        try:
            blob_store.tombstone(content_ref)
        except Exception as exc:  # blob already gone / shared — audit row already written
            click.echo(f"  (blob tombstone note: {exc})", err=True)
    click.echo(f"Forgotten: {crystal_id} (audited; reason={reason!r}).")


# ---------------------------------------------------------------------------
# dream
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--force", is_flag=True, default=False, help="Force a Dream run even if gap not met.")
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=False, path_type=Path),
    default=None,
)
def dream(force: bool, config_path: Optional[Path]) -> None:
    """Manually trigger the Dream worker consolidation run."""
    from crystalium.config import Config
    from crystalium.dream.worker import DreamWorker
    from crystalium.dream.scheduler import DreamScheduler, _make_run_id
    from crystalium.enforcement import Enforcement
    from crystalium.gate import PromotionGate
    from crystalium.storage.relational import RelationalStore

    if config_path and config_path.exists():
        config = Config.from_yaml(config_path)
    else:
        config = Config.from_env()

    relational = RelationalStore(db_path=config.sqlite_path)
    enforcement = Enforcement(config)
    gate = PromotionGate(config, relational, enforcement)

    worker = DreamWorker(
        relational=relational,
        vector_store=None,
        graph_store=None,
        enforcement=enforcement,
        gate=gate,
        importance_fn=_select_importance_fn(config),
        persist_dynamics=config.evb_enabled,
        replay_evb=config.dream_replay_evb,
        interleave=config.dream_interleave,
        interleave_ratio=config.dream_interleave_ratio,
        stc=config.dream_stc,
        stc_threshold=config.stc_threshold,
        stc_window_s=config.stc_window_s,
    )

    if force:
        run_id = _make_run_id()
        click.echo(f"Forcing Dream run: {run_id}")
        worker.enqueue(run_id)
        worker.run_pending()
        click.echo("Dream run complete.")
    else:
        scheduler = DreamScheduler(config=config, worker=worker, store=relational)
        triggered = scheduler.on_session_end()
        if triggered:
            click.echo(f"Dream enqueued: {triggered}")
            worker.run_pending()
            click.echo("Dream run complete.")
        else:
            click.echo("Dream skipped: min_dream_gap not met. Use --force to override.")


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------


@cli.command()
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=False, path_type=Path),
    default=None,
)
def doctor(config_path: Optional[Path]) -> None:
    """Check CRYSTALIUM installation health.

    P0 checks (non-zero exit on failure):
      1. Core imports loadable
      2. Config loads without error
      3. JSON schemas parse
      4. data_dir writable

    Optional checks (warning only):
      5. sentence-transformers importable
      6. apscheduler importable
      7. lancedb importable
      8. kuzu importable
    """
    p0_ok = True

    def _check(label: str, fn):
        nonlocal p0_ok
        try:
            fn()
            click.echo(f"  [OK]   {label}")
        except Exception as exc:
            click.echo(f"  [FAIL] {label}: {exc}", err=True)
            p0_ok = False

    def _optional(label: str, fn):
        try:
            fn()
            click.echo(f"  [OK]   {label} (optional)")
        except Exception as exc:
            click.echo(f"  [WARN] {label} (optional): {exc}")

    click.echo("CRYSTALIUM doctor\n")

    # P0-1: core imports
    def _imports():
        import crystalium  # noqa: F401
        from crystalium.config import Config  # noqa: F401
        from crystalium.enforcement import Enforcement  # noqa: F401
        from crystalium.trust import Tier  # noqa: F401
        from crystalium.schemas import Crystal  # noqa: F401
        from crystalium.ecl import build_for_tool_result  # noqa: F401

    _check("core imports", _imports)

    # P0-2: config loads
    def _config():
        from crystalium.config import Config
        if config_path and Path(config_path).exists():
            Config.from_yaml(Path(config_path))
        else:
            Config.from_env()

    _check("config loads", _config)

    # P0-3: JSON schemas parse
    def _schemas():
        import json
        schema_dir = Path(__file__).parent.parent.parent.parent / "schemas"
        if not schema_dir.exists():
            # Try relative to package root
            schema_dir = Path(__file__).parent.parent.parent.parent.parent / "schemas"
        if schema_dir.exists():
            for schema_file in schema_dir.glob("*.json"):
                json.loads(schema_file.read_text())
        # If no schemas dir found, soft-pass (may be running from src tree)

    _check("JSON schemas parse", _schemas)

    # P0-4: data_dir writable
    def _data_dir():
        from crystalium.config import Config
        if config_path and Path(config_path).exists():
            cfg = Config.from_yaml(Path(config_path))
        else:
            cfg = Config.from_env()
        probe = cfg.data_dir / ".doctor_probe"
        probe.write_text("ok")
        probe.unlink()

    _check("data_dir writable", _data_dir)

    # Optional deps
    _optional("sentence-transformers", lambda: __import__("sentence_transformers"))
    _optional("apscheduler", lambda: __import__("apscheduler"))
    _optional("lancedb", lambda: __import__("lancedb"))
    _optional("kuzu", lambda: __import__("kuzu"))

    click.echo()
    if p0_ok:
        click.echo("doctor: all P0 checks passed.")
        sys.exit(0)
    else:
        click.echo("doctor: P0 checks FAILED — see errors above.", err=True)
        sys.exit(1)


# ---------------------------------------------------------------------------
# canary
# ---------------------------------------------------------------------------


@cli.command()
@click.option(
    "--mode", default="both",
    type=click.Choice(["both", "on_only", "off_only"]),
    help="Which A/B arm(s) to run.",
)
@click.option("--no-write", is_flag=True, help="Do not persist a results JSON.")
def canary(mode: str, no_write: bool) -> None:
    """Run the canary suite memory-on/off A/B and print the headline.

    Dispatches to the evals bench (evals.ab_memory_onoff.run_all). The bench is a
    separate top-level package (evals/, not crystalium.evals); see also
    `python -m evals` for the ablation/axes/forget subcommands.
    """
    try:
        from evals.ab_memory_onoff import run_all
    except ImportError as exc:  # pragma: no cover - import-environment guard
        raise click.ClickException(
            f"evals package not importable ({exc}). Ensure PYTHONPATH includes the "
            "repo root (the container sets PYTHONPATH=/app/src:/app)."
        ) from exc

    result = run_all(mode=mode, write_results=not no_write)
    click.echo(json.dumps(result.get("headline", {}), indent=2, default=str))


# ---------------------------------------------------------------------------
# promote
# ---------------------------------------------------------------------------


@cli.group()
def promote() -> None:
    """Manage the pending promotion queue (G5 human-confirm path)."""


@promote.command("list")
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=False, path_type=Path),
    default=None,
)
@click.option("--layer", default=None, help="Filter by target layer.")
def promote_list(config_path: Optional[Path], layer: Optional[str]) -> None:
    """List pending promotions awaiting human review."""
    from crystalium.config import Config

    if config_path and Path(str(config_path)).exists():
        config = Config.from_yaml(Path(config_path))
    else:
        config = Config.from_env()

    relational = RelationalStore(db_path=config.sqlite_path)

    try:
        # Try with layer_filter kwarg first (v0.2+ RelationalStore extension)
        try:
            rows = relational.list_pending_promotions(layer_filter=layer)
        except TypeError:
            # RelationalStore.list_pending_promotions doesn't accept layer_filter yet;
            # fetch all and filter client-side
            all_rows = relational.list_pending_promotions()
            rows = [r for r in all_rows if layer is None or r.get("target_layer") == layer]
    except AttributeError:
        # Fallback if list_pending_promotions not yet on RelationalStore
        rows = []

    if not rows:
        click.echo("No pending promotions.")
        return

    click.echo(f"{'promotion_id':<38}  {'crystal_id':<38}  {'layer':<12}  {'proposed_at'}")
    click.echo("-" * 110)
    for row in rows:
        pid = row.get("promotion_id", "?")
        cid = row.get("crystal_id", "?")
        tgt = row.get("target_layer", "?")
        pat = row.get("proposed_at", "?")
        click.echo(f"{pid:<38}  {cid:<38}  {tgt:<12}  {pat}")


@promote.command("review")
@click.argument("promotion_id")
@click.option("--accept", "action", flag_value="accept", default=True)
@click.option("--reject", "action", flag_value="reject")
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=False, path_type=Path),
    default=None,
)
def promote_review(
    promotion_id: str,
    action: str,
    config_path: Optional[Path],
) -> None:
    """Accept or reject a pending promotion.

    PROMOTION_ID  UUID of the pending promotion to review.
    """
    from crystalium.config import Config

    if config_path and Path(str(config_path)).exists():
        config = Config.from_yaml(Path(config_path))
    else:
        config = Config.from_env()

    relational = RelationalStore(db_path=config.sqlite_path)
    enforcement = Enforcement(config)
    gate = PromotionGate(config, relational, enforcement)

    gate.process_pending(promotion_id, action)  # type: ignore[arg-type]
    click.echo(f"Promotion {promotion_id}: {action}ed.")


# ---------------------------------------------------------------------------
# quarantine — operator triage over validation_state='quarantined' (W6)
# ---------------------------------------------------------------------------


@cli.group()
def quarantine() -> None:
    """Operator triage queue for quarantined crystals (W6; T0-gated, audited)."""


@quarantine.command("list")
@click.option("--config", "config_path", type=click.Path(exists=False, path_type=Path), default=None)
@click.option("--layer", default=None, help="Filter by layer (default: all).")
def quarantine_list(config_path: Optional[Path], layer: Optional[str]) -> None:
    """List crystals awaiting quarantine triage (validation_state='quarantined')."""
    from crystalium.config import Config

    config = Config.from_yaml(config_path) if (config_path and config_path.exists()) else Config.from_env()
    relational = RelationalStore(db_path=config.sqlite_path)

    rows = relational.list_by_validation_state("quarantined", layer_filter=layer)
    if not rows:
        click.echo("No quarantined crystals.")
        return
    click.echo(f"{'crystal_id':<38}  {'layer':<10}  {'tier':<5}  {'summary'}")
    click.echo("-" * 100)
    for r in rows:
        click.echo(f"{r.get('id','?'):<38}  {r.get('layer','?'):<10}  "
                   f"{r.get('trust_tier','?'):<5}  {(r.get('summary') or '')[:48]}")


@quarantine.command("review")
@click.argument("crystal_id")
@click.option("--accept", "action", flag_value="accept", default=True,
              help="Clear quarantine (validation_state -> unverified).")
@click.option("--reject", "action", flag_value="reject",
              help="Soft-deprecate the crystal (status -> deprecated; not a hard-delete).")
@click.option("--reason", required=True, help="Audited reason for the decision.")
@click.option("--config", "config_path", type=click.Path(exists=False, path_type=Path), default=None)
@click.option("--yes", is_flag=True, default=False, help="Skip the interactive confirmation.")
def quarantine_review(
    crystal_id: str, action: str, reason: str, config_path: Optional[Path], yes: bool
) -> None:
    """Accept or reject a quarantined crystal (T0 operator, audited reason).

    CRYSTAL_ID  id of the quarantined crystal to triage.

    accept -> validation_state quarantined→unverified (crystal stays, active).
    reject -> status→deprecated (soft-delete; the row survives; P0-5 preserved).
    Both append a quarantine_audit row. Routes through the T0-only `review` op.
    """
    import datetime as _dt

    from crystalium.config import Config
    from crystalium.enforcement import Enforcement
    from crystalium.trust import Tier

    config = Config.from_yaml(config_path) if (config_path and config_path.exists()) else Config.from_env()
    relational = RelationalStore(db_path=config.sqlite_path)
    enforcement = Enforcement(config)

    crystal = relational.get_crystal(crystal_id)
    if crystal is None:
        raise click.ClickException(f"Crystal not found: {crystal_id!r}")
    if crystal.get("validation_state") != "quarantined":
        raise click.ClickException(
            f"Crystal {crystal_id!r} is not quarantined "
            f"(validation_state={crystal.get('validation_state')!r}); nothing to triage."
        )
    layer = crystal.get("layer", "episodic")

    # Operator gate THROUGH the chokepoint: review op is T0-only.
    try:
        enforcement.assert_tier_allowed("crystalium.review", layer, Tier.T0, "review")
    except Exception as exc:
        raise click.ClickException(f"review denied by enforcement: {exc}") from exc

    verb = "accept (clear quarantine)" if action == "accept" else "reject (deprecate)"
    if not yes:
        click.confirm(f"{verb} crystal {crystal_id!r} ({layer})?", abort=True)

    now = _dt.datetime.now(_dt.timezone.utc)
    if action == "accept":
        relational.set_validation_state(crystal_id, "unverified")
    else:
        relational.set_status(crystal_id, "deprecated")
    relational.record_quarantine_review(
        crystal_id, action, reason, actor_tier=str(Tier.T0), now=now
    )
    click.echo(f"Quarantine {action}ed: {crystal_id} (audited; reason={reason!r}).")


# ---------------------------------------------------------------------------
# Entry-point
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entry-point called by pyproject.toml [scripts] crystalium = ..."""
    cli()


if __name__ == "__main__":
    main()
