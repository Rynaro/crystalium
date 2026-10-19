"""Capture six historical probe exchanges from the real stdio server."""

import argparse
import importlib.metadata
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from evals.wire.compare import validate


def capture(timeout: float = 90) -> dict:
    """Start a fresh isolated server, retaining stderr and bounding every wait."""
    with tempfile.TemporaryDirectory(prefix="crystalium-wire-") as temporary:
        env = {key: value for key, value in os.environ.items() if not key.startswith("CRYSTALIUM_")}
        # Stable basename preserves the data-dir-derived scope across captures.
        env.update(
            CRYSTALIUM_DATA_DIR=str(Path(temporary) / "golden-wire"),
            CRYSTALIUM_TRANSPORT="stdio",
            CRYSTALIUM_SKIP_SLOW="1",
            HF_HUB_OFFLINE="1",
            TRANSFORMERS_OFFLINE="1",
        )
        with tempfile.TemporaryFile(mode="w+") as errors:
            proc = subprocess.Popen(
                [sys.executable, "-m", "crystalium", "serve"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=errors,
                text=True,
                env=env,
            )
            output: queue.Queue = queue.Queue()

            def pump() -> None:
                assert proc.stdout is not None
                for line in proc.stdout:
                    output.put(line)
                output.put(None)

            threading.Thread(target=pump, daemon=True).start()

            def rpc(method: str, params: dict, request_id: int | None = None) -> dict | None:
                message = {"jsonrpc": "2.0", "method": method, "params": params}
                if request_id is not None:
                    message["id"] = request_id
                assert proc.stdin is not None
                proc.stdin.write(json.dumps(message) + "\n")
                proc.stdin.flush()
                if request_id is None:
                    return None
                deadline = time.monotonic() + timeout
                while True:
                    try:
                        line = output.get(timeout=max(0, deadline - time.monotonic()))
                    except queue.Empty as exc:
                        raise RuntimeError("Timed out awaiting " + method) from exc
                    if line is None:
                        raise RuntimeError("Server exited awaiting " + method)
                    # Some supported server versions log to stdout at startup.
                    # Keep the same historical JSON-RPC-only capture boundary.
                    try:
                        response = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(response, dict):
                        continue
                    if response.get("id") == request_id:
                        return response

            def call(name: str, arguments: dict, request_id: int) -> dict | None:
                return rpc("tools/call", {"name": name, "arguments": arguments}, request_id)

            try:
                result = {
                    "initialize": rpc(
                        "initialize",
                        {
                            "protocolVersion": "2025-06-18",
                            "capabilities": {},
                            "clientInfo": {"name": "golden-wire", "version": "1"},
                        },
                        1,
                    )
                }
                rpc("notifications/initialized", {})
                result["tools_list"] = rpc("tools/list", {}, 2)
                scope = {"project": "golden-wire", "agent_class_visibility": "all"}
                result["call_recall"] = call(
                    "recall",
                    {
                        "scope": scope,
                        "query": "golden wire probe",
                        "k": 1,
                    },
                    3,
                )
                result["call_commit"] = call(
                    "commit",
                    {
                        "layer": "episodic",
                        "payload": {"summary": "golden wire fixture record", "scope": scope},
                        "provenance": {
                            "source": "environment",
                            "author_agent": "golden-wire",
                            "task_id": "gw-1",
                            "created_at": "2026-08-05T00:00:00Z",
                        },
                    },
                    5,
                )
                result["call_schema_violation"] = call(
                    "recall",
                    {
                        "query": "missing scope on purpose",
                    },
                    6,
                )
                result["call_unknown_tool"] = call("definitely_not_a_tool", {}, 4)
                validate(result)
                # A syntactically valid digest must also point at the stored bytes.
                from hashlib import sha256

                from evals.wire.compare import commit_payload

                pointer = commit_payload(result)["content_ref"]
                blobs = list(Path(env["CRYSTALIUM_DATA_DIR"]).rglob(pointer))
                if len(blobs) != 1 or sha256(blobs[0].read_bytes()).hexdigest() != pointer:
                    raise ValueError("content_ref does not resolve to the committed blob")
                return result
            except Exception as exc:
                errors.seek(0)
                raise RuntimeError(str(exc) + "\nserver stderr:\n" + errors.read()) from exc
            finally:
                assert proc.stdin is not None
                proc.stdin.close()
                try:
                    proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
                if proc.stdout:
                    proc.stdout.close()


def main() -> None:
    """Write raw wire plus independent interpreter/dependency provenance."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    result = capture()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    metadata = {
        "python": sys.version,
        "mcp": importlib.metadata.version("mcp"),
        "crystalium_metadata": importlib.metadata.version("crystalium"),
        "transport": "stdio",
        "protocol_requested": "2025-06-18",
        "fresh_store": True,
        "skip_slow": True,
        "offline_models": True,
    }
    args.output.with_suffix(".environment.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n"
    )
    print("Captured six exchanges: " + str(args.output))


if __name__ == "__main__":
    main()
