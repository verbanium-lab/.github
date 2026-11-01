#!/usr/bin/env python3
"""Replace the README status block between marker comments (stdlib only)."""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any

START = "<!-- status:start -->"
END = "<!-- status:end -->"
MISSING = "—"
CI_BADGE = re.compile(r"!\[ci\]\([^)]+\)")


def ci_badge_line(repository: str, server: str = "https://github.com") -> str:
    return f"![ci]({server}/{repository}/actions/workflows/ci.yml/badge.svg?branch=main)"


def _link(label: str, url: str | None) -> str:
    if not url:
        return label if label else MISSING
    safe = label.replace("\\", "\\\\").replace("[", "\\[")
    return f"[{safe}]({url})"


def _digest_cell(entry: dict[str, Any] | None) -> str:
    if not entry:
        return MISSING
    label = entry.get("label") or MISSING
    return _link(label, entry.get("url"))


def _coverage_cell(entry: dict[str, Any] | None) -> str:
    if not entry:
        return MISSING
    unit = entry.get("unit")
    e2e = entry.get("e2e")
    if not unit and not e2e:
        return MISSING

    def part(value: Any, label: str) -> str:
        if not value:
            return MISSING
        if isinstance(value, dict):
            url = value.get("url")
            text = value.get("label") or label
            return _link(text, url) if url else text
        return str(value)

    return f"{part(unit, 'unit')} / {part(e2e, 'e2e')}"


def render_table(rows: list[dict[str, Any]], server: str, repository: str) -> str:
    """Render the markdown table body (no markers, no badge)."""
    header = "| App | Release | Production | RC | Coverage |"
    separator = "| --- | --- | --- | --- | --- |"
    lines = [header, separator]
    for row in rows:
        slug = row.get("slug") or MISSING
        release = row.get("release") or {}
        rel_label = release.get("tag") or MISSING
        rel_url = release.get("url")
        lines.append("| "
                     + " | ".join([
                         _link(slug, None),
                         _link(rel_label, rel_url),
                         _digest_cell(row.get("production")),
                         _digest_cell(row.get("rc")),
                         _coverage_cell(row.get("coverage")),
                     ])
                     + " |")
    return "\n".join(lines)


def render_block(rows: list[dict[str, Any]], repository: str, server: str = "https://github.com") -> str:
    table = render_table(rows, server, repository)
    parts = [START]
    badge = ci_badge_line(repository, server)
    if not CI_BADGE.search(table):
        parts.append(badge)
        parts.append("")
    parts.append(table)
    parts.append(END)
    return "\n".join(parts) + "\n"


def replace_status_block(readme: str, block: str) -> str:
    start = readme.find(START)
    end = readme.find(END)
    if start == -1 or end == -1 or end < start:
        raise SystemExit("README status markers not found")
    end += len(END)
    before = readme[:start]
    after = readme[end:]
    if after.startswith("\n"):
        after = after[1:]
    return before + block.rstrip("\n") + "\n" + after


def apply_status(readme: str, rows: list[dict[str, Any]], repository: str,
                 server: str = "https://github.com") -> tuple[str, bool]:
    block = render_block(rows, repository, server)
    updated = replace_status_block(readme, block)
    return updated, updated != readme


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--readme", required=True, type=str, help="Path to README.md")
    parser.add_argument("--data", required=True, type=str, help="JSON file with rows and metadata")
    parser.add_argument("--dry-run", action="store_true", help="Print README to stdout instead of writing")
    args = parser.parse_args(argv)
    payload = json.loads(open(args.data, encoding="utf-8").read())
    repository = payload["repository"]
    server = payload.get("server", "https://github.com")
    rows = payload["rows"]
    readme = open(args.readme, encoding="utf-8").read()
    updated, changed = apply_status(readme, rows, repository, server)
    if args.dry_run:
        sys.stdout.write(updated)
    elif changed:
        with open(args.readme, "w", encoding="utf-8") as handle:
            handle.write(updated)
    print(f"changed={'true' if changed else 'false'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
