#!/usr/bin/env bash
#
# Test suite for the Claudeception scripts and manifests.
#
#   tests/run-tests.sh
#
# Exits non-zero on the first category that fails. Safe to run anywhere: all
# state is written under a temporary directory and HOME is redirected, so the
# suite never touches a real skill library.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ACTIVATOR="$REPO_ROOT/scripts/claudeception-activator.sh"
VALIDATOR="$REPO_ROOT/scripts/validate-skill.py"
INVENTORY="$REPO_ROOT/scripts/skill-inventory.sh"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

PASS=0
FAIL=0

ok() { printf '  ok    %s\n' "$1"; PASS=$((PASS + 1)); }
no() { printf '  FAIL  %s\n' "$1"; [ -n "${2:-}" ] && printf '        %s\n' "$2"; FAIL=$((FAIL + 1)); }

assert_eq() {
  if [ "$2" = "$3" ]; then ok "$1"; else no "$1" "expected '$3', got '$2'"; fi
}

hook() {
  # hook <event> <session-id> [extra env assignments...]
  local event="$1" session="$2"; shift 2
  printf '{"session_id":"%s","hook_event_name":"%s"}' "$session" "$event" \
    | env HOME="$TMP/home" XDG_STATE_HOME="$TMP/state" "$@" "$ACTIVATOR"
}

echo "== manifests =="
for manifest in .claude-plugin/plugin.json .claude-plugin/marketplace.json hooks/hooks.json \
                plugins/verified-math/.claude-plugin/plugin.json \
                plugins/quantum-memory/.claude-plugin/plugin.json; do
  if python3 -c "import json,sys; json.load(open(sys.argv[1]))" "$REPO_ROOT/$manifest" 2>/dev/null; then
    ok "$manifest is valid JSON"
  else
    no "$manifest is valid JSON"
  fi
done

# The plugin manifest points at the hooks file; a rename would break the wiring
# silently, since a missing hooks file is not a load error.
HOOKS_REF="$(python3 -c "
import json
print(json.load(open('$REPO_ROOT/.claude-plugin/plugin.json')).get('hooks',''))
")"
if [ -f "$REPO_ROOT/${HOOKS_REF#./}" ]; then
  ok "plugin.json hooks path resolves ($HOOKS_REF)"
else
  no "plugin.json hooks path resolves" "no file at $HOOKS_REF"
fi

# Every command referenced by a hook must exist and be executable.
while IFS= read -r cmd; do
  [ -n "$cmd" ] || continue
  resolved="${cmd//\$\{CLAUDE_PLUGIN_ROOT\}/$REPO_ROOT}"
  resolved="${resolved//\"/}"
  if [ -x "$resolved" ]; then
    ok "hook command is executable ($(basename "$resolved"))"
  else
    no "hook command is executable" "$resolved is missing or not +x"
  fi
done < <(python3 -c "
import json
cfg = json.load(open('$REPO_ROOT/hooks/hooks.json'))
for matchers in cfg.get('hooks', {}).values():
    for matcher in matchers:
        for h in matcher.get('hooks', []):
            if h.get('command'):
                print(h['command'])
")

# Every plugin the marketplace advertises must actually exist at its source
# path, or the entry 404s at install time rather than at review time.
while IFS= read -r entry; do
  [ -n "$entry" ] || continue
  name="${entry%%|*}"
  source_path="${entry#*|}"
  if [ -d "$REPO_ROOT/${source_path#./}" ]; then
    ok "marketplace entry '$name' resolves to $source_path"
  else
    no "marketplace entry '$name' resolves" "no directory at $source_path"
  fi
done < <(python3 -c "
import json
cfg = json.load(open('$REPO_ROOT/.claude-plugin/marketplace.json'))
for p in cfg.get('plugins', []):
    src = p.get('source')
    if isinstance(src, str):
        print(f\"{p['name']}|{src}\")
")

echo "== activator: SessionStart =="
OUT="$(hook SessionStart sess-1)"
if printf '%s' "$OUT" | python3 -c "
import json, sys
d = json.load(sys.stdin)
assert d['hookSpecificOutput']['hookEventName'] == 'SessionStart'
assert 'Claudeception' in d['hookSpecificOutput']['additionalContext']
" 2>/dev/null; then
  ok "emits parseable JSON with SessionStart context"
else
  no "emits parseable JSON with SessionStart context" "$OUT"
fi

echo "== activator: UserPromptSubmit throttling =="
EMITS=0
for _ in 1 2 3 4 5 6; do
  out="$(hook UserPromptSubmit sess-throttle CLAUDECEPTION_REMIND_EVERY=3)"
  [ -n "$out" ] && EMITS=$((EMITS + 1))
done
assert_eq "reminds every 3rd prompt over 6 prompts" "$EMITS" "2"

OUT="$(hook UserPromptSubmit sess-throttle CLAUDECEPTION_REMIND_EVERY=1)"
if printf '%s' "$OUT" | python3 -c "
import json, sys
d = json.load(sys.stdin)
assert d['hookSpecificOutput']['hookEventName'] == 'UserPromptSubmit'
assert d['hookSpecificOutput']['additionalContext'].strip()
" 2>/dev/null; then
  ok "reminder is parseable JSON tagged UserPromptSubmit"
else
  no "reminder is parseable JSON tagged UserPromptSubmit" "$OUT"
fi

# Counters must not bleed between concurrent sessions.
rm -rf "$TMP/state"
hook UserPromptSubmit sess-a CLAUDECEPTION_REMIND_EVERY=2 >/dev/null
OUT_B="$(hook UserPromptSubmit sess-b CLAUDECEPTION_REMIND_EVERY=2)"
assert_eq "separate sessions count independently" "${OUT_B:-empty}" "empty"

echo "== activator: opt-outs and bad input =="
assert_eq "CLAUDECEPTION_DISABLE silences the hook" \
  "$(hook SessionStart s CLAUDECEPTION_DISABLE=1)" ""
assert_eq "REMIND_EVERY=0 sends no reminders" \
  "$(hook UserPromptSubmit s CLAUDECEPTION_REMIND_EVERY=0)" ""
assert_eq "unrecognised events produce no output" \
  "$(hook PreToolUse s)" ""
assert_eq "malformed stdin produces no output" \
  "$(printf 'not json' | env HOME="$TMP/home" XDG_STATE_HOME="$TMP/state" "$ACTIVATOR")" ""

# A session id is used to build a filename, so it must not be able to escape
# the state directory.
hook UserPromptSubmit "../../escape" CLAUDECEPTION_REMIND_EVERY=1 >/dev/null
if [ -e "$TMP/escape.count" ] || [ -e "$TMP/state/escape.count" ]; then
  no "session id cannot traverse out of the state directory"
else
  ok "session id cannot traverse out of the state directory"
fi

echo "== validator =="
if python3 "$VALIDATOR" --quiet "$REPO_ROOT/SKILL.md" "$REPO_ROOT/examples" >/dev/null 2>&1; then
  ok "bundled skill and examples pass validation"
else
  no "bundled skill and examples pass validation" \
     "$(python3 "$VALIDATOR" "$REPO_ROOT/SKILL.md" "$REPO_ROOT/examples" 2>&1 | tail -20)"
fi

mkdir -p "$TMP/bad/broken"
cat > "$TMP/bad/broken/SKILL.md" <<'EOF'
---
name: Not_Kebab_Case
description: Use when the deploy fails with ERR_AUTH during rollout.
---
# Broken
## Problem
p
## Solution
export TOKEN=ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
## Verification
v
EOF
if OUT="$(python3 "$VALIDATOR" "$TMP/bad/broken" 2>&1)"; then
  no "validator rejects a malformed skill" "exited 0"
else
  case "$OUT" in
    *kebab-case*) ok "validator rejects a non-kebab-case name" ;;
    *) no "validator rejects a non-kebab-case name" "$OUT" ;;
  esac
  case "$OUT" in
    *GitHub\ token*) ok "validator flags a leaked credential" ;;
    *) no "validator flags a leaked credential" "$OUT" ;;
  esac
fi

mkdir -p "$TMP/bad/nofm"
printf 'no frontmatter here\n' > "$TMP/bad/nofm/SKILL.md"
if python3 "$VALIDATOR" "$TMP/bad/nofm" >/dev/null 2>&1; then
  no "validator rejects a file with no frontmatter" "exited 0"
else
  ok "validator rejects a file with no frontmatter"
fi

echo "== inventory =="
mkdir -p "$TMP/home/.claude/skills/prisma-connection-pool-exhaustion"
cp "$REPO_ROOT/examples/prisma-connection-pool-exhaustion/SKILL.md" \
   "$TMP/home/.claude/skills/prisma-connection-pool-exhaustion/"

OUT="$(HOME="$TMP/home" "$INVENTORY" prisma 2>&1)"
case "$OUT" in
  *prisma-connection-pool-exhaustion*) ok "finds an installed skill by name" ;;
  *) no "finds an installed skill by name" "$OUT" ;;
esac

OUT="$(HOME="$TMP/home" "$INVENTORY" kubernetes-ingress-timeout 2>&1)"
case "$OUT" in
  *"No installed skill matches"*) ok "reports a clean miss" ;;
  *) no "reports a clean miss" "$OUT" ;;
esac

OUT="$(HOME="$TMP/empty" "$INVENTORY" 2>&1)"
case "$OUT" in
  *"No skills installed"*) ok "handles an empty skill library" ;;
  *) no "handles an empty skill library" "$OUT" ;;
esac

echo
echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ] || exit 1
