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
#
# ---------------------------------------------------------------------------
# .git is NOT pruned, deliberately
# ---------------------------------------------------------------------------
# An earlier revision carried `-path ./.git -prune`. That excluded exactly the
# territory #66 exists to protect: the motivating incident was `git worktree
# remove` deregistering a worktree and then failing to delete it, and worktree
# metadata lives in `.git/worktrees/<name>/`. Root-owned residue there reported
# clean forever:
#
#     $ <pruned rule>            -> OK: every path ... is deletable
#     $ rm -rf .git/worktrees/ghost  -> Permission denied
#
# It was also asymmetric with the remedy: `make fix-ownership` runs a bare
# `find /app -not -user …` with no .git exclusion, so it reaches in and repairs
# what the audit could not see. An audit narrower than its own remedy will
# report green on damage the remedy is standing by to fix.
#
# ---------------------------------------------------------------------------
# Known limitation: filesystem attributes are out of model
# ---------------------------------------------------------------------------
# Every rule here reasons over POSIX mode bits. A file carrying the immutable
# attribute (`chattr +i`) looks perfectly normal to all of them and passes,
# while `rm` fails with EPERM — and `make fix-ownership` cannot repair it either,
# since even a root container's `chown`/`rm` are refused without an explicit
# `chattr -i`.
#
# Not defended against, on reachability grounds rather than convenience:
# setting +i needs CAP_LINUX_IMMUTABLE, which is NOT in Docker's default
# capability set. Measured — the compose service has no `chattr` binary at all
# and runs as uid 1000; a root container with default caps gets "Operation not
# permitted"; only an explicit `--cap-add LINUX_IMMUTABLE`, which appears
# nowhere in this repo, can set it. So no container this project runs can
# produce the state.
#
# Detection would mean shelling out to `lsattr`, which does not exist on macOS
# and has no cross-platform equivalent — a portability cost paid on every run to
# catch a state nothing here can reach. (An earlier revision of this comment also
# claimed `lsattr` errors on overlayfs and tmpfs. That was asserted, not
# measured, and a checker disproved it: it round-trips cleanly on tmpfs,
# overlay2 and btrfs. Reachability is the argument; that one was never load
# bearing and is removed rather than left standing as decoration.)

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
done < <(find . ! -uid "$uid" -print0 2>"$stderr_log")

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
