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
        protected_patterns=(".github/workflows/**", "**/.sops.yaml", "**/secrets/**"),
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


if __name__ == "__main__":
    unittest.main()
