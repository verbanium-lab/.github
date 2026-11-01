---
title: Reusable image builds and registry cleanup
owner: verbanium
status: draft
last_reviewed: 2026-11-01
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
workflow inputs or this repository. `mode=max` provenance can disclose build arguments. Image builds
can opt into the job's `GITHUB_TOKEN` as the `npm_token` BuildKit secret; no caller-provided workflow
secret is accepted. Do not use `secrets: inherit` for image builds or previews.

## Image contract

Call `.github/workflows/image.yml` by a reviewed commit SHA. Inputs are `app-name`, `image-name`
(full `ghcr.io/verbanium-lab/...` name without a tag), `dockerfile`, `context`, `platforms`
(default `linux/amd64,linux/arm64`), `push-mode` (`main`, `preview`, `none`; default `none`),
optional `npm-registry-auth` (boolean, default `false`; see [Private npm packages](#private-npm-packages)),
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
platform publication and index merge jobs get `packages: write`; `none` jobs get `contents: read`
and `packages: read`. Job permissions are static, so the read grant is present even when npm auth
is off; the token is passed to BuildKit only when `npm-registry-auth: true`.
The workflow rejects main publication outside a main push and preview publication outside a
same-repository `pull_request`. It rejects privileged PR events, including `pull_request_target`.
`none` mode builds without publishing and runs with read-only permissions, but see
[Why callers have no fork job](#why-callers-have-no-fork-job): a caller job that calls this
workflow must still be granted `packages: write`. Same-repository previews use only
`GITHUB_TOKEN`; no App token or environment secret is available. Existing image packages
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
  provenance, and this repository is public. Use the opt-in BuildKit secret below for npm installs;
  keep runtime configuration in the caller's runtime environment.
- Keep `sha-<sha>` deterministic per commit. A build argument changes the image bytes, so a
  rerun of the same commit must produce the same values. Derive them only from the commit:
  number = `git rev-list --count <sha>`, date = the commit's committer date
  (`git show -s --format=%cI <sha>`), SHA = `github.sha`. Never use the current time, the run
  number or attempt, the actor or any other per-run value. A rerun with different values would
  publish different bytes under the same `sha-<sha>` tag.
- The Dockerfile must declare each argument with `ARG` and give it a safe default so a local
  `docker build` still works. Do not pass an argument that selects code, base images or registries.

## Private npm packages

The owner selected private GitHub Packages in `verbanium-lab/packages` on 2026-10-28, with scope
`@verbanium-lab` and registry `https://npm.pkg.github.com`. In each consumer, commit this `.npmrc`
beside its workspace `package.json`. Keep the environment placeholder literal:

```ini
@verbanium-lab:registry=https://npm.pkg.github.com
//npm.pkg.github.com/:_authToken=${NODE_AUTH_TOKEN}
```

### Image installs

Set `npm-registry-auth: true` on the calling image job. Both native build paths pass their own
job's `GITHUB_TOKEN` through the pinned build-push action's [`secrets` input](https://github.com/docker/build-push-action/blob/10e90e3645eae34f1e60eeb005ba3a3d33f178e8/action.yml#L84-L86),
using secret ID `npm_token`. With the default `false`, that input is empty and no npm secret is
passed. No token is added to build arguments, labels, outputs or persisted npm configuration.
The caller must permit package reads. This combined build/publish workflow already requires
`packages: write` on calling jobs, which includes read access; `packages: read` alone fails its
existing static permission check even for `push-mode: none` (see [Why callers have no fork job](#why-callers-have-no-fork-job)).

Copy the committed placeholder `.npmrc` into the dependency stage before installing. Keep the
caller's pinned Node/pnpm setup, manifests and workspace files. Use this install instruction:

```dockerfile
# syntax=docker/dockerfile:1
# In the dependency stage, after copying package manifests and pnpm-lock.yaml:
COPY .npmrc ./
RUN --mount=type=secret,id=npm_token,required=true \
    NODE_AUTH_TOKEN="$(cat /run/secrets/npm_token)" pnpm install --frozen-lockfile
```

This file mount sets `NODE_AUTH_TOKEN` for that command only; the mount is not committed to an
image layer. Never use `ARG`/`ENV` for the token, expand the placeholder into a file, copy the
secret mount, or enable shell tracing / print the token. Package install scripts run with this
credential, so enable auth only for trusted consumer code and dependencies. Keep the existing
same-repository preview boundary; do not widen it to fork PRs or privileged PR events.

Docker's [`env=` secret mount](https://docs.docker.com/reference/dockerfile/#run---mounttypesecret)
requires Dockerfile frontend 1.10.0 or later. The [pinned setup-buildx action](https://github.com/docker/setup-buildx-action/blob/8d2750c68a42422c14e847fe6c8ac0403b4cbd6f/action.yml)
does not pin the BuildKit image or Dockerfile frontend in this workflow. Use the file mount above
as the compatibility pattern. After verifying the consumer's frontend and builder support it,
the equivalent environment mount is:

```dockerfile
RUN --mount=type=secret,id=npm_token,env=NODE_AUTH_TOKEN,required=true pnpm install --frozen-lockfile
```

### CI installs outside Docker

In the consumer's existing CI job, add `packages: read` and set the token only on install steps.
Retain its pinned checkout, Node and pnpm setup before these steps:

```yaml
jobs:
  ci:
    runs-on: ubuntu-24.04
    permissions:
      contents: read
      packages: read
    steps:
      # Keep the existing pinned checkout and Node/pnpm setup here.
      - name: Install dependencies
        run: pnpm install --frozen-lockfile
        env:
          NODE_AUTH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

### Owner setup after first publish

For **each** published package, open its settings and use **Manage Actions access** →
**Add repository** to grant the consumer repositories **Read** access: wiki first, then
tower-apps and iblonda-apps as they adopt the packages. This grant is on each npm package, not
the shared workflow repository or the GHCR image. Workflow token permissions alone do not grant
cross-repository access. GitHub documents this [package access configuration](https://docs.github.com/en/packages/learn-github-packages/configuring-a-packages-access-control-and-visibility#ensuring-workflow-access-to-your-package)
and [npm authentication with GITHUB_TOKEN](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-npm-registry#authenticating-to-github-packages).
No PAT, inherited secret or organization credential is required.

Verify a clean CI install and an uncached native image install in the consumer after granting
access; offline repository checks cannot prove package authorization or absence of credential
leaks from consumer install scripts. Review logs, image history and provenance without printing
the token. To stop passing it, remove `npm-registry-auth` or set it to `false`.

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
      npm-registry-auth: true
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
      npm-registry-auth: true
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
`origin/main` catalog pins Turbo 2.11.9 and its app is `@verbanium-lab/tower` at `apps/dashboard`.
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
    apps = [{'package': '@verbanium-lab/tower', 'app': 'tower',
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
protection tag, moved by [`deployed-tag.yml`](#protect-the-deployed-digest). Keep dry-run enabled
until this protection exists. The latest-ten rule alone cannot protect every running digest.

There is a deliberate limit: the action's age filter uses package-version age, **not the time a
PR closed**. An open preview older than 14 days can be selected. This implements issue 8's age
policy; ADR 0009's stronger “14 days after closure” rule needs PR lifecycle tracking in a follow-up.
Also verify how reusing a digest with several SHA aliases affects creation-age retention; the
policy protects at least ten recent sha-tagged versions and all their SHA aliases.

## Protect the deployed digest

Owner decision of 2026-10-28: registry retention for app images (tower, iblonda, landing) is
switched on only after the running digest of each environment carries a `deployed-<env>` tag.
`ghcr-cleanup.yml` already excludes `deployed-*`; `.github/workflows/deployed-tag.yml` moves the tag.

When to run it: right after every Coolify deploy by digest, and after every rollback, with the
digest that now runs. The latest-ten `sha-*` rule protects a digest only while it is recent. An
environment that stays on one digest for weeks would lose it to the 30-day `sha-*` cleanup, and the
next restart, scale-out or rollback would fail to pull. A moved tag releases the previous digest
to normal retention.

Caller: copy this into the app repository as `.github/workflows/deployed-tag.yml`. Pin
`REVIEWED_WORKFLOW_COMMIT` to a reviewed full commit SHA. The calling job grants `contents: read`
and `packages: write`, as the reusable job requests exactly these.

```yaml
name: Deployed tag
on:
  workflow_dispatch:
    inputs:
      image:
        description: Image without tag or digest, e.g. ghcr.io/verbanium-lab/iblonda-web
        required: true
        type: string
      digest:
        description: Deployed OCI index digest, sha256:<64 hex>
        required: true
        type: string
      env:
        description: Environment that runs this digest
        required: true
        type: choice
        options: [dev, rc, prod]
      release:
        description: Optional release tag when the digest matches a GitHub Release
        required: false
        type: string
permissions: {}
jobs:
  deployed-tag:
    permissions:
      contents: read
      packages: write
      deployments: write
    uses: verbanium-lab/.github/.github/workflows/deployed-tag.yml@REVIEWED_WORKFLOW_COMMIT
    with:
      image: ${{ inputs.image }}
      digest: ${{ inputs.digest }}
      env: ${{ inputs.env }}
      release: ${{ inputs.release }}
```

The `image` is a full `ghcr.io/verbanium-lab/...` name without a tag or digest; `digest` is the OCI
index digest that was deployed (the `digest` output of `image.yml`, or the digest in the Coolify
image reference); `env` is `dev`, `rc` or `prod`.

What the workflow does:

- Fails closed unless the image is under `ghcr.io/verbanium-lab/` in lowercase, the digest matches
  `sha256:` plus 64 lowercase hex characters and `env` is one of the three values. The inputs reach
  the validation script through `env`, never through an expression inside `run`. It also requires a
  `workflow_dispatch` on the default branch of a `verbanium-lab` repository, like the cleanup workflow's
  main-only boundary.
- Runs `docker buildx imagetools create --tag <image>:deployed-<env> <image>@<digest>`. This copies
  the manifest by digest: no layers are pulled and the OCI index, with its per-platform manifests,
  provenance and SBOM, is kept.
- Runs `docker buildx imagetools inspect <image>:deployed-<env>` and fails unless the top digest equals
  the input digest. The job summary shows the tag, the expected digest and the tag's digest.
- Creates a GitHub Deployment in the caller repository (`production`, `rc` or `dev`) with `ref` set to
  the release target commit when `release` is supplied and resolves, otherwise the default branch.
  Payload fields: `image`, `digest`, `release`. A success deployment status is recorded with no
  `environment_url`.
- Uses only the job's `GITHUB_TOKEN` with `contents: read`, `packages: write` and `deployments: write`;
  no checkout and no secret. The package must grant the calling repository write access, as for `image.yml`.
- Serializes moves per image and environment; a newer waiting request replaces an older waiting one.

Not verified offline: that `imagetools create` from a single index source returns an identical
index digest. The check after the move fails the job if it does not. Confirm it in the first real
caller run, then record the evidence in the retention rollout.

## README status block

Caller repositories that publish releases and move `deployed-*` tags can refresh a Markdown table in
`README.md` between `<!-- status:start -->` and `<!-- status:end -->`. The reusable
`.github/workflows/status.yml` workflow:

- Reads the latest GitHub Release per app slug (name prefix `<slug>-`), resolves `deployed-prod` and
  `deployed-rc` digests from GHCR, and links each digest to the release whose body contains the
  matching ``Deploy by digest: `<image>@sha256:…` `` line (otherwise shows a short digest).
- Optionally merges a downloaded artifact's `coverage.json` (`slug` → `{unit, e2e}` URL objects) into
  the Coverage column.
- Inserts the native CI badge line when it is absent inside the block. When the block changed, it
  commits `README.md` through the GraphQL `createCommitOnBranch` mutation — signed by GitHub
  (Verified), author `github-actions[bot]`, always the same message
  `chore(status): update README status [skip ci]` with the trailer `Status-Update: automated`. If
  repository rules refuse it, the same commit goes to `bot/readme-status-<run>` with a pull request
  of the same title. The commit starts no workflow (`GITHUB_TOKEN`, `[skip ci]`); callers also add
  `paths-ignore: [README.md]` to the push trigger of their CI.

Grant `contents: write`, `packages: read` and `deployments: read` on the calling job. Add markers to
`README.md` before the first run.

Example for `iblonda-apps`:

```yaml
name: README status
on:
  release:
    types: [published]
  workflow_run:
    workflows: [Deployed tag]
    types: [completed]
  workflow_dispatch:
permissions: {}
jobs:
  status:
    if: >-
      github.event_name != 'workflow_run' ||
      github.event.workflow_run.conclusion == 'success'
    permissions:
      contents: write
      packages: read
      deployments: read
    uses: verbanium-lab/.github/.github/workflows/status.yml@REVIEWED_WORKFLOW_COMMIT
    with:
      apps: >-
        [{"slug":"iblonda-web","image":"ghcr.io/verbanium-lab/iblonda-web"},
         {"slug":"iblonda-admin","image":"ghcr.io/verbanium-lab/iblonda-admin"}]
```

When repository rulesets block bot pushes to the default branch, merge the automation PR or add a
ruleset bypass for `github-actions[bot]` on the status commit message.

## Deploy to Coolify

`deploy-coolify.yml` deploys the main image of one app to its Coolify application (ADR 0009
amendment 2026-10-28; .github#25). Its three jobs:

| Job | Permissions | What it does |
| --- | --- | --- |
| `plan` | none | Validates every input against the run: main push, `image-tags` is `<image>:sha-<this commit>`, `sha7` is this commit, the digest, the Coolify UUID, https `verbanium.dev` URLs, the environment and the App client ID. Later jobs read only its outputs. |
| `record` | none (App token) | Creates the GitHub Deployment with the verbanium-ci App in the caller's environment (`deployment: false`). |
| `deploy` | `id-token: write` | Fails first if a deploy secret is empty, then joins the tailnet as an ephemeral `tag:ci` node (Tailscale workload identity), sets `sha256-<hex>` as the Coolify image tag, deploys, waits up to 10 minutes for Coolify, then up to 5 minutes for `verify-url` to report `sha7`. Deploys of one app and environment queue (`cancel-in-progress: false`). |

`TS_OAUTH_CLIENT_ID`, `TS_AUDIENCE` and `COOLIFY_DEPLOY_TOKEN` are environment secrets of
`production` (main-only branch policy); the verbanium-ci App key is an environment secret (Studio)
or an organization secret (wiki). The calling job must use `secrets: inherit`. Naming the
environment in the reusable job is not enough: without `inherit` GitHub resolves environment
secrets as empty there ([actions/runner#4453](https://github.com/actions/runner/issues/4453)); the
first wiki deploy failed this way (run 41306724863). The deploy job checks the three secrets first
and fails with this explanation. zizmor flags `secrets: inherit`; the caller line carries a
reviewed `zizmor: ignore[secrets-inherit]`.

| Input | Required | Meaning |
| --- | --- | --- |
| `app` | yes | App slug, used in the record, the summary and the concurrency group |
| `image`, `digest`, `image-tags` | yes | Outputs `image`, `digest` and `tags` of `image.yml` (`push-mode: main`) |
| `sha7` | yes | Short commit the live site must report |
| `build-number` | no | Build number for the record; empty when the app has none |
| `coolify-app-uuid` | yes | Coolify application UUID (a repository variable, e.g. `STUDIO_COOLIFY_APP_UUID`) |
| `site-url` | yes | Environment URL shown on the deployment |
| `verify-url` | yes | Fetched after the deploy until it reports `sha7` |
| `verify-json-field` | no | JSON field of `verify-url` holding `sha7` (e.g. `sha` for `/healthz`); empty: the page text must contain `sha7` |
| `environment` | no | `production` (default), `release-candidate` or `dev` |
| `client-id` | yes | `${{ vars.VERBANIUM_CI_CLIENT_ID }}` |

```yaml
  deploy:
    name: deploy (studio)
    needs: [checks, image-main]
    if: ${{ github.event_name == 'push' && github.ref == 'refs/heads/main' && needs.checks.outputs.studio == 'true' }}
    permissions:
      id-token: write # Tailscale workload identity in the deploy job
    uses: verbanium-lab/.github/.github/workflows/deploy-coolify.yml@REVIEWED_WORKFLOW_COMMIT
    with:
      app: studio
      image: ${{ needs.image-main.outputs.image }}
      digest: ${{ needs.image-main.outputs.digest }}
      image-tags: ${{ needs.image-main.outputs.tags }}
      sha7: ${{ needs.checks.outputs.sha7 }}
      coolify-app-uuid: ${{ vars.STUDIO_COOLIFY_APP_UUID }}
      site-url: https://studio.verbanium.dev
      verify-url: https://studio.verbanium.dev/healthz
      verify-json-field: sha
      client-id: ${{ vars.VERBANIUM_CI_CLIENT_ID }}
    secrets: inherit # zizmor: ignore[secrets-inherit] environment secrets need it
```

The wiki reports the build in the page footer, so it passes `verify-url: https://wiki.verbanium.dev/`
and no `verify-json-field`.

## Verification and recovery

Run the repository check from [CONTRIBUTING.md](../CONTRIBUTING.md). Rejection tests cover action
pinning, build-argument validation and its raw-input boundary, token permissions, PR publication boundaries, malformed digest artifacts, cache writes
and cleanup retention. Offline tests cannot prove action input compatibility or registry effects.
In a disposable caller/package, first build `none`, publish both native platforms, inspect the
index's platform descriptors and attestation manifests, and exercise cleanup with protected,
expired and multi-tagged versions in dry-run. Compare candidates with the policy before enabling
deletion. Disable the caller schedule or restore `dry-run: true` to stop future deletion; deleted
image bytes require rebuilding or restoring from another registry and cannot be recovered from tags.
