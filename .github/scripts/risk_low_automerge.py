#!/usr/bin/env python3
"""Pure decision logic for the risk:low auto-merge gate (unit-tested offline)."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

# GitHub caps pull request file lists at 3000 entries.
PR_FILES_LIST_MAX = 3000


@dataclass(frozen=True)
class Decision:
    """Whether platform auto-merge may be enabled and why not."""

    enable_auto_merge: bool
    reasons: tuple[str, ...]


def parse_list(text: str, *, sep: str = ",") -> frozenset[str]:
    """Turn a comma- or newline-separated list into a set."""
    items = [part.strip() for chunk in text.splitlines() for part in chunk.split(sep)]
    return frozenset(item for item in items if item)


def parse_allowed_authors(text: str) -> frozenset[tuple[str, str]]:
    """Parse allowed authors as login|Type pairs (Type defaults to User)."""
    result: set[tuple[str, str]] = set()
    for item in parse_list(text):
        if "|" in item:
            login, author_type = item.split("|", 1)
            login, author_type = login.strip(), author_type.strip()
        else:
            login, author_type = item, "User"
        if login and author_type:
            result.add((login, author_type))
    return frozenset(result)


def parse_patterns(text: str) -> tuple[str, ...]:
    """One glob pattern per non-empty line."""
    return tuple(line.strip() for line in text.splitlines() if line.strip())


def path_has_dotdot(filename: str) -> bool:
    return any(segment == ".." for segment in filename.split("/"))


def _glob_match(path_segments: Sequence[str], pattern_segments: Sequence[str]) -> bool:
    """Case-sensitive glob: * within one segment, ** across segments (** may match empty)."""
    pi = 0
    ti = 0
    while True:
        if ti >= len(pattern_segments):
            return pi >= len(path_segments)
        token = pattern_segments[ti]
        if token == "**":
            if ti == len(pattern_segments) - 1:
                return True
            ti += 1
            for skip in range(len(path_segments) - pi + 1):
                if _glob_match(path_segments[pi + skip :], pattern_segments[ti:]):
                    return True
            return False
        if pi >= len(path_segments):
            return False
        if token == "*":
            if not path_segments[pi]:
                return False
        elif token != path_segments[pi]:
            return False
        pi += 1
        ti += 1


def glob_path_match(path: str, pattern: str) -> bool:
    """Return whether path matches pattern (case-sensitive; reject .. path segments)."""
    if path_has_dotdot(path):
        return False
    path_segments = path.split("/") if path else []
    if any(segment == "" for segment in path_segments):
        return False
    pattern_segments = pattern.split("/") if pattern else [""]
    return _glob_match(path_segments, pattern_segments)


def path_matches_protected(filename: str, patterns: Sequence[str]) -> str | None:
    """Return the first matching pattern, if any."""
    if path_has_dotdot(filename):
        return ".."
    for pattern in patterns:
        if glob_path_match(filename, pattern):
            return pattern
    return None


def paths_from_pr_files(files: Iterable[Mapping[str, Any]]) -> tuple[str, ...]:
    """Collect current, previous and deleted paths from pull request file entries."""
    paths: set[str] = set()
    for entry in files:
        name = entry.get("filename")
        if isinstance(name, str) and name:
            paths.add(name)
        previous = entry.get("previous_filename")
        if isinstance(previous, str) and previous:
            paths.add(previous)
    return tuple(sorted(paths))


def pr_files_inventory_ok(*, file_count: int | None, listed_count: int) -> tuple[bool, str]:
    """False when the API file count is unknown or the list is truncated."""
    if file_count is None:
        return False, "changed-file count unknown"
    if listed_count >= PR_FILES_LIST_MAX:
        return False, f"changed-file list truncated (>={PR_FILES_LIST_MAX} files)"
    if file_count != listed_count:
        return False, "changed-file count does not match listed files"
    return True, ""


def author_allowed(
    login: str,
    author_type: str,
    author_id: int,
    *,
    allowed_authors: frozenset[tuple[str, str]],
    agent_bot_id: int | None,
) -> bool:
    if (login, author_type) in allowed_authors:
        return True
    if agent_bot_id is not None and author_type == "Bot" and author_id == agent_bot_id:
        return True
    return False


def parse_agent_bot_id(text: str) -> int | None:
    stripped = text.strip()
    if not stripped:
        return None
    if not re.fullmatch(r"[0-9]+", stripped):
        raise ValueError("agent-bot-id must be digits or empty")
    return int(stripped)


def latest_check_success(
    check_runs: Iterable[Mapping[str, Any]],
    *,
    check_name: str,
    actions_app_id: int,
) -> tuple[bool, str]:
    """True when the newest GitHub Actions check run with the given name succeeded."""
    matches = [
        run
        for run in check_runs
        if run.get("name") == check_name
        and (run.get("app") or {}).get("id") == actions_app_id
    ]
    if not matches:
        return False, f"check `{check_name}` missing"
    latest = max(matches, key=lambda run: (run.get("started_at") or "", run.get("id") or 0))
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
    author_id: int,
    base_branch: str,
    pr_base: str,
    head_repo_full_name: str,
    caller_repo: str,
    changed_files: Sequence[str],
    changed_files_ok: bool,
    changed_files_detail: str,
    check_runs: Sequence[Mapping[str, Any]],
    low_risk_label: str,
    high_risk_label: str,
    protected_patterns: Sequence[str],
    allowed_authors: frozenset[tuple[str, str]],
    agent_bot_id: int | None,
    required_check: str,
    ai_review_check: str,
    actions_app_id: int,
    ai_review_actions_app_id: int,
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

    if not changed_files_ok:
        reasons.append(changed_files_detail or "changed-file inventory incomplete")

    if not author_allowed(
        author_login,
        author_type,
        author_id,
        allowed_authors=allowed_authors,
        agent_bot_id=agent_bot_id,
    ):
        reasons.append(f"author {author_login!r} is not allowed")

    for filename in changed_files:
        pattern = path_matches_protected(filename, protected_patterns)
        if pattern is not None:
            reasons.append(f"protected path {filename!r} matches {pattern!r}")

    ok, detail = latest_check_success(
        check_runs, check_name=required_check, actions_app_id=actions_app_id
    )
    if not ok:
        reasons.append(detail)

    ok, detail = latest_check_success(
        check_runs,
        check_name=ai_review_check,
        actions_app_id=ai_review_actions_app_id,
    )
    if not ok:
        reasons.append(detail)

    return Decision(enable_auto_merge=not reasons, reasons=tuple(reasons))


def decide_from_payload(payload: Mapping[str, Any]) -> Decision:
    """Decode a JSON payload produced by the workflow step."""
    label_names = frozenset(payload.get("label_names") or ())
    agent_bot_raw = str(payload.get("agent_bot_id") or "")
    return decide(
        label_names=label_names,
        draft=bool(payload.get("draft")),
        author_login=str(payload.get("author_login") or ""),
        author_type=str(payload.get("author_type") or ""),
        author_id=int(payload.get("author_id") or 0),
        base_branch=str(payload["base_branch"]),
        pr_base=str(payload.get("pr_base") or ""),
        head_repo_full_name=str(payload.get("head_repo") or ""),
        caller_repo=str(payload["caller_repo"]),
        changed_files=tuple(payload.get("changed_files") or ()),
        changed_files_ok=bool(payload.get("changed_files_ok", True)),
        changed_files_detail=str(payload.get("changed_files_detail") or ""),
        check_runs=tuple(payload.get("check_runs") or ()),
        low_risk_label=str(payload["low_risk_label"]),
        high_risk_label=str(payload["high_risk_label"]),
        protected_patterns=parse_patterns(str(payload.get("protected_paths") or "")),
        allowed_authors=parse_allowed_authors(str(payload.get("allowed_authors") or "")),
        agent_bot_id=parse_agent_bot_id(agent_bot_raw),
        required_check=str(payload["required_check"]),
        ai_review_check=str(payload["ai_review_check"]),
        actions_app_id=int(payload["actions_app_id"]),
        ai_review_actions_app_id=int(
            payload.get("ai_review_actions_app_id") or payload["actions_app_id"]
        ),
    )


def github_actions_output_value(name: str, value: str) -> str:
    """Format a name=value or multiline block safe for appending to GITHUB_OUTPUT."""
    if "\n" in value or "\r" in value:
        delimiter = f"GATE_{name.upper()}"
        return f"{name}<<{delimiter}\n{value}\n{delimiter}\n"
    return f"{name}={value}\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload", help="Path to JSON payload; omit to read stdin")
    parser.add_argument("--json", action="store_true", help="Print the decision as JSON")
    args = parser.parse_args(argv)
    raw = (open(args.payload, encoding="utf-8") if args.payload else sys.stdin).read()
    decision = decide_from_payload(json.loads(raw))
    if args.json:
        print(
            json.dumps(
                {
                    "enable_auto_merge": decision.enable_auto_merge,
                    "reasons": list(decision.reasons),
                },
                indent=2,
            )
        )
    else:
        if decision.enable_auto_merge:
            print("enable")
        else:
            print("skip: " + "; ".join(decision.reasons))
    return 0


if __name__ == "__main__":
    sys.exit(main())
