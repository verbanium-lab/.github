#!/usr/bin/env python3
"""Pure decision logic for the risk:low auto-merge gate (unit-tested offline)."""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import sys
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence


@dataclass(frozen=True)
class Decision:
    """Whether platform auto-merge may be enabled and why not."""

    enable_auto_merge: bool
    reasons: tuple[str, ...]


def parse_list(text: str, *, sep: str = ",") -> frozenset[str]:
    """Turn a comma- or newline-separated login list into a set."""
    items = [part.strip() for chunk in text.splitlines() for part in chunk.split(sep)]
    return frozenset(item for item in items if item)


def parse_patterns(text: str) -> tuple[str, ...]:
    """One glob pattern per non-empty line."""
    return tuple(line.strip() for line in text.splitlines() if line.strip())


def path_matches_protected(filename: str, patterns: Sequence[str]) -> str | None:
    """Return the first matching pattern, if any."""
    for pattern in patterns:
        if fnmatch.fnmatch(filename, pattern) or fnmatch.fnmatchcase(filename, pattern):
            return pattern
    return None


def author_allowed(
    login: str,
    author_type: str,
    *,
    allowed_authors: frozenset[str],
    agent_author_regex: str,
) -> bool:
    if login in allowed_authors:
        return True
    if author_type == "Bot" and re.fullmatch(agent_author_regex, login or ""):
        return True
    return False


def latest_check_success(
    check_runs: Iterable[Mapping[str, Any]],
    *,
    check_name: str,
    actions_app_id: int,
) -> tuple[bool, str]:
    """True when a completed GitHub Actions check run with the given name succeeded."""
    matches = [
        run
        for run in check_runs
        if run.get("name") == check_name
        and (run.get("app") or {}).get("id") == actions_app_id
    ]
    if not matches:
        return False, f"check `{check_name}` missing"
    latest = max(matches, key=lambda run: (run.get("completed_at") or "", run.get("id") or 0))
    status = latest.get("status")
    conclusion = latest.get("conclusion")
    if status != "completed":
        return False, f"check `{check_name}` status is {status!r}"
    if conclusion != "success":
        return False, f"check `{check_name}` conclusion is {conclusion!r}"
    return True, ""


def decide(
    *,
    label_names: frozenset[str],
    draft: bool,
    author_login: str,
    author_type: str,
    base_branch: str,
    pr_base: str,
    head_repo_full_name: str,
    caller_repo: str,
    changed_files: Sequence[str],
    check_runs: Sequence[Mapping[str, Any]],
    low_risk_label: str,
    high_risk_label: str,
    protected_patterns: Sequence[str],
    allowed_authors: frozenset[str],
    agent_author_regex: str,
    required_check: str,
    ai_review_check: str,
    actions_app_id: int,
) -> Decision:
    """Return whether gh pr merge --auto --squash may be enabled."""
    reasons: list[str] = []

    if high_risk_label in label_names:
        reasons.append(f"label {high_risk_label}")
    if low_risk_label not in label_names:
        reasons.append(f"missing label {low_risk_label}")

    if draft:
        reasons.append("draft pull request")
    if pr_base != base_branch:
        reasons.append(f"base branch {pr_base!r} is not {base_branch!r}")
    if head_repo_full_name != caller_repo:
        reasons.append("head is not in this repository")

    if not author_allowed(
        author_login,
        author_type,
        allowed_authors=allowed_authors,
        agent_author_regex=agent_author_regex,
    ):
        reasons.append(f"author {author_login!r} is not allowed")

    for filename in changed_files:
        pattern = path_matches_protected(filename, protected_patterns)
        if pattern is not None:
            reasons.append(f"protected path {filename!r} matches {pattern!r}")

    for check_name in (required_check, ai_review_check):
        ok, detail = latest_check_success(
            check_runs, check_name=check_name, actions_app_id=actions_app_id
        )
        if not ok:
            reasons.append(detail)

    return Decision(enable_auto_merge=not reasons, reasons=tuple(reasons))


def decide_from_payload(payload: Mapping[str, Any]) -> Decision:
    """Decode a JSON payload produced by the workflow step."""
    label_names = frozenset(payload.get("label_names") or ())
    return decide(
        label_names=label_names,
        draft=bool(payload.get("draft")),
        author_login=str(payload.get("author_login") or ""),
        author_type=str(payload.get("author_type") or ""),
        base_branch=str(payload["base_branch"]),
        pr_base=str(payload.get("pr_base") or ""),
        head_repo_full_name=str(payload.get("head_repo") or ""),
        caller_repo=str(payload["caller_repo"]),
        changed_files=tuple(payload.get("changed_files") or ()),
        check_runs=tuple(payload.get("check_runs") or ()),
        low_risk_label=str(payload["low_risk_label"]),
        high_risk_label=str(payload["high_risk_label"]),
        protected_patterns=parse_patterns(str(payload.get("protected_paths") or "")),
        allowed_authors=parse_list(str(payload.get("allowed_authors") or "")),
        agent_author_regex=str(payload["agent_author_regex"]),
        required_check=str(payload["required_check"]),
        ai_review_check=str(payload["ai_review_check"]),
        actions_app_id=int(payload["actions_app_id"]),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload", help="Path to JSON payload; omit to read stdin")
    parser.add_argument("--json", action="store_true", help="Print the decision as JSON")
    args = parser.parse_args(argv)
    raw = (open(args.payload, encoding="utf-8") if args.payload else sys.stdin).read()
    decision = decide_from_payload(json.loads(raw))
    if args.json:
        print(json.dumps({"enable_auto_merge": decision.enable_auto_merge, "reasons": list(decision.reasons)}, indent=2))
    else:
        if decision.enable_auto_merge:
            print("enable")
        else:
            print("skip: " + "; ".join(decision.reasons))
    return 0


if __name__ == "__main__":
    sys.exit(main())
