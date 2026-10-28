---
title: AGENTS.md — .github
owner: verbanium
status: active
last_reviewed: 2026-10-29
---

# AGENTS.md — .github

Shared agent rules are not copied here. They come from the `agents` repository
(`verbanium-lab/agents`): Claude Code loads them through the `verbanium-lab` plugins; Codex and
Cursor read the rules and skills that `agents-sync` installs (`~/.codex/AGENTS.md`,
`~/.agents/skills/`). Read the `task` and `pr-workflow` skills first. In the owner's workspace,
also read the workspace `AGENTS.md`. If the rules, the skills or `agent-git` are missing, stop task
work and say so.

This file adds only what is specific to this repository.
