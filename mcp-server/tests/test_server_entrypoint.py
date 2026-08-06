"""Server entrypoint smoke test (crystalium#57 / W-ENTRY).

Every other server test in this suite drives crystalium.server's request
handlers IN-PROCESS: `_drive_call_tool` in test_server.py fabricates a
`ServerRequestContext` and calls the registered handler directly, and
test_http_smoke_initialize (test_server.py:159-178) drives `build_http_app`'s
Starlette ASGI app in-process via `starlette.testclient.TestClient`. Neither
of those can ever observe a server that dies before its own transport
plumbing is reached: an import-time exception, a crash inside `serve` before
`stdio_server()` is entered, or an argument-parsing regression in the click
entrypoint (`__main__.py`) all pass every in-process test unchanged, because
none of them fork a real OS process and exercise the actual `python -m
crystalium serve` boundary. That is exactly the gap crystalium#57 names: at
v1.12.0 both suite targets passed 972/976 green on a build that crashed
instantly on `serve` — nothing in the suite could have caught it.

This module closes that gap with ONE test that launches `python -m crystalium
serve` as a real subprocess and speaks raw JSON-RPC over its stdio transport,
porting the mechanism proven by the WU-1a golden-wire capture script
(`.spectra/changes/archive/2026-08-05-crystalium-mcp-sdk-2x-39/golden_wire.py`
— the only oracle that has ever caught a server that cannot start), with two
deliberate deviations from that template:

  1. stderr is captured (`subprocess.PIPE`, never `subprocess.DEVNULL`) and
     folded into every assertion message, so a startup traceback is visible
     in a failure instead of being silently swallowed (NC-4).
  2. Tool names asserted here are the CURRENT single-segment names shipped in
     v2.0.0 (crystalium#35/#33) — e.g. "recall" — not the old dotted
     `crystalium.recall`-style names the archived template used. Names are
     read from the live `tools/list` response, never hardcoded from memory.

What this module does NOT cover:

  - `run_http`'s uvicorn startup + real socket/port binding. That would
    require driving a live TCP listener from a subprocess (ephemeral port
    selection, startup-log polling, teardown) — this was judged not
    achievable deterministically in-container within this unit's timebox, so
    it is recorded here as an explicit gap rather than shipped as a test that
    would occasionally flake in CI. `build_http_app`'s ASGI construction and
    the Streamable-HTTP request/response path ARE already covered, in-process
    (no socket, no uvicorn), by test_server.py:159-178
    (`test_http_smoke_initialize`, via `starlette.testclient.TestClient`) —
    that test just never launches a real `uvicorn.Server` on a real port, so
    it cannot catch a `run_http`-specific startup failure (bad host/port
    config, uvicorn/starlette version-skew at the ASGI-server boundary, a
    lifespan crash inside `uvicorn.Server.serve`, etc.).
  - `serverInfo.version` is deliberately never asserted (NC-6): `__version__`
    is resolved from the installed package METADATA (see
    `crystalium.__init__`), not the bind-mounted source tree, so a dev
    capture reports the image's baked version rather than the worktree's —
    asserting on it here would be either meaningless or flaky depending on
    how the container was built.

Container-first:
  docker compose run --rm crystalium pytest mcp-server/tests/test_server_entrypoint.py -v
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

import pytest

# Deliberately NOT marked slow (AC-304): the stdio handshake needs no model —
# CRYSTALIUM_SKIP_SLOW=1 is set for the child process below and nothing on the
# initialize/tools-list path touches the embedder — so this must run under
# `make test-ci` (CRYSTALIUM_SKIP_SLOW=1 pytest -v, slow tests still SELECTED)
# where it actually protects CI, not just the slower `make test` lane.

_STARTUP_TIMEOUT_S = 30.0
_READ_TIMEOUT_S = 20.0
_EXIT_TIMEOUT_S = 15.0


def _pump_lines(stream, sink: "queue.Queue[Optional[str]]") -> None:
    """Background-thread target: forward lines from *stream* to *sink* until EOF.

    Decouples reading the child's stdout/stderr from writing its stdin, so a
    `queue.Queue.get(timeout=...)` on the sink can bound how long we wait for
    a response instead of blocking forever on a stalled/hung child process.
    """
    try:
        for line in iter(stream.readline, ""):
            sink.put(line)
    finally:
        sink.put(None)  # EOF sentinel
        try:
            stream.close()
        except Exception:
            pass


class _ServerProcess:
    """Drives `python -m crystalium serve` as a real subprocess over raw stdio
    JSON-RPC. Ports the golden-wire mechanism (see module docstring); the two
    changes from that template are stderr=PIPE (never DEVNULL, NC-4) and no
    hardcoded dotted tool names.
    """

    def __init__(self, data_dir: Path) -> None:
        env = dict(os.environ)
        env["CRYSTALIUM_DATA_DIR"] = str(data_dir)
        # Isolated per-test data_dir (never the shared docker-compose named
        # volume default) so this smoke test cannot race other tests/processes
        # touching the same SQLite/Lance/Kuzu paths.
        env.setdefault("CRYSTALIUM_SKIP_SLOW", "1")

        self.proc = subprocess.Popen(
            [sys.executable, "-m", "crystalium", "serve"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,  # NC-4: never DEVNULL.
            text=True,
            bufsize=1,
            env=env,
        )
        self._out_q: "queue.Queue[Optional[str]]" = queue.Queue()
        self._err_q: "queue.Queue[Optional[str]]" = queue.Queue()
        self._err_lines: list[str] = []
        assert self.proc.stdout is not None and self.proc.stderr is not None
        self._out_thread = threading.Thread(
            target=_pump_lines, args=(self.proc.stdout, self._out_q), daemon=True
        )
        self._err_thread = threading.Thread(
            target=_pump_lines, args=(self.proc.stderr, self._err_q), daemon=True
        )
        self._out_thread.start()
        self._err_thread.start()

    def stderr_text(self) -> str:
        """Drain whatever stderr has arrived so far (non-blocking) and return it
        in full, so every assertion message can show the real traceback instead
        of swallowing it (NC-4)."""
        while True:
            try:
                line = self._err_q.get_nowait()
            except queue.Empty:
                break
            if line is None:
                break
            self._err_lines.append(line)
        return "".join(self._err_lines)

    def rpc(self, obj: dict) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(obj) + "\n")
        self.proc.stdin.flush()

    def read_response(self, want_id: int, timeout_s: float) -> Optional[dict]:
        """Read lines until the response with id == want_id arrives (skipping
        notifications/log lines), bounded by *timeout_s* total."""
        deadline = time.monotonic() + timeout_s
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                line = self._out_q.get(timeout=remaining)
            except queue.Empty:
                return None
            if line is None:  # EOF — the process closed stdout.
                return None
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                msg = json.loads(line)
            except Exception:
                continue
            if msg.get("id") == want_id:
                return msg

    def close(self, timeout_s: float = _EXIT_TIMEOUT_S) -> int:
        """Close stdin (EOF signal) and wait for the process to exit cleanly.

        Returns the process return code (kills and returns -1-ish sentinel on
        timeout, but never raises)."""
        try:
            if self.proc.stdin is not None:
                self.proc.stdin.close()
        except Exception:
            pass
        try:
            return self.proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            try:
                self.proc.wait(timeout=5)
            except Exception:
                pass
            return self.proc.returncode if self.proc.returncode is not None else -1


@pytest.fixture
def server_data_dir(tmp_path: Path) -> Path:
    d = tmp_path / "entrypoint_smoke_data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_serve_stdio_handshake(server_data_dir: Path) -> None:
    """Launch `python -m crystalium serve` as a real OS subprocess and drive
    the full stdio JSON-RPC handshake: initialize -> notifications/initialized
    -> tools/list. Assert a non-empty tool list, then close stdin and assert a
    clean exit within a timeout.

    AC-301..AC-304 (crystalium#57 / W-ENTRY). This is the ONLY test in the
    suite that would have caught the v1.12.0 regression (972/976 green on a
    build that crashed instantly on `serve` — see module docstring).
    """
    srv = _ServerProcess(server_data_dir)
    exit_code: Optional[int] = None
    try:
        srv.rpc(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "entrypoint-smoke", "version": "1"},
                },
            }
        )
        init_resp = srv.read_response(1, timeout_s=_STARTUP_TIMEOUT_S)
        assert init_resp is not None, (
            f"no response to initialize within {_STARTUP_TIMEOUT_S}s "
            f"(a dead/crashed entrypoint looks exactly like this) -- "
            f"server stderr:\n{srv.stderr_text()}"
        )
        assert "result" in init_resp and "error" not in init_resp, (
            f"initialize did not succeed: {init_resp} -- "
            f"server stderr:\n{srv.stderr_text()}"
        )

        srv.rpc({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

        srv.rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        tools_resp = srv.read_response(2, timeout_s=_READ_TIMEOUT_S)
        assert tools_resp is not None, (
            f"no response to tools/list within {_READ_TIMEOUT_S}s -- "
            f"server stderr:\n{srv.stderr_text()}"
        )
        assert "result" in tools_resp and "error" not in tools_resp, (
            f"tools/list did not succeed: {tools_resp} -- "
            f"server stderr:\n{srv.stderr_text()}"
        )

        tools = tools_resp["result"]["tools"]
        assert isinstance(tools, list) and len(tools) > 0, (
            f"tools/list returned an empty/malformed tool list: {tools_resp} -- "
            f"server stderr:\n{srv.stderr_text()}"
        )

        # Proves the handshake response was genuinely PARSED into structured
        # tool descriptors (name-bearing dicts), not merely that the process
        # didn't crash. "recall" is read as a real, currently-shipped
        # single-segment tool name (v2.0.0 / crystalium#35) — never a guessed
        # or hardcoded-from-memory dotted legacy name.
        names = {t["name"] for t in tools}
        assert "recall" in names, (
            f"expected tool 'recall' among tools/list names, got {sorted(names)} -- "
            f"server stderr:\n{srv.stderr_text()}"
        )
    finally:
        exit_code = srv.close(timeout_s=_EXIT_TIMEOUT_S)

    assert exit_code == 0, (
        f"server did not exit cleanly after stdin close (exit={exit_code}) -- "
        f"server stderr:\n{srv.stderr_text()}"
    )
