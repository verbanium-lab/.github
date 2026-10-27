"""Rejection fixtures for the public defaults contract; no real credentials."""

import copy
import importlib.util
import os
from pathlib import Path
import random
import shlex
import string
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("validate", ROOT / ".github/scripts/validate.py")
validator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validator)


class DefaultsTests(unittest.TestCase):
    def setUp(self):
        self.form = validator.load_yaml(ROOT / ".github/ISSUE_TEMPLATE/task.yml")

    def reject(self, form, message):
        with self.assertRaisesRegex(validator.Invalid, message):
            validator.validate_form(form)

    def test_existing_form(self):
        validator.validate_form(self.form)

    def test_missing_and_optional_required_fields(self):
        for field in ("goal", "scope", "done", "verify", "product", "size", "risk"):
            with self.subTest(field=field):
                form = copy.deepcopy(self.form)
                form["body"] = [x for x in form["body"] if x.get("id") != field]
                self.reject(form, "required fields")
                form = copy.deepcopy(self.form)
                next(x for x in form["body"] if x.get("id") == field)["validations"]["required"] = False
                self.reject(form, "must be required")

    def test_duplicate_id(self):
        self.form["body"].append(copy.deepcopy(self.form["body"][1]))
        self.reject(self.form, "duplicate field ID")

    def test_schema_rejections(self):
        for mutate in (
            lambda f: f.update(description=False),
            lambda f: f.update(body={}),
            lambda f: f.update(unknown="typo"),
            lambda f: f["body"][1].update(type="textrea"),
            lambda f: f["body"][1]["validations"].update(required="true"),
            lambda f: f["body"][1]["attributes"].update(render=False),
            lambda f: f["body"][1].update(id="bad id"),
            lambda f: f["body"][1].update(id=123),
        ):
            form = copy.deepcopy(self.form)
            mutate(form)
            with self.subTest(form=form):
                self.reject(form, ".+")

    def test_dropdowns_and_defaults(self):
        for field in ("product", "size", "risk"):
            for options in (["wrong"], [True], ["low", "low"]):
                form = copy.deepcopy(self.form)
                next(x for x in form["body"] if x.get("id") == field)["attributes"]["options"] = options
                self.reject(form, ".+")
        for key, value in (("labels", []), ("projects", ["verbanium-lab/1"])):
            form = copy.deepcopy(self.form)
            form[key] = value
            self.reject(form, "default")

    def test_yaml_errors_and_duplicate_keys(self):
        for contents in ("body: [", "name: Task\nname: Other\n", "!!python/object:object {}", "x: &x [*x]"):
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "form.yml"
                path.write_text(contents)
                with self.assertRaises(validator.Invalid):
                    validator.load_yaml(path)

    def test_config_contract(self):
        config = validator.load_yaml(ROOT / ".github/ISSUE_TEMPLATE/config.yml")
        validator.validate_config(config)
        config["blank_issues_enabled"] = True
        with self.assertRaisesRegex(validator.Invalid, "blank issues"):
            validator.validate_config(config)

    def test_pr_template_and_owner_override(self):
        text = (ROOT / ".github/pull_request_template.md").read_text()
        validator.validate_pr(text)
        for heading in ("Closes #", "## What changed", "## How it was verified", "## Owner actions"):
            with self.assertRaises(validator.Invalid):
                validator.validate_pr(text.replace(heading, "missing"))
        owners = (ROOT / ".github/CODEOWNERS").read_text()
        validator.validate_owners(owners)
        for bad in ("* @other\n", owners + "\n/.github/workflows/ @other\n", "* @verbanium\n", owners + "\n*.py\n"):
            with self.assertRaises(validator.Invalid):
                validator.validate_owners(bad)

    def test_owned_file_policy(self):
        validator.validate_entry("README.md", "100644", 10)
        for path, mode, size in (
            (".env", "100644", 1),
            (".github/scripts/key.pem", "100644", 1),
            ("private/toolkit.md", "100644", 1),
            ("README.md", "120000", 1),
            ("README.md", "160000", 1),
            ("README.md", "100644", 262145),
            ("bad\nname.md", "100644", 1),
        ):
            with self.subTest(path=path, mode=mode, size=size):
                with self.assertRaises(validator.Invalid):
                    validator.validate_entry(path, mode, size)

    def test_actual_git_index_rejects_unapproved_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init", "-q", tmp], check=True)
            path = Path(tmp) / "private.txt"
            path.write_text("nonsecret fixture")
            subprocess.run(["git", "-C", tmp, "add", "private.txt"], check=True)
            with self.assertRaisesRegex(validator.Invalid, "owned file"):
                validator.validate_files(Path(tmp))

    def test_workflow_cannot_skip_or_weaken_ci(self):
        workflow = validator.load_yaml(ROOT / ".github/workflows/ci.yml")
        validator.validate_workflow(workflow)
        for mutate in (
            lambda w: w.update(on={"pull_request_target": None}),
            lambda w: w.update(on={"pull_request": {"paths": ["*.md"]}, "push": {"branches": ["main"]}}),
            lambda w: w.update(permissions={"contents": "write"}),
            lambda w: w["jobs"].update(dummy={}),
            lambda w: w["jobs"]["ci"].update({"if": "false"}),
            lambda w: w["jobs"]["ci"].update({"continue-on-error": True}),
            lambda w: w["jobs"]["ci"]["steps"][-1].update({"if": "false"}),
            lambda w: w["jobs"]["ci"]["steps"][-1].update(run="true"),
            lambda w: w["jobs"]["ci"]["steps"][0].update(uses="actions/checkout@main"),
            lambda w: w["jobs"]["ci"]["steps"][0]["with"].update({"persist-credentials": True}),
        ):
            bad = copy.deepcopy(workflow)
            mutate(bad)
            with self.subTest(workflow=bad):
                with self.assertRaises(validator.Invalid):
                    validator.validate_workflow(bad)

    def reusable(self, kind):
        name = 'image' if kind == 'image' else 'ghcr-cleanup'
        workflow = validator.load_yaml(ROOT / f'.github/workflows/{name}.yml')
        # Synthetic pinned references isolate boundary rejection tests from the offline pin gate.
        for job in workflow['jobs'].values():
            for step in job.get('steps', []):
                if 'uses' in step:
                    step['uses'] = step['uses'].split('@')[0] + '@' + 'a' * 40
        validator.validate_reusable(workflow, kind)
        return workflow

    def test_reusable_rejects_unpinned_actions_and_interpolation(self):
        for kind in ('image', 'cleanup'):
            workflow = self.reusable(kind)
            for job in workflow['jobs'].values():
                for step in job.get('steps', []):
                    if 'uses' in step:
                        original = step['uses']
                        step['uses'] = original.split('@')[0] + '@v1'
                        with self.assertRaisesRegex(validator.Invalid, 'pinned approved'):
                            validator.validate_reusable(workflow, kind)
                        step['uses'] = original
            first = next(iter(workflow['jobs'].values()))['steps'][0]
            first['run'] = 'echo "${{ inputs.untrusted }}"'
            with self.assertRaisesRegex(validator.Invalid, 'through env'):
                validator.validate_reusable(workflow, kind)

    def test_image_credential_and_event_rejections(self):
        for mutate in (
            lambda w: w.update(on={'pull_request_target': None}),
            lambda w: w.update(permissions={'contents': 'read'}),
            lambda w: w['jobs']['plan'].update(permissions={'packages': 'write'}),
            lambda w: w['jobs']['build'].update(permissions={'contents': 'read', 'packages': 'write'}),
            lambda w: w['jobs']['push'].update({'if': '${{ true }}'}),
            lambda w: w['jobs']['merge'].update(needs=['plan']),
            lambda w: w['jobs']['push']['steps'][0]['with'].update({'persist-credentials': True}),
        ):
            workflow = self.reusable('image')
            mutate(workflow)
            with self.subTest(workflow=workflow), self.assertRaises(validator.Invalid):
                validator.validate_reusable(workflow, 'image')

    def test_image_cache_and_attachment_rejections(self):
        for job, key, value in (('build', 'push', True), ('build', 'cache-to', 'type=gha'),
                                ('push', 'cache-to', 'type=gha'), ('push', 'sbom', False),
                                ('push', 'provenance', False), ('push', 'outputs', 'type=image,push=true')):
            workflow = self.reusable('image')
            step = next(s for s in workflow['jobs'][job]['steps']
                        if s.get('uses', '').startswith('docker/build-push-action@'))
            step['with'][key] = value
            with self.subTest(job=job, key=key), self.assertRaises(validator.Invalid):
                validator.validate_reusable(workflow, 'image')

    def test_image_build_args_boundary_rejections(self):
        def build_step(workflow, job):
            return next(s for s in workflow['jobs'][job]['steps']
                        if s.get('uses', '').startswith('docker/build-push-action@'))

        for mutate in (
            lambda w: build_step(w, 'build')['with'].update({'build-args': '${{ inputs.build-args }}'}),
            lambda w: build_step(w, 'push')['with'].update({'build-args': '${{ inputs.build-args }}'}),
            lambda w: build_step(w, 'push')['with'].update({'build-args': 'KEY=${{ secrets.TOKEN }}'}),
            lambda w: build_step(w, 'push')['with'].pop('build-args'),
            lambda w: w['jobs']['push']['steps'][0].update(run='echo "${{ inputs.build-args }}"'),
            lambda w: w['jobs']['push']['steps'][0].update(env={'ARGS': '${{ inputs.build-args }}'}),
            lambda w: w['jobs']['merge'].update({'if': "${{ inputs.build-args != '' }}"}),
            lambda w: w['jobs']['push']['steps'][0].update(
                {'with': {'persist-credentials': False, 'build-args': 'KEY=value'}}),
            lambda w: w['jobs']['plan']['steps'][0]['env'].update(BUILD_ARGS='KEY=value'),
            lambda w: w['jobs']['plan']['steps'][0]['env'].pop('BUILD_ARGS'),
            lambda w: w['jobs']['plan']['outputs'].pop('build-args'),
            lambda w: w[True]['workflow_call']['inputs']['build-args'].update(default='KEY=value'),
            lambda w: w[True]['workflow_call']['inputs']['build-args'].update(required=True),
            lambda w: w[True]['workflow_call']['inputs']['build-args'].update(type='boolean'),
        ):
            workflow = self.reusable('image')
            mutate(workflow)
            with self.subTest(workflow=workflow), self.assertRaises(validator.Invalid):
                validator.validate_reusable(workflow, 'image')

    def test_cleanup_default_and_retention_rejections(self):
        for mutate in (
            lambda w: w[True]['workflow_call']['inputs']['dry-run'].update(default=False),
            lambda w: w['jobs']['cleanup'].update({'if': '${{ true }}'}),
            lambda w: w['jobs']['cleanup'].update(permissions={'contents': 'write', 'packages': 'write'}),
            lambda w: w['jobs']['cleanup']['steps'][1]['with'].update({'dry-run': False}),
            lambda w: w['jobs']['cleanup']['steps'][1]['with'].update(validate=False),
            lambda w: w['jobs']['cleanup']['steps'][1]['with'].update({'older-than': '1 day'}),
            lambda w: w['jobs']['cleanup']['steps'][2]['with'].update({'exclude-tags': ''}),
        ):
            workflow = self.reusable('cleanup')
            mutate(workflow)
            with self.subTest(workflow=workflow), self.assertRaises(validator.Invalid):
                validator.validate_reusable(workflow, 'cleanup')

    def test_secret_fixture_is_rejected_and_redacted(self):
        # Construct a recognizable, deliberately fake token only in a temporary file.
        token = "ghp" + "_" + "".join(random.Random(42).choices(string.ascii_letters + string.digits, k=36))
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "example.txt").write_text("token = " + token)
            result = subprocess.run(
                ["gitleaks", "dir", "--redact=100", "--no-banner", "--verbose", "--exit-code=17", tmp],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 17, "synthetic token must fail the scan")
            self.assertNotIn(token, result.stdout + result.stderr)
            self.assertIn("REDACTED", result.stdout + result.stderr)

    def test_actual_scan_commands_reject_inline_suppression(self):
        token = "ghp" + "_" + "".join(random.Random(42).choices(string.ascii_letters + string.digits, k=36))
        commands = [shlex.split(line) for line in (ROOT / ".github/scripts/check.sh").read_text().splitlines()
                    if line.startswith("gitleaks ")]
        self.assertEqual([command[1] for command in commands], ["dir", "git"])
        with tempfile.TemporaryDirectory() as tmp:
            # No real credential or global identity is used by this disposable Git fixture.
            env = dict(os.environ, GIT_AUTHOR_NAME="CI Fixture", GIT_COMMITTER_NAME="CI Fixture",
                       GIT_AUTHOR_EMAIL="ci@example.test", GIT_COMMITTER_EMAIL="ci@example.test",
                       GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
            subprocess.run(["git", "init", "-q", tmp], check=True, env=env)
            (Path(tmp) / "example.txt").write_text("token = " + token + " # gitleaks:allow\n")
            subprocess.run(["git", "-C", tmp, "add", "example.txt"], check=True, env=env)
            subprocess.run(["git", "-C", tmp, "commit", "-qm", "synthetic fixture"], check=True, env=env)
            for command in commands:
                command = [tmp if part == "$snapshot" else part for part in command]
                with self.subTest(scan=command[1]):
                    result = subprocess.run(command, cwd=tmp, env=env, capture_output=True, text=True, check=False)
                    self.assertEqual(result.returncode, 1, "production scan must reject inline suppression")
                    self.assertNotIn(token, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
