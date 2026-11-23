#!/usr/bin/env python3
"""Pure decision logic for the auto-merge gate (unit-tested offline).

Two modes: `risk-low` (agent pull requests, the default) and `dependencies` (Renovate pull requests).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

# GitHub caps pull request file lists at 3000 entries.
PR_FILES_LIST_MAX = 3000

# Callers may only add paths; this set is always enforced and cannot be narrowed through inputs.
MINIMUM_PROTECTED_PATTERNS: tuple[str, ...] = (
    ".github/**",
    "**/CODEOWNERS",
    "CODEOWNERS",
    "justfile",
    "lefthook.yml",
    "**/.sops.yaml",
    "**/secrets/**",
    "package.json",
    "pnpm-lock.yaml",
    "pnpm-workspace.yaml",
    "Dockerfile*",
    "**/Dockerfile*",
    "**/package.json",
    "Justfile",
    ".justfile",
    ".npmrc",
    "**/.npmrc",
    ".dockerignore",
    "**/.dockerignore",
    "docker-compose*",
    "**/docker-compose*",
    "turbo.json",
    "**/.env*",
    ".env*",
)


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


def effective_protected_patterns(additional: Sequence[str]) -> tuple[str, ...]:
    """Union the baked-in minimum protected paths with caller-supplied extras."""
    seen: set[str] = set()
    ordered: list[str] = []
    for pattern in (*MINIMUM_PROTECTED_PATTERNS, *additional):
        if pattern not in seen:
            seen.add(pattern)
            ordered.append(pattern)
    return tuple(ordered)


def neutralize_actions_log_text(text: str) -> str:
    """Strip workflow log and summary injection from PR-derived strings."""
    return (
        text.replace("\\", "\\\\")
        .replace("\r", " ")
        .replace("\n", " ")
        .replace("::", " ")
    )


def path_has_dotdot(filename: str) -> bool:
    return any(segment == ".." for segment in filename.split("/"))


def _segment_match(path_segment: str, pattern_segment: str) -> bool:
    if pattern_segment == "*":
        return bool(path_segment)
    if "*" not in pattern_segment:
        return path_segment == pattern_segment
    if pattern_segment.count("*") != 1 or not pattern_segment.endswith("*"):
        return False
    return path_segment.startswith(pattern_segment[:-1])


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
        if not _segment_match(path_segments[pi], token):
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

    patterns = effective_protected_patterns(protected_patterns)
    for filename in changed_files:
        pattern = path_matches_protected(filename, patterns)
        if pattern is not None:
            reasons.append(f"protected path {filename!r} matches {pattern!r}")

    ok, detail = latest_check_success(
        check_runs, check_name=required_check, actions_app_id=actions_app_id
    )
    if not ok:
        reasons.append(detail)

    # An empty name disables the AI review requirement; any other name is required as before.
    if ai_review_check != "":
        ok, detail = latest_check_success(
            check_runs,
            check_name=ai_review_check,
            actions_app_id=ai_review_actions_app_id,
        )
        if not ok:
            reasons.append(detail)

    return Decision(enable_auto_merge=not reasons, reasons=tuple(reasons))


MODE_RISK_LOW = "risk-low"
MODE_DEPENDENCIES = "dependencies"
MODES: tuple[str, ...] = (MODE_RISK_LOW, MODE_DEPENDENCIES)

# Dependency mode: the allow-list below REPLACES MINIMUM_PROTECTED_PATTERNS. The protected list
# exists to keep third-party code out of agent pull requests, and it names exactly the files a
# dependency update must change (package.json, lock files, Dockerfiles, workflow pins). Applying
# both would refuse every dependency update. A path that is not on this list refuses, so the
# allow-list is the narrower rule, not a looser one. Workflow and Dockerfile entries are checked
# line by line (see `dependency_patch_problem`).
DEPENDENCY_ANY_CHANGE_PATTERNS: tuple[str, ...] = (
    "package.json",
    "**/package.json",
    "pnpm-lock.yaml",
    "pnpm-workspace.yaml",
)
DEPENDENCY_DOCKERFILE_PATTERNS: tuple[str, ...] = ("Dockerfile*", "**/Dockerfile*")
# `.github/workflows/*.yml`: the glob helper has no `*.yml` form, so this is a regular expression.
DEPENDENCY_WORKFLOW_RE = re.compile(r"\.github/workflows/[^/]+\.yml", re.ASCII)

_USES_LINE_RE = re.compile(
    r"(?P<lead>[ \t]*(?:-[ \t]+)?)uses:[ \t]+(?P<name>[^\s#@]+)(?:@(?P<ref>[^\s#]+))?(?:[ \t]+#.*)?",
    re.ASCII,
)


def _clean_patch_line(line: str) -> str | None:
    """Drop one trailing CR; None when the line carries other control characters."""
    text = line[:-1] if line.endswith("\r") else line
    if any(ord(char) < 32 and char != "\t" for char in text):
        return None
    return text


def patch_changed_lines(patch: str) -> list[tuple[str, str]] | None:
    """Added and removed lines of a unified-diff patch as (sign, text); None if unreadable.

    The API patch has no file headers, so every line that starts with + or - is a change (even
    one that begins with +++). Only "\\n" separates lines: str.splitlines also splits on other
    separators and could hide a line.
    """
    changes: list[tuple[str, str]] = []
    for raw in patch.split("\n"):
        if raw.startswith("@@") or raw.startswith("\\") or raw[:1] not in ("+", "-"):
            continue
        text = _clean_patch_line(raw[1:])
        if text is None:
            return None
        changes.append((raw[0], text))
    return changes


def _uses_key(text: str) -> tuple[str, ...] | None:
    """Key of a `uses:` line: everything but the version and the trailing comment."""
    match = _USES_LINE_RE.fullmatch(text)
    if match is None:
        return None
    return ("uses", match["lead"], match["name"])


def _dockerfile_key(text: str) -> tuple[str, ...] | None:
    """Key of a FROM or COPY --from= line: the whole line without the image digest.

    A changed tag, stage name or any other part changes the key, so only a digest change pairs up.
    """
    tokens = [token for token in re.split(r"[ \t]+", text.strip(" \t")) if token]
    if not tokens:
        return None
    head = tokens[0].upper()
    if head == "FROM":
        for index in range(1, len(tokens)):
            if not tokens[index].startswith("--"):
                tokens[index] = tokens[index].split("@", 1)[0]
                return ("FROM", *tokens[1:])
        return None
    if head == "COPY":
        found = False
        for index, token in enumerate(tokens):
            if token.startswith("--from="):
                tokens[index] = token.split("@", 1)[0]
                found = True
        return ("COPY", *tokens[1:]) if found else None
    return None


def dependency_patch_problem(kind: str, patch: str | None) -> str | None:
    """Why the line-level rule refuses this patch (kind: "uses" or "dockerfile"), else None.

    Every added or removed line must be a `uses:` line (a FROM or COPY --from= line) and the
    removed lines must pair up with the added ones, so a new step or stage cannot ride along.
    """
    if not patch:
        return "no patch to check"
    changes = patch_changed_lines(patch)
    if changes is None:
        return "patch contains control characters"
    if not changes:
        return "patch has no changed lines"
    keyer = _uses_key if kind == "uses" else _dockerfile_key
    removed: list[tuple[str, ...]] = []
    added: list[tuple[str, ...]] = []
    for sign, text in changes:
        key = keyer(text)
        if key is None:
            return "changed line is not a " + ("uses: line" if kind == "uses" else "FROM or COPY --from= line")
        (removed if sign == "-" else added).append(key)
    if sorted(removed) != sorted(added):
        return "added and removed lines do not pair up (only the version or digest may change)"
    return None


def dependency_file_problem(entry: Mapping[str, Any]) -> str | None:
    """Why a changed file refuses in dependency mode, else None."""
    filename = entry.get("filename")
    if not isinstance(filename, str) or not filename:
        return "file entry without a name"
    if path_has_dotdot(filename):
        return "path contains .."
    if entry.get("status") != "modified":
        return f"status {entry.get('status')!r} (only modified files may change)"
    if entry.get("previous_filename"):
        return "renamed file"
    if any(glob_path_match(filename, pattern) for pattern in DEPENDENCY_ANY_CHANGE_PATTERNS):
        return None
    if DEPENDENCY_WORKFLOW_RE.fullmatch(filename):
        return dependency_patch_problem("uses", entry.get("patch"))
    if any(glob_path_match(filename, pattern) for pattern in DEPENDENCY_DOCKERFILE_PATTERNS):
        return dependency_patch_problem("dockerfile", entry.get("patch"))
    return "not on the dependency allow-list"


def decide_dependencies(
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
    files: Sequence[Mapping[str, Any]],
    changed_files_ok: bool,
    changed_files_detail: str,
    check_runs: Sequence[Mapping[str, Any]],
    dependency_label: str,
    high_risk_label: str,
    dependency_bot_id: int | None,
    required_check: str,
    actions_app_id: int,
) -> Decision:
    """Return whether a dependency update pull request may be merged (dependency mode).

    `files` are the pull request file entries of the API (filename, status, previous_filename,
    patch). The AI review check is not part of this mode: only `required_check` gates the merge.
    """
    reasons: list[str] = []

    if dependency_bot_id is None:
        reasons.append("no dependency bot id configured")
    elif author_type != "Bot" or author_id != dependency_bot_id:
        reasons.append(f"author {author_login!r} is not the dependency bot")

    if dependency_label == "" or dependency_label not in label_names:
        reasons.append(f"missing label {dependency_label}")
    if high_risk_label in label_names:
        reasons.append(f"label {high_risk_label}")

    if draft:
        reasons.append("draft pull request")
    if pr_base != base_branch:
        reasons.append(f"base branch {pr_base!r} is not {base_branch!r}")
    if head_repo_full_name != caller_repo:
        reasons.append("head is not in this repository")

    if not changed_files_ok:
        reasons.append(changed_files_detail or "changed-file inventory incomplete")

    for entry in files:
        problem = dependency_file_problem(entry)
        if problem is not None:
            reasons.append(f"file {entry.get('filename')!r}: {problem}")
    if not files:
        reasons.append("no changed files")

    ok, detail = latest_check_success(
        check_runs, check_name=required_check, actions_app_id=actions_app_id
    )
    if not ok:
        reasons.append(detail)

    return Decision(enable_auto_merge=not reasons, reasons=tuple(reasons))


def decide_from_payload(payload: Mapping[str, Any]) -> Decision:
    """Decode a JSON payload produced by the workflow step."""
    label_names = frozenset(payload.get("label_names") or ())
    mode = str(payload.get("mode") or MODE_RISK_LOW)
    if mode not in MODES:
        raise ValueError(f"mode must be one of {', '.join(MODES)}")
    if mode == MODE_DEPENDENCIES:
        return decide_dependencies(
            label_names=label_names,
            draft=bool(payload.get("draft")),
            author_login=str(payload.get("author_login") or ""),
            author_type=str(payload.get("author_type") or ""),
            author_id=int(payload.get("author_id") or 0),
            base_branch=str(payload["base_branch"]),
            pr_base=str(payload.get("pr_base") or ""),
            head_repo_full_name=str(payload.get("head_repo") or ""),
            caller_repo=str(payload["caller_repo"]),
            files=tuple(payload.get("files") or ()),
            changed_files_ok=bool(payload.get("changed_files_ok", True)),
            changed_files_detail=str(payload.get("changed_files_detail") or ""),
            check_runs=tuple(payload.get("check_runs") or ()),
            dependency_label=str(payload.get("dependency_label") or ""),
            high_risk_label=str(payload["high_risk_label"]),
            dependency_bot_id=parse_agent_bot_id(str(payload.get("dependency_bot_id") or "")),
            required_check=str(payload["required_check"]),
            actions_app_id=int(payload["actions_app_id"]),
        )
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
        protected_patterns=effective_protected_patterns(
            parse_patterns(str(payload.get("protected_paths") or ""))
        ),
        allowed_authors=parse_allowed_authors(str(payload.get("allowed_authors") or "")),
        agent_bot_id=parse_agent_bot_id(agent_bot_raw),
        required_check=str(payload["required_check"]),
        ai_review_check=str(payload["ai_review_check"]),
        actions_app_id=int(payload["actions_app_id"]),
        ai_review_actions_app_id=int(
            payload.get("ai_review_actions_app_id") or payload["actions_app_id"]
        ),
    )


def github_actions_output_safe_name(name: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        raise ValueError("output name must be alphanumeric, underscore or hyphen")


def github_actions_output_value(name: str, value: str) -> str:
    """Format a name=value or multiline block safe for appending to GITHUB_OUTPUT."""
    github_actions_output_safe_name(name)
    if "\n" in value or "\r" in value:
        delimiter = f"GATE_{name.upper()}"
        return f"{name}<<{delimiter}\n{value}\n{delimiter}\n"
    if "%0A" in value or "%0D" in value:
        raise ValueError("output value must not encode newlines")
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
