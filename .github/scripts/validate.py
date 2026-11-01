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
    ".github/workflows/release-notify.yml",
    ".github/workflows/status.yml",
    ".github/scripts/collect_status.py", ".github/scripts/render_status.py",
    ".github/tests/test_status.py",
    ".github/tests/test_workflows.py", "docs/reusable-workflows.md",
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
        "deployed": {"docker/setup-buildx-action"},
        "release": {"actions/checkout"},
        "notify": set(),
        "status": {"actions/checkout", "actions/download-artifact"},
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
                        require(options.get("cache-to", "").startswith("${{ inputs.push-mode == 'main' &&"),
                                "only main may save publishing caches")
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
        retag = " ".join(s.get("run", "") for s in steps)
        require("docker buildx imagetools create --tag" in retag and "imagetools inspect" in retag
                and "docker pull" not in retag, "manifest retag and verification required")
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
    elif kind == "status":
        require(set(jobs) == {"status"}, "one status job required")
        require(not call.get("secrets"), "status workflow must not accept secrets")
        declared = call.get("inputs", {})
        require(set(declared) == {"apps", "coverage-artifact"}, "apps and coverage-artifact inputs required")
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
        validate_reusable(load_yaml(root / ".github/workflows/status.yml"), "status")
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
