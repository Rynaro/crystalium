#!/usr/bin/env bash
# Declarative EIIS v3 package installer. Host adapters are nexus-owned.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PACKAGE_MANIFEST="$SCRIPT_DIR/manifest.json"
TARGET=""
HOSTS="raw"
FORCE=false
DRY_RUN=false
NON_INTERACTIVE=false
MANIFEST_ONLY=false

usage() {
  cat <<'EOF'
Usage: bash install.sh [OPTIONS]
  --target DIR
  --hosts LIST              Accepted for orchestration compatibility; adapters are nexus-owned.
  --force
  --non-interactive
  --dry-run
  --manifest-only
  --shared-dispatch         Accepted no-op; root routing is nexus-owned.
  --no-shared-dispatch      Accepted no-op; root routing is nexus-owned.
  --version
  -h, --help
EOF
}

pkg_name() { jq -r '.name' "$PACKAGE_MANIFEST"; }
pkg_version() { jq -r '.version' "$PACKAGE_MANIFEST"; }
sha256_file() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}'
  else shasum -a 256 "$1" | awk '{print $1}'; fi
}
tree_sha256() {
  local root="$1" listing
  listing="$(mktemp)"
  find "$root" -type f ! -name install.receipt.json -print | LC_ALL=C sort | while IFS= read -r file; do
    printf '%s  %s\n' "$(sha256_file "$file")" "${file#"$root/"}"
  done > "$listing"
  sha256_file "$listing"
  rm -f "$listing"
}

while [ $# -gt 0 ]; do
  case "$1" in
    --target) TARGET="$2"; shift 2 ;;
    --hosts) HOSTS="$2"; shift 2 ;;
    --force) FORCE=true; shift ;;
    --non-interactive) NON_INTERACTIVE=true; shift ;;
    --dry-run) DRY_RUN=true; shift ;;
    --manifest-only) MANIFEST_ONLY=true; shift ;;
    --shared-dispatch|--no-shared-dispatch) shift ;;
    --version) pkg_version; exit 0 ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; exit 2 ;;
  esac
done

jq empty "$PACKAGE_MANIFEST" >/dev/null
NAME="$(pkg_name)"
VERSION="$(pkg_version)"
[ -n "$TARGET" ] || TARGET="./.eidolons/$NAME"
MANIFEST_SHA="$(sha256_file "$PACKAGE_MANIFEST")"
PREVIOUS_INSTALLED_AT=""
if [ -f "$TARGET/install.receipt.json" ]; then
  PREVIOUS_INSTALLED_AT="$(jq -r --arg name "$NAME" --arg version "$VERSION" --arg digest "$MANIFEST_SHA" '
    if .package.name == $name and .package.version == $version and
       .package.manifest_sha256 == $digest then .installed_at else empty end
  ' "$TARGET/install.receipt.json" 2>/dev/null || true)"
fi

if [ "$DRY_RUN" = true ]; then
  printf 'install %s@%s -> %s (package only; hosts=%s)\n' "$NAME" "$VERSION" "$TARGET" "$HOSTS"
  exit 0
fi

if [ -e "$TARGET" ] && [ "$FORCE" != true ]; then
  if [ "$NON_INTERACTIVE" = true ]; then
    printf 'Already installed at %s; pass --force.\n' "$TARGET" >&2
    exit 3
  fi
  printf 'Already installed at %s; pass --force.\n' "$TARGET" >&2
  exit 3
fi

mkdir -p "$TARGET"
if [ "$MANIFEST_ONLY" != true ]; then
  cp "$SCRIPT_DIR/PERSONA.md" "$TARGET/PERSONA.md"
  cp "$SCRIPT_DIR/SPEC.md" "$TARGET/SPEC.md"
  cp "$SCRIPT_DIR/EIIS_VERSION" "$TARGET/EIIS_VERSION"
  cp "$PACKAGE_MANIFEST" "$TARGET/manifest.json"
  for dir in skills hooks shared; do
    if [ -d "$SCRIPT_DIR/$dir" ]; then
      rm -rf "$TARGET/$dir"
      cp -R "$SCRIPT_DIR/$dir" "$TARGET/$dir"
    fi
  done
  while IFS= read -r resource; do
    [ -n "$resource" ] || continue
    case "/$resource/" in */../*|*/./*) printf 'Unsafe package resource: %s\n' "$resource" >&2; exit 2 ;; esac
    [ -e "$SCRIPT_DIR/$resource" ] || { printf 'Missing package resource: %s\n' "$resource" >&2; exit 2; }
    mkdir -p "$(dirname "$TARGET/$resource")"
    rm -rf "$TARGET/$resource"
    cp -R "$SCRIPT_DIR/$resource" "$TARGET/$resource"
  done < <(jq -r '.resources[]' "$PACKAGE_MANIFEST")
fi

TREE_SHA="$(tree_sha256 "$TARGET")"
INSTALLED_AT="${PREVIOUS_INSTALLED_AT:-$(date -u +'%Y-%m-%dT%H:%M:%SZ')}"
cat > "$TARGET/install.receipt.json" <<EOF
{
  "schema_version": "1.0",
  "eiis_version": "3.0.0",
  "package": {"name":"$NAME","version":"$VERSION","manifest_sha256":"$MANIFEST_SHA"},
  "installed_at": "$INSTALLED_AT",
  "target": "$TARGET",
  "tree_sha256": "$TREE_SHA",
  "adapters": []
}
EOF
printf '%s@%s installed -> %s\n' "$NAME" "$VERSION" "$TARGET"
