# CRYSTALIUM — Claude Code host wiring

## Overview

Claude Code wires CRYSTALIUM as a persistent memory substrate via the MCP
protocol. The server runs over stdio inside Docker; Claude Code spawns it
automatically when the `.mcp.json` entry is present.

## Prerequisites

- Docker + docker compose installed and running.
- The `crystalium` repo checked out at a path accessible to Docker.

## `.mcp.json` snippet

Place this at the project root `.mcp.json` (or merge into existing file):

```json
{
  "mcpServers": {
    "crystalium": {
      "command": "docker",
      "args": [
        "compose", "-f", "/path/to/crystalium/docker-compose.yml",
        "run", "--rm", "-i", "crystalium",
        "python", "-m", "crystalium", "serve"
      ],
      "env": {
        "CRYSTALIUM_PROJECT": "${workspaceFolder:basename}"
      }
    }
  }
}
```

Replace `/path/to/crystalium` with the absolute path to this repo.

## Install target wiring (EIIS v1.4 §4.2.3)

After running `bash install.sh`, the install target contains:
- `.eidolons/crystalium/agent.md` — always-loaded P0 rules.
- `.eidolons/crystalium/SPEC.md` — deep on-demand methodology spec.
- `.eidolons/crystalium/skills/*.md` — on-demand skill cards.

Add this to your project's `.claude/agents/crystalium.md`:

```markdown
---
name: crystalium
description: Portable memory harness — recall, commit, dream consolidation.
model: sonnet
---

You are CRYSTALIUM. Read these files at session start:

1. `./.eidolons/crystalium/agent.md` — always-loaded P0 rules.
2. `./.eidolons/crystalium/SPEC.md` — deep on-demand methodology spec.

Skills live at `./.eidolons/crystalium/skills/<skill>.md` (load on demand).
```

## Quick start

```bash
bash install.sh                      # stage to .eidolons/crystalium/
docker compose up -d crystalium      # start the MCP server
# or: docker compose run --rm -i crystalium python -m crystalium serve
```
