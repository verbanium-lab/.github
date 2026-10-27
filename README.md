---
title: Public organization defaults
owner: verbanium
status: active
last_reviewed: 2026-10-27
---

# .github

Organization-wide files for `verbanium-lab`: the task form, the pull request template and other defaults that
GitHub applies to every repository without its own copy.

| File | Purpose |
| --- | --- |
| `.github/ISSUE_TEMPLATE/task.yml` | "Task" form: goal, scope, done when, how to verify, product, size, risk. Adds the `agent` label and Project 2 |
| `.github/ISSUE_TEMPLATE/config.yml` | Disables blank issues |
| `.github/pull_request_template.md` | PR template: `Closes #N`, what changed, how it was verified |

GitHub applies these defaults to other repositories only while the `.github` repository is public.

The single `ci` job validates these public contracts, file boundaries and workflows on every pull
request and main push. See [validation and the public protection boundary](CONTRIBUTING.md) for the
local check and the owner-controlled activation steps.

## Reusable workflows

| Workflow | Purpose |
| --- | --- |
| [image.yml](.github/workflows/image.yml) | Native amd64/arm64 builds, main and same-repository preview publication, provenance and SBOM |
| [ghcr-cleanup.yml](.github/workflows/ghcr-cleanup.yml) | Per-package retention with protected release/deployed tags; dry-run by default |

See [caller recipes and adoption requirements](docs/reusable-workflows.md) for fixed-app wiki and
affected-matrix Tower examples, release-commit filters and token boundaries. Unresolved action pins
must be completed before adoption. This change does not migrate any app caller.
