# CRYSTALIUM — OpenCode host wiring

## Overview

OpenCode wires MCP servers via its configuration. CRYSTALIUM runs as a Docker
stdio server.

## Prerequisites

- Docker installed and running.
- `crystalium` repo accessible.

## OpenCode config snippet

In your OpenCode config (typically `~/.config/opencode/config.json` or the
project-local equivalent):

```json
{
  "mcp": {
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

Add to `.opencode/agents/crystalium.md`:

```markdown
---
name: crystalium
description: Portable memory harness.
---

You are using CRYSTALIUM. Read at session start:

1. `./.eidolons/crystalium/agent.md` — always-loaded P0 rules.
2. `./.eidolons/crystalium/SPEC.md` — deep on-demand methodology spec.

Skills: `./.eidolons/crystalium/skills/<skill>.md` (load on demand).
```

## Quick start

```bash
bash install.sh
docker compose run --rm -i crystalium python -m crystalium
```
