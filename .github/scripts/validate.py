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
    "README.md", "CONTRIBUTING.md", ".gitignore",
    ".github/CODEOWNERS", ".github/pull_request_template.md",
    ".github/ISSUE_TEMPLATE/task.yml", ".github/ISSUE_TEMPLATE/config.yml",
    ".github/workflows/ci.yml", ".github/scripts/validate.py",
    ".github/scripts/check.sh", ".github/scripts/install-tool.sh",
    ".github/scripts/requirements.txt", ".github/tests/test_validate.py",
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, help="Copy validated owned files for a redacted secret scan")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    try:
        paths = validate_files(root)
        validate_form(load_yaml(root / ".github/ISSUE_TEMPLATE/task.yml"))
        validate_config(load_yaml(root / ".github/ISSUE_TEMPLATE/config.yml"))
        validate_pr((root / ".github/pull_request_template.md").read_text())
        validate_owners((root / ".github/CODEOWNERS").read_text())
        validate_workflow(load_yaml(root / ".github/workflows/ci.yml"))
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
