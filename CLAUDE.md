# CLAUDE.md — Claudeception

This file provides AI assistants with the context needed to work effectively in this repository.

## What This Project Is

Claudeception is a **Claude Code skill** that enables continuous learning across sessions. It extracts reusable knowledge from work sessions and codifies it into new Claude Code skills, allowing agents to avoid re-learning solutions to previously solved problems.

This is a documentation/configuration repository — there is no compiled code, no build system, and no test suite in the traditional sense.

## Repository Structure

```
Claudeception/
├── SKILL.md                          # Main skill definition (YAML frontmatter + instructions)
├── README.md                         # Installation and usage guide
├── WARP.md                           # Guidance for WARP editor integration
├── LICENSE                           # MIT
├── examples/                         # Reference skill implementations
│   ├── nextjs-server-side-error-debugging/SKILL.md
│   ├── prisma-connection-pool-exhaustion/SKILL.md
│   └── typescript-circular-dependency/SKILL.md
├── resources/
│   ├── skill-template.md             # Template for creating new skills
│   └── research-references.md        # Academic papers behind the design
└── scripts/
    └── claudeception-activator.sh    # Bash hook for auto-triggering skill evaluation
```

## Key Files

### `SKILL.md` (the meta-skill)
The core of the project. Loaded by Claude Code at startup. Contains:
- YAML frontmatter with metadata and `allowed-tools`
- Instructions for when and how to extract skills
- Quality gates, anti-patterns, and the complete extraction workflow
- Guidance on web research integration

### `resources/skill-template.md`
Template every extracted skill must follow. Use this when creating new skills. It includes an extraction checklist (as an HTML comment at the end) that should be removed before saving.

### `scripts/claudeception-activator.sh`
A `UserPromptSubmit` hook that injects a mandatory evaluation reminder before each user prompt. Install it to ensure skill evaluation happens on every session, not just via semantic matching.

## Skill File Format

All skills — both in `examples/` and skills created by Claudeception — follow this format:

```markdown
---
name: kebab-case-skill-name
description: |
  Precise, searchable description. Include: specific error messages or
  symptoms, trigger conditions, key technologies. Use phrases like
  "Use when:", "Helps with:", "Solves:". Semantic matching depends on this.
author: Claude Code
version: 1.0.0
date: YYYY-MM-DD
---

# Skill Name

## Problem
## Context / Trigger Conditions
## Solution
## Verification
## Example
## Notes
## References  ← optional, include if web sources were consulted
```

## Conventions

### Naming
- Skill names: `kebab-case` (e.g., `prisma-connection-pool-exhaustion`)
- Directory name must match the skill name
- Skill file is always named `SKILL.md`

### Descriptions
The `description` field in YAML frontmatter is critical — Claude Code uses it for semantic matching. Requirements:
- Include specific error messages (e.g., `"P2024: Timed out fetching a connection from the connection pool"`)
- List trigger conditions explicitly with "Use when:" phrasing
- Mention relevant technologies (frameworks, tools, platforms)
- Keep under ~300 characters when possible

### Quality Gates
Before any skill is finalized, verify all of these:
- [ ] Description contains specific trigger conditions (not vague)
- [ ] Solution has been verified to actually work
- [ ] Content is specific enough to be actionable
- [ ] Content is general enough to be reusable across projects
- [ ] No sensitive information (credentials, internal URLs)
- [ ] Doesn't duplicate existing documentation or other skills
- [ ] Web research conducted for technology-specific topics
- [ ] References section included if web sources were consulted
- [ ] Current best practices incorporated where applicable
- [ ] Name is descriptive and uses kebab-case
- [ ] All required sections (Problem, Context, Solution, Verification) are present

### Anti-Patterns
- **Over-extraction**: Don't create skills for mundane, obvious solutions
- **Vague descriptions**: "Helps with React problems" won't match when needed
- **Unverified solutions**: Only extract what has actually worked
- **Documentation duplication**: Link to official docs; add what's missing, not what's already there
- **Stale knowledge**: Skills include version and date for this reason

## Skill Lifecycle

1. **Creation** — extracted with documented verification
2. **Refinement** — updated when additional edge cases are discovered
3. **Deprecation** — marked when underlying tools/patterns change significantly
4. **Archival** — removed when no longer relevant

## Where Skills Are Saved

- **Project-specific**: `.claude/skills/[skill-name]/SKILL.md` within a project repo
- **User-wide**: `~/.claude/skills/[skill-name]/SKILL.md` for cross-project reuse

## Installation Paths (for context)

```
# User-level
~/.claude/skills/claudeception/

# Project-level
.claude/skills/claudeception/
```

The activation hook goes to:
```
~/.claude/hooks/claudeception-activator.sh     # user-level
.claude/hooks/claudeception-activator.sh       # project-level
```

## Development Workflow

Since this is a documentation repository:

1. **Editing SKILL.md** — changes to the meta-skill directly affect how Claudeception behaves. Test by installing and running Claude Code.
2. **Adding examples** — create a new directory under `examples/` following the existing structure. Use `resources/skill-template.md` as the base.
3. **Updating the hook** — `scripts/claudeception-activator.sh` is a simple bash script that outputs text. Test by running it directly: `bash scripts/claudeception-activator.sh`.
4. **No build step** — changes take effect immediately when the skill is loaded by Claude Code.

## Git Conventions

- Commit messages are descriptive and use imperative mood
- PRs target `master`
- The project has been rebased/consolidated; there are ~13 commits total

## Academic Foundation

The design is based on peer-reviewed research:
- **Voyager** (Wang et al., 2023) — skill library architecture for agents
- **CASCADE** (2024) — meta-skills (skills for acquiring skills)
- **SEAgent** (2025) — experiential learning through trial and error
- **Reflexion** (Shinn et al., 2023) — self-reflection for agent improvement
- **EvoFSM** (2024) — experience pools for agent knowledge

See `resources/research-references.md` for full citations and links.
