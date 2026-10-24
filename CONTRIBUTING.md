---
title: Validate public organization defaults
owner: verbanium
status: draft
last_reviewed: 2026-10-24
---

# Validate public organization defaults

The `ci` job runs for every ordinary pull request and every push to `main`, including documentation
changes. There are no path filters, privileged pull-request events, secrets, services or paid review
jobs. The workflow reads only this public repository with `contents: read` and disables checkout
credential persistence. The owner controls required-check activation and Code Owner review.

## Local check

Use CPython 3.12.12 on Linux x86_64 (the CI runtime), or CPython 3.11 on macOS arm64. Create a virtual
environment outside the checkout, activate it, and run from the repository root:

```bash
python -m pip install --require-hashes --only-binary=:all: --no-deps -r .github/scripts/requirements.txt
bash .github/scripts/install-tool.sh actionlint /tmp/defaults-bin
bash .github/scripts/install-tool.sh zizmor /tmp/defaults-bin
bash .github/scripts/install-tool.sh gitleaks /tmp/defaults-bin
export PATH="/tmp/defaults-bin:$PATH"
bash .github/scripts/check.sh
```

Stage intended new files before checking: the validator uses Git's tracked file inventory. It fails
closed on unapproved paths, missing required files, symlinks, submodules, unresolved index entries,
or a file larger than 256 KiB. The allowlist covers only the public defaults, their CI, tests and
documentation. New owned files require an explicit allowlist change reviewed by the owner. Do not
copy private toolkit content, environment files, credentials or local caches into this repository.
No private toolkit dependency is needed to run these checks.

The YAML validator rejects duplicate keys, unsafe tags, aliases, invalid types and unsupported keys.
Its schema intentionally supports this Task form's `markdown`, `textarea` and `dropdown` profile;
adding another GitHub field type requires extending the validator and its rejection tests. It checks
unique IDs, required Goal/Scope/Done when/How to verify inputs, exact Product/Size/Risk choices, and
the agent label and Project 2 defaults. These match GitHub's documented
[issue form syntax](https://docs.github.com/en/communities/using-templates-to-encourage-useful-issues-and-pull-requests/syntax-for-issue-forms)
and [form schema](https://docs.github.com/en/communities/using-templates-to-encourage-useful-issues-and-pull-requests/syntax-for-githubs-form-schema).
GitHub still requires the issue creator to have write access to the target project; a form default
cannot grant that access.

The PR template must retain its issue reference and change/verification/owner sections. CODEOWNERS
must retain the current owner catch-all and explicit self-ownership rule; later override patterns
are rejected. Actionlint and offline zizmor inspect workflows. Gitleaks scans a temporary copy of
validated tracked files plus this repository's Git history, with findings fully redacted. It does
not honor inline suppression comments or walk other checkouts or local dependency stores. Secret detection is heuristic; a green scan
does not establish that arbitrary private text is safe to publish.

## Verified tool inputs

Pins were checked against vendor release metadata on 2026-10-24. The installer verifies the vendor
GitHub release asset SHA-256 before extracting the named executable. Changing a tool version requires
verifying the new asset digest for each supported platform and rerunning the full check.

| Input | Primary source |
| --- | --- |
| checkout v4.3.1 commit | [actions/checkout tag](https://github.com/actions/checkout/tree/v4.3.1) |
| setup-python v5.6.0 commit | [actions/setup-python tag](https://github.com/actions/setup-python/tree/v5.6.0) |
| Python 3.12.12 runtime | [vendor build](https://github.com/actions/python-versions/releases/tag/3.12.12-18393146713) |
| PyYAML 6.0.3 wheel hashes | [PyPI release](https://pypi.org/project/PyYAML/6.0.3/#files) |
| actionlint 1.7.12 archive hashes | [vendor release](https://github.com/rhysd/actionlint/releases/tag/v1.7.12) |
| zizmor 1.31.1 archive hashes | [vendor release](https://github.com/zizmorcore/zizmor/releases/tag/v1.31.1) |
| gitleaks 8.30.1 archive hashes | [vendor release](https://github.com/gitleaks/gitleaks/releases/tag/v8.30.1) |

Ubuntu 24.04 runner images and their built-in operating-system tools remain GitHub-managed inputs.
The setup action verifies/downloads its runtime through the vendor manifest; it is not a locally
vendored Python distribution. Revisit the pins when vendor security fixes or runtime support change.

These existing standalone tools cover YAML parsing (PyYAML), workflow correctness (actionlint),
workflow security (zizmor) and redacted credential detection (gitleaks). They run locally or in GitHub
Actions with no authentication, server edition, event API or native notifications. GitHub Actions
owns the check result and the owner's existing notification preferences. The only custom gap is the
organization's form, ownership and public-file contract; a new service would add maintenance without
covering that contract. Upgrades change the recorded pins, and removing this workflow removes the
tool dependencies without migrating stored data.

## Public protection boundary

This repository must stay public so other repositories can inherit the defaults. GitHub documents
[push rulesets for private and internal repositories](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/creating-rulesets-for-a-repository).
Those push restrictions are not available for this public defaults repository. The owned-path and
size checks here run at merge time once the owner requires `ci`; they cannot prevent every feature
branch push, prevent a fork from publishing content, or replace secret push protection. Required
checks and owner review depend on the owner's ruleset configuration and its bypass policy.

An owner-reviewed PR and the genuine current-main `ci` result are prerequisites for parent issue 6's
activation work. This change does not alter visibility, permissions, branch rules, review gates,
billing or repository settings. Validate the first real PR run before merge and the resulting main
run after the owner's squash merge; do not test enforcement with an unsafe direct main push.
