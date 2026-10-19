"""Compare raw captures, rejecting malformed captures before narrow masking."""

import argparse
import copy
import difflib
import json
import re
from pathlib import Path
from typing import Any

EXCHANGES = {
    "initialize": 1,
    "tools_list": 2,
    "call_recall": 3,
    "call_unknown_tool": 4,
    "call_commit": 5,
    "call_schema_violation": 6,
}
VOLATILE = {"id", "content_ref", "created_at", "committed_at", "timestamp"}


def nonempty(value: Any, path: str) -> None:
    """Require a nonblank string, including on excluded fields."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(path + " must be a non-empty string")


def commit_payload(capture: dict) -> dict:
    """Decode the successful commit's sole text result."""
    content = capture["call_commit"]["result"]["content"]
    if not isinstance(content, list) or len(content) != 1 or content[0]["type"] != "text":
        raise ValueError("call_commit must contain one text payload")
    payload = json.loads(content[0]["text"])
    if not isinstance(payload, dict):
        raise ValueError("call_commit payload must be an object")
    return payload


def validate(capture: dict) -> None:
    """Validate both sides before masking; no null can satisfy presence checks."""
    if not isinstance(capture, dict) or set(capture) != set(EXCHANGES):
        raise ValueError("capture must contain exactly the six named exchanges")
    for name, request_id in EXCHANGES.items():
        response = capture[name]
        if not isinstance(response, dict) or response.get("jsonrpc") != "2.0":
            raise ValueError(name + " must be a JSON-RPC response")
        if type(response.get("id")) is not int or response["id"] != request_id:
            raise ValueError(name + " has an invalid response id")
        if not isinstance(response.get("result"), dict) or "error" in response:
            raise ValueError(name + " must have a result, not a JSON-RPC error")
        if name.startswith("call_"):
            result = response["result"]
            content = result.get("content")
            if (
                type(result.get("isError")) is not bool
                or not isinstance(content, list)
                or not content
            ):
                raise ValueError(name + " requires content and boolean isError")
            for item in content:
                if not isinstance(item, dict) or item.get("type") != "text":
                    raise ValueError(name + " requires text content objects")
                nonempty(item.get("text"), name + ".content[].text")
            expected_error = name in {"call_unknown_tool", "call_schema_violation"}
            if result["isError"] is not expected_error:
                raise ValueError(name + " has unexpected success/error status")
    info = capture["initialize"]["result"]["serverInfo"]
    if not isinstance(info, dict):
        raise ValueError("serverInfo must be an object")
    for key in ("name", "version"):
        nonempty(info.get(key), "serverInfo." + key)
    initialization = capture["initialize"]["result"]
    nonempty(initialization.get("protocolVersion"), "initialize.protocolVersion")
    if not isinstance(initialization.get("capabilities"), dict):
        raise ValueError("initialize.capabilities must be an object")
    tools = capture["tools_list"]["result"]["tools"]
    if not isinstance(tools, list) or not tools:
        raise ValueError("tools must be a non-empty array")
    for tool in tools:
        if not isinstance(tool, dict) or not isinstance(tool.get("inputSchema"), dict):
            raise ValueError("each tool requires an object and inputSchema")
        nonempty(tool.get("name"), "tools[].name")
    if len({tool["name"] for tool in tools}) != len(tools):
        raise ValueError("tool names must be unique")
    recall = json.loads(capture["call_recall"]["result"]["content"][0]["text"])
    if not isinstance(recall, dict) or not isinstance(recall.get("records"), list):
        raise ValueError("call_recall must return an object with records array")
    for key in ("budget", "slot_breakdown"):
        if not isinstance(recall.get(key), dict):
            raise ValueError("call_recall." + key + " must be an object")
    unknown = json.loads(capture["call_unknown_tool"]["result"]["content"][0]["text"])
    if not isinstance(unknown, dict) or unknown.get("error") != "UNKNOWN_TOOL":
        raise ValueError("call_unknown_tool must return UNKNOWN_TOOL")
    nonempty(unknown.get("message"), "call_unknown_tool.message")
    payload = commit_payload(capture)
    if capture["call_commit"]["result"]["isError"] or payload.get("status") != "committed":
        raise ValueError("call_commit must succeed")
    nonempty(payload.get("id"), "call_commit.id")
    if not isinstance(payload.get("content_ref"), str) or not re.fullmatch(
        r"[0-9a-f]{64}", payload["content_ref"]
    ):
        raise ValueError("call_commit.content_ref must be a SHA-256 hex pointer")
    for key in VOLATILE - {"id", "content_ref"}:
        if key in payload:
            nonempty(payload[key], "call_commit." + key)


def normalise(capture: dict) -> dict:
    """Mask validated version and top-level commit volatility only."""
    validate(capture)
    result = copy.deepcopy(capture)
    result["initialize"]["result"]["serverInfo"]["version"] = "<version>"
    payload = commit_payload(result)
    for key in VOLATILE:
        if key in payload and payload[key] is not None:
            payload[key] = "<volatile:" + key + ">"
    result["call_commit"]["result"]["content"][0]["text"] = json.dumps(payload, sort_keys=True)
    return result


def compare(baseline: dict, candidate: dict) -> list[str]:
    """Return a unified diff; never auto-accept an intentional contract change."""
    # Complete raw validation of BOTH sides before either is normalised.
    validate(baseline)
    validate(candidate)
    return list(
        difflib.unified_diff(
            json.dumps(normalise(baseline), indent=2, sort_keys=True).splitlines(),
            json.dumps(normalise(candidate), indent=2, sort_keys=True).splitlines(),
            fromfile="baseline",
            tofile="candidate",
            lineterm="",
        )
    )


def main() -> int:
    """CLI: exit one on malformed capture or any unreviewed difference."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument(
        "--reviewed-delta",
        type=Path,
        help="Exact unified diff with a nonempty review reason; never a mask",
    )
    args = parser.parse_args()
    try:
        differences = compare(
            json.loads(args.baseline.read_text()), json.loads(args.candidate.read_text())
        )
        if args.reviewed_delta:
            review = json.loads(args.reviewed_delta.read_text())
            nonempty(review.get("reason"), "review reason")
            if differences == review.get("diff"):
                print("WIRE MATCHES EXACT REVIEWED DELTA: " + review["reason"])
                return 0
            print("REVIEWED DELTA MISMATCH")
            return 1
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print("INVALID CAPTURE: " + str(exc))
        return 1
    if differences:
        print("WIRE DIFFERENCES (review required)\n" + "\n".join(differences))
        return 1
    print("WIRE IDENTICAL — six exchanges, modulo documented volatility")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
