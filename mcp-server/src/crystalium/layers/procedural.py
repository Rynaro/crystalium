"""Procedural layer adapter — verifier-sandboxed skill admission.

T2 callers land as 'candidate'; T0/T1 with passing verifier-result land as 'shared'.
skill_invoke runs a subprocess inside the container (D5 sandbox contract).

All writes funnel through enforcement.assert_tier_allowed() before any store call.
record() called in finally blocks.

Source: spec.md §4 (Procedural row), FORGE D1, D5; gates G2, G3.
"""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import structlog

from crystalium.enforcement import Enforcement, VerifierFailed
from crystalium.gate import PromotionGate, SkillResult
from crystalium.aetheryte.redact import Redactor
from crystalium.schemas import Provenance
from crystalium.telemetry import now_ms
from crystalium.trust import Tier

log = structlog.get_logger("crystalium.layers.procedural")

CommitResult = dict[str, Any]


class ProceduralLayer:
    """Procedural memory layer — verifier-sandboxed skill admission.

    Args:
        blob_store:    BlobStore for payload.
        relational:    RelationalStore for index.
        enforcement:   Enforcement instance (tier checks, sandbox guard, telemetry).
        gate:          PromotionGate for verifier-pass admission.
        redactor:      Redactor for handoff re-redaction.
        importance_fn: Callable from importance.py.
        data_dir:      Root data directory for sandbox creation.
    """

    def __init__(
        self,
        blob_store: Any,
        relational: Any,
        enforcement: Enforcement,
        gate: PromotionGate,
        redactor: Redactor,
        importance_fn: Callable[..., float],
        data_dir: Path | None = None,
    ) -> None:
        self.blob_store = blob_store
        self.relational = relational
        self.enforcement = enforcement
        self.gate = gate
        self.redactor = redactor
        self.importance_fn = importance_fn
        self.data_dir = data_dir or Path("/tmp/crystalium")

    # ------------------------------------------------------------------
    # commit (G2: T2 → candidate; T0/T1 + verifier → shared)
    # ------------------------------------------------------------------

    def commit(
        self,
        payload: dict[str, Any],
        provenance: Provenance | dict[str, Any],
        caller_tier: Tier,
        skill_invoke_result: SkillResult | None = None,
    ) -> CommitResult:
        """Commit to the Procedural layer.

        Enforcement order:
          1. assert_rate_limit
          2. assert_tier_allowed(layer='procedural', op='commit')  [G2: T3 denied]
          3. gate.propose_procedural(skill_invoke_result, caller_tier)
             → admission state: 'candidate' | 'shared' (via pending if human-confirm)
          4. put blob + insert relational
          5. record (finally)

        T2: always lands as 'candidate' (G2) regardless of skill_invoke_result.
        T0/T1 + passing verifier: lands as 'shared' (per D1 matrix + D5 gate).
        T0/T1 without verifier: lands as 'candidate'.

        Returns:
            CommitResult dict.
        """
        t0 = now_ms()
        outcome = "ok"
        error_code: str | None = None

        try:
            # 1. Rate limit
            self.enforcement.assert_rate_limit()

            # 2. Tier check (G2: T3 denied entirely)
            self.enforcement.assert_tier_allowed(
                "crystalium.commit", "procedural", caller_tier, "commit"
            )

            prov_dict = (
                provenance.model_dump()
                if hasattr(provenance, "model_dump")
                else dict(provenance)
            )

            crystal_id = str(uuid.uuid4())
            stub_crystal = {"id": crystal_id, **payload}

            # 3. Gate: T2 always candidate; T0/T1 may reach 'shared' via verifier
            if caller_tier <= Tier.T1:
                # T0 or T1: gate evaluates verifier result
                gate_result = self.gate.propose_procedural(
                    crystal=stub_crystal,
                    skill_invoke_result=skill_invoke_result,
                    caller_tier=caller_tier,
                )
                if gate_result.decision == "admit":
                    validation_state = "validated"
                    status = "active"
                else:
                    # pending → stays candidate until human review
                    validation_state = "candidate"
                    status = "candidate"
            else:
                # T2: always candidate (G2 invariant)
                validation_state = "candidate"
                status = "candidate"
                gate_result = None

            now = datetime.now(timezone.utc)
            payload_bytes = json.dumps(payload, default=str).encode()
            content_ref = self.blob_store.put(payload_bytes)

            scope = payload.get("scope", {})
            summary = payload.get("summary", "")

            utility = {
                "access_count": 0,
                "last_access": now.isoformat(),
                "outcome_success_score": None,
                "importance": 0.0,
                "novelty_at_write": payload.get("novelty_at_write", 0.5),
            }

            crystal_record: dict[str, Any] = {
                "id": crystal_id,
                "layer": "procedural",
                "content_ref": content_ref,
                "summary": summary or str(payload)[:256],
                "embedding_ref": None,
                "provenance": prov_dict,
                "trust_tier": str(caller_tier),
                "validation_state": validation_state,
                "scope": scope,
                "temporal": {
                    "t_valid_from": now.isoformat(),
                    "t_valid_to": None,
                    "superseded_by": None,
                },
                "utility": utility,
                "status": status,
            }

            self.relational.insert_crystal(crystal_record)

            log.info(
                "procedural_commit",
                crystal_id=crystal_id,
                validation_state=validation_state,
                caller_tier=str(caller_tier),
            )

            result: CommitResult = {
                "status": "committed",
                "id": crystal_id,
                "layer": "procedural",
                "validation_state": validation_state,
                "importance": 0.0,
                "content_ref": content_ref,
            }

            if gate_result and gate_result.promotion_id:
                result["promotion_id"] = gate_result.promotion_id

            return result

        except Exception as exc:
            outcome = "error" if not hasattr(exc, "reason_code") else "rejected"
            error_code = getattr(exc, "reason_code", type(exc).__name__)
            raise

        finally:
            self.enforcement.record(
                "crystalium.commit",
                "procedural",
                caller_tier,
                "commit",
                outcome,
                now_ms() - t0,
                error=error_code,
            )

    # ------------------------------------------------------------------
    # invoke — D5 sandbox (G3)
    # ------------------------------------------------------------------

    def invoke(
        self,
        skill_id: str,
        args: dict[str, Any],
        caller_tier: Tier,
        timeout_s: int | None = None,
        output_cap_bytes: int | None = None,
        workdir: Path | None = None,
    ) -> SkillResult:
        """Run a procedural verifier in a sandboxed subprocess (D5 / G3).

        Enforcement order (spec.md §5.4):
          1. assert_rate_limit
          2. assert_no_path_escape(workdir, /sandbox/<skill_id>)
          3. warn_skill_invoke (operator warning — D5)
          4. subprocess.run(timeout=timeout_s)
          5. cap_output(output_cap_bytes)
          6. record (finally)

        Args:
            skill_id:          Skill identifier (used in sandbox path).
            args:              Args passed to the verifier via env/stdin.
            caller_tier:       Caller's trust tier (for telemetry).
            timeout_s:         Override timeout (config default: 30s).
            output_cap_bytes:  Override output cap (config default: 8192).
            workdir:           Working directory (MUST be under /sandbox/<skill_id>).

        Returns:
            SkillResult with exit_code, stdout, stderr, overflow_flag.
        """
        t0 = now_ms()
        outcome = "ok"
        error_code: str | None = None

        cfg = self.enforcement.config
        _timeout = timeout_s if timeout_s is not None else cfg.skill_invoke_timeout_s
        _cap = output_cap_bytes if output_cap_bytes is not None else cfg.skill_invoke_output_cap_bytes

        try:
            # 1. Rate limit
            self.enforcement.assert_rate_limit()

            # 2. Path guard: workdir must resolve under <sandbox_root>/<skill_id>
            sandbox_root = self.enforcement.config.sandbox_root / skill_id
            if workdir is not None:
                self.enforcement.assert_no_path_escape(workdir, sandbox_root)
            else:
                # Create default sandbox dir under data_dir
                workdir = self.data_dir / "sandboxes" / skill_id
                workdir.mkdir(parents=True, exist_ok=True)

            # 3. Operator warning (D5)
            self.enforcement.warn_skill_invoke(skill_id)

            # Build command: look for verifier script in blob store path or direct
            # For v0.1, skill_id maps to a verifier command via args["command"]
            command = args.get("command", skill_id)
            env = {**os.environ, **{k: str(v) for k, v in args.items() if k != "command"}}

            # 4. subprocess.run with sandbox constraints
            try:
                proc = subprocess.run(
                    command if isinstance(command, list) else [command],
                    capture_output=True,
                    timeout=_timeout,
                    cwd=str(workdir),
                    env=env,
                )
                stdout = proc.stdout
                stderr = proc.stderr
                exit_code = proc.returncode
            except subprocess.TimeoutExpired:
                return SkillResult(
                    exit_code=-1,
                    stdout=b"",
                    stderr=b"timeout",
                    overflow_flag=False,
                )
            except FileNotFoundError:
                return SkillResult(
                    exit_code=127,
                    stdout=b"",
                    stderr=f"command not found: {command!r}".encode(),
                    overflow_flag=False,
                )

            # 5. Cap output
            stdout_capped, stdout_overflow = self.enforcement.cap_output(stdout, _cap)
            stderr_capped, stderr_overflow = self.enforcement.cap_output(stderr, _cap)
            overflow_flag = stdout_overflow or stderr_overflow

            result = SkillResult(
                exit_code=exit_code,
                stdout=stdout_capped,
                stderr=stderr_capped,
                overflow_flag=overflow_flag,
            )

            if not result.passed:
                outcome = "rejected"
                error_code = "VERIFIER_FAIL"

            log.info(
                "skill_invoke",
                skill_id=skill_id,
                exit_code=exit_code,
                overflow=overflow_flag,
                caller_tier=str(caller_tier),
            )

            return result

        except Exception as exc:
            outcome = "error" if not hasattr(exc, "reason_code") else "rejected"
            error_code = getattr(exc, "reason_code", type(exc).__name__)
            raise

        finally:
            self.enforcement.record(
                "crystalium.skill_invoke",
                "procedural",
                caller_tier,
                "skill_invoke",
                outcome,
                now_ms() - t0,
                error=error_code,
            )
