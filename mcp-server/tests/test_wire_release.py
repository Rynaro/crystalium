"""Release-wire anchors: malformed inputs fail before excluded values disappear."""

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import evals.wire
import pytest
from evals.wire.capture import capture
from evals.wire.compare import commit_payload, compare

WIRE = Path(evals.wire.__file__).resolve().parent
BASELINE = WIRE / "baselines/golden-wire-v2.4.0.json"


@pytest.fixture
def baseline() -> dict:
    """Load the actual released v2.4.0 raw wire."""
    return json.loads(BASELINE.read_text())


@pytest.mark.parametrize("side", ["baseline", "candidate", "both"])
@pytest.mark.parametrize("value", [None, "", " ", 42, [], {}])
@pytest.mark.parametrize("field", ["name", "version", "tool_name", "id", "content_ref"])
def test_invalid_required_fields_rejected(
    baseline: dict, side: str, value: object, field: str
) -> None:
    """Neither side may launder null/empty/wrong-typed fields through masking."""
    bad = copy.deepcopy(baseline)
    if field in ("name", "version"):
        bad["initialize"]["result"]["serverInfo"][field] = value
    elif field == "tool_name":
        bad["tools_list"]["result"]["tools"][0]["name"] = value
    else:
        payload = commit_payload(bad)
        payload[field] = value
        bad["call_commit"]["result"]["content"][0]["text"] = json.dumps(payload)
    pair = (bad, baseline) if side == "baseline" else (baseline, bad)
    if side == "both":
        pair = (bad, bad)
    with pytest.raises((ValueError, TypeError)):
        compare(*pair)


@pytest.mark.parametrize("change", ["remove", "add", "rename", "schema", "isError", "error_text"])
def test_semantic_changes_fail(baseline: dict, change: str) -> None:
    """Tool surface, schema, and error behavior remain compared verbatim."""
    bad = copy.deepcopy(baseline)
    tools = bad["tools_list"]["result"]["tools"]
    if change == "remove":
        tools.pop()
    elif change == "add":
        tool = copy.deepcopy(tools[0])
        tool["name"] = "unexpected_tool"
        tools.append(tool)
    elif change == "rename":
        tools[0]["name"] = "renamed"
    elif change == "schema":
        tools[0]["inputSchema"]["required"] = []
    elif change == "isError":
        bad["call_unknown_tool"]["result"]["isError"] = False
    else:
        bad["call_unknown_tool"]["result"]["content"][0]["text"] = "changed"
    if change in {"isError", "error_text"}:
        with pytest.raises(ValueError):
            compare(baseline, bad)
    else:
        assert compare(baseline, bad)


def test_volatility_is_narrow_and_input_unchanged(baseline: dict) -> None:
    """Only enumerated validated values change; raw captures remain untouched."""
    candidate = copy.deepcopy(baseline)
    candidate["initialize"]["result"]["serverInfo"]["version"] = "next-release"
    payload = commit_payload(candidate)
    payload.update(id="new-id", content_ref="a" * 64)
    candidate["call_commit"]["result"]["content"][0]["text"] = json.dumps(payload)
    saved = copy.deepcopy(candidate)
    assert compare(baseline, candidate) == []
    assert candidate == saved
    payload["new_volatile_looking_id"] = "do-not-mask"
    candidate["call_commit"]["result"]["content"][0]["text"] = json.dumps(payload)
    assert compare(baseline, candidate)


def test_null_redcheck_cli(baseline: dict, tmp_path: Path) -> None:
    """The user-visible command exits one on the issue's live null poison."""
    baseline["initialize"]["result"]["serverInfo"]["version"] = None
    poisoned = tmp_path / "null.json"
    poisoned.write_text(json.dumps(baseline))
    result = subprocess.run(
        [sys.executable, "-m", "evals.wire.compare", str(BASELINE), str(poisoned)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 1
    assert "serverInfo.version" in result.stdout
    assert "WIRE IDENTICAL" not in result.stdout


def test_baseline_integrity_and_released_metadata(baseline: dict) -> None:
    """The reviewed source-release capture and historical bytes cannot drift silently."""
    provenance = json.loads((WIRE / "baselines/v2.4.0.provenance.json").read_text())
    assert hashlib.sha256(BASELINE.read_bytes()).hexdigest() == provenance["capture_sha256"]
    assert baseline["initialize"]["result"]["serverInfo"]["version"] == "2.4.0"
    historical = WIRE / "historical/golden-wire-v1.11.0.json"
    assert hashlib.sha256(historical.read_bytes()).hexdigest() == (
        "52ee03efdc8bfce02a1eec0b364033ca96bb28ad907e0e054ebc1606d195b983"
    )


@pytest.mark.parametrize(
    "change",
    [
        "null_text",
        "object_content",
        "null_schema",
        "null_capabilities",
        "null_protocol",
        "empty_tools",
    ],
)
def test_same_malformed_shape_on_both_sides_fails(baseline: dict, change: str) -> None:
    """Equal corruption must never masquerade as a valid matching wire."""
    if change == "null_text":
        baseline["call_recall"]["result"]["content"][0]["text"] = None
    elif change == "object_content":
        baseline["call_unknown_tool"]["result"]["content"] = {"text": "x"}
    elif change == "null_schema":
        baseline["tools_list"]["result"]["tools"][0]["inputSchema"] = None
    elif change == "empty_tools":
        baseline["tools_list"]["result"]["tools"] = []
    elif change == "null_capabilities":
        baseline["initialize"]["result"]["capabilities"] = None
    else:
        baseline["initialize"]["result"]["protocolVersion"] = None
    with pytest.raises(ValueError):
        compare(baseline, baseline)


def test_fresh_live_captures_repeat_and_match_release(baseline: dict) -> None:
    """Independent stores produce the same contract, checked against the release."""
    first = capture()
    second = capture()
    assert compare(first, second) == []
    review = json.loads((WIRE / "reviewed-deltas/main-after-v2.4.0.json").read_text())
    assert compare(baseline, first) == review["diff"]
