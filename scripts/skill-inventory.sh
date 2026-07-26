#!/usr/bin/env bash
#
# List the skills already installed, so Claudeception can check whether a
# discovery belongs in an existing skill before writing a new one.
#
# Without this, every extraction looks like a first-time discovery and the
# library slowly fills with near-duplicates.
#
# Usage:
#   scripts/skill-inventory.sh                 list everything
#   scripts/skill-inventory.sh prisma pool     list skills matching any term
#
# Search terms match against both the skill name and its description, case
# insensitively. Exits 0 whether or not anything matched: this is a lookup, not
# a test.

set -uo pipefail

TERMS=("$@")

search_roots() {
  # Project scope first, since a project skill shadows a user one by name.
  [ -n "${CLAUDE_PROJECT_DIR:-}" ] && printf '%s\t%s\n' "$CLAUDE_PROJECT_DIR/.claude/skills" "project"
  printf '%s\t%s\n' "./.claude/skills" "project"
  printf '%s\t%s\n' "$HOME/.claude/skills" "user"
  printf '%s\t%s\n' "$HOME/.claude/plugins" "plugin"
}

# Pull `name:` out of the frontmatter, falling back to the directory name the
# same way Claude Code does.
skill_name() {
  local file="$1"
  local name
  name="$(sed -n '/^---[[:space:]]*$/,/^---[[:space:]]*$/p' "$file" 2>/dev/null \
    | sed -n 's/^name:[[:space:]]*\(.*\)$/\1/p' \
    | head -1 | tr -d "\"'" | xargs 2>/dev/null)"
  if [ -z "$name" ]; then
    name="$(basename "$(dirname "$file")")"
  fi
  printf '%s' "$name"
}

# First non-empty line of the description, whether written inline or as a
# block scalar. Enough to judge overlap without printing the whole thing.
skill_description() {
  local file="$1"
  sed -n '/^---[[:space:]]*$/,/^---[[:space:]]*$/p' "$file" 2>/dev/null \
    | awk '
        /^description:[[:space:]]*[|>]/ { inblock = 1; next }
        inblock && /^[[:space:]]+[^[:space:]]/ {
          sub(/^[[:space:]]+/, ""); print; exit
        }
        inblock { inblock = 0 }
        /^description:[[:space:]]*[^|>[:space:]]/ {
          sub(/^description:[[:space:]]*/, ""); print; exit
        }
      ' | tr -d "\"'" | cut -c1-100
}

matches() {
  [ ${#TERMS[@]} -eq 0 ] && return 0
  local haystack
  haystack="$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')"
  local term
  for term in "${TERMS[@]}"; do
    case "$haystack" in
      *"$(printf '%s' "$term" | tr '[:upper:]' '[:lower:]')"*) return 0 ;;
    esac
  done
  return 1
}

FOUND=0
SEEN_FILES=""

while IFS=$'\t' read -r root scope; do
  [ -d "$root" ] || continue
  # Resolve so the same directory reached by two roots is only listed once.
  resolved="$(cd "$root" 2>/dev/null && pwd -P)" || continue

  while IFS= read -r file; do
    [ -n "$file" ] || continue
    real="$(cd "$(dirname "$file")" 2>/dev/null && pwd -P)/SKILL.md"
    case "$SEEN_FILES" in
      *"|$real|"*) continue ;;
    esac
    SEEN_FILES="$SEEN_FILES|$real|"

    name="$(skill_name "$file")"
    desc="$(skill_description "$file")"

    matches "$name $desc" || continue

    if [ "$FOUND" -eq 0 ]; then
      printf '%-32s  %-8s  %s\n' "SKILL" "SCOPE" "DESCRIPTION"
      printf '%-32s  %-8s  %s\n' "--------------------------------" "--------" "-----------"
    fi
    FOUND=$((FOUND + 1))
    printf '%-32s  %-8s  %s\n' "$name" "$scope" "$desc"
  done < <(find "$resolved" -maxdepth 4 -name SKILL.md -type f 2>/dev/null | sort)
done < <(search_roots)

if [ "$FOUND" -eq 0 ]; then
  if [ ${#TERMS[@]} -eq 0 ]; then
    echo "No skills installed yet."
  else
    echo "No installed skill matches: ${TERMS[*]}"
    echo "Nothing to update, so a new skill is the right call."
  fi
else
  printf '\n%d skill(s) found.' "$FOUND"
  if [ ${#TERMS[@]} -gt 0 ]; then
    printf ' Check these for overlap before writing a new one.'
  fi
  printf '\n'
fi

exit 0
