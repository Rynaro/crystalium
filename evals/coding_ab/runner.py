"""First-party MCP sessions, isolated coding runs and executable result oracles."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import secrets
import subprocess
import sys
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import patch

import anyio
from mcp import ClientSession
from mcp.shared.memory import create_client_server_memory_streams

from evals.coding_ab.report import emit, summarize
from evals.coding_ab.solver import solve

ROOT = Path(__file__).parent
ARMS = ("off", "always_on_k10", "always_on_k3", "gated_k3", "fix_span", "raw", "irrelevant")


def count_tokens(text: str) -> int:
    """Fixture protocol token unit: whitespace-delimited words, never model tokens."""
    return len(text.split())


def check_source(source: str, oracle: str, directory: Path) -> bool:
    """Execute source against a private, immutable test outside the solver workspace."""
    directory.mkdir(parents=True)
    (directory / "implementation.py").write_text(source)
    (directory / "check.py").write_text(oracle)
    marker = "CRYSTALIUM_ORACLE_COMPLETE_" + secrets.token_hex(16)
    # Exit zero alone is insufficient: imports may call exit(0) before tests.
    # The evaluator wrapper emits a fresh completion marker only after run_path
    # returns normally. This detects premature termination, not hostile spoofing.
    wrapper = "import runpy; runpy.run_path('check.py'); print(" + repr(marker) + ")"
    result = subprocess.run(
        [sys.executable, "-B", "-c", wrapper],
        cwd=directory,
        capture_output=True,
        timeout=10,
        check=False,
        env={"PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    if result.returncode not in (0, 1):
        raise RuntimeError("fixture oracle process failed: " + result.stderr.decode())
    return result.returncode == 0 and result.stdout.splitlines()[-1:] == [marker.encode()]


@asynccontextmanager
async def memory_session(config: Any) -> Any:
    """Run the production server through actual MCP initialize/list/call traffic.

    Sparse-only fixture transport: null vector/graph adapters avoid models and
    network, while real relational/blob stores, enforcement, assembly and recall
    remain untouched. No direct writes to a memory store are performed here.
    """
    from crystalium import server as implementation

    with (
        patch.object(implementation, "VectorStore", lambda **_: implementation._NullVectorStore()),
        patch.object(implementation, "GraphStore", lambda **_: implementation._NullGraphStore()),
        patch("crystalium.composer._TOKENIZER", count_tokens),
    ):
        server, _scheduler = implementation._build_server(config)
    async with (
        create_client_server_memory_streams() as (client_streams, server_streams),
        anyio.create_task_group() as group,
    ):
        group.start_soon(server.run, *server_streams, server.create_initialization_options())
        async with ClientSession(*client_streams) as client:
            await client.initialize()
            names = {tool.name for tool in (await client.list_tools()).tools}
            if not {"commit", "assemble_context"} <= names:
                raise RuntimeError("first-party MCP tools unavailable")
            yield client
        group.cancel_scope.cancel()


async def call(client: Any, name: str, arguments: dict) -> dict:
    """Fail closed on MCP errors rather than counting infrastructure failure as loss."""
    result = await client.call_tool(name, arguments)
    if result.is_error:
        raise RuntimeError(str(result.content))
    texts = [item.text for item in result.content if item.type == "text"]
    return json.loads(texts[0])


def payloads(task: dict, form: str, unrelated: bool) -> list[dict]:
    """Same repair facts in raw/structured forms; unrelated records are independent."""
    from crystalium.fix_span import render_fix_span

    query = "image_padding" if unrelated else task["id"]
    fix = "return x * 0" if unrelated else task["replacement"]
    directive = "PATCH[return x + 1 => " + fix + "]"
    anchor = "implementation.py" if task["anchor"] else "historical/missing.py"
    result = []
    for index in range(10):
        structured = {
            "kind": "fix_span",
            "fix_span": {
                "schema_version": "1.0",
                "symptom": query + " incorrect transform",
                "root_cause": "Wrong arithmetic operator",
                "fix_pattern": directive,
                "anchors": [{"kind": "file", "value": anchor}],
                "constraints": ["Preserve the public function"],
                "locator": {"kind": "handoff", "value": "calibration-" + str(index)},
            },
        }
        record = (
            structured
            if form == "fix_span"
            else {"summary": "Historical trace: " + render_fix_span(structured)}
        )
        result.append(record)
    return result


def token_match(text: str, budget: int) -> str:
    """Match exactly in the declared protocol units, preserving leading advice."""
    words = text.split()
    return " ".join((words + ["padding"] * max(0, budget - len(words)))[:budget])


def live_solve(request: dict, workspace: Path, command: list[str]) -> dict:
    """Invoke an opt-in external solver using a JSON file protocol, never a shell."""
    request_file = workspace / "request.json"
    response_file = workspace / "response.json"
    request_file.write_text(json.dumps(request))
    subprocess.run(
        [*command, str(request_file), str(response_file)], cwd=workspace, timeout=300, check=True
    )
    response = json.loads(response_file.read_text())
    if not isinstance(response.get("source"), str):
        raise ValueError("solver must return source text")
    if type(response.get("steps")) is not int or response["steps"] < 0:
        raise ValueError("solver steps must be a nonnegative integer")
    if type(response.get("total_tokens")) is not int or response["total_tokens"] < 0:
        raise ValueError("live solver must report nonnegative total_tokens")
    return response


async def run_sample(
    task: dict,
    seed: int,
    arm: str,
    directory: Path,
    oracle: str,
    budget: int | None,
    live_command: list[str] | None,
) -> dict:
    """Fresh repo and server per sample; commit -> enforcement -> recall -> solver -> test."""
    from crystalium.config import Config
    from crystalium.scope import canonical_project_key

    workspace = directory / "workspace"
    workspace.mkdir(parents=True)
    source = (ROOT / "fixtures" / task["repo"] / "implementation.py").read_text()
    (workspace / "implementation.py").write_text(source)
    store = directory / "memory"
    if store.exists():
        raise ValueError("sample must start with an absent memory root")
    config = Config(
        data_dir=store,
        repo_root=workspace,
        fix_span_enabled=(arm == "fix_span"),
        memory_injection_mode="off"
        if arm == "off"
        else "gated"
        if arm == "gated_k3"
        else "always_on",
        rate_limit_per_minute=100000,
    )
    scope = {"project": canonical_project_key(store)}
    async with memory_session(config) as client:
        unrelated = arm == "irrelevant"
        for record in payloads(task, arm, unrelated):
            await call(
                client,
                "commit",
                {
                    "layer": "episodic",
                    "payload": {**record, "scope": scope},
                    "provenance": {"source": "verified_agent", "author_agent": "coding-ab"},
                },
            )
        result = await call(
            client,
            "assemble_context",
            {
                "scope": scope,
                "query": "image_padding" if unrelated else task["id"],
                "k": 10 if arm in ("always_on_k10", "irrelevant") else 3,
                "layers": ["episodic"],
                "needs_memory": task["needs_memory"],
            },
        )
    memory = "\n".join(record["summary"] for record in result["records"])
    retrieved_tokens = count_tokens(memory)
    if retrieved_tokens != result["injected_token_count"]:
        raise AssertionError("assembly accounting differs from injected text")
    if arm == "irrelevant":
        if budget is None:
            raise ValueError("irrelevant arm requires paired k10 token count")
        memory = token_match(memory, budget)
    request = {
        "source": source,
        "memory": memory,
        "seed": seed,
        "exploration": task["exploration"],
        "task": "Repair transform according to its docstring.",
    }
    response = (
        solve(request) if live_command is None else live_solve(request, workspace, live_command)
    )
    (workspace / "implementation.py").write_text(response["source"])
    resolved = check_source(response["source"], oracle, directory / "oracle")
    return {
        "task": task["id"],
        "seed": seed,
        "arm": arm,
        "solver": "fixture-v1" if live_command is None else "external-json-v1",
        "resolved": resolved,
        "injected_tokens": count_tokens(memory),
        "retrieved_tokens": retrieved_tokens,
        "records": len(result["records"]),
        "gate_reason": result["gate_reason"],
        "memory_sha256": hashlib.sha256(memory.encode()).hexdigest(),
        "output_sha256": hashlib.sha256(response["source"].encode()).hexdigest(),
        "steps": response["steps"],
        "total_tokens": response["total_tokens"],
        "clean_store": True,
    }


async def experiment(
    seeds: tuple[int, ...] = (0, 1, 2, 3),
    arms: tuple[str, ...] = ARMS,
    resamples: int = 2000,
    rng_seed: int = 74,
    live_command: list[str] | None = None,
) -> dict:
    """Execute an exactly paired experiment; never reuse state across samples."""
    if not seeds or len(set(seeds)) != len(seeds):
        raise ValueError("seeds must be nonempty and unique")
    if "off" not in arms or len(set(arms)) != len(arms) or not set(arms) <= set(ARMS):
        raise ValueError("unique valid arms including off required")
    if "irrelevant" in arms and "always_on_k10" not in arms:
        raise ValueError("irrelevant requires always_on_k10 for token matching")
    if resamples < 1000:
        raise ValueError("at least 1000 bootstrap resamples required")
    if live_command is not None and os.environ.get("CRYSTALIUM_CODING_AB_LIVE") != "1":
        raise ValueError("live solver requires CRYSTALIUM_CODING_AB_LIVE=1")
    tasks = json.loads((ROOT / "tasks.json").read_text())["tasks"]
    rows = []
    # Set process identity explicitly; no T0 bypass and no direct store seeding.
    with (
        patch("crystalium.telemetry._relational_store", None),
        patch.dict(
            os.environ, {"CRYSTALIUM_CALLER_EIDOLON": "atlas", "CRYSTALIUM_CALLER_TIER": "T1"}
        ),
        tempfile.TemporaryDirectory(prefix="coding-ab-") as temp,
    ):
        root = Path(temp)
        for task in tasks:
            fixture = ROOT / "fixtures" / task["repo"]
            oracle = (fixture / "check.py").read_text()
            if check_source(
                (fixture / "implementation.py").read_text(), oracle, root / task["id"] / "red"
            ):
                raise AssertionError("fixture must fail before repair")
            if not check_source(
                (fixture / "reference.txt").read_text(), oracle, root / task["id"] / "reference"
            ):
                raise AssertionError("reference must pass oracle")
            for seed in seeds:
                budget = None
                # Always match against the same paired k10, independent of CLI arm order.
                ordered = sorted(arms, key=lambda a: (a == "irrelevant", ARMS.index(a)))
                for arm in ordered:
                    row = await run_sample(
                        task,
                        seed,
                        arm,
                        root / task["id"] / str(seed) / arm,
                        oracle,
                        budget,
                        live_command,
                    )
                    if arm == "always_on_k10":
                        budget = row["injected_tokens"]
                    rows.append(row)
    report = {
        "schema_version": 1,
        "mode": "fixture" if live_command is None else "live",
        "measurement": "synthetic instrument calibration; not product efficacy evidence",
        "default_decision": "no claim; fixture evidence cannot change production defaults",
        "tokenizer": "whitespace words (protocol units, not model tokens)",
        "retrieval": "production MCP/assembly/Aetheryte/SQLite; sparse-only; null vector and graph",
        "irrelevant_token_match": "exact per task/seed against always_on_k10",
        "bootstrap_unit": "task (paired seed means)",
        "bootstrap_resamples": resamples,
        "bootstrap_seed": rng_seed,
        "seeds": list(seeds),
        "arms": list(arms),
        "fixture_sha256": hashlib.sha256(
            b"".join(
                path.relative_to(ROOT).as_posix().encode() + path.read_bytes()
                for path in sorted(ROOT.rglob("*"))
                if path.is_file()
                and ("fixtures" in path.parts or path.name == "tasks.json")
                and "__pycache__" not in path.parts
            )
        ).hexdigest(),
        "runs": rows,
        **summarize(rows, tasks, resamples, rng_seed),
    }
    return report


def main() -> None:
    """Run fixtures by default; live execution is separately gated by environment."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("evals/coding_ab/results/latest"))
    parser.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2, 3])
    parser.add_argument("--arms", nargs="+", choices=ARMS, default=ARMS)
    parser.add_argument("--resamples", type=int, default=2000)
    parser.add_argument("--bootstrap-seed", type=int, default=74)
    parser.add_argument(
        "--live-command-json", help="JSON argv array; request/response paths appended"
    )
    parser.add_argument("--check-determinism", action="store_true")
    parser.add_argument("--expected-report", type=Path, help="Require equality with reviewed JSON")
    args = parser.parse_args()
    import logging

    import structlog

    structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING))
    command = json.loads(args.live_command_json) if args.live_command_json else None
    if command is not None and (
        not isinstance(command, list)
        or not command
        or not all(isinstance(x, str) and x for x in command)
    ):
        parser.error("live command must be a nonempty JSON argv array")
    if command is not None and args.check_determinism:
        parser.error("determinism check is fixture-only")
    kwargs = dict(
        seeds=tuple(args.seeds),
        arms=tuple(args.arms),
        resamples=args.resamples,
        rng_seed=args.bootstrap_seed,
        live_command=command,
    )
    report = asyncio.run(experiment(**kwargs))
    if args.check_determinism:
        repeated = asyncio.run(experiment(**kwargs))
        if report != repeated:
            raise AssertionError("fixture reports differ")
    if args.expected_report is not None and report != json.loads(args.expected_report.read_text()):
        raise AssertionError("fixture artifact changed; regenerate and review")
    digest = emit(report, args.out)
    print("Coding A/B report SHA-256: " + digest)


if __name__ == "__main__":
    main()
