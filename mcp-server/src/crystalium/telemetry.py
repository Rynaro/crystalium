"""Telemetry sink for CRYSTALIUM.

Configures structlog JSONL output and provides OpenTelemetry span helpers.
Every MCP tool call emits one record_call() entry (P0-7, atlas-aci pattern).

No enforcement logic here — that lives in enforcement.py (W2).
"""

from __future__ import annotations

import math
import time
from contextlib import contextmanager
from typing import Any, Generator

import structlog

# ---------------------------------------------------------------------------
# structlog configuration — JSONL, stdlib processors chain
# ---------------------------------------------------------------------------


def configure_logging(level: str = "INFO", *, json_logs: bool = True) -> None:
    """Configure structlog for JSONL output.

    Call once at server startup before any log.* calls.
    """
    import logging

    shared_processors: list[Any] = [
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]

    if json_logs:
        shared_processors.append(structlog.processors.JSONRenderer())
    else:
        shared_processors.append(structlog.dev.ConsoleRenderer())

    structlog.configure(
        processors=shared_processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


# Module-level logger — each module should use structlog.get_logger() directly.
log = structlog.get_logger("crystalium.telemetry")


# ---------------------------------------------------------------------------
# OpenTelemetry span helpers
# ---------------------------------------------------------------------------


def _get_tracer() -> Any:
    """Return an OTel tracer if opentelemetry-sdk is importable, else a no-op."""
    try:
        from opentelemetry import trace  # type: ignore[import-untyped]

        return trace.get_tracer("crystalium", "0.1.0")
    except ImportError:
        return None


@contextmanager
def tool_span(
    tool_name: str,
    *,
    layer: str | None = None,
    tier: str | None = None,
) -> Generator[Any, None, None]:
    """Context manager that wraps a tool call in an OpenTelemetry span.

    Falls back to a no-op if opentelemetry-sdk is not available.

    Usage:
        with tool_span("crystalium.recall", layer="semantic", tier="T1"):
            result = await do_recall(...)
    """
    tracer = _get_tracer()
    if tracer is None:
        yield None
        return

    with tracer.start_as_current_span(f"crystalium.{tool_name}") as span:
        if layer:
            span.set_attribute("crystalium.layer", layer)
        if tier:
            span.set_attribute("crystalium.tier", tier)
        yield span


# ---------------------------------------------------------------------------
# Single telemetry sink — called by every tool at the end of its handler
# ---------------------------------------------------------------------------


def record_call(
    *,
    tool: str,
    layer: str | None = None,
    tier: str | None = None,
    op: str | None = None,
    result: str = "ok",
    latency_ms: float,
    overflow_flag: bool = False,
    error: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Emit one structured telemetry record for a tool call.

    Args:
        tool:         Tool name (e.g. 'crystalium.recall').
        layer:        Memory layer involved, if any.
        tier:         Caller trust tier (T0..T3), if known.
        op:           Operation type (commit / recall / propose_promote / force_promote).
        result:       'ok' | 'error' | 'rejected' | 'pending'.
        latency_ms:   Wall-clock duration in milliseconds.
        overflow_flag: True if any output/token cap was hit.
        error:        Error code if result != 'ok'.
        extra:        Any additional structured fields (never include raw content).

    Intentionally does NOT log raw content or sensitive payload data (P0-7,
    cross-cutting controls, MISSION.md §Security & privacy surface).
    """
    fields: dict[str, Any] = {
        "tool": tool,
        "result": result,
        "latency_ms": round(latency_ms, 2),
        "overflow": overflow_flag,
    }
    if layer:
        fields["layer"] = layer
    if tier:
        fields["tier"] = tier
    if op:
        fields["op"] = op
    if error:
        fields["error"] = error
    if extra:
        # Merge extra but guard against reserved keys
        for k, v in extra.items():
            if k not in fields:
                fields[k] = v

    _record_latency(tool, latency_ms)
    log.info("tool_call", **fields)


# ---------------------------------------------------------------------------
# Latency aggregation + panels
#
# A bounded in-memory sample store fed by record_call(). Used to emit per-tool
# latency panels (p50/p95/p99) and the recall-p95 panel that the W8 availability
# SLO depends on. In-process only; degrades to empty panels with no samples.
# ---------------------------------------------------------------------------

_MAX_SAMPLES = 2048
_latency_samples: dict[str, list[float]] = {}


def _record_latency(tool: str, latency_ms: float) -> None:
    """Append a latency sample for *tool*, bounded to the most recent N."""
    buf = _latency_samples.setdefault(tool, [])
    buf.append(latency_ms)
    if len(buf) > _MAX_SAMPLES:
        del buf[0 : len(buf) - _MAX_SAMPLES]


def latency_percentile(tool: str, p: float) -> float | None:
    """Nearest-rank pth-percentile latency (ms) for *tool*, or None if no samples."""
    buf = sorted(_latency_samples.get(tool, []))
    if not buf:
        return None
    rank = int(math.ceil((p / 100.0) * len(buf)))
    idx = min(max(rank, 1), len(buf)) - 1
    return round(buf[idx], 2)


def recall_p95() -> float | None:
    """Recall p95 latency (ms) — the W8 availability-SLO panel metric."""
    return latency_percentile("crystalium.recall", 95)


def latency_panel() -> dict[str, dict[str, Any]]:
    """Build a per-tool latency panel: count + p50/p95/p99 (ms)."""
    panel: dict[str, dict[str, Any]] = {}
    for tool, buf in _latency_samples.items():
        if not buf:
            continue
        panel[tool] = {
            "count": len(buf),
            "p50_ms": latency_percentile(tool, 50),
            "p95_ms": latency_percentile(tool, 95),
            "p99_ms": latency_percentile(tool, 99),
        }
    return panel


def emit_latency_panel() -> dict[str, dict[str, Any]]:
    """Log the latency panel (recall/commit/forget/dream) + recall p95. Returns it."""
    panel = latency_panel()
    log.info("latency_panel", panel=panel, recall_p95_ms=recall_p95())
    return panel


def reset_latency_samples() -> None:
    """Clear all latency samples (test isolation / panel reset)."""
    _latency_samples.clear()


# ---------------------------------------------------------------------------
# Convenience: measure latency
# ---------------------------------------------------------------------------


def now_ms() -> float:
    """Return current monotonic time in milliseconds."""
    return time.monotonic() * 1000.0
