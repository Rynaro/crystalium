"""Small content-driven fixture solver; no arm names, gold patches or test access."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


def solve(request: dict) -> dict:
    """Choose a source edit from memory, otherwise explore the public docstring.

    Exploration is deliberately planted to exercise three baseline strata. This
    is an instrument calibration solver, not an estimate of model competence.
    Memory suggestions take precedence, including harmful unrelated advice.
    """
    source = request["source"]
    edits = re.findall(r"PATCH\[([^\]\n]+) => ([^\]\n]+)\]", request["memory"])
    replacement = next((new for old, new in edits if old in source), None)
    if replacement is None and request["seed"] % 4 < request["exploration"]:
        hints = {
            "preceding integer": "return x - 1",
            "twice the integer": "return x * 2",
            "absolute integer": "return abs(x)",
        }
        replacement = next((edit for hint, edit in hints.items() if hint in source), None)
    updated = source.replace("return x + 1", replacement, 1) if replacement else source
    return {"source": updated, "steps": int(updated != source), "total_tokens": None}


if __name__ == "__main__":
    task_request = json.loads(Path(sys.argv[1]).read_text())
    Path(sys.argv[2]).write_text(json.dumps(solve(task_request)))
