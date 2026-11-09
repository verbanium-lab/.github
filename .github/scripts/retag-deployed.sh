#!/usr/bin/env bash
# Shared manifest-only retag; callers supply validated IMAGE, DIGEST and TAG.
set -euo pipefail
# Revalidate: this step must not trust earlier step output.
[[ "$IMAGE" =~ ^ghcr\.io/verbanium-lab/[a-z0-9]+([._/-][a-z0-9]+)*$ ]] || exit 1
[[ "$DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]] || exit 1
[[ "$TAG" =~ ^deployed-(dev|rc|prod)$ ]] || exit 1
# Manifest copy by digest: no layer pull, and the OCI index is kept as it is.
docker buildx imagetools create --tag "$IMAGE:$TAG" "$IMAGE@$DIGEST"
actual="$(docker buildx imagetools inspect "$IMAGE:$TAG" --format '{{.Manifest.Digest}}')"
{
  echo "### Deployed tag"
  echo
  echo "- Tag: \`$IMAGE:$TAG\`"
  echo "- Expected digest: \`$DIGEST\`"
  echo "- Tag digest: \`$actual\`"
} >> "$GITHUB_STEP_SUMMARY"
if [[ "$actual" != "$DIGEST" ]]; then
  echo "::error::$TAG does not point at the requested digest"
  exit 1
fi
echo "- Result: match" >> "$GITHUB_STEP_SUMMARY"
