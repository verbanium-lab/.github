#!/usr/bin/env bash
# Lefthook pre-push: local secret scan of the commits being pushed, then the
# repository checks. CI stays the gate; missing tools are skipped with a notice.
# usage: pre-push.sh <check script> [tools the check script needs...]
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

# Git exports GIT_DIR, GIT_INDEX_FILE and similar variables to hooks. Checks that
# create temporary repositories would otherwise act on this clone.
while IFS= read -r var; do unset "$var"; done < <(env | sed -n 's/^\(GIT_[A-Za-z0-9_]*\)=.*/\1/p')

check="${1:?usage: pre-push.sh <check script> [required tools...]}"
shift

if ! git rev-parse -q --verify origin/main >/dev/null; then
  echo "pre-push: origin/main not found; fetch it to scan the pushed commits. Skipping."
  exit 0
fi

if command -v gitleaks >/dev/null; then
  config=()
  [ -f .github/gitleaks.toml ] && config=(--config .github/gitleaks.toml)
  gitleaks git ${config[@]+"${config[@]}"} --log-opts=origin/main..HEAD --redact --no-banner
else
  echo "pre-push: gitleaks not on PATH; skipping the local secret scan (CI still runs it)."
fi

for tool in "$@"; do
  if ! command -v "$tool" >/dev/null; then
    echo "pre-push: $tool not on PATH; skipping $check (CI still runs it)."
    exit 0
  fi
done
bash "$check"
