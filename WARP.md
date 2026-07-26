# WARP.md

This file provides guidance to WARP (warp.dev) when working with code in this repository.

## Project Overview

Claudeception is a **Claude Code skill** for continuous learning—it enables Claude Code to autonomously extract and preserve learned knowledge into reusable skills. It is not an application codebase but rather a skill definition with documentation and examples.

## Key Files

- `SKILL.md` — The main skill definition (YAML frontmatter + instructions). This is what Claude Code loads.
- `.claude-plugin/plugin.json` — Plugin manifest. The repo is a single-skill plugin: `SKILL.md` sits at the root with no `skills/` directory, which Claude Code loads directly.
- `.claude-plugin/marketplace.json` — Marketplace catalog, so the repo can be added with `/plugin marketplace add`.
- `hooks/hooks.json` — Registers the activation hook when installed as a plugin.
- `scripts/claudeception-activator.sh` — Handles `SessionStart` and `UserPromptSubmit`, emitting `hookSpecificOutput.additionalContext`.
- `scripts/validate-skill.py` — Validates a generated `SKILL.md` before it lands.
- `scripts/skill-inventory.sh` — Lists installed skills so extraction can update one instead of duplicating it.
- `resources/skill-template.md` — Template for creating new skills
- `examples/` — Sample extracted skills demonstrating proper format
- `tests/run-tests.sh` — Test suite covering the scripts and manifests

## Development Commands

```bash
./tests/run-tests.sh                                  # full suite
python3 scripts/validate-skill.py --strict SKILL.md examples/
shellcheck scripts/*.sh tests/*.sh
```

CI runs all three on every push (`.github/workflows/validate.yml`). Shell scripts must stay `shellcheck`-clean and executable.

## Skill File Format

Skills use YAML frontmatter followed by markdown:

```yaml
---
name: kebab-case-name
description: |
  Must be precise for semantic matching. Include:
  (1) exact use cases, (2) trigger conditions like error messages,
  (3) what problem this solves
when_to_use: |
  Optional extra trigger phrases. Appended to description in the skill
  listing, where the two are truncated together at 1,536 characters.
author: Claude Code
version: 1.0.0
allowed-tools:
  - Read
  - Write
  - Bash
  - Grep
  - Glob
---
```

The description field is critical—it determines when the skill surfaces during semantic matching.

## Installation Paths

- **User-level**: `~/.claude/skills/[skill-name]/`
- **Project-level**: `.claude/skills/[skill-name]/`

## Quality Criteria for Skills

When modifying or creating skills, ensure:
- **Reusable**: Helps with future tasks, not just one instance
- **Non-trivial**: Requires discovery, not just documentation lookup
- **Specific**: Clear trigger conditions (exact error messages, symptoms)
- **Verified**: Solution has actually been tested and works

## Research Foundation

The approach is based on academic work on skill libraries (Voyager, CASCADE, SEAgent, Reflexion). See `resources/research-references.md` for details.
