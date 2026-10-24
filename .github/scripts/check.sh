#!/usr/bin/env bash
# Run from the repository root, with the pinned tools on PATH.
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1
snapshot="$(mktemp -d)"
trap 'rm -rf "$snapshot"' EXIT
python .github/scripts/validate.py --snapshot "$snapshot"
python -m unittest discover -s .github/tests -v
git diff --check
actionlint -color
zizmor --offline --format=github .
# Only validated, tracked owned files are copied; never traverse local caches/secrets.
gitleaks dir --no-banner --redact=100 "$snapshot"
# Full history is small here and catches credentials deleted within the PR.
gitleaks git --no-banner --redact=100 .
