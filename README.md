# Claudeception

Every time you use an AI coding agent, it starts from zero. You spend an hour debugging some obscure error, the agent figures it out, session ends. Next time you hit the same issue? Another hour.

This skill fixes that. When Claude Code discovers something non-obvious (a debugging technique, a workaround, some project-specific pattern), it saves that knowledge as a new skill. Next time a similar problem comes up, the skill gets loaded automatically.

## Installation

### As a plugin (recommended)

Installing as a plugin brings the activation hook with it, so there's nothing to copy and no settings file to edit:

```
/plugin marketplace add jonw80/Claudeception
/plugin install claudeception@claudeception
```

That's the whole setup. The hook is registered by the plugin, and uninstalling removes it again.

The same marketplace also carries [Verified Math](plugins/verified-math), a separate plugin described below:

```
/plugin install verified-math@claudeception
```

### As a skill

If you'd rather not use the plugin system, clone the skill directly:

```bash
# User-level
git clone https://github.com/jonw80/Claudeception.git ~/.claude/skills/claudeception

# or project-level
git clone https://github.com/jonw80/Claudeception.git .claude/skills/claudeception
```

The skill will activate on semantic matching alone, but the hook makes it far more reliable. To wire it up manually, point your settings at the script where you cloned it (`~/.claude/settings.json` for user-level, `.claude/settings.json` for a single project):

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "~/.claude/skills/claudeception/scripts/claudeception-activator.sh"
          }
        ]
      }
    ],
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "~/.claude/skills/claudeception/scripts/claudeception-activator.sh"
          }
        ]
      }
    ]
  }
}
```

If you already have a `settings.json`, merge the `hooks` block into it. One script handles both events and decides what to send based on which one fired.

### How the hook behaves

`SessionStart` injects the evaluation criteria once, at the top of the session. `UserPromptSubmit` then stays quiet and re-sends a one-line reminder only every tenth prompt, which keeps the reminder alive through a long session without paying for it on every turn.

Two environment variables adjust this:

| Variable | Effect |
| --- | --- |
| `CLAUDECEPTION_REMIND_EVERY` | Prompts between reminders. Default `10`; set `0` to send none. |
| `CLAUDECEPTION_DISABLE` | Set to `1` to silence the hook without unwiring it. |

## Usage

### Automatic Mode

The skill activates automatically when Claude Code:
- Just completed debugging and discovered a non-obvious solution
- Found a workaround through investigation or trial-and-error
- Resolved an error where the root cause wasn't immediately apparent
- Learned project-specific patterns or configurations through investigation
- Completed any task where the solution required meaningful discovery

### Explicit Mode

Trigger a learning retrospective:

```
/claudeception
```

Or explicitly request skill extraction:

```
Save what we just learned as a skill
```

### What Gets Extracted

Not every task produces a skill. It only extracts knowledge that required actual discovery (not just reading docs), will help with future tasks, has clear trigger conditions, and has been verified to work.

## Research

The idea comes from academic work on skill libraries for AI agents.

[Voyager](https://arxiv.org/abs/2305.16291) (Wang et al., 2023) showed that game-playing agents can build up libraries of reusable skills over time, and that this helps them avoid re-learning things they already figured out. [CASCADE](https://arxiv.org/abs/2512.23880) (2024) introduced "meta-skills" (skills for acquiring skills), which is what this is. [SEAgent](https://arxiv.org/abs/2508.04700) (2025) showed agents can learn new software environments through trial and error, which inspired the retrospective feature. [Reflexion](https://arxiv.org/abs/2303.11366) (Shinn et al., 2023) showed that self-reflection helps.

Agents that persist what they learn do better than agents that start fresh.

## How It Works

Claude Code has a native skills system. At startup, it loads skill names and descriptions (about 100 tokens each). When you're working, it matches your current context against those descriptions and pulls in relevant skills.

But this retrieval system can be written to, not just read from. So when this skill notices extractable knowledge, it writes a new skill with a description optimized for future retrieval.

The description matters a lot. "Helps with database problems" won't match anything useful. "Fix for PrismaClientKnownRequestError in serverless" will match when someone hits that error.

More on the skills architecture [here](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills).

## Skill Format

Extracted skills are markdown files with YAML frontmatter:

```yaml
---
name: prisma-connection-pool-exhaustion
description: |
  Fix for PrismaClientKnownRequestError: Too many database connections 
  in serverless environments (Vercel, AWS Lambda). Use when connection 
  count errors appear after ~5 concurrent requests.
author: Claude Code
version: 1.0.0
date: 2024-01-15
---

# Prisma Connection Pool Exhaustion

## Problem
[What this skill solves]

## Context / Trigger Conditions
[Exact error messages, symptoms, scenarios]

## Solution
[Step-by-step fix]

## Verification
[How to confirm it worked]
```

See `resources/skill-template.md` for the full template.

Skills follow the [Agent Skills](https://agentskills.io) open standard, so an extracted skill isn't locked to Claude Code.

## Quality Gates

The skill is picky about what it extracts. If something is just a documentation lookup, or only useful for this one case, or hasn't actually been tested, it won't create a skill. Would this actually help someone who hits this problem in six months? If not, no skill.

Two scripts back this up, and Claude runs them as part of extraction. You can also run them yourself.

**Before writing**, check whether the knowledge belongs in a skill that already exists:

```bash
scripts/skill-inventory.sh prisma connection pool
```

It lists matching skills across project, user, and plugin scopes. A skill covering three related failures beats three skills competing for the same match.

**After writing**, validate the file:

```bash
python3 scripts/validate-skill.py ~/.claude/skills/my-new-skill/SKILL.md
```

This catches the problems you'd otherwise only notice as silence: frontmatter that won't parse, a name Claude Code rejects, a description past the 1,536-character listing budget or with no trigger condition in it, missing sections, and credentials that shouldn't have been written down. Add `--strict` to fail on warnings too.

## Examples

See `examples/` for sample skills:

- `nextjs-server-side-error-debugging/`: errors that don't show in browser console
- `prisma-connection-pool-exhaustion/`: the "too many connections" serverless problem
- `typescript-circular-dependency/`: detecting and fixing import cycles

## Verified Math

A second plugin in this marketplace, aimed at a different failure mode.

Model arithmetic fails quietly. Not on the hard parts, where the difficulty is visible and care gets applied, but on a carried digit, a percentage taken of the wrong base, a unit left unconverted. The answer comes out plausible, and plausible survives review.

The skill's rule is that no computed number gets stated unless code produced it, and `scripts/calc.py` backs that up by checking every result a second way that shares no code path with the first:

```bash
calc.py eval  "2**100 / 3"                  # exact rational plus decimal expansion
calc.py check "(x+1)**2 == x**2 + 1"        # symbolic proof and random sampling
calc.py solve "x**5 - x - 1 = 0"            # every root back-substituted
calc.py integ "exp(-x**2)" --from=-oo --to=oo   # symbolic result vs quadrature
```

Exit code `0` means verified, `1` means the claim is false or the two methods disagreed. Requires `pip install sympy mpmath`; NumPy and SciPy are worth having for numerics.

The test suite is built from cases that break a naive implementation: `atan(1)+atan(2)+atan(3) == pi` is true but `simplify()` cannot reduce it, so treating "did not simplify" as a disproof reports a true identity as false. `exp(pi*sqrt(163))` matches an integer to about 30 significant figures without being that integer. `x**2 > 0` fails only at zero, which random sampling never lands on. Each of those caught a real bug during development.

## Quantum Memory

A third plugin: associative memory that recalls a whole pattern from a noisy or partial cue, either by phase-encoded quantum state simulation or by Hopfield attractor dynamics.

```bash
qamn.py demo         # recall from 20% noise, then from half a pattern
qamn.py benchmark    # accuracy against the optimal classical decoder
qamn.py capacity --qubits 64
qamn.py phases       # what the phase constant actually changes
```

The governing result is that phase-encoding fidelity has an exact closed form, `cos²(π·c/2) ^ hamming_distance`, verified against the simulator to nine decimal places. Because that is strictly decreasing in distance, ranking by fidelity and ranking by Hamming distance give the same order — so quantum recall *cannot* beat nearest-neighbour matching, which is already the optimal decoder on a symmetric noise channel. Matching it is the target; beating it would mean the measurement is wrong. It matches, at 85.2% against 85.2% on noisy queries and 96.4% against 96.4% on fragments.

This was adapted from an existing QAMN implementation whose gate simulation was sound but whose memory layer was not. Four defects are fixed and covered by regression tests: `cnot` double-swapped every pair and was a no-op, so no entanglement was possible at all; the entanglement network was applied to queries but not to stored patterns, costing 15 points of accuracy; `check_stability` returned `fidelity(state, state)`, which is 1.0 by definition; and `capacity` floored to 1 for every network below 17 units, reporting capacity 1 while the demo stored 4 patterns.

Requires `pip install numpy`.

## Contributing

Contributions welcome. Fork, make changes, submit a PR.

Before opening one, run:

```bash
./tests/run-tests.sh
python3 scripts/validate-skill.py --strict SKILL.md examples/
shellcheck scripts/*.sh tests/*.sh
```

CI runs the same three checks on every push.

## License

MIT
