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


def run(name, *, concl="success", status="completed", app_id=ACTIONS_ID):
    return {"name": name, "status": status, "conclusion": concl, "completed_at": "2026-01-01", "id": 1,
            "app": {"id": app_id}}


def base_kwargs(**overrides):
    data = dict(
        label_names=frozenset({"risk:low"}),
        draft=False,
        author_login="verbanium-dev-agent[bot]",
        author_type="Bot",
        base_branch="main",
        pr_base="main",
        head_repo_full_name="verbanium-lab/wiki",
        caller_repo="verbanium-lab/wiki",
        changed_files=("src/readme.md",),
        check_runs=(run("ci"), run("ai-review")),
        low_risk_label="risk:low",
        high_risk_label="risk:high",
        protected_patterns=(".github/workflows/**",),
        allowed_authors=frozenset(),
        agent_author_regex=r".*\[bot\]$",
        required_check="ci",
        ai_review_check="ai-review",
        actions_app_id=ACTIONS_ID,
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

    def test_author_allowlist(self):
        self.assert_enable(author_login="human", author_type="User", allowed_authors=frozenset({"human"}))

    def test_non_agent_author_rejected(self):
        self.assert_skip("not allowed", author_login="human", author_type="User")

    def test_failed_ci(self):
        self.assert_skip("ci", check_runs=(run("ci", concl="failure"), run("ai-review")))

    def test_missing_ai_review(self):
        self.assert_skip("ai-review", check_runs=(run("ci"),))

    def test_wrong_base_branch(self):
        self.assert_skip("base branch", pr_base="develop")

    def test_fork_head_rejected(self):
        self.assert_skip("head is not", head_repo_full_name="other/repo")


class HelperTests(unittest.TestCase):
    def test_parse_patterns_and_protected(self):
        patterns = gate.parse_patterns(".github/workflows/**\nREADME.md")
        self.assertEqual(patterns, (".github/workflows/**", "README.md"))
        self.assertEqual(
            gate.path_matches_protected(".github/workflows/ci.yml", patterns),
            ".github/workflows/**",
        )
        self.assertEqual(gate.path_matches_protected("README.md", patterns), "README.md")


if __name__ == "__main__":
    unittest.main()
