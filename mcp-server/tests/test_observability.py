"""W1 Objective 6 — observability: latency aggregation + panels + recall p95.

Container-first:
  docker compose run --rm crystalium pytest mcp-server/tests/test_observability.py -v

Covers the telemetry aggregation that record_call() feeds and the recall-p95
panel the W8 availability SLO depends on. The server-handler wiring (tool_span +
record_call) is exercised indirectly: these tests assert the sink behaves.
"""

from __future__ import annotations

import pytest

from crystalium import telemetry


@pytest.fixture(autouse=True)
def _clean_samples():
    telemetry.reset_latency_samples()
    yield
    telemetry.reset_latency_samples()


def test_record_call_feeds_latency_samples():
    for ms in (10.0, 20.0, 30.0):
        telemetry.record_call(tool="crystalium.recall", latency_ms=ms)
    assert telemetry.latency_percentile("crystalium.recall", 50) is not None


def test_percentile_nearest_rank():
    for ms in range(1, 101):  # 1..100 ms
        telemetry.record_call(tool="crystalium.recall", latency_ms=float(ms))
    assert telemetry.latency_percentile("crystalium.recall", 50) == 50.0
    assert telemetry.latency_percentile("crystalium.recall", 95) == 95.0
    assert telemetry.latency_percentile("crystalium.recall", 99) == 99.0
    assert telemetry.latency_percentile("crystalium.recall", 100) == 100.0


def test_percentile_empty_is_none():
    assert telemetry.latency_percentile("crystalium.recall", 95) is None
    assert telemetry.recall_p95() is None


def test_recall_p95_panel_metric():
    for ms in range(1, 21):  # 1..20
        telemetry.record_call(tool="crystalium.recall", latency_ms=float(ms))
    # nearest-rank p95 of 20 samples -> rank ceil(0.95*20)=19 -> 19th value = 19.0
    assert telemetry.recall_p95() == 19.0


def test_latency_panel_per_tool():
    telemetry.record_call(tool="crystalium.recall", latency_ms=5.0)
    telemetry.record_call(tool="crystalium.commit", latency_ms=7.0, layer="semantic")
    telemetry.record_call(tool="dream.prune", latency_ms=9.0, op="prune")
    panel = telemetry.latency_panel()
    assert set(panel) == {"crystalium.recall", "crystalium.commit", "dream.prune"}
    assert panel["crystalium.recall"]["count"] == 1
    assert panel["crystalium.commit"]["p95_ms"] == 7.0


def test_emit_latency_panel_returns_panel():
    telemetry.record_call(tool="crystalium.recall", latency_ms=3.0)
    panel = telemetry.emit_latency_panel()
    assert "crystalium.recall" in panel


def test_sample_buffer_is_bounded():
    n = telemetry._MAX_SAMPLES + 50
    for i in range(n):
        telemetry.record_call(tool="crystalium.recall", latency_ms=float(i))
    assert len(telemetry._latency_samples["crystalium.recall"]) == telemetry._MAX_SAMPLES


def test_tool_span_is_noop_safe():
    # Must not raise whether or not an OTel backend is configured.
    with telemetry.tool_span("crystalium.recall", tier="T1") as span:
        _ = span  # may be None (no-op fallback) — that's fine
