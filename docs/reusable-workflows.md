---
title: Reusable image builds and registry cleanup
owner: verbanium
status: draft
last_reviewed: 2026-10-27
---

# Reusable image builds and registry cleanup

Implements the Build and Registry parts of wiki ADR 0009, at the owner's direction in
[issue 8](https://github.com/verbanium-lab/.github/issues/8). App repository migration is separate.
No release, signing or deployment operation is performed here. BuildKit provenance and SBOMs are
image attachments; they are not signatures or GitHub artifact attestations.

## Adoption gate and public boundary

Every `TODO(pin)` action reference must be replaced with a vendor-verified full commit SHA and an
exact version comment before adoption. Those references intentionally fail the pin validator.
Network access was unavailable during authoring. The dispatcher must also verify the selected
cleanup action version's inputs and retention semantics, and run actionlint, offline zizmor and
the full repository check. The first registry run must remain a dry run. Native runner availability,
GHCR access, index attachments and cleanup candidates require a real caller run before migration.

This repository is public. Caller examples contain only public organization repository references.
Keep runtime configuration and secrets in callers; never put them in build arguments, labels,
workflow inputs or this repository. `mode=max` provenance can disclose build arguments. Neither
workflow accepts a Docker build secret. Do not use `secrets: inherit` for image builds or previews.

## Image contract

Call `.github/workflows/image.yml` by a reviewed commit SHA. Inputs are `app-name`, `image-name`
(full `ghcr.io/verbanium-lab/...` name without a tag), `dockerfile`, `context`, `platforms`
(default `linux/amd64,linux/arm64`), `push-mode` (`main`, `preview`, `none`; default `none`),
and optional newline-separated `build-args` (default empty; see [Build arguments](#build-arguments)).

Native `ubuntu-24.04` and `ubuntu-24.04-arm` jobs build one platform each. Publishing jobs push
by digest and upload only digest files, retained for one day. A separate job downloads this run's
artifacts, checks the expected architectures and digest syntax, then creates one OCI index. Even
a single-platform publication gets an index because BuildKit attaches provenance and an SBOM.
The merge preserves these attachments. No QEMU or cross-architecture runtime emulation is used.

Outputs are `image` (untagged name), `digest` (published index `sha256:...`) and `tags` (the fully
qualified published tag). `digest` and `tags` are empty in `none` mode. Deploy using
`image@digest`; a moving tag is not a deployment identity. Matrix callers must not use a reusable
workflow's last-finishing matrix output as a collection of all apps' digests; follow-up deployment
work must collect each app's result explicitly.

Main tags use `sha-<full 40-character github.sha>`. Preview tags use
`pr-<number>-<first 12 characters of github.sha>`. For `pull_request`, this is the tested merge
commit, also used by checkout and the revision label, rather than the PR head commit. The OCI
title, source URL and full revision are included for every platform. GHA cache scopes are
`<app>-<arch>-main`; builds can read them, but only main publications export caches. Preview and
fork code cannot populate publishing caches. Call each app at most once per caller run, and
keep app names unique, to avoid digest-artifact collisions.
Each image must have one owning caller repository. The reusable workflow serializes calls for
the same image, source SHA and mode so a rerun cannot replace the tag between index creation and
digest inspection. Reserve its `reusable-image-*` concurrency group prefix for this workflow.

The workflow itself grants no permissions. The caller grants `contents: read` and
`packages: write` on every job that calls it. GitHub validates a nested job's requested
permissions statically against the caller job's grant, so a caller job without `packages: write`
is rejected even when the job is skipped or calls `none` mode. Inside the reusable workflow, only
platform publication and index merge jobs get `packages: write`; `none` jobs get `contents: read`.
The workflow rejects main publication outside a main push and preview publication outside a
same-repository `pull_request`. It rejects privileged PR events, including `pull_request_target`.
`none` mode builds without publishing and runs with read-only permissions, but see
[Why callers have no fork job](#why-callers-have-no-fork-job): a caller job that calls this
workflow must still be granted `packages: write`. Same-repository previews use only
`GITHUB_TOKEN`; no App token, environment secret or build secret is available. Existing packages
must grant the caller repository the required GHCR access.

## Build arguments

`build-args` carries public, non-secret values such as the wiki footer's build number, date and
commit SHA (ADR 0009). It is the only free-form input, so it is validated before any build job
runs. The `plan` job reads the input through `env`, never through an expression inside `run`, and
fails closed unless every line matches `^[A-Z][A-Z0-9_]{0,63}=[A-Za-z0-9._:/+-]{0,128}$`, there
are at most 10 lines, and no key repeats. Blank lines, spaces, quotes, `$`, backticks, `;`,
`=` inside a value, carriage returns and any non-ASCII character are rejected. One trailing
newline is allowed, so a YAML block scalar works. The build jobs receive only the validated
`plan` output; they never see the raw input.

Rules for callers:

- No secrets, tokens, passwords or private URLs. Values reach image metadata and `mode=max`
  provenance, and this repository is public. The workflow has no Docker build secrets; keep
  runtime configuration in the caller's runtime environment.
- Keep `sha-<sha>` deterministic per commit. A build argument changes the image bytes, so a
  rerun of the same commit must produce the same values. Derive them only from the commit:
  number = `git rev-list --count <sha>`, date = the commit's committer date
  (`git show -s --format=%cI <sha>`), SHA = `github.sha`. Never use the current time, the run
  number or attempt, the actor or any other per-run value. A rerun with different values would
  publish different bytes under the same `sha-<sha>` tag.
- The Dockerfile must declare each argument with `ARG` and give it a safe default so a local
  `docker build` still works. Do not pass an argument that selects code, base images or registries.

## Wiki caller (fixed app)

These are jobs to add to the existing caller, after its `ci` job. Keep its existing checks. Pin
`REVIEWED_WORKFLOW_COMMIT` to the full commit that lands these workflows; placeholders below are
documentation, not runnable pins. Start the caller with `permissions: {}` and grant its `ci` job
`contents: read` explicitly. Retain its PR cancellation/main-per-commit concurrency policy.

```yaml
jobs:
  build-meta:
    runs-on: ubuntu-24.04
    timeout-minutes: 5
    permissions:
      contents: read
    outputs:
      number: ${{ steps.meta.outputs.number }}
      date: ${{ steps.meta.outputs.date }}
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          fetch-depth: 0
          persist-credentials: false
      - name: Derive build metadata from the commit
        id: meta
        env:
          SHA: ${{ github.sha }}
        run: |
          [[ "$SHA" =~ ^[0-9a-f]{40}$ ]]
          number="$(git rev-list --count "$SHA")"
          date="$(git show -s --format=%cI "$SHA")"
          [[ "$number" =~ ^[1-9][0-9]*$ ]]
          [[ "$date" =~ ^[0-9T:+-]+$ ]]
          printf 'number=%s\ndate=%s\n' "$number" "$date" >> "$GITHUB_OUTPUT"

  image-main:
    needs: [ci, build-meta]
    if: >-
      ${{ github.event_name == 'push' && github.ref == 'refs/heads/main' &&
          !startsWith(github.event.head_commit.message, 'chore: release') &&
          github.event.head_commit.author.username != 'verbanium-ci[bot]' }}
    permissions:
      contents: read
      packages: write
    uses: verbanium-lab/.github/.github/workflows/image.yml@REVIEWED_WORKFLOW_COMMIT
    with:
      app-name: wiki
      image-name: ghcr.io/verbanium-lab/wiki
      dockerfile: portal/Dockerfile
      context: .
      push-mode: main
      build-args: |
        WIKI_BUILD_NUMBER=${{ needs.build-meta.outputs.number }}
        WIKI_BUILD_DATE=${{ needs.build-meta.outputs.date }}
        WIKI_BUILD_SHA=${{ github.sha }}

  image-preview:
    needs: [ci, build-meta]
    if: >-
      ${{ github.event_name == 'pull_request' &&
          github.event.pull_request.head.repo.full_name == github.repository }}
    permissions:
      contents: read
      packages: write
    uses: verbanium-lab/.github/.github/workflows/image.yml@REVIEWED_WORKFLOW_COMMIT
    with:
      app-name: wiki
      image-name: ghcr.io/verbanium-lab/wiki
      dockerfile: portal/Dockerfile
      context: .
      push-mode: preview
      build-args: |
        WIKI_BUILD_NUMBER=${{ needs.build-meta.outputs.number }}
        WIKI_BUILD_DATE=${{ needs.build-meta.outputs.date }}
        WIKI_BUILD_SHA=${{ github.sha }}

```

The `build-meta` job holds only `contents: read`, so it adds no publication path; the shared
workflow validates its outputs again. In a preview, `github.sha` is the tested merge commit, so
the values describe that commit, matching the preview tag. Pin the checkout action like every
other action. Recipes have exactly two image jobs: a main push (`push-mode: main`) and a
same-repository pull request (`push-mode: preview`). Both grant `contents: read` and `packages: write`. Do not add a
third job for fork pull requests.

### Why callers have no fork job

An earlier recipe had a fork-only `image-fork` job calling `push-mode: none` with only
`contents: read`. The first real caller (tower-apps PR #17) failed with "The nested job 'push' is
requesting 'packages: write', but is only allowed 'packages: none'". The reusable workflow
declares `packages: write` on its publication jobs, and GitHub checks that against the caller
job's grant when it parses the workflow. The check is static: the `if:` condition and `push-mode`
are not evaluated, and a skipped job is still validated. A caller job cannot lower the nested
permissions, so every calling job needs `packages: write`.

The job is also unnecessary for this organization's private repositories. A fork of a private
organization repository cannot exist outside the organization, so pull requests in these
repositories come from same-repository branches and the same-repository condition on the preview
job covers them. Keep that condition; do not widen it to run previews for forks.

A public repository that accepts outside fork pull requests needs a different design. Granting
`packages: write` to a fork-facing job is not acceptable, and a caller-level `packages: write`
grant is required even for build-only calls to this workflow. Follow-up options, not implemented
here:

- a separate build-only workflow, with no jobs that call publishing workflows, run on
  `pull_request` with read-only permissions; or
- a build-only reusable workflow without publication jobs, so no nested job requests
  `packages: write`.

Do not use `pull_request_target` or `secrets: inherit` for either option.

The main `if:` skips a commit whose first line begins `chore: release`, or whose author username
is `verbanium-ci[bot]`. Keep the condition on the caller job so a release metadata commit starts
no image runners. This is an optimization, not an authorization check. If release-please uses
another title or GitHub reports a different author field, verify the event and update the recipe.
Wiki does not need Turbo or a release-please job. Its footer build arguments are supplied as in
the recipe above; the wiki Dockerfile must declare `WIKI_BUILD_NUMBER`, `WIKI_BUILD_DATE` and
`WIKI_BUILD_SHA`, and this workflow does not define that contract.

## Tower caller (affected app matrix)

Use a documented caller step rather than another reusable workflow: installing dependencies,
the package-to-image mapping and smoke services belong to the app repository. Tower's current
`origin/main` catalog pins Turbo 2.11.9 and its app is `@verbanium/tower` at `apps/dashboard`.
The JSON schema below needs verification against that installed CLI before migration; unknown
schema or missing Git base fails instead of quietly skipping images.

After checkout with `fetch-depth: 0` and `persist-credentials: false`, the caller's existing pinned
Node/pnpm setup and `pnpm install --frozen-lockfile`, add the following to its `ci` job. It reads
only local Git data, and has no package-write permission. Main compares with `event.before`; a
PR compares its base SHA with the checked-out merge commit. All referenced commits must exist.

```yaml
- name: Select affected images
  id: affected
  env:
    TURBO_SCM_BASE: ${{ github.event_name == 'pull_request' && github.event.pull_request.base.sha || github.event.before }}
    TURBO_SCM_HEAD: ${{ github.sha }}
  run: |
    [[ "$TURBO_SCM_BASE" =~ ^[0-9a-f]{40}$ ]]
    [[ "$TURBO_SCM_HEAD" =~ ^[0-9a-f]{40}$ ]]
    git cat-file -e "$TURBO_SCM_BASE^{commit}"
    git cat-file -e "$TURBO_SCM_HEAD^{commit}"
    pnpm exec turbo ls --affected --output json > "$RUNNER_TEMP/affected.json"
    python3 - <<'PY'
    import json
    import os
    from pathlib import Path

    data = json.loads((Path(os.environ['RUNNER_TEMP']) / 'affected.json').read_text())
    items = data['packages']['items']
    if not isinstance(items, list) or any(not isinstance(p, dict) or not isinstance(p.get('name'), str) for p in items):
        raise SystemExit('Unsupported turbo ls output')
    affected = {p['name'] for p in items}
    # Explicit allowlist: never turn a package's arbitrary metadata into a publish destination.
    apps = [{'package': '@verbanium/tower', 'app': 'tower',
             'image': 'ghcr.io/verbanium-lab/tower', 'dockerfile': 'Dockerfile', 'context': '.'}]
    selected = [{k: v for k, v in app.items() if k != 'package'} for app in apps if app['package'] in affected]
    with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
        print('matrix=' + json.dumps({'include': selected}, separators=(',', ':')), file=output)
        print('has-apps=' + str(bool(selected)).lower(), file=output)
    PY
```

Expose `steps.affected.outputs.matrix` and `steps.affected.outputs.has-apps` as `ci` job outputs
named `image-matrix` and `has-images`. An initial push with a zero `before` SHA fails explicitly;
the caller must select all known apps for that bootstrap case. Keep Tower's existing
`bash scripts/image-smoke.sh` in `ci` before publishing. When migrating IBLONDA later, preserve
its disposable Postgres, migration, liveness, readiness and non-root checks in a prerequisite
job; the shared builder cannot replace those checks. Run smoke tests before publishing, without
production secrets or an inherited App token.

```yaml
  image-main:
    needs: ci
    if: >-
      ${{ needs.ci.outputs.has-images == 'true' && github.event_name == 'push' &&
          github.ref == 'refs/heads/main' &&
          !startsWith(github.event.head_commit.message, 'chore: release') &&
          github.event.head_commit.author.username != 'verbanium-ci[bot]' }}
    strategy:
      fail-fast: false
      matrix: ${{ fromJSON(needs.ci.outputs.image-matrix) }}
    permissions:
      contents: read
      packages: write
    uses: verbanium-lab/.github/.github/workflows/image.yml@REVIEWED_WORKFLOW_COMMIT
    with:
      app-name: ${{ matrix.app }}
      image-name: ${{ matrix.image }}
      dockerfile: ${{ matrix.dockerfile }}
      context: ${{ matrix.context }}
      push-mode: main
```

Add the wiki preview job (no fork job; see above) with the same `has-images` guard, matrix and
`with` mapping for monorepo PRs, granting `contents: read` and `packages: write`. Add apps to the explicit mapping as the repository gains apps; Turbo includes
dependents affected by shared packages. Validate changes to workspace-wide files in the first
caller run so they cannot unexpectedly produce an empty app matrix.

## Cleanup caller and retention

Call `.github/workflows/ghcr-cleanup.yml` once per package, from a daily main-branch schedule or
a main-branch manual run. The reusable workflow has no schedule itself: the default token in
this public defaults repository is not an organization-wide package administrator. An app
repository's `GITHUB_TOKEN` can manage only packages for which that repository has admin access.
Organization-wide scheduling here requires the `verbanium-ci` App from
[issue 9](https://github.com/verbanium-lab/.github/issues/9); this task does not create the App.

```yaml
name: Registry retention
on:
  schedule:
    - cron: '23 3 * * *'
  workflow_dispatch:
permissions: {}
jobs:
  cleanup:
    permissions:
      packages: write
    uses: verbanium-lab/.github/.github/workflows/ghcr-cleanup.yml@REVIEWED_WORKFLOW_COMMIT
    with:
      package-name: tower
      dry-run: true
    # Optional; omit this entire secrets mapping to use GITHUB_TOKEN.
    # secrets:
    #   app-token: ${{ secrets.GHCR_CLEANUP_TOKEN }}
```

`app-token` is an optional **workflow secret**, not a string input. Once issue 9 defines token
issuance, supply a short-lived installation token from a separate trusted main-only job; never
pass the App private key here. The `production` environment restricted to main owns any stored
credential used to mint it. A downstream caller job cannot attach an environment to a reusable
workflow call. No checkout or caller-provided command runs in the cleanup job.

Both cleanup passes default to dry-run and use `validate: true`. A paginated GitHub API read
protects the newest ten sha-tagged versions independently of release tags. Exclusion patterns
also protect every `v*` and `deployed-*` tag, including versions with both preview and protected
aliases. The preview pass selects `pr-*` older than 14 days; the SHA pass selects other `sha-*`
older than 30 days and asks the action to remove untagged, ghost and orphaned multi-arch children.
Its index traversal must preserve children and attestations referenced by retained indexes.
Unknown tagged versions are not selected. Keep cleanup serialized per package, and confirm
the pinned action's dry-run shows no retained index losing a referenced manifest or attestation.

Before enabling deletion, every digest running in any environment must have a `deployed-*`
protection tag. Maintaining those tags is Tower's follow-up responsibility. Keep dry-run enabled
until this protection exists. The latest-ten rule alone cannot protect every running digest.

There is a deliberate limit: the action's age filter uses package-version age, **not the time a
PR closed**. An open preview older than 14 days can be selected. This implements issue 8's age
policy; ADR 0009's stronger “14 days after closure” rule needs PR lifecycle tracking in a follow-up.
Also verify how reusing a digest with several SHA aliases affects creation-age retention; the
policy protects at least ten recent sha-tagged versions and all their SHA aliases.

## Verification and recovery

Run the repository check from [CONTRIBUTING.md](../CONTRIBUTING.md). Rejection tests cover action
pinning, build-argument validation and its raw-input boundary, token permissions, PR publication boundaries, malformed digest artifacts, cache writes
and cleanup retention. Offline tests cannot prove action input compatibility or registry effects.
In a disposable caller/package, first build `none`, publish both native platforms, inspect the
index's platform descriptors and attestation manifests, and exercise cleanup with protected,
expired and multi-tagged versions in dry-run. Compare candidates with the policy before enabling
deletion. Disable the caller schedule or restore `dry-run: true` to stop future deletion; deleted
image bytes require rebuilding or restoring from another registry and cannot be recovered from tags.
