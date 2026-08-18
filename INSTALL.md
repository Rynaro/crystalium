# Installing CRYSTALIUM

## Through the Eidolons nexus

```bash
eidolons add crystalium
eidolons sync
eidolons harness check
```

The nexus installs the canonical package below `.eidolons/crystalium/`
and owns all Claude Code, Codex, Copilot, Cursor, and OpenCode discovery
adapters. This repository does not ship vendor-specific copies.

## Standalone package install

```bash
bash install.sh \
  --target ./.eidolons/crystalium \
  --hosts raw \
  --non-interactive \
  --force
```

The installed tree contains `PERSONA.md`, `SPEC.md`, `manifest.json`, every
declared skill and resource, plus the generated `install.receipt.json`.
Repeated installation of the same package is byte-identical.

## Verify the source package

```bash
git clone https://github.com/Rynaro/eidolons-eiis /tmp/eidolons-eiis
bash /tmp/eidolons-eiis/conformance/check.sh .
```
