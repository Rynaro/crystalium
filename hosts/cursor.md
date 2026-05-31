# CRYSTALIUM — Cursor host wiring

## Overview

Cursor wires MCP servers via its `mcp.json` configuration file. CRYSTALIUM
runs as a Docker stdio server.

## Prerequisites

- Docker installed and running.
- `crystalium` repo accessible at a fixed path.

## `mcp.json` snippet

In Cursor settings (`~/.cursor/mcp.json` or workspace `.cursor/mcp.json`):

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
        "CRYSTALIUM_PROJECT": "my-project"
      }
    }
  }
}
```

## Install target wiring (EIIS v1.4 §4.2.8)

After running `bash install.sh`:

Add to `.cursor/rules/crystalium.mdc`:

```markdown
---
alwaysApply: false
---

You are using CRYSTALIUM memory harness. Reference files:

- `./.eidolons/crystalium/agent.md` — P0 always-loaded rules.
- `./.eidolons/crystalium/SPEC.md` — deep on-demand methodology spec.
- `./.eidolons/crystalium/skills/<skill>.md` — on-demand skill cards.
```

## Quick start

```bash
bash install.sh                      # stage to .eidolons/crystalium/
docker compose run --rm -i crystalium python -m crystalium serve
```
