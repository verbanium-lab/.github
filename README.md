---
title: Public organization defaults
owner: verbanium
status: active
last_reviewed: 2026-11-01
---

# .github

Organization-wide files for `verbanium-lab`: the task form, the pull request template and other defaults that
GitHub applies to every repository without its own copy.

| File | Purpose |
| --- | --- |
| `.github/ISSUE_TEMPLATE/task.yml` | "Task" form: goal, scope, done when, how to verify, product, size, risk. Adds the `agent` label and Project 2 |
| `.github/ISSUE_TEMPLATE/config.yml` | Disables blank issues |
| `.github/pull_request_template.md` | PR template: `Closes #N`, what changed, how it was verified |
| `SECURITY.md` | Default security policy: private reports through GitHub on this repository |
| `profile/README.md` | Public organization profile on github.com/verbanium-lab |
| `LICENSE` | MIT for this repository; other repositories carry their own `LICENSE` |

GitHub applies these defaults to other repositories only while the `.github` repository is public.

The single `ci` job validates these public contracts, file boundaries and workflows on every pull
request and main push. See [validation and the public protection boundary](CONTRIBUTING.md) for the
local check and the owner-controlled activation steps.

## Reusable workflows

| Workflow | Purpose |
| --- | --- |
| [image.yml](.github/workflows/image.yml) | Native amd64/arm64 builds, main and same-repository preview publication, provenance and SBOM |
| [ghcr-cleanup.yml](.github/workflows/ghcr-cleanup.yml) | Per-package retention with protected release/deployed tags; dry-run by default |
| [deployed-tag.yml](.github/workflows/deployed-tag.yml) | Moves `deployed-<env>` to the digest that runs in an environment, without pulling layers |
| [status.yml](.github/workflows/status.yml) | Refreshes the README status table from releases, GHCR deployed tags and optional coverage |
| [deploy-coolify.yml](.github/workflows/deploy-coolify.yml) | Deploys a main image digest to a Coolify application over an ephemeral tailnet node, records the GitHub Deployment, waits for the live build |
| [release-notify.yml](.github/workflows/release-notify.yml) | Telegram notice for every `<component>-vX.Y.Z` tag that release-please created in the caller |
| [release.yml](.github/workflows/release.yml) | **Deprecated** (one SemVer scheme, ADR 0009 amendment 2026-11-01 b): date-sha release per main image; no callers left |

See [caller recipes and adoption requirements](docs/reusable-workflows.md) for fixed-app wiki and
affected-matrix Tower examples, release-commit filters and token boundaries. Unresolved action pins
must be completed before adoption. This change does not migrate any app caller.

## Protect the deployed digest

Registry retention deletes old `sha-*` versions, and the newest ten alone do not cover a digest that
has been running for weeks. `ghcr-cleanup.yml` keeps every `deployed-*` tag, so retention for app
images (tower, iblonda, landing) may be switched on only after the running digest of each
environment carries `deployed-<env>` (`dev`, `rc` or `prod`).

Run the tag move right after every Coolify deploy by digest, and again after every rollback (with
the digest that now runs). Without it, the running digest is protected only while it is among the
newest ten `sha-*` versions, and it can be deleted later; the next restart or rollback then fails.

An app repository adds `.github/workflows/deployed-tag.yml`, a manual workflow that calls the
reusable one. Pin the reusable workflow by a reviewed full commit SHA in place of
`REVIEWED_WORKFLOW_COMMIT`:

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
      release: ''
```

Run it from the default branch with the image, the deployed `sha256:` digest and the environment.
Inputs are validated strictly and the job fails if the tag does not resolve to the digest afterwards.
See [the guide](docs/reusable-workflows.md#protect-the-deployed-digest).

## Deploy to Coolify (main)

`deploy-coolify.yml` replaces the deploy jobs copied between repositories (wiki, Studio). Pin it by
a reviewed full commit SHA and call it after `image.yml` on main pushes; keep it out of the required
`ci` job, so a failed deploy never blocks merging a fix. The caller grants only `id-token: write`
and passes at most the verbanium-ci App key: the record and deploy jobs run in the caller's
`production` environment (main-only branch policy) and read its environment secrets
`TS_OAUTH_CLIENT_ID`, `TS_AUDIENCE` and `COOLIFY_DEPLOY_TOKEN` there. Recipe and inputs:
[the guide](docs/reusable-workflows.md#deploy-to-coolify); setting up a new app:
[Launch an internal site](https://wiki.verbanium.dev/docs/delivery/launch-an-internal-site).

## Releases (SemVer)

Every app and package releases with release-please in its own repository: tags
`<component>-vX.Y.Z`, a CHANGELOG per component, the Release PR opened with the verbanium-ci App
token in the `production` environment. After release-please, the caller collects the created tags
and calls `release-notify.yml` (pinned by a reviewed full commit SHA):

```yaml
  notify:
    needs: release-please
    if: ${{ needs.release-please.outputs.tags != '' }}
    permissions:
      contents: read
    secrets:
      TELEGRAM_RELEASES_BOT_TOKEN: ${{ secrets.TELEGRAM_RELEASES_BOT_TOKEN }}
    uses: verbanium-lab/.github/.github/workflows/release-notify.yml@REVIEWED_WORKFLOW_COMMIT
    with:
      tags: ${{ needs.release-please.outputs.tags }}
```

Callers: wiki, packages, verbanium-templates (Studio), iblonda-apps, tower-apps, verbanium-apps.
Build number, UTC date and sha7 are build metadata (footer, `/healthz`, image tags), not versions.

## Release after image (main), deprecated

Pin `release.yml` by a reviewed full commit SHA. Run it in the caller repository on every successful
main image publication. Pass the image workflow outputs and grant `contents: write` on the release job.
Map the organization secret `TELEGRAM_RELEASES_BOT_TOKEN` when Telegram notifications are configured;
organization variables `TELEGRAM_RELEASES_CHAT_ID` and `TELEGRAM_RELEASES_THREAD_ID` are read inside the
reusable workflow. When the bot token is absent, Telegram is skipped with a notice and the release still
succeeds.

Example for `verbanium-apps` (`landing`):

```yaml
  release-landing:
    name: release (landing)
    needs: image-main
    if: ${{ github.event_name == 'push' && github.ref == 'refs/heads/main' && needs.image-main.outputs.digest != '' }}
    permissions:
      contents: write
    secrets:
      TELEGRAM_RELEASES_BOT_TOKEN: ${{ secrets.TELEGRAM_RELEASES_BOT_TOKEN }}
    uses: verbanium-lab/.github/.github/workflows/release.yml@REVIEWED_WORKFLOW_COMMIT
    with:
      slug: landing
      image: ${{ needs.image-main.outputs.image }}
      digest: ${{ needs.image-main.outputs.digest }}
```

Workflow outputs (`tag`, `digest`, `image`, `slug`, `release-url`) are available to downstream jobs.
A future release-candidate auto-deploy workflow can read those outputs (and the release body digest line)
after merge; opt-in from the pull request template is not part of this workflow yet.
