#!/usr/bin/env python3
"""Validate this repository's deliberately small public-defaults profile."""

import argparse
from pathlib import Path
import re
import shutil
import subprocess
import sys

import yaml

MAX_BYTES = 256 * 1024
OWNED_FILES = {
    "README.md", "CONTRIBUTING.md", ".gitignore", "LICENSE", "SECURITY.md", "profile/README.md",
    ".github/CODEOWNERS", ".github/pull_request_template.md",
    ".github/ISSUE_TEMPLATE/task.yml", ".github/ISSUE_TEMPLATE/config.yml",
    ".github/workflows/ci.yml", ".github/scripts/validate.py",
    ".github/scripts/check.sh", ".github/scripts/install-tool.sh",
    ".github/scripts/requirements.txt", ".github/tests/test_validate.py",
    ".github/workflows/image.yml", ".github/workflows/ghcr-cleanup.yml",
    ".github/workflows/deployed-tag.yml", ".github/workflows/release.yml",
    ".github/workflows/release-notify.yml", ".github/workflows/deploy-coolify.yml",
    ".github/workflows/status.yml", ".github/workflows/release-pr-merge.yml",
    ".github/workflows/risk-low-automerge.yml",
    ".github/scripts/risk_low_automerge.py",
    ".github/scripts/retag-deployed.sh", ".github/tests/test_deploy_tag.py",
    ".github/scripts/collect_status.py", ".github/scripts/render_status.py",
    ".github/tests/test_status.py",
    ".github/tests/test_workflows.py", ".github/tests/test_risk_low_automerge.py",
    "docs/reusable-workflows.md",
    "lefthook.yml", ".github/scripts/hooks/pre-push.sh", ".github/scripts/hooks/staged.sh",
    ".github/actionlint.yaml",
}
FIELDS = {
    "goal": ("textarea", "Goal", None),
    "scope": ("textarea", "Scope", None),
    "done": ("textarea", "Done when", None),
    "verify": ("textarea", "How to verify", None),
    "product": ("dropdown", "Product", ["iblonda", "tower", "verbanium", "platform"]),
    "size": ("dropdown", "Size", ["S", "M", "L"]),
    "risk": ("dropdown", "Risk", ["low", "high"]),
}
# Retired npm scope: "@verbanium" followed by a slash, also regex-escaped. Packages use @verbanium-lab.
RETIRED_SCOPE = re.compile(r"@verbanium\\?/")


class Invalid(ValueError):
    """A contract violation. Diagnostics never include file contents."""


def require(condition, message):
    if not condition:
        raise Invalid(message)


class UniqueLoader(yaml.SafeLoader):
    """Safe YAML with duplicate mapping keys rejected, rather than overwritten."""


def unique_mapping(loader, node):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        require(isinstance(key, (str, bool)), "invalid YAML key type")
        require(key not in result, "duplicate YAML key")
        result[key] = loader.construct_object(value_node)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)


def load_yaml(path):
    require(path.stat().st_size <= MAX_BYTES, "YAML exceeds size limit")
    try:
        text = path.read_text(encoding="utf-8")
        require(not any(isinstance(t, (yaml.AliasToken, yaml.AnchorToken)) for t in yaml.scan(text)),
                "YAML anchors and aliases are outside this profile")
        return yaml.load(text, Loader=UniqueLoader)
    except (yaml.YAMLError, UnicodeError) as error:
        raise Invalid("invalid YAML syntax or encoding") from error


def mapping(value, allowed, message):
    require(isinstance(value, dict) and set(value) <= set(allowed), message)


def string(value):
    return isinstance(value, str) and bool(value.strip())


def validate_form(form):
    mapping(form, {"name", "description", "title", "labels", "projects", "body"}, "invalid form schema")
    for key in ("name", "description", "title"):
        require(string(form.get(key)), "form metadata must be strings")
    require(form["name"] == "Task", "Task name contract")
    require(form.get("labels") == ["agent"], "agent default contract")
    require(form.get("projects") == ["verbanium-lab/2"], "Project default contract")
    require(isinstance(form.get("body"), list) and form["body"], "form body must be a nonempty array")
    fields = {}
    for item in form["body"]:
        mapping(item, {"type", "id", "attributes", "validations"}, "invalid field schema")
        kind = item.get("type")
        require(kind in ("markdown", "textarea", "dropdown"), "unsupported field type")
        attrs = item.get("attributes")
        allowed = {
            "markdown": {"value"},
            "textarea": {"label", "description", "placeholder", "value", "render"},
            "dropdown": {"label", "description", "options", "multiple", "default"},
        }[kind]
        mapping(attrs, allowed, "invalid field attributes")
        for key, value in attrs.items():
            if key not in ("options", "multiple", "default"):
                require(string(value), "field attributes must be strings")
        if kind == "markdown":
            require(string(attrs.get("value")) and "validations" not in item, "invalid markdown field")
        else:
            require(string(attrs.get("label")), "field label required")
            require(isinstance(item.get("id"), str) and re.fullmatch(r"[A-Za-z0-9_-]+", item["id"]), "invalid field ID")
        if "id" in item:
            field_id = item["id"]
            require(isinstance(field_id, str) and re.fullmatch(r"[A-Za-z0-9_-]+", field_id), "invalid field ID")
            require(field_id not in fields, "duplicate field ID")
            fields[field_id] = item
        validations = item.get("validations", {})
        mapping(validations, {"required"}, "invalid field validations")
        if "required" in validations:
            require(type(validations["required"]) is bool, "required must be boolean")
        if kind == "dropdown":
            options = attrs.get("options")
            require(isinstance(options, list) and options and all(string(x) for x in options), "invalid options")
            require(len(set(options)) == len(options), "duplicate options")
            require(not any(x.casefold() in ("none", "n/a") for x in options), "reserved options")
            require(type(attrs.get("multiple", False)) is bool, "multiple must be boolean")
            if "default" in attrs:
                require(type(attrs["default"]) is int and 0 <= attrs["default"] < len(options), "invalid default index")
    require(FIELDS.keys() <= fields.keys(), "missing required fields")
    for field_id, (kind, label, options) in FIELDS.items():
        item = fields[field_id]
        require(item["type"] == kind and item["attributes"]["label"] == label, "required field contract")
        require(item.get("validations", {}).get("required") is True, "field must be required")
        if options:
            require(item["attributes"]["options"] == options, "dropdown choices contract")
            require(item["attributes"].get("multiple", False) is False, "single choice required")


def validate_config(config):
    mapping(config, {"blank_issues_enabled", "contact_links"}, "invalid issue config")
    require(config.get("blank_issues_enabled") is False, "blank issues must stay disabled")
    require(isinstance(config.get("contact_links"), list) and config["contact_links"], "contact links required")
    for link in config["contact_links"]:
        mapping(link, {"name", "url", "about"}, "invalid contact link")
        require(all(string(link.get(key)) for key in ("name", "url", "about")), "contact link strings required")
        require(link["url"].startswith("https://github.com/verbanium-lab/"), "organization contact link required")


def validate_pr(text):
    require(re.search(r"^Closes #", text, re.MULTILINE), "PR issue reference required")
    for heading in ("What changed", "How it was verified", "Owner actions"):
        require(text.splitlines().count("## " + heading) == 1, "PR heading contract")


def validate_owners(text):
    # Exact supported rules prevent later patterns silently overriding owner review.
    rules = [line.split() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    require(rules == [["*", "@verbanium"], ["/.github/CODEOWNERS", "@verbanium"]], "CODEOWNERS contract")


def validate_entry(path, mode, size):
    require(path in OWNED_FILES, "path is not an owned file in this public profile")
    require(mode in ("100644", "100755"), "only regular tracked files are allowed")
    require(size <= MAX_BYTES, "owned file exceeds 256 KiB")


def validate_files(root):
    records = subprocess.check_output(["git", "-C", str(root), "ls-files", "--stage", "-z"]).split(b"\0")
    paths = []
    for record in filter(None, records):
        metadata, raw_path = record.split(b"\t", 1)
        mode, _, stage = metadata.decode("ascii").split()
        path = raw_path.decode("utf-8")
        require(stage == "0", "unmerged index entry")
        validate_entry(path, mode, 0)  # Reject secret/private paths before reading any file.
        file = root / path
        require(file.is_file() and not file.is_symlink(), "owned file missing or symlinked")
        require(all(not p.is_symlink() for p in file.parents if p != root.parent), "symlinked parent")
        validate_entry(path, mode, file.stat().st_size)
        paths.append(path)
    require(OWNED_FILES <= set(paths), "required owned files missing from index")
    return paths


def validate_package_scope(path, text):
    if path != "pnpm-lock.yaml":
        require(not RETIRED_SCOPE.search(text), f"retired package scope in {path}; use @verbanium-lab")


def validate_workflow(workflow):
    require(isinstance(workflow, dict), "workflow must be a mapping")
    # SafeLoader uses YAML 1.1, where an unquoted `on` key is the boolean True.
    events = workflow.get("on", workflow.get(True))
    require(events == {"pull_request": None, "push": {"branches": ["main"]}}, "ordinary unfiltered PR/main events required")
    require(workflow.get("permissions") == {"contents": "read"}, "contents read permission required")
    require(set(workflow.get("jobs", {})) == {"ci"}, "one meaningful ci job required")
    job = workflow["jobs"]["ci"]
    require(isinstance(job, dict), "ci must be a job mapping")
    require(job.get("name") == "ci" and not ({"if", "permissions", "continue-on-error", "strategy"} & job.keys()), "ci context must always run")
    require(isinstance(job.get("timeout-minutes"), int) and job["timeout-minutes"] <= 15, "bounded ci timeout required")
    steps = job.get("steps", [])
    require(isinstance(steps, list) and steps, "ci steps required")
    for step in steps:
        require(isinstance(step, dict), "step must be a mapping")
        require(not ({"if", "continue-on-error"} & step.keys()), "checks must not skip or tolerate failure")
        if "uses" in step:
            require(re.fullmatch(r"actions/(checkout|setup-python)@[0-9a-f]{40}", step["uses"]), "pinned approved action required")
            if step["uses"].startswith("actions/checkout@"):
                require(step.get("with", {}).get("persist-credentials") is False, "checkout credentials must not persist")
    require(any(step.get("run") == "bash .github/scripts/check.sh" for step in steps), "real repository check required")


def validate_retag_steps(steps, source):
    """Both callers execute the shared script from their reviewed workflow commit."""
    retags = [s for s in steps if s.get("name") == "Retag the deployed digest and verify"]
    require(len(retags) == 1, "one shared retag step required")
    retag = retags[0]
    require(retag.get("run", "").strip() == "bash _github-defaults/.github/scripts/retag-deployed.sh"
            and retag.get("env") == {k: "${{ " + source + "." + k.lower() + " }}" for k in ("IMAGE", "DIGEST", "TAG")},
            "retag must use the shared script and validated outputs")
    checkouts = [s for s in steps if s.get("uses", "").startswith("actions/checkout@")]
    require(len(checkouts) == 1, "one trusted retag script checkout required")
    checkout = checkouts[0]
    require(checkout.get("with") == {
        "repository": "verbanium-lab/.github", "ref": "${{ job.workflow_sha }}", "path": "_github-defaults",
        "persist-credentials": False, "sparse-checkout-cone-mode": False,
        "sparse-checkout": ".github/scripts/retag-deployed.sh",
    }, "retag script must come from this workflow's own commit")
    require(steps.index(checkout) < steps.index(retag), "checkout must precede retag")
    require(all("if" not in s for s in steps if s.get("name") != "Log out of GHCR"),
            "deployment and retag steps must use the default success condition")
    require(any(s.get("uses", "").startswith("docker/setup-buildx-action@") for s in steps[:steps.index(retag)]),
            "Buildx setup required before retag")
    login = next((s for s in steps if s.get("name") == "Log in to GHCR"), {})
    require(login.get("env") == {"GH_TOKEN": "${{ github.token }}", "ACTOR": "${{ github.actor }}"}
            and login.get("run") == 'printf \'%s\' "$GH_TOKEN" | docker login ghcr.io --username "$ACTOR" --password-stdin'
            and steps.index(login) < steps.index(retag), "job token login required before retag")


def validate_runners(call, jobs, kind):
    """Every job runs on the caller's runner input, which defaults to the GitHub-hosted label.

    Returns the call schema without the runner inputs, so the per-workflow input contracts stay exact.
    """
    defaults = {"runs-on": "ubuntu-24.04"}
    if kind == "image":
        defaults["runs-on-arm64"] = "ubuntu-24.04-arm"
    declared = call.get("inputs") or {}
    require(isinstance(declared, dict), "reusable inputs must be a mapping")
    for name, default in defaults.items():
        field = declared.get(name)
        require(isinstance(field, dict) and field.get("type") == "string" and not field.get("required")
                and field.get("default") == default, "optional runner input with the GitHub-hosted default: " + name)
    for name, job in jobs.items():
        matrix = kind == "image" and name in ("build", "push")
        expected = "${{ matrix.runner }}" if matrix else "${{ inputs.runs-on }}"
        require(job.get("runs-on") == expected, "jobs must run on the runner input")
        # Runner inputs choose a machine only; they never reach a step.
        require("inputs.runs-on" not in repr(job.get("steps", [])) or (kind == "image" and name == "plan"),
                "runner inputs must not enter steps")
    return {**call, "inputs": {k: v for k, v in declared.items() if k not in defaults}}


def validate_reusable(workflow, kind):
    """Keep the reusable workflows inside their reviewed credential boundaries."""
    require(isinstance(workflow, dict), "workflow must be a mapping")
    events = workflow.get("on", workflow.get(True))
    require(isinstance(events, dict) and set(events) == {"workflow_call"}, "reusable workflow_call only")
    require(workflow.get("permissions") == {}, "reusable workflow permissions must be empty")
    jobs = workflow.get("jobs", {})
    require(isinstance(jobs, dict), "reusable jobs required")
    actions = {
        "image": {"actions/checkout", "docker/setup-buildx-action", "docker/build-push-action",
                  "actions/upload-artifact", "actions/download-artifact"},
        "cleanup": {"dataaxiom/ghcr-cleanup-action"},
        "deployed": {"actions/checkout", "docker/setup-buildx-action"},
        "release": {"actions/checkout"},
        "notify": set(),
        "deploy": {"actions/create-github-app-token", "tailscale/github-action", "actions/checkout", "docker/setup-buildx-action"},
        "status": {"actions/checkout", "actions/download-artifact"},
        "merge": {"actions/create-github-app-token"},
        "automerge": {"actions/checkout", "actions/create-github-app-token"},
    }[kind]
    for job in jobs.values():
        require(isinstance(job, dict) and "uses" not in job, "local reusable jobs required")
        require(type(job.get("timeout-minutes")) is int and 0 < job["timeout-minutes"] <= 30,
                "bounded reusable timeout required")
        require("continue-on-error" not in job, "reusable jobs must fail closed")
        for step in job.get("steps", []):
            require(isinstance(step, dict) and "continue-on-error" not in step, "reusable steps must fail closed")
            require("${{" not in step.get("run", ""), "expressions must enter scripts through env")
            if "uses" in step:
                action, _, ref = step["uses"].partition("@")
                require(action in actions and re.fullmatch(r"[0-9a-f]{40}", ref),
                        "pinned approved reusable action required (resolve TODO(pin))")
                if action == "actions/checkout":
                    require(step.get("with", {}).get("persist-credentials") is False,
                            "checkout credentials must not persist")
    call = events["workflow_call"]
    require(isinstance(call, dict), "reusable call schema required")
    call = validate_runners(call, jobs, kind)
    if kind == "image":
        require(set(jobs) == {"plan", "build", "push", "merge"}, "image job boundary required")
        require(not call.get("secrets"), "image workflow must not accept secrets")
        require(call.get("inputs", {}).get("push-mode", {}).get("default") == "none", "build-only default required")
        permissions = {"plan": {}, "build": {"contents": "read", "packages": "read"},
                       "push": {"contents": "read", "packages": "write"}, "merge": {"packages": "write"}}
        for name, expected in permissions.items():
            require(jobs[name].get("permissions", {}) == expected, "image job permission boundary required")
        for name, publish in (("build", "false"), ("push", "true")):
            require(jobs[name].get("needs") == "plan"
                    and jobs[name].get("if") == "${{ needs.plan.outputs.publish == '" + publish + "' }}",
                    "publication must depend on validated plan")
        require(jobs["merge"].get("needs") == ["plan", "push"], "merge must wait for platform publication")
        declared = call.get("inputs", {}).get("build-args", {})
        require(declared.get("type") == "string" and declared.get("default") == "" and not declared.get("required"),
                "optional string build-args input required")
        auth = call.get("inputs", {}).get("npm-registry-auth", {})
        require(auth.get("type") == "boolean" and auth.get("default") is False and not auth.get("required"),
                "optional default-off npm registry auth required")
        require(jobs["plan"]["outputs"].get("build-args") == "${{ steps.plan.outputs.build-args }}",
                "validated build-args output required")
        # The raw input may enter only the plan step's env; every other use is an injection path.
        for name, job in jobs.items():
            for step in job.get("steps", []):
                env = {k: v for k, v in step.get("env", {}).items() if not (name == "plan" and k == "BUILD_ARGS")}
                require("inputs.build-args" not in repr({**step, "env": env}) and "inputs.build-args" not in repr(
                    {k: v for k, v in job.items() if k != "steps"}), "raw build-args input may only enter the plan step through env")
                if step.get("env", {}).get("BUILD_ARGS") is not None:
                    require(name == "plan" and step["env"]["BUILD_ARGS"] == "${{ inputs.build-args }}",
                            "plan must read build-args through env")
                if not step.get("uses", "").startswith("docker/build-push-action@"):
                    require("build-args" not in step.get("with", {}), "build-args only on the build-push step")
        require(any(s.get("env", {}).get("BUILD_ARGS") == "${{ inputs.build-args }}" for s in jobs["plan"]["steps"]),
                "plan must validate build-args")
        for name in ("build", "push"):
            for step in jobs[name]["steps"]:
                if step.get("uses", "").startswith("docker/build-push-action@"):
                    options = step.get("with", {})
                    require(options.get("build-args") == "${{ needs.plan.outputs.build-args }}",
                            "build-args only from the validated plan output")
                    require(options.get("secrets") == "${{ inputs.npm-registry-auth && format('npm_token={0}', github.token) || '' }}"
                            and not ({"secret-envs", "secret-files"} & options.keys()),
                            "npm token must use only the opt-in BuildKit secret")
                    require(options.get("provenance") == "mode=max" and options.get("sbom") is True,
                            "image attachments required")
                    if name == "build":
                        require(options.get("push") is False and "outputs" not in options
                                and "cache-to" not in options, "build-only must not publish or save caches")
                    else:
                        require("push-by-digest=true" in options.get("outputs", ""), "digest publication required")
                        require(options.get("cache-to") == "${{ inputs.push-mode == 'main' && format("
                                "'type=registry,ref={0}:buildcache-{1},mode=max,ignore-error=true', "
                                "needs.plan.outputs.image, matrix.arch) || '' }}",
                                "only main may save the registry cache, and a cache failure must not fail publication")
                    require(options.get("cache-from")
                            == "type=registry,ref=${{ needs.plan.outputs.image }}:buildcache-${{ matrix.arch }}",
                            "registry cache of the validated image required")
        # Self-hosted runners: the plan picks runners from validated labels, fork PRs stay GitHub-hosted,
        # publication needs a main push or a dispatch from main.
        plan_step = jobs["plan"]["steps"][0]
        require(plan_step.get("env", {}).get("RUNS_ON") == "${{ inputs.runs-on }}"
                and plan_step["env"].get("RUNS_ON_ARM64") == "${{ inputs.runs-on-arm64 }}",
                "plan must read runner inputs through env")
        plan_run = plan_step.get("run", "")
        for needle in ("fork = env['EVENT'] == 'pull_request' and env['HEAD_REPOSITORY'] != env['REPOSITORY']",
                       "'ubuntu-24.04' if fork else env['RUNS_ON']",
                       "'ubuntu-24.04-arm' if fork else env['RUNS_ON_ARM64']",
                       "'Invalid runner label'",
                       "on_main = env['EVENT'] in ('push', 'workflow_dispatch') and env['REF'] == 'refs/heads/main'",
                       "require(on_main, 'Main push or dispatch required')",
                       "env['HEAD_REPOSITORY'] == env['REPOSITORY']"):
            require(needle in plan_run, "image plan runner and event guard required: " + needle)
        require(jobs["plan"].get("outputs", {}).get("fork") == "${{ steps.plan.outputs.fork }}", "fork plan output required")
        for name in ("build", "push", "merge"):
            steps = jobs[name]["steps"]
            logins = [s for s in steps if "docker login" in s.get("run", "")]
            require(len(logins) == 1 and logins[0].get("env") == {"GH_TOKEN": "${{ github.token }}", "ACTOR": "${{ github.actor }}"},
                    "one docker login with the job token only")
            require(logins[0].get("if") == ("${{ needs.plan.outputs.fork == 'false' }}" if name == "build" else None),
                    "fork pull requests never log in; publication always does")
            logout = steps[-1]
            require(logout.get("run") == "docker logout ghcr.io" and logout.get("if") == "${{ always() }}",
                    "docker logout must run last, always")
            require(not re.findall(r"secrets\.", repr(steps)), "no secrets in image jobs")
            if name != "merge":
                emulation = [s for s in steps if s.get("name") == "Require aarch64 emulation on non-arm64 runners"]
                require(len(emulation) == 1
                        and emulation[0].get("if") == "${{ matrix.arch == 'arm64' && runner.arch != 'ARM64' }}"
                        and "/proc/sys/fs/binfmt_misc/qemu-aarch64" in emulation[0].get("run", "")
                        and "exit 1" in emulation[0].get("run", ""),
                        "arm64 on a non-arm64 runner must fail closed without QEMU binfmt")
            for step in steps:
                require("if" not in step or step in logins + [steps[-1]] or step.get("name", "").startswith("Require aarch64"),
                        "image steps must not be skipped")
    elif kind == "deployed":
        require(set(jobs) == {"tag"}, "one tag job required")
        require(not call.get("secrets"), "deployed-tag workflow must not accept secrets")
        declared = call.get("inputs", {})
        require(set(declared) == {"image", "digest", "env", "release"}, "image, digest, env and release inputs required")
        for name in ("image", "digest", "env"):
            field = declared[name]
            require(field.get("type") == "string" and field.get("required") is True and "default" not in field,
                    "required string inputs without defaults")
        release = declared["release"]
        require(release.get("type") == "string" and release.get("default") == "" and not release.get("required"),
                "optional release input required")
        require(jobs["tag"].get("permissions") == {"contents": "read", "packages": "write", "deployments": "write"},
                "tag permission boundary required")
        require("if" not in jobs["tag"] and "needs" not in jobs["tag"], "tag job must always run alone")
        steps = jobs["tag"].get("steps", [])
        require(steps and all("if" not in s or s.get("name") == "Log out of GHCR" for s in steps),
                "tag steps must not be skipped")
        # Inputs may enter only the validation step's env; later steps read the validated outputs.
        for step in steps:
            used = repr({k: v for k, v in step.items() if not (step.get("id") == "plan" and k == "env")})
            require("inputs." not in used, "raw inputs may only enter the validation step through env")
        plan = next((s for s in steps if s.get("id") == "plan"), None)
        require(plan is not None and steps[0] is plan, "validation must run first")
        require({plan["env"].get(k) for k in ("IMAGE", "DIGEST", "TARGET_ENV", "RELEASE")}
                == {"${{ inputs.image }}", "${{ inputs.digest }}", "${{ inputs.env }}", "${{ inputs.release }}"},
                "validation must read inputs through env")
        validate_retag_steps(steps, "steps.plan.outputs")
        require(any(s.get("name") == "Record GitHub Deployment" for s in steps), "deployment record step required")
        deployment = next(s for s in steps if s.get("name") == "Record GitHub Deployment")
        require("gh" in deployment.get("run", "") and "deployments" in deployment.get("run", ""),
                "gh deployment API required")
    elif kind == "release":
        require(set(jobs) == {"release"}, "one release job required")
        declared = call.get("inputs", {})
        require(set(declared) == {"slug", "image", "digest", "notes"}, "slug, image, digest and notes inputs required")
        for name in ("slug", "image", "digest"):
            field = declared[name]
            require(field.get("type") == "string" and field.get("required") is True and "default" not in field,
                    "required string inputs without defaults")
        notes = declared["notes"]
        require(notes.get("type") == "string" and notes.get("default") == "" and not notes.get("required"),
                "optional notes input required")
        secrets = call.get("secrets", {})
        require(set(secrets) == {"TELEGRAM_RELEASES_BOT_TOKEN"}, "optional telegram secret only")
        require(secrets["TELEGRAM_RELEASES_BOT_TOKEN"].get("required") is False, "telegram secret must be optional")
        require(jobs["release"].get("permissions") == {"contents": "write"}, "release permission boundary required")
        require("if" not in jobs["release"] and "needs" not in jobs["release"], "release job must run alone")
        steps = jobs["release"].get("steps", [])
        plan = next((s for s in steps if s.get("id") == "plan"), None)
        require(plan is not None and steps[0] is plan, "validation must run first")
        require({plan["env"].get(k) for k in ("SLUG", "IMAGE", "DIGEST", "NOTES")}
                == {"${{ inputs.slug }}", "${{ inputs.image }}", "${{ inputs.digest }}", "${{ inputs.notes }}"},
                "validation must read inputs through env")
        checkout = next((s for s in steps if s.get("uses", "").startswith("actions/checkout@")), None)
        require(checkout is not None and checkout.get("with", {}).get("fetch-depth") == 0,
                "full history checkout required")
        for step in steps:
            if step is plan:
                continue
            used = repr(step)
            require("inputs." not in used, "raw inputs may only enter the validation step through env")
        telegram = next((s for s in steps if s.get("name") == "Notify Telegram"), None)
        require(telegram is not None, "telegram notify step required")
        require(telegram["env"].get("TELEGRAM_RELEASES_BOT_TOKEN") == "${{ secrets.TELEGRAM_RELEASES_BOT_TOKEN }}",
                "telegram token must use the optional workflow secret")
        require("gh release create" in " ".join(s.get("run", "") for s in steps), "gh release create required")
        require("curl --fail-with-body" in telegram.get("run", ""), "telegram curl boundary required")
        outputs = call.get("outputs", {})
        require(set(outputs) == {"tag", "digest", "image", "slug", "release-url"}, "release outputs required")
    elif kind == "notify":
        require(set(jobs) == {"notify"}, "one notify job required")
        declared = call.get("inputs", {})
        require(set(declared) == {"tags"}, "tags input only")
        tags = declared["tags"]
        require(tags.get("type") == "string" and tags.get("required") is True and "default" not in tags,
                "required tags input without default")
        secrets = call.get("secrets", {})
        require(set(secrets) == {"TELEGRAM_RELEASES_BOT_TOKEN"}, "optional telegram secret only")
        require(secrets["TELEGRAM_RELEASES_BOT_TOKEN"].get("required") is False, "telegram secret must be optional")
        job = jobs["notify"]
        require(job.get("permissions") == {"contents": "read"}, "notify permission boundary required")
        require("if" not in job and "needs" not in job, "notify job must run alone")
        steps = job.get("steps", [])
        require(steps and steps[0].get("id") == "plan" and steps[0]["env"].get("TAGS") == "${{ inputs.tags }}",
                "validation must run first and read tags through env")
        for step in steps[1:]:
            require("inputs." not in repr(step), "raw inputs may only enter the validation step through env")
            require("uses" not in step, "notify uses no actions")
        telegram = next((s for s in steps if s.get("name") == "Notify Telegram"), None)
        require(telegram is not None and telegram["env"].get("TELEGRAM_RELEASES_BOT_TOKEN")
                == "${{ secrets.TELEGRAM_RELEASES_BOT_TOKEN }}", "telegram token must use the optional workflow secret")
        require("gh release create" not in " ".join(s.get("run", "") for s in steps), "notify never creates releases")
    elif kind == "deploy":
        require(set(jobs) == {"plan", "record", "deploy"}, "plan, record and deploy jobs required")
        secrets = call.get("secrets", {})
        require(set(secrets) == {"VERBANIUM_CI_PRIVATE_KEY", "TS_OAUTH_CLIENT_ID", "TS_AUDIENCE", "COOLIFY_DEPLOY_TOKEN"}
                and all(field.get("required") is False for field in secrets.values()),
                "reviewed optional secrets only; deploy secrets come from the caller's environment")
        declared = call.get("inputs", {})
        required = {"app", "image", "digest", "image-tags", "sha7", "coolify-app-uuid", "site-url", "verify-url", "client-id"}
        optional = {"build-number": "", "verify-json-field": "", "environment": "production", "env": "prod"}
        require(set(declared) == required | set(optional), "reviewed deploy inputs only")
        for name in required:
            field = declared[name]
            require(field.get("type") == "string" and field.get("required") is True and "default" not in field,
                    "required string inputs without defaults")
        for name, default in optional.items():
            field = declared[name]
            require(field.get("type") == "string" and not field.get("required") and field.get("default") == default,
                    "optional deploy inputs with reviewed defaults")
        require(jobs["plan"].get("permissions") == {} and "needs" not in jobs["plan"] and "if" not in jobs["plan"]
                and "environment" not in jobs["plan"], "plan runs first without permissions or secrets")
        require(jobs["record"].get("permissions") == {} and jobs["record"].get("needs") == "plan",
                "record uses only the App token")
        require(jobs["record"].get("environment") == {"name": "${{ needs.plan.outputs.environment }}", "deployment": False},
                "record runs in the caller environment without an Actions deployment")
        require(jobs["deploy"].get("permissions") == {"contents": "read", "id-token": "write", "packages": "write"}
                and jobs["deploy"].get("needs") == ["plan", "record"] and "if" not in jobs["deploy"],
                "deploy permission boundary required")
        require(jobs["deploy"].get("environment", {}).get("name") == "${{ needs.plan.outputs.environment }}",
                "deploy runs in the caller environment")
        require(jobs["deploy"].get("concurrency", {}).get("cancel-in-progress") is False, "deploys must queue, never cancel")
        require(jobs["deploy"].get("concurrency", {}).get("group")
                == "deploy-${{ needs.plan.outputs.app }}-${{ needs.plan.outputs.environment }}",
                "deploy and retag must share the app/environment lock")
        steps = jobs["plan"].get("steps", [])
        require(len(steps) == 1 and steps[0].get("id") == "plan", "one validation step required")
        expected = {"APP": "app", "IMAGE": "image", "DIGEST": "digest", "TAGS": "image-tags", "SHA7": "sha7",
                    "BUILD_NUMBER": "build-number", "APP_UUID": "coolify-app-uuid", "SITE_URL": "site-url",
                    "VERIFY_URL": "verify-url", "VERIFY_FIELD": "verify-json-field", "TARGET_ENV": "environment",
                    "CLIENT_ID": "client-id", "TAG_ENV": "env"}
        require(steps[0].get("env") == {k: "${{ inputs." + v + " }}" for k, v in expected.items()},
                "validation must read every input through env")
        plan_run = steps[0].get("run", "")
        require('"$GITHUB_EVENT_NAME" == push && "$GITHUB_REF" == refs/heads/main' in plan_run
                and '"$TAGS" == "$IMAGE:sha-$GITHUB_SHA"' in plan_run and '"$SHA7" == "${GITHUB_SHA::7}"' in plan_run,
                "the plan must bind the deploy to this main commit")
        require('"$TAG_ENV" =~ ^(dev|rc|prod)$' in plan_run
                and 'production:prod|release-candidate:rc|dev:dev)' in plan_run
                and 'echo "tag=deployed-$TAG_ENV"' in plan_run
                and jobs["plan"].get("outputs", {}).get("tag") == "${{ steps.plan.outputs.tag }}",
                "the plan must bind the protection tag to the target environment")
        allowed_secrets = {"record": {"VERBANIUM_CI_PRIVATE_KEY"}, "deploy": {"TS_OAUTH_CLIENT_ID", "TS_AUDIENCE", "COOLIFY_DEPLOY_TOKEN"}}
        for name, job in jobs.items():
            text = repr({k: v for k, v in job.items() if k != "runs-on"})
            if name != "plan":
                require("inputs." not in text, "raw inputs may only enter the plan step through env")
            used = set(re.findall(r"secrets\.([A-Z0-9_]+)", text))
            require(used <= allowed_secrets.get(name, set()), "secrets only in the record and deploy jobs")
        guard = (jobs["deploy"].get("steps") or [{}])[0]
        deploy_secrets = ("TS_OAUTH_CLIENT_ID", "TS_AUDIENCE", "COOLIFY_DEPLOY_TOKEN")
        require(guard.get("env") == {n: "${{ secrets." + n + " }}" for n in deploy_secrets}
                and all(n in guard.get("run", "") for n in deploy_secrets)
                and "secrets: inherit" in guard.get("run", "") and "exit 1" in guard.get("run", ""),
                "the deploy job must first fail on empty environment secrets (callers need secrets: inherit)")
        deploy_run = " ".join(s.get("run", "") for s in jobs["deploy"]["steps"])
        require("COOLIFY_URL/api/v1/applications/$APP_UUID" in deploy_run and "api/v1/deploy?uuid=$APP_UUID" in deploy_run,
                "Coolify API deploy required")
        require(any(s.get("uses", "").startswith("tailscale/github-action@") and s.get("with", {}).get("tags") == "tag:ci"
                    for s in jobs["deploy"]["steps"]), "ephemeral tag:ci tailnet node required")
        deploy_steps = jobs["deploy"]["steps"]
        validate_retag_steps(deploy_steps, "needs.plan.outputs")
        verify = next((s for s in deploy_steps if s.get("name") == "Deploy the digest in Coolify and wait for the live build"), {})
        require('if live; then' in verify.get("run", "")
                and '"$status" == finished' in verify.get("run", "")
                and 'does not report $SHA7 five minutes after the deploy finished"; exit 1' in verify.get("run", ""),
                "Coolify and live commit verification must fail closed")
        checkout = next(s for s in deploy_steps if s.get("uses", "").startswith("actions/checkout@"))
        require(deploy_steps.index(verify) < deploy_steps.index(checkout),
                "live verification must precede checkout and retag")
    elif kind == "merge":
        require(set(jobs) == {"merge"}, "one merge job required")
        secrets = call.get("secrets", {})
        require(set(secrets) == {"VERBANIUM_CI_PRIVATE_KEY"} and secrets["VERBANIUM_CI_PRIVATE_KEY"].get("required") is True,
                "the verbanium-ci key is the only secret")
        declared = call.get("inputs", {})
        require(set(declared) == {"client-id"} and declared["client-id"].get("type") == "string"
                and declared["client-id"].get("required") is True, "client-id input only")
        job = jobs["merge"]
        require(job.get("permissions") == {"checks": "read"} and "needs" not in job and "if" not in job,
                "merge job token reads checks only (the merge uses the App token) and always runs")
        require(job.get("environment") == {"name": "production", "deployment": False},
                "the App key stays behind the production environment")
        steps = job.get("steps", [])
        require(steps and steps[0].get("id") == "plan" and steps[0].get("env") == {"CLIENT_ID": "${{ inputs.client-id }}"},
                "validation must run first and read client-id through env")
        require('("$GITHUB_EVENT_NAME" == push || "$GITHUB_EVENT_NAME" == workflow_run) && "$GITHUB_REF" == refs/heads/main'
                in steps[0].get("run", ""), "main pushes or CI workflow_run on main only")
        for step in steps[1:]:
            require("inputs." not in repr(step), "raw inputs may only enter the validation step through env")
        token = next((s for s in steps if s.get("uses", "").startswith("actions/create-github-app-token@")), None)
        require(token is not None and set(token.get("with", {})) == {"client-id", "private-key", "permission-contents",
                "permission-pull-requests"}, "App token scoped to contents and pull requests")
        run = " ".join(s.get("run", "") for s in steps)
        for needle in ("release-please--branches--main", "verbanium-ci[bot]", '"merge_method=squash"', 'f"sha={sha}"',
                       'REQUIRED_CHECK = "ci"', "ACTIONS_APP_ID = 15368", '"conclusion") != "success"',
                       "MANIFEST_LINE", "VERSION_LINE"):
            require(needle in run, "release PR guard required: " + needle)
        require("gh pr review" not in run and "--admin" not in run and "--auto" not in run,
                "no approvals, admin merges or auto-merge (auto-merge does not apply the ruleset bypass)")
        merge_step = steps[-1]
        require(merge_step.get("env") == {"GH_TOKEN": "${{ steps.app.outputs.token }}", "CHECKS_TOKEN": "${{ github.token }}"},
                "App token for the merge, job token only for reading checks")
    elif kind == "automerge":
        require(set(jobs) == {"gate"}, "one gate job required")
        secrets = call.get("secrets", {})
        require(set(secrets) == {"VERBANIUM_CI_PRIVATE_KEY"} and secrets["VERBANIUM_CI_PRIVATE_KEY"].get("required") is True,
                "the verbanium-ci key is the only secret")
        declared = call.get("inputs", {})
        required = {"client-id", "gate-ref", "pr-number"}
        optional = {
            "required-check": ("ci", "string"),
            "ai-review-check": ("ai-review", "string"),
            "low-risk-label": ("risk:low", "string"),
            "high-risk-label": ("risk:high", "string"),
            "allowed-authors": ("", "string"),
            "agent-bot-id": ("", "string"),
            "actions-app-id": ("15368", "string"),
            "ai-review-actions-app-id": ("15368", "string"),
            "base-branch": ("main", "string"),
            "protected-paths": ("", "string"),
        }
        require(set(declared) == required | set(optional), "reviewed automerge inputs only")
        for name in required:
            field = declared[name]
            require(field.get("type") == "string" and field.get("required") is True and "default" not in field,
                    "required string inputs without defaults")
        for name, (default, kind_name) in optional.items():
            field = declared[name]
            require(field.get("type") == kind_name and not field.get("required") and field.get("default") == default,
                    "optional automerge input default: " + name)
        gate_ref = declared["gate-ref"]
        require("workflow_sha" not in repr(declared), "gate-ref must not default to workflow_sha")
        job = jobs["gate"]
        require(job.get("permissions") == {"checks": "read", "contents": "read"} and "needs" not in job and "if" not in job,
                "gate job token reads checks and gate scripts; always runs")
        require(job.get("environment") == {"name": "production", "deployment": False},
                "the App key stays behind the production environment")
        steps = job.get("steps", [])
        plan = steps[0] if steps else {}
        expected_plan = {
            "CLIENT_ID": "${{ inputs.client-id }}",
            "GATE_REF": "${{ inputs.gate-ref }}",
            "PR_NUMBER": "${{ inputs.pr-number }}",
            "REQUIRED_CHECK": "${{ inputs.required-check }}",
            "AI_REVIEW_CHECK": "${{ inputs.ai-review-check }}",
            "LOW_RISK_LABEL": "${{ inputs.low-risk-label }}",
            "HIGH_RISK_LABEL": "${{ inputs.high-risk-label }}",
            "PROTECTED_PATHS": "${{ inputs.protected-paths }}",
            "ALLOWED_AUTHORS": "${{ inputs.allowed-authors }}",
            "AGENT_BOT_ID": "${{ inputs.agent-bot-id }}",
            "ACTIONS_APP_ID": "${{ inputs.actions-app-id }}",
            "AI_REVIEW_ACTIONS_APP_ID": "${{ inputs.ai-review-actions-app-id }}",
            "BASE_BRANCH": "${{ inputs.base-branch }}",
        }
        require(plan.get("id") == "plan" and plan.get("env") == expected_plan,
                "validation must read every input through env in the plan step")
        for step in steps[1:]:
            require("inputs." not in repr(step), "raw inputs may only enter the validation step through env")
        checkout = next((s for s in steps if s.get("uses", "").startswith("actions/checkout@")), None)
        require(checkout is not None and checkout.get("with", {}).get("repository") == "verbanium-lab/.github"
                and checkout.get("with", {}).get("ref") == "${{ steps.plan.outputs.gate-ref }}"
                and "risk_low_automerge.py" in checkout.get("with", {}).get("sparse-checkout", ""),
                "gate scripts sparse checkout required")
        plan_run = plan.get("run", "")
        require('"$GATE_REF" =~ ^[0-9a-f]{40}$' in plan_run, "gate-ref sha validation required in plan")
        require("gate-ref=$GATE_REF" in plan_run, "gate-ref plan output required")
        token = next((s for s in steps if s.get("uses", "").startswith("actions/create-github-app-token@")), None)
        require(token is not None and token.get("with", {}).get("permission-contents") == "write"
                and token.get("with", {}).get("permission-pull-requests") == "write",
                "App token scoped to write contents and pull requests (auto-merge GraphQL)")
        run = " ".join(s.get("run", "") for s in steps)
        for needle in (
            "from risk_low_automerge import decide",
            "neutralize_actions_log_text",
            "paths_from_pr_files",
            '"--method", "PUT"',
            "pulls/{number}/merge",
            '"merge_method=squash"',
            'f"sha={sha}"',
            "CHANGES_REQUESTED",
            "::error::PR #",
            "merge refused",
            "HIGH_RISK_LABEL",
            "LOW_RISK_LABEL",
            "AI_REVIEW_CHECK",
            "AI_REVIEW_ACTIONS_APP_ID",
        ):
            require(needle in run, "automerge guard required: " + needle)
        require("--auto" not in run and "--admin" not in run and "--disable-auto" not in run,
                "direct squash merge pinned to the head SHA; no GitHub auto-merge (it ignores the App bypass) or admin")
        require("release-please--branches--main" not in run, "release PR merge boundary must not overlap")
        script = (Path(__file__).resolve().parent / "risk_low_automerge.py").read_text(encoding="utf-8")
        for needle in ("MINIMUM_PROTECTED_PATTERNS", ".github/**", "pnpm-lock.yaml", "Dockerfile*"):
            require(needle in script, "minimum protected paths baked into gate script: " + needle)
        gate_step = steps[-1]
        require(gate_step.get("env") == {"GH_TOKEN": "${{ steps.app.outputs.token }}", "CHECKS_TOKEN": "${{ github.token }}"}
                or set(gate_step.get("env", {})) >= {"GH_TOKEN", "CHECKS_TOKEN"},
                "App token for merge, job token only for reading checks")
    elif kind == "status":

        require(set(jobs) == {"status"}, "one status job required")
        require(not call.get("secrets"), "status workflow must not accept secrets")
        declared = call.get("inputs", {})
        require(set(declared) == {"apps", "coverage-artifact", "commit-readme"},
                "apps, coverage-artifact and commit-readme inputs required")
        commit = declared.get("commit-readme", {})
        require(commit.get("type") == "boolean" and commit.get("default") is False,
                "commit-readme must be a boolean that defaults to false (option C)")
        apps = declared["apps"]
        require(apps.get("type") == "string" and apps.get("required") is True and "default" not in apps,
                "required apps JSON input required")
        coverage = declared["coverage-artifact"]
        require(coverage.get("type") == "string" and coverage.get("default") == "" and not coverage.get("required"),
                "optional coverage-artifact input required")
        require(jobs["status"].get("permissions") == {"contents": "write", "packages": "read", "deployments": "read"},
                "status permission boundary required")
        steps = jobs["status"].get("steps", [])
        require(steps and all("continue-on-error" not in s for s in steps), "status steps must fail closed")
        script_checkout = next((s for s in steps if s.get("name") == "Check out status scripts"), None)
        require(script_checkout is not None and script_checkout.get("with", {}).get("repository") == "verbanium-lab/.github",
                "status scripts checkout required")
        require(script_checkout.get("with", {}).get("ref") == "${{ job.workflow_sha }}",
                "status scripts must be checked out at this workflow's own commit (job.workflow_sha)")
        require("collect_status.py" in repr(steps) and "render_status.py" in repr(steps),
                "status data and render scripts required")
        commit = next((s for s in steps if s.get("name") == "Commit status update"), None)
        run = commit.get("run", "") if commit else ""
        require(commit is not None and "chore(status): update README status [skip ci]" in run
                and "Status-Update: automated" in run, "fixed status commit message required")
        require("createCommitOnBranch" in run, "signed commit through createCommitOnBranch required")
        require("git push" not in run, "status commit must not use git push")
        require("gh', 'pr', 'create'" in run, "status PR fallback required")
    else:
        require(set(jobs) == {"cleanup"}, "one cleanup job required")
        require(call.get("inputs", {}).get("dry-run", {}).get("default") is True, "cleanup dry-run default required")
        require(jobs["cleanup"].get("permissions") == {"packages": "write"}, "cleanup permission boundary required")
        guard = jobs["cleanup"].get("if", "")
        require("github.ref == 'refs/heads/main'" in guard and "github.event_name == 'schedule'" in guard
                and "github.event_name == 'workflow_dispatch'" in guard, "main-only cleanup events required")
        passes = [s for s in jobs["cleanup"]["steps"] if "uses" in s]
        require(len(passes) == 2, "two age-specific cleanup passes required")
        for step, age in zip(passes, ("14 days", "30 days")):
            options = step.get("with", {})
            require(options.get("dry-run") == "${{ inputs.dry-run }}" and options.get("validate") is True,
                    "cleanup must honor dry-run and validate")
            require(options.get("older-than") == age and options.get("exclude-tags")
                    and options.get("use-regex") is True, "cleanup retention boundary required")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, help="Copy validated owned files for a redacted secret scan")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    try:
        paths = validate_files(root)
        for path in paths:
            validate_package_scope(path, (root / path).read_text(encoding="utf-8"))
        validate_form(load_yaml(root / ".github/ISSUE_TEMPLATE/task.yml"))
        validate_config(load_yaml(root / ".github/ISSUE_TEMPLATE/config.yml"))
        validate_pr((root / ".github/pull_request_template.md").read_text())
        validate_owners((root / ".github/CODEOWNERS").read_text())
        validate_workflow(load_yaml(root / ".github/workflows/ci.yml"))
        validate_reusable(load_yaml(root / ".github/workflows/image.yml"), "image")
        validate_reusable(load_yaml(root / ".github/workflows/ghcr-cleanup.yml"), "cleanup")
        validate_reusable(load_yaml(root / ".github/workflows/deployed-tag.yml"), "deployed")
        validate_reusable(load_yaml(root / ".github/workflows/release.yml"), "release")
        validate_reusable(load_yaml(root / ".github/workflows/release-notify.yml"), "notify")
        validate_reusable(load_yaml(root / ".github/workflows/deploy-coolify.yml"), "deploy")
        validate_reusable(load_yaml(root / ".github/workflows/status.yml"), "status")
        validate_reusable(load_yaml(root / ".github/workflows/release-pr-merge.yml"), "merge")
        validate_reusable(load_yaml(root / ".github/workflows/risk-low-automerge.yml"), "automerge")
        if args.snapshot:
            for path in paths:
                target = args.snapshot / path
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(root / path, target)
        print("PASS: form, config, PR, ownership, workflow and owned-file contracts")
    except (Invalid, OSError, UnicodeError, TypeError) as error:
        print(f"FAIL: {type(error).__name__}: contract validation failed" if not isinstance(error, Invalid) else f"FAIL: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
