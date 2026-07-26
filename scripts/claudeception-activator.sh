#!/usr/bin/env bash
#
# Claudeception activation hook.
#
# Handles two hook events with one script, deciding what to emit from the
# hook_event_name on stdin:
#
#   SessionStart      full evaluation protocol, injected once per session
#   UserPromptSubmit  a one-line reminder, throttled to every Nth prompt
#
# Output is structured JSON (hookSpecificOutput.additionalContext) rather than
# bare stdout, so Claude Code attaches it as context instead of it landing in
# the transcript as loose text.
#
# Environment:
#   CLAUDECEPTION_DISABLE=1        turn the hook off without unwiring it
#   CLAUDECEPTION_REMIND_EVERY=N   prompts between reminders (default 10, 0 = never)
#
# Installed automatically when Claudeception is used as a plugin. For a manual
# install, see the README.

set -uo pipefail

[ "${CLAUDECEPTION_DISABLE:-0}" = "1" ] && exit 0

REMIND_EVERY="${CLAUDECEPTION_REMIND_EVERY:-10}"

INPUT=""
if [ ! -t 0 ]; then
  INPUT="$(cat)"
fi

# Pull a top-level string field out of the hook payload. Uses jq when it is
# available and falls back to sed, which is sufficient because the two fields
# read here (hook_event_name, session_id) are plain strings.
json_field() {
  local key="$1"
  if command -v jq >/dev/null 2>&1; then
    printf '%s' "$INPUT" | jq -r --arg k "$key" '.[$k] // empty' 2>/dev/null
  else
    printf '%s' "$INPUT" \
      | sed -n "s/.*\"$key\"[[:space:]]*:[[:space:]]*\"\([^\"]*\)\".*/\1/p" \
      | head -1
  fi
}

# Collapse stdin into a single JSON string body. The payload text below is
# written without double quotes or backslashes, so newlines are the only thing
# that needs escaping.
to_json_body() {
  awk 'BEGIN { ORS = "" } { if (NR > 1) printf "\\n"; printf "%s", $0 }'
}

emit() {
  local event="$1"
  local body
  body="$(to_json_body)"
  printf '{"hookSpecificOutput":{"hookEventName":"%s","additionalContext":"%s"},"suppressOutput":true}\n' \
    "$event" "$body"
}

EVENT="$(json_field hook_event_name)"
SESSION="$(json_field session_id)"
[ -n "$SESSION" ] || SESSION="unknown"

full_protocol() {
  cat <<'EOF'
Claudeception is active for this session.

After you finish a task, decide whether it produced knowledge worth keeping:

  - Did the solution take real investigation, rather than a documentation lookup?
  - Was the root cause different from what the error message suggested?
  - Did you find a workaround for a tool or framework limitation?
  - Did you learn a project-specific pattern that is not written down anywhere?
  - Would a future session hitting this same problem save meaningful time?

If any answer is yes, invoke the claudeception skill to extract it. The skill
applies its own quality gates and will decline to create a skill when the
knowledge does not clear them, so the cost of evaluating is low.

If every answer is no, carry on. Routine work does not need a skill, and
over-extraction makes the skill library harder to search.
EOF
}

short_reminder() {
  cat <<'EOF'
Claudeception check: if recent work involved non-obvious debugging, a
workaround, or a discovered project-specific pattern, invoke the claudeception
skill to extract it. Otherwise ignore this and continue.
EOF
}

case "$EVENT" in
  SessionStart)
    full_protocol | emit SessionStart
    exit 0
    ;;
  UserPromptSubmit) ;;
  *)
    # Unknown or absent event: nothing sensible to attach.
    exit 0
    ;;
esac

# --- UserPromptSubmit: throttle so the reminder is not re-sent every turn ---

case "$REMIND_EVERY" in
  ''|*[!0-9]*) REMIND_EVERY=10 ;;
esac
[ "$REMIND_EVERY" -eq 0 ] && exit 0

STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/claudeception"
COUNT_FILE=""
if mkdir -p "$STATE_DIR" 2>/dev/null; then
  # Session ids come from Claude Code and are uuid-shaped, but strip anything
  # that could escape the state directory before using one as a filename.
  SAFE_SESSION="$(printf '%s' "$SESSION" | tr -c 'A-Za-z0-9._-' '_')"
  COUNT_FILE="$STATE_DIR/$SAFE_SESSION.count"
  # Opportunistic cleanup of state left behind by finished sessions.
  find "$STATE_DIR" -name '*.count' -type f -mtime +7 -delete 2>/dev/null
fi

if [ -z "$COUNT_FILE" ]; then
  # No writable state directory, so the prompt count cannot be tracked. Emit
  # the short reminder every turn rather than going silent, which keeps the
  # hook useful at the cost of some repetition.
  short_reminder | emit UserPromptSubmit
  exit 0
fi

COUNT=0
if [ -r "$COUNT_FILE" ]; then
  COUNT="$(cat "$COUNT_FILE" 2>/dev/null)"
  case "$COUNT" in
    ''|*[!0-9]*) COUNT=0 ;;
  esac
fi

COUNT=$((COUNT + 1))
printf '%s' "$COUNT" > "$COUNT_FILE" 2>/dev/null

# SessionStart already delivered the full protocol, so stay quiet until the
# session has run long enough for it to have scrolled out of mind.
if [ $((COUNT % REMIND_EVERY)) -eq 0 ]; then
  short_reminder | emit UserPromptSubmit
fi

exit 0
