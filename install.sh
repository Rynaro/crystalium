#!/usr/bin/env bash
# install.sh — CRYSTALIUM EIIS v1.4-conformant installer
#
# EIIS references:
#   §1.8.1    — SPEC.md at <target>/SPEC.md
#   §1.8.6    — agent.md in files_written[] with role="agent-profile"
#   §1.9.1    — canonical install-target inventory whitelist
#   §3.7.1.1  — ECL_VERSION copied to <target>/ECL_VERSION with role="ecl-version"
#   §6.X      — cleanup sweep: remove non-whitelisted files after staging
#   §6.Y      — agent.md skill references must resolve to files_written[] entries
#
# Usage:
#   bash install.sh [options]
#
# Options:
#   --scope  <name>          Scope name (unused for standalone Eidolon; reserved)
#   --members <list>         Members list (no-op for crystalium; reserved)
#   --profile=local|server   Deployment profile; only 'local' is supported in v0.1
#   --target <path>          Install target directory (default: ./.eidolons/crystalium/)
#   --force                  Re-stage files even when already present
#   --non-interactive        Suppress prompts; proceed with defaults
#
# Idempotency (P0-16, EIIS §5):
#   Re-running with no source changes produces no file changes (no diff).
#   Verified by CI job "idempotency" which diffs the target before and after
#   a second run.
#
# Bash 3.2 compatible (macOS system shell policy — see CLAUDE.md).
# No associative arrays, no ${var,,}, no readarray, no &>>.
#
# Source-of-truth references:
#   EIIS v1.4 Appendix A — canonical_inventory_sweep reference implementation
#   .spectra/crystalium-v0.1.0-spec.yaml eiis_v1_4_conformance block

set -euo pipefail

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

EIIS_VERSION_VALUE="1.4"
ECL_VERSION_VALUE="2.0"
CRYSTALIUM_VERSION="0.1.0"

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

SCOPE=""
MEMBERS=""
PROFILE="local"
TARGET=""
FORCE=0
NON_INTERACTIVE=0

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

while [ $# -gt 0 ]; do
    case "$1" in
        --scope)
            SCOPE="${2:-}"
            shift 2
            ;;
        --members)
            MEMBERS="${2:-}"
            shift 2
            ;;
        --profile=*)
            PROFILE="${1#--profile=}"
            shift
            ;;
        --profile)
            PROFILE="${2:-local}"
            shift 2
            ;;
        --target)
            TARGET="${2:-}"
            shift 2
            ;;
        --force)
            FORCE=1
            shift
            ;;
        --non-interactive)
            NON_INTERACTIVE=1
            shift
            ;;
        *)
            echo "[install.sh] WARNING: unrecognised argument: $1" >&2
            shift
            ;;
    esac
done

# Profile guard — only 'local' is implemented in v0.1 (FORGE D2, out_of_scope_hooks)
if [ "${PROFILE}" = "server" ]; then
    echo "[install.sh] ERROR: --profile=server is not supported in v0.1." >&2
    echo "  Postgres/Qdrant/Neo4j backend is deferred to v0.2." >&2
    echo "  Use --profile=local (the default) for the embedded store." >&2
    exit 1
fi
if [ "${PROFILE}" != "local" ]; then
    echo "[install.sh] ERROR: Unknown profile '${PROFILE}'. Valid: local, server." >&2
    exit 1
fi

# Resolve TARGET
if [ -z "${TARGET}" ]; then
    TARGET="./.eidolons/crystalium"
fi

# Resolve REPO_ROOT — directory containing this script
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# ---------------------------------------------------------------------------
# Helper: say / ok / warn / die
# ---------------------------------------------------------------------------

say()  { echo "[crystalium install] $*" >&2; }
ok()   { echo "[crystalium install] OK: $*" >&2; }
warn() { echo "[crystalium install] WARN: $*" >&2; }
die()  { echo "[crystalium install] ERROR: $*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# Helper: SHA-256 of a file (bash 3.2 compatible; prefers shasum, falls back
# to sha256sum)
# ---------------------------------------------------------------------------

sha256_file() {
    local path="$1"
    if command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "${path}" | awk '{print $1}'
    elif command -v sha256sum >/dev/null 2>&1; then
        sha256sum "${path}" | awk '{print $1}'
    else
        # [UNVERIFIED fallback] python3 always available in the container
        python3 -c "import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest())" "${path}"
    fi
}

# ---------------------------------------------------------------------------
# FILES_WRITTEN_PATHS — indexed array (bash 3.2 compatible)
# Also FILES_WRITTEN_JSON — parallel array of JSON entries for manifest
# ---------------------------------------------------------------------------

FILES_WRITTEN_PATHS=()
FILES_WRITTEN_JSON=()

add_fw() {
    # add_fw <abs_path_on_disk> <target_relative_path> <role>
    local abs_path="$1"
    local rel_path="$2"
    local role="$3"
    local file_sha256=""
    if [ -f "${abs_path}" ]; then
        file_sha256="$(sha256_file "${abs_path}")"
    fi
    FILES_WRITTEN_PATHS+=("${rel_path}")
    local entry
    entry="{\"path\":\"${rel_path}\",\"role\":\"${role}\",\"sha256\":\"${file_sha256}\"}"
    FILES_WRITTEN_JSON+=("${entry}")
}

# ---------------------------------------------------------------------------
# Helper: copy file if source newer than destination (or --force)
# ---------------------------------------------------------------------------

copy_if_changed() {
    # copy_if_changed <src> <dst> <role>
    local src="$1"
    local dst="$2"
    local role="$3"

    [ -f "${src}" ] || die "Source file missing: ${src}"

    local dst_dir
    dst_dir="$(dirname "${dst}")"
    mkdir -p "${dst_dir}"

    local should_copy=0
    if [ "${FORCE}" -eq 1 ]; then
        should_copy=1
    elif [ ! -f "${dst}" ]; then
        should_copy=1
    else
        # Compare SHA-256 to determine if content differs
        local src_sha dst_sha
        src_sha="$(sha256_file "${src}")"
        dst_sha="$(sha256_file "${dst}")"
        if [ "${src_sha}" != "${dst_sha}" ]; then
            should_copy=1
        fi
    fi

    if [ "${should_copy}" -eq 1 ]; then
        cp "${src}" "${dst}"
        say "wrote ${dst}"
    fi

    # Compute target-relative path for manifest
    # Strip TARGET prefix + leading slash to get relative path
    local target_abs
    target_abs="$(cd "${TARGET}" && pwd)"
    local dst_abs
    dst_abs="$(cd "$(dirname "${dst}")" && pwd)/$(basename "${dst}")"
    local rel_path="${dst_abs#${target_abs}/}"

    add_fw "${dst}" "${rel_path}" "${role}"
}

# ---------------------------------------------------------------------------
# Ensure target directory exists
# ---------------------------------------------------------------------------

mkdir -p "${TARGET}"
TARGET_ABS="$(cd "${TARGET}" && pwd)"

say "Installing CRYSTALIUM v${CRYSTALIUM_VERSION} → ${TARGET_ABS}"
say "EIIS_VERSION=${EIIS_VERSION_VALUE}  ECL_VERSION=${ECL_VERSION_VALUE}  profile=${PROFILE}"

# ---------------------------------------------------------------------------
# Write EIIS_VERSION and ECL_VERSION at source repo root (idempotent)
# ---------------------------------------------------------------------------

# These files live at the SOURCE REPO ROOT, not in the install target.
# Write them here so the repo satisfies §1.1 even if they were absent.

EIIS_VERSION_SRC="${SCRIPT_DIR}/EIIS_VERSION"
ECL_VERSION_SRC="${SCRIPT_DIR}/ECL_VERSION"

if [ ! -f "${EIIS_VERSION_SRC}" ] || [ "$(cat "${EIIS_VERSION_SRC}" | tr -d '\n')" != "${EIIS_VERSION_VALUE}" ]; then
    printf '%s\n' "${EIIS_VERSION_VALUE}" > "${EIIS_VERSION_SRC}"
    say "wrote source EIIS_VERSION"
fi
if [ ! -f "${ECL_VERSION_SRC}" ] || [ "$(cat "${ECL_VERSION_SRC}" | tr -d '\n')" != "${ECL_VERSION_VALUE}" ]; then
    printf '%s\n' "${ECL_VERSION_VALUE}" > "${ECL_VERSION_SRC}"
    say "wrote source ECL_VERSION"
fi

# ---------------------------------------------------------------------------
# §1.8.1 + §1.8.6 — Stage agent.md and SPEC.md
# ---------------------------------------------------------------------------

# agent.md → <target>/agent.md (role="agent-profile", §1.8.6)
AGENT_MD_SRC="${SCRIPT_DIR}/agent.md"
AGENT_MD_DST="${TARGET}/agent.md"
[ -f "${AGENT_MD_SRC}" ] || die "agent.md not found at ${AGENT_MD_SRC}"
copy_if_changed "${AGENT_MD_SRC}" "${AGENT_MD_DST}" "agent-profile"

# SPEC.md → <target>/SPEC.md (role="spec", §1.8.1)
# Crystalium's canonical spec is at .spectra/crystalium-v0.1.0-spec.md
# but is also mirrored to SPEC.md at repo root (as per file_layout in spec.yaml).
SPEC_MD_SRC="${SCRIPT_DIR}/SPEC.md"
SPEC_MD_DST="${TARGET}/SPEC.md"
[ -f "${SPEC_MD_SRC}" ] || die "SPEC.md not found at ${SPEC_MD_SRC}"
copy_if_changed "${SPEC_MD_SRC}" "${SPEC_MD_DST}" "spec"

# ---------------------------------------------------------------------------
# §3.7.1 — Stage ECL_VERSION into install target
# ---------------------------------------------------------------------------

ECL_VERSION_DST="${TARGET}/ECL_VERSION"
copy_if_changed "${ECL_VERSION_SRC}" "${ECL_VERSION_DST}" "ecl-version"

# ---------------------------------------------------------------------------
# Skills (if present at source repo skills/) — §1.9.1 "MAY"
# ---------------------------------------------------------------------------

SKILLS_SRC_DIR="${SCRIPT_DIR}/skills"
if [ -d "${SKILLS_SRC_DIR}" ]; then
    for skill_src in "${SKILLS_SRC_DIR}"/*.md; do
        [ -f "${skill_src}" ] || continue
        skill_name="$(basename "${skill_src}")"
        skill_dst="${TARGET}/skills/${skill_name}"
        copy_if_changed "${skill_src}" "${skill_dst}" "skill"
    done
fi

# ---------------------------------------------------------------------------
# Schemas (auxiliary JSON schemas) — §1.9.1 "SHOULD"/"MAY"
# ---------------------------------------------------------------------------

SCHEMAS_SRC_DIR="${SCRIPT_DIR}/schemas"
if [ -d "${SCHEMAS_SRC_DIR}" ]; then
    for schema_src in "${SCHEMAS_SRC_DIR}"/*.json; do
        [ -f "${schema_src}" ] || continue
        schema_name="$(basename "${schema_src}")"
        # install.manifest.v1.json gets role="other" (vendored schema per §1.5)
        schema_role="other"
        schema_dst="${TARGET}/schemas/${schema_name}"
        copy_if_changed "${schema_src}" "${schema_dst}" "${schema_role}"
    done
fi

# ---------------------------------------------------------------------------
# §6.X — canonical_inventory_sweep
# Remove every file under <target>/ that is NOT in FILES_WRITTEN_PATHS.
# Appendix A reference implementation (bash 3.2 compatible).
# ---------------------------------------------------------------------------

canonical_inventory_sweep() {
    local target="$1"
    local file_rel
    local found
    local known

    if [ -z "${target}" ] || [ ! -d "${target}" ]; then
        return 0
    fi

    find "${target}" -type f -print0 | while IFS= read -r -d '' file; do
        # Skip the manifest itself — it is written AFTER the sweep
        case "${file}" in
            */install.manifest.json) continue ;;
        esac

        file_rel="${file#${target}/}"

        found=0
        for known in "${FILES_WRITTEN_PATHS[@]}"; do
            case "${known}" in
                *"/${file_rel}"|"${file_rel}")
                    found=1
                    break
                    ;;
            esac
        done

        if [ "${found}" -eq 0 ]; then
            rm -f "${file}"
            warn "sweep: removed non-whitelisted file: ${file}"
        fi
    done

    # Remove empty directories left after sweep
    find "${target}" -mindepth 1 -type d -empty -delete 2>/dev/null || true

    return 0
}

canonical_inventory_sweep "${TARGET_ABS}"

# ---------------------------------------------------------------------------
# §3 — Generate install.manifest.json
# MUST be written LAST (after sweep, so the sweep doesn't remove it).
# Role: "manifest" per §3.3.
# ---------------------------------------------------------------------------

MANIFEST_PATH="${TARGET}/install.manifest.json"

# Build files_written JSON array from parallel arrays
FW_ARRAY=""
FW_IDX=0
for entry in "${FILES_WRITTEN_JSON[@]}"; do
    if [ "${FW_IDX}" -gt 0 ]; then
        FW_ARRAY="${FW_ARRAY},"
    fi
    FW_ARRAY="${FW_ARRAY}${entry}"
    FW_IDX=$((FW_IDX + 1))
done

# Add manifest itself to files_written[] (role="manifest")
MANIFEST_SHA=""  # computed below after write
if [ -n "${FW_ARRAY}" ]; then
    FW_ARRAY="${FW_ARRAY},"
fi
FW_ARRAY="${FW_ARRAY}{\"path\":\"install.manifest.json\",\"role\":\"manifest\",\"sha256\":\"\"}"

# Write install timestamp to <data_dir>/install.ts (G5 human-confirm window)
DATA_DIR="${HOME}/.crystalium"
mkdir -p "${DATA_DIR}"
INSTALL_TS_PATH="${DATA_DIR}/install.ts"
INSTALL_TS="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
if [ ! -f "${INSTALL_TS_PATH}" ] || [ "${FORCE}" -eq 1 ]; then
    printf '%s\n' "${INSTALL_TS}" > "${INSTALL_TS_PATH}"
    say "wrote install.ts → ${INSTALL_TS_PATH}"
fi

# Build the manifest JSON
MANIFEST_JSON="{
  \"eiis_version\": \"${EIIS_VERSION_VALUE}\",
  \"eidolon\": \"crystalium\",
  \"version\": \"${CRYSTALIUM_VERSION}\",
  \"install_ts\": \"${INSTALL_TS}\",
  \"profile\": \"${PROFILE}\",
  \"canonical_inventory_strict\": true,
  \"ecl_version_emitted\": \"${ECL_VERSION_VALUE}\",
  \"files_written\": [${FW_ARRAY}]
}"

# Write manifest (only if changed, for idempotency)
MANIFEST_CHANGED=0
if [ "${FORCE}" -eq 1 ] || [ ! -f "${MANIFEST_PATH}" ]; then
    MANIFEST_CHANGED=1
else
    # Compare content (strip the install_ts line which always differs)
    EXISTING_MINUS_TS="$(grep -v '"install_ts"' "${MANIFEST_PATH}" 2>/dev/null || true)"
    NEW_MINUS_TS="$(echo "${MANIFEST_JSON}" | grep -v '"install_ts"')"
    if [ "${EXISTING_MINUS_TS}" != "${NEW_MINUS_TS}" ]; then
        MANIFEST_CHANGED=1
    fi
fi

if [ "${MANIFEST_CHANGED}" -eq 1 ]; then
    printf '%s\n' "${MANIFEST_JSON}" > "${MANIFEST_PATH}"
    say "wrote install.manifest.json"
fi

ok "Installation complete."
ok "  Target: ${TARGET_ABS}"
ok "  Files written: ${#FILES_WRITTEN_PATHS[@]} source files + manifest"
ok "  EIIS v${EIIS_VERSION_VALUE} canonical inventory strict: yes"
ok "  ECL_VERSION: ${ECL_VERSION_VALUE}"
