# CRYSTALIUM — GitHub Copilot host wiring

## Overview

GitHub Copilot (VS Code extension) can wire MCP servers for tool use. CRYSTALIUM
runs as a Docker stdio server.

## Prerequisites

- Docker installed and running.
- VS Code + GitHub Copilot extension (with MCP support).

## `.vscode/mcp.json` snippet

```json
{
  "servers": {
    "crystalium": {
      "command": "docker",
      "args": [
        "compose", "-f", "/path/to/crystalium/docker-compose.yml",
        "run", "--rm", "-i", "crystalium",
        "python", "-m", "crystalium"
      ],
      "env": {
        "CRYSTALIUM_PROJECT": "my-project"
      }
    }
  }
}
```

## Install target wiring (EIIS v1.4 §4.2.8)

After running `bash install.sh`:

Add to `.github/instructions/crystalium.instructions.md`:

```markdown
---
applyTo: "**"
---

CRYSTALIUM memory harness is available. Reference:

- `./.eidolons/crystalium/agent.md` — always-loaded P0 rules.
- `./.eidolons/crystalium/SPEC.md` — deep on-demand methodology spec.
```

## Quick start

```bash
bash install.sh
docker compose run --rm -i crystalium python -m crystalium
```
