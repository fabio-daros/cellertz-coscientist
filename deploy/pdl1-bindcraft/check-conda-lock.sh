#!/bin/sh
# Reject a conda lock that would let the solver choose versions or builds.
# Usage: check-conda-lock.sh LOCK REQUIRED_NAMES DEPENDS_TSV
# DEPENDS_TSV must list dependency specs. A comment marker is not closure.
# Version and build constraints are checked by check-conda-constraints.py.

set -eu

if [ "$#" -ne 3 ]; then
    echo "base_lock_unconstrained" >&2
    exit 1
fi

lock=$1
required=$2
depends=$3

if [ ! -f "$lock" ] || [ ! -f "$required" ]; then
    echo "base_lock_incomplete" >&2
    exit 1
fi

bad=$(
    grep -E -v '^(#|[[:space:]]*$)' "$lock" |
        grep -E -v '^[a-z0-9_][a-z0-9._+-]*=[A-Za-z0-9._+!-]+=[A-Za-z0-9._+-]+$' ||
        true
)
if [ -n "$bad" ]; then
    echo "base_lock_unconstrained" >&2
    printf '%s\n' "$bad" >&2
    exit 1
fi

conflicts=$(
    grep -E '^[a-z0-9_]' "$lock" |
        cut -d= -f1 |
        sort |
        uniq -d ||
        true
)
if [ -n "$conflicts" ]; then
    echo "base_lock_conflict:${conflicts}" >&2
    exit 1
fi

missing=""
while IFS= read -r name || [ -n "$name" ]; do
    case $name in
        "" | \#*)
            continue
            ;;
    esac
    if ! grep -E -q "^${name}=[A-Za-z0-9._+!-]+=[A-Za-z0-9._+-]+$" "$lock"; then
        missing="${missing} ${name}"
    fi
done < "$required"

if [ -n "$missing" ]; then
    echo "base_lock_incomplete:${missing# }" >&2
    exit 1
fi

# A comment, including a closure marker, does not satisfy this check.
# The Python checker compares version and build constraints. It does not
# treat installed names, virtual packages, or pip rows as a reproducible build.
if [ ! -s "$depends" ]; then
    echo "base_lock_dependencies_unverified" >&2
    exit 1
fi

checker=$(CDPATH= cd -- "$(dirname "$0")" && pwd)/check-conda-constraints.py
if [ ! -f "$checker" ]; then
    echo "base_lock_dependencies_unverified" >&2
    exit 1
fi
python3 "$checker" "$lock" "$depends"
