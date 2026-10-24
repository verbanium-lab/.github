---
title: Public organization defaults
owner: verbanium
status: active
last_reviewed: 2026-10-24
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
