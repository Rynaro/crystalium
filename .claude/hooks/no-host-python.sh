#!/usr/bin/env bash
# CRYSTALIUM container-first guard — PreToolUse(Bash) hook.
#
# Blocks HOST invocations of python/python3/pip/pip3/pytest/uv. The project
# toolchain lives entirely inside the `crystalium` docker compose service
# (CLAUDE.md §Container-first), so these must be wrapped:
#     docker compose run --rm crystalium pytest ...
#     make test
#
# Mechanism: Claude Code passes the tool call as JSON on stdin
# ({tool_name, tool_input:{command,...}, ...}). We extract tool_input.command,
# split it into shell segments, and inspect each segment's FIRST token. A
# wrapped call has `docker`/`make` as the segment's first token (the banned tool
# is only an argument), so segment-first-token detection lets wrapped calls
# through while catching raw `pytest`, `python -m ...`, `cd x && pytest`, etc.
#
# Exit codes (Claude Code PreToolUse contract):
#   0  allow the tool call
#   2  block the tool call; stderr is returned to the model
#
# Dev-harness only — NOT shipped to the install target (EIIS FORBIDDEN_DIRS).
set -u

input="$(cat)"

# --- extract tool_input.command -------------------------------------------
cmd=""
if command -v jq >/dev/null 2>&1; then
  cmd="$(printf '%s' "$input" | jq -r '.tool_input.command // empty' 2>/dev/null)"
elif command -v python3 >/dev/null 2>&1; then
  cmd="$(printf '%s' "$input" | python3 -c \
    'import sys,json; d=json.load(sys.stdin); print(d.get("tool_input",{}).get("command",""))' \
    2>/dev/null)"
fi

# Nothing to inspect → allow (fail-open: never block on a parse miss).
[ -z "$cmd" ] && exit 0

# --- split into shell segments on ; && || | & and newlines -----------------
segments="$(printf '%s' "$cmd" | sed -E 's/&&/\n/g; s/\|\|/\n/g; s/;/\n/g; s/\|/\n/g; s/&/\n/g')"

banned='^(python3?|pip3?|pytest|uv)$'
blocked=""

while IFS= read -r seg; do
  # strip leading whitespace
  seg="${seg#"${seg%%[![:space:]]*}"}"
  [ -z "$seg" ] && continue

  # peel leading `sudo`, `env`, and `VAR=value` assignment prefixes
  while :; do
    first="${seg%%[[:space:]]*}"
    case "$first" in
      sudo|env)
        seg="${seg#"$first"}"; seg="${seg#"${seg%%[![:space:]]*}"}" ;;
      [A-Za-z_]*=*)
        seg="${seg#"$first"}"; seg="${seg#"${seg%%[![:space:]]*}"}" ;;
      *) break ;;
    esac
  done

  first="${seg%%[[:space:]]*}"
  if printf '%s' "$first" | grep -Eq "$banned"; then
    blocked="$first"
    break
  fi
done <<EOF
$segments
EOF

if [ -n "$blocked" ]; then
  cat >&2 <<MSG
BLOCKED by container-first guard: host '$blocked' is forbidden.
This project's toolchain runs inside Docker. Use one of:
  docker compose run --rm crystalium $blocked ...
  make <target>   (e.g. make test, make lint, make bench)
See CLAUDE.md §Container-first.
MSG
  exit 2
fi

exit 0
