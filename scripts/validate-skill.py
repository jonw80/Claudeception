#!/usr/bin/env python3
"""Validate a SKILL.md before it joins the skill library.

Claudeception writes skills automatically, so nothing catches a malformed one
until Claude Code silently fails to load it. This checks the things that
actually break: frontmatter that will not parse, a name Claude Code rejects, a
description too vague to ever match, and credentials that should never have
been written down.

Usage:
    python3 scripts/validate-skill.py path/to/SKILL.md
    python3 scripts/validate-skill.py examples/          # walks a directory
    python3 scripts/validate-skill.py --strict examples/ # warnings fail too

Exit codes: 0 clean, 1 errors found (or warnings under --strict).
"""

import argparse
import os
import re
import sys

# Frontmatter fields Claude Code recognises, plus the metadata fields
# Claudeception writes for provenance. Anything else is reported as a warning
# rather than an error: Claude Code ignores fields it does not know.
KNOWN_FIELDS = {
    "name", "description", "when_to_use", "argument-hint", "arguments",
    "disable-model-invocation", "user-invocable", "allowed-tools",
    "disallowed-tools", "model", "effort", "context", "agent", "background",
    "hooks", "paths", "shell",
    # Provenance metadata, ignored by the loader but useful in a skill library.
    "author", "version", "date", "license", "tags",
}

RECOMMENDED_SECTIONS = ["Problem", "Solution", "Verification"]

# The skill listing truncates description + when_to_use at this length, so
# anything past it is invisible to matching.
DESCRIPTION_BUDGET = 1536

NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

SECRET_PATTERNS = [
    (re.compile(r"sk-ant-[A-Za-z0-9_-]{16,}"), "Anthropic API key"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS access key id"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), "GitHub token"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"), "GitHub fine-grained token"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"), "Slack token"),
    (re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----"), "private key"),
    (re.compile(r"(?i)\b(?:password|passwd|secret|api[_-]?key|access[_-]?token)"
                r"\s*[:=]\s*[\"']?[A-Za-z0-9/+_.-]{12,}"), "hardcoded credential"),
]

# A description earns its keep by naming the situation it fires on. These are
# the shapes that do that: an explicit trigger clause, a quoted error string,
# or a symptom word.
TRIGGER_HINTS = re.compile(
    r"(?i)\buse when\b|\btriggers?\b|\bwhen:\b|\bwhen you\b|\bfails? with\b|"
    r"\berror\b|\bexception\b|\bsymptom|\bafter\b.*\bfails\b"
)

VAGUE_OPENERS = re.compile(
    r"(?i)^\s*(helps? with|useful for|general(?:ly)? |various |stuff |things )"
)


class Finding:
    def __init__(self, level, message):
        self.level = level
        self.message = message


def parse_frontmatter(text):
    """Parse the YAML subset that appears in skill frontmatter.

    Handles ``key: value``, block scalars (``key: |`` / ``key: >``) and simple
    ``- item`` lists. Returns (fields, body, error) where error is a string
    when the frontmatter itself is unusable.
    """
    if not text.startswith("---"):
        return None, text, "file does not start with a --- frontmatter block"

    lines = text.split("\n")
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return None, text, "frontmatter block is never closed with ---"

    fields = {}
    i = 1
    while i < end:
        raw = lines[i]
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue

        match = re.match(r"^([A-Za-z_][A-Za-z0-9_-]*)\s*:\s*(.*)$", raw)
        if not match:
            i += 1
            continue

        key, value = match.group(1), match.group(2).strip()

        if value in ("|", ">", "|-", ">-", "|+", ">+"):
            collected = []
            i += 1
            while i < end:
                nxt = lines[i]
                if nxt.strip() and not nxt.startswith((" ", "\t")):
                    break
                collected.append(nxt.strip())
                i += 1
            joiner = "\n" if value.startswith("|") else " "
            fields[key] = joiner.join(collected).strip()
            continue

        if value == "":
            items = []
            j = i + 1
            while j < end and lines[j].strip().startswith("- "):
                items.append(lines[j].strip()[2:].strip())
                j += 1
            if items:
                fields[key] = items
                i = j
                continue

        fields[key] = value.strip("'\"")
        i += 1

    return fields, "\n".join(lines[end + 1:]), None


def check(path, findings):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
    except OSError as exc:
        findings.append(Finding("error", f"cannot read file: {exc}"))
        return

    fields, body, err = parse_frontmatter(text)
    if err:
        findings.append(Finding("error", err))
        return

    # --- name ---------------------------------------------------------
    name = fields.get("name")
    if name is None:
        findings.append(Finding(
            "warning",
            "no name field; Claude Code will fall back to the directory name"))
    elif not isinstance(name, str) or not NAME_RE.match(name):
        findings.append(Finding(
            "error",
            f"name {name!r} must be kebab-case: lowercase letters, digits and "
            "single hyphens only"))
    elif len(name) > 64:
        findings.append(Finding(
            "error", f"name is {len(name)} characters; the limit is 64"))

    dirname = os.path.basename(os.path.dirname(os.path.abspath(path)))
    # Compare against a normalised directory name. A skill living at a repo
    # root legitimately sits in a differently-cased or punctuated directory,
    # and the frontmatter name wins in that case anyway.
    normalised_dir = re.sub(r"[^a-z0-9]+", "-", dirname.lower()).strip("-")
    if isinstance(name, str) and name and dirname and name != normalised_dir:
        findings.append(Finding(
            "warning",
            f"name {name!r} does not match directory {dirname!r}; the skill is "
            "invoked as /" + name))

    # --- description --------------------------------------------------
    description = fields.get("description")
    if isinstance(description, list):
        description = " ".join(description)

    if not description:
        findings.append(Finding(
            "error",
            "no description; without one Claude falls back to the first "
            "paragraph and the skill rarely matches"))
    else:
        when_to_use = fields.get("when_to_use") or ""
        if isinstance(when_to_use, list):
            when_to_use = " ".join(when_to_use)
        budget_used = len(description) + len(when_to_use)

        if budget_used > DESCRIPTION_BUDGET:
            findings.append(Finding(
                "error",
                f"description plus when_to_use is {budget_used} characters; "
                f"the skill listing truncates at {DESCRIPTION_BUDGET}, so the "
                "tail will never be seen"))

        if "<" in description or ">" in description:
            findings.append(Finding(
                "error",
                "description contains angle brackets, which are rejected when "
                "uploading a skill to claude.ai"))

        if len(description) < 40:
            findings.append(Finding(
                "warning",
                f"description is only {len(description)} characters; add the "
                "trigger conditions so semantic matching has something to bite on"))

        if VAGUE_OPENERS.search(description):
            findings.append(Finding(
                "warning",
                "description opens vaguely; lead with the specific error or "
                "symptom rather than 'helps with...'"))

        if not TRIGGER_HINTS.search(description) and not TRIGGER_HINTS.search(str(when_to_use)):
            findings.append(Finding(
                "warning",
                "description names no trigger condition; include the error "
                "message, symptom, or 'Use when...' clause that should surface it"))

    # --- unknown fields -----------------------------------------------
    for key in fields:
        if key not in KNOWN_FIELDS:
            findings.append(Finding(
                "warning",
                f"unrecognised frontmatter field {key!r}; Claude Code ignores it"))

    # --- body ---------------------------------------------------------
    if not body.strip():
        findings.append(Finding("error", "skill body is empty"))
    else:
        headings = set(re.findall(r"^#+\s*(.+?)\s*$", body, re.MULTILINE))
        for section in RECOMMENDED_SECTIONS:
            if not any(section.lower() in h.lower() for h in headings):
                findings.append(Finding(
                    "warning", f"no '{section}' section"))

    # --- secrets ------------------------------------------------------
    for pattern, label in SECRET_PATTERNS:
        hit = pattern.search(text)
        if hit:
            line = text[:hit.start()].count("\n") + 1
            findings.append(Finding(
                "error",
                f"possible {label} at line {line}; remove it before saving the skill"))


def collect(target):
    if os.path.isfile(target):
        return [target]
    found = []
    for root, _dirs, files in os.walk(target):
        for filename in files:
            if filename == "SKILL.md":
                found.append(os.path.join(root, filename))
    return sorted(found)


def main():
    parser = argparse.ArgumentParser(
        description="Validate SKILL.md files for Claude Code.")
    parser.add_argument("paths", nargs="+",
                        help="SKILL.md files or directories to search")
    parser.add_argument("--strict", action="store_true",
                        help="treat warnings as failures")
    parser.add_argument("--quiet", action="store_true",
                        help="only report files with findings")
    args = parser.parse_args()

    targets = []
    for path in args.paths:
        if not os.path.exists(path):
            print(f"error: {path} does not exist", file=sys.stderr)
            return 1
        targets.extend(collect(path))

    if not targets:
        print("error: no SKILL.md files found", file=sys.stderr)
        return 1

    total_errors = 0
    total_warnings = 0

    for path in targets:
        findings = []
        check(path, findings)
        errors = [f for f in findings if f.level == "error"]
        warnings = [f for f in findings if f.level == "warning"]
        total_errors += len(errors)
        total_warnings += len(warnings)

        if not findings:
            if not args.quiet:
                print(f"ok       {path}")
            continue

        print(f"{'FAIL' if errors else 'warn'}     {path}")
        for finding in errors:
            print(f"  error:   {finding.message}")
        for finding in warnings:
            print(f"  warning: {finding.message}")

    print(f"\n{len(targets)} skill(s) checked, "
          f"{total_errors} error(s), {total_warnings} warning(s)")

    if total_errors:
        return 1
    if args.strict and total_warnings:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
