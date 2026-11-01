#!/usr/bin/env bash
# Lefthook pre-commit helper: run a linter on staged files if it is installed.
# usage: staged.sh <tool> [tool args...] -- <files...>
set -euo pipefail
tool="$1"; shift
if ! command -v "$tool" >/dev/null; then
  echo "pre-commit: $tool not on PATH; skipped (CI still runs it)."
  exit 0
fi
args=()
while [ "$#" -gt 0 ] && [ "$1" != "--" ]; do args+=("$1"); shift; done
[ "${1:-}" = "--" ] && shift
[ "$#" -eq 0 ] && exit 0
exec "$tool" ${args[@]+"${args[@]}"} "$@"
