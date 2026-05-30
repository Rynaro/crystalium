"""CRYSTALIUM ablation/canary bench entrypoint.

Container-first:
  docker compose run --rm crystalium python -m evals canary --mode both
  docker compose run --rm crystalium python -m evals ab --flag memory
  docker compose run --rm crystalium python -m evals axes --demo
  make bench

Subcommands:
  canary  — legacy memory-on/off A/B headline over the canary suite (run_all)
  ab      — generalized ablation: ab(flag, missions) -> {axis:(on,off,delta)}
  axes    — SWE-Bench-CL axes from an accuracy matrix (--demo or --matrix)
  forget  — selective-forgetting probe summary (reuses selective_forgetting)
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any


def _cmd_canary(args: argparse.Namespace) -> dict[str, Any]:
    from evals.ab_memory_onoff import run_all

    return run_all(mode=args.mode, write_results=not args.no_write)


def _cmd_ab(args: argparse.Namespace) -> dict[str, Any]:
    from evals.ablation import ab

    missions = [m.strip() for m in args.missions.split(",")] if args.missions else None
    return ab(
        flag=args.flag,
        missions=missions,
        seed_fixture=not args.no_seed,
        write_results=not args.no_write,
    )


_DEMO_MATRIX = [
    [0.90, 0.00, 0.00],
    [0.85, 0.95, 0.00],
    [0.80, 0.90, 0.92],
]


def _cmd_axes(args: argparse.Namespace) -> dict[str, Any]:
    from evals.metrics import swe_bench_cl_axes

    if args.matrix:
        matrix = json.loads(args.matrix)
    elif args.demo:
        matrix = _DEMO_MATRIX
    else:
        # Default to the demo matrix so the subcommand is always runnable; a live
        # sequence runner over the fixture repo is a later-wave concern.
        matrix = _DEMO_MATRIX
    return {
        "matrix": matrix,
        "axes": swe_bench_cl_axes(
            matrix,
            successes=args.successes,
            tool_calls=args.tool_calls,
        ),
    }


def _cmd_evb_gate(args: argparse.Namespace) -> dict[str, Any]:
    from evals.evb_gate import run

    return run()


def _cmd_dream_gate(args: argparse.Namespace) -> dict[str, Any]:
    from evals.dream_gate import run

    return run()


def _cmd_forgetting_gate(args: argparse.Namespace) -> dict[str, Any]:
    from evals.forgetting_gate import run

    return run()


def _cmd_retrieval_gate(args: argparse.Namespace) -> dict[str, Any]:
    from evals.retrieval_gate import run

    return run()


def _cmd_dedup_gate(args: argparse.Namespace) -> dict[str, Any]:
    from evals.dedup_gate import run

    return run()


def _cmd_prefetch_gate(args: argparse.Namespace) -> dict[str, Any]:
    from evals.prefetch_gate import run

    return run()


def _cmd_forget(args: argparse.Namespace) -> dict[str, Any]:
    from evals.ab_memory_onoff import _build_live_handlers
    from evals.selective_forgetting import run_test

    h = _build_live_handlers(config_override={"project": "forget-probe"})

    def commit_fn(*, layer, summary, content, trust_tier, project=None):
        return h["commit"]({
            "layer": layer,
            "payload": {
                "summary": summary,
                "content": content,
                "scope": {"project": "forget-probe"},
                "trust_tier": trust_tier,
            },
            "provenance": {"source": "verified_agent", "author_agent": "forget-probe"},
        })

    def recall_fn(query, project=None, k=5):
        return h["recall"]({"scope": {"project": "forget-probe"}, "query": query, "k": k})

    res = run_test(
        commit_fn=commit_fn,
        update_fn=h["update"],
        recall_fn=recall_fn,
        get_crystal_fn=h["get_crystal"],
    )
    return res.__dict__


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m evals", description="CRYSTALIUM bench")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("canary", help="memory-on/off A/B headline")
    c.add_argument("--mode", default="both", choices=["both", "on_only", "off_only"])
    c.add_argument("--no-write", action="store_true", help="do not persist results JSON")
    c.set_defaults(func=_cmd_canary)

    a = sub.add_parser("ab", help="generalized ablation ab(flag, missions)")
    a.add_argument("--flag", required=True, help="'memory' or a Config bool field")
    a.add_argument("--missions", default="", help="comma-separated CAN-N ids")
    a.add_argument("--no-seed", action="store_true", help="skip fixture seeding")
    a.add_argument("--no-write", action="store_true")
    a.set_defaults(func=_cmd_ab)

    x = sub.add_parser("axes", help="SWE-Bench-CL axes from an accuracy matrix")
    x.add_argument("--matrix", default="", help="JSON NxN accuracy matrix")
    x.add_argument("--demo", action="store_true", help="use the built-in demo matrix")
    x.add_argument("--successes", type=float, default=None)
    x.add_argument("--tool-calls", type=float, default=None)
    x.set_defaults(func=_cmd_axes)

    f = sub.add_parser("forget", help="selective-forgetting probe")
    f.set_defaults(func=_cmd_forget)

    g = sub.add_parser("evb-gate", help="deterministic EVB ablation gate (W2 DoD)")
    g.set_defaults(func=_cmd_evb_gate)

    dg = sub.add_parser("dream-gate", help="deterministic Dream-intelligence ablation gate (W3 DoD)")
    dg.set_defaults(func=_cmd_dream_gate)

    fg = sub.add_parser("forgetting-gate", help="long-session forgetting ablation gate (W4 DoD)")
    fg.set_defaults(func=_cmd_forgetting_gate)

    rg = sub.add_parser("retrieval-gate", help="multi-hop completion + context-match ablation gate (W5 DoD)")
    rg.set_defaults(func=_cmd_retrieval_gate)

    ddg = sub.add_parser("dedup-gate", help="pattern-separation write-amplification ablation gate (W5 DoD)")
    ddg.set_defaults(func=_cmd_dedup_gate)

    pg = sub.add_parser("prefetch-gate", help="predictive-prefetch cache-hit ablation gate (W5 DoD)")
    pg.set_defaults(func=_cmd_prefetch_gate)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    out = args.func(args)
    json.dump(out, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
