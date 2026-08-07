#!/usr/bin/env bash
# crystalium#66 — assert the container left nothing the host user cannot delete.
#
# Run from the repo root (see `make check-ownership`).
#
# ---------------------------------------------------------------------------
# Why this is a script and not a one-line find
# ---------------------------------------------------------------------------
# The first version of this check lived inline in the Makefile and gated on:
#
#     for every path not owned by me: flag it if its PARENT is not writable
#
# That is the correct removal condition for a FILE — POSIX unlink permission
# comes from the parent directory's write bit — but it is only half the story
# for a DIRECTORY, and the missing half is silent:
#
#     $ mkdir D && touch D/a D/b && chmod 700 D    # as root
#     $ <old check>                          -> "OK: everything deletable"
#     $ rm -rf D                             -> Permission denied, D survives
#
# `D`'s own parent is writable, so the old rule cleared it. And because `D` is
# mode 0700 owned by root, `find` could not descend into it, so `D/a` and `D/b`
# were never enumerated at all — the traversal error went to a `2>/dev/null`
# the check itself installed. A whole undeletable subtree passed as clean.
#
# Mode-0700 directories are not exotic: tempfile.mkdtemp, .ssh, .gnupg and
# assorted caches all produce them, and root-owned residue of exactly this
# shape is what motivated #66 in the first place.
#
# So the rule below is the real removability condition, stated for both kinds:
#
#   a path is STUCK unless its parent is writable by the host user, AND
#   -- if it is a directory -- the host user can actually clear it: readable
#      and executable (to enumerate and traverse) and writable (to unlink the
#      children), or else genuinely empty.
#
# An unreadable directory is STUCK by definition: we cannot even determine
# whether it is empty, and `ls` on it returns nothing, which would otherwise
# read as "empty" and pass. Failing closed matters more than a tidy report.
#
# find's stderr is captured rather than discarded, and any traversal failure is
# itself a finding — belt and braces for anything the explicit rules miss.

set -uo pipefail

uid="$(id -u)"
stderr_log="$(mktemp)"
trap 'rm -f "$stderr_log"' EXIT

# NUL-delimited throughout. A newline-delimited read splits a filename containing a
# newline into fragments, and the fragments are then tested as if they were paths:
# a root-owned undeletable directory named $'ev\nil' was reported as `il/a`, a path
# that does not exist. It happened to still exit non-zero — `dirname il/a` is `il`,
# which does not exist and so tests as non-writable — i.e. the gate was right by
# accident while naming a ghost. Contrive the fragment so it lands on a real writable
# directory and the same split becomes a MISS.
#
# An array, not a string: paths can contain backslashes, and `printf '%b'` on an
# accumulated string would interpret them.
stuck=()
record() { stuck+=("$1"); }

while IFS= read -r -d '' p; do
	[ -n "$p" ] || continue

	# A file or directory can only be unlinked if its parent is writable.
	if [ ! -w "$(dirname "$p")" ]; then
		record "$p  [parent not writable — cannot unlink]"
		continue
	fi

	# Files are done: parent writable is sufficient.
	[ -d "$p" ] || continue

	# Symlinks are unlinked from the parent, never traversed.
	[ -L "$p" ] && continue

	# A directory we cannot read cannot be cleared, and cannot even be shown
	# to be empty. Fail closed.
	if [ ! -r "$p" ] || [ ! -x "$p" ]; then
		record "$p  [directory not readable/traversable — contents unclearable]"
		continue
	fi

	# Readable: an empty directory is removable via rmdir regardless of its
	# own mode. A non-empty one needs the write bit to unlink its children.
	if [ -n "$(ls -A "$p" 2>/dev/null)" ] && [ ! -w "$p" ]; then
		record "$p  [non-empty directory not writable — children unclearable]"
	fi
done < <(find . -path ./.git -prune -o ! -uid "$uid" -print0 2>"$stderr_log")

# A traversal error means find could not see into something — treat as a finding
# rather than letting it vanish into a redirect.
if [ -s "$stderr_log" ]; then
	while IFS= read -r line; do
		record "find traversal error: $line"
	done <"$stderr_log"
fi

if [ "${#stuck[@]}" -gt 0 ]; then
	echo "FAIL: container left paths the host user (uid $uid) cannot delete:"
	printf '%s\n' "${stuck[@]}" | head -20
	echo "  (total: ${#stuck[@]})"
	echo "  remedy: make fix-ownership"
	exit 1
fi

echo "OK: every path the container wrote is deletable by uid $uid"
