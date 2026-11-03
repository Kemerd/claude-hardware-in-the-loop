#!/usr/bin/env bash
# ======================================================================================================================
#  handoff_hook.sh - Claude Code PostToolUse hook: tells the lead AND every subagent to stop and write a resume
#                    document once the usage tripwire (usage_statusline.py) has created harness/swarm/HANDOFF_NOW.
#
#  Interpreter: bash (Git Bash on Windows). Wired in .claude/settings.local.json (see templates/claude/).
#
#  Fast path: two file tests and an exit. The hook runs after EVERY tool call of every agent, so it must cost nothing
#  when the flag is absent. The hook payload on stdin is always drained, so the harness never sees a broken pipe on a
#  large tool result.
#
#  HANDOFF_NOW present     -> print handoff_hook.json (hookSpecificOutput.additionalContext = the resume instructions)
#  HANDOFF_SELFTEST present -> answer only calls from the main session (no "agent_id" in the payload) with a short
#                              "hook is live" context, so the lead can prove the wiring without disturbing subagents
#  neither                 -> silent exit 0
#  The flag is never removed by this hook or by the status line: the lead removes it by hand after the reset.
# ======================================================================================================================
F="$(cd "$(dirname "$0")" && pwd)"
if [ -f "$F/HANDOFF_NOW" ]; then
  cat > /dev/null
  if [ -f "$F/handoff_hook.json" ]; then
    cat "$F/handoff_hook.json"
  else
    # the instruction file is missing: still stop the swarm with a minimal message rather than staying silent
    printf '%s' '{"hookSpecificOutput":{"hookEventName":"PostToolUse","additionalContext":"HANDOFF_NOW is set: stop starting work, commit and push your branch, release your slots, write your resume document under harness/swarm/resume_<date>/ and return its path."}}'
  fi
  exit 0
fi
if [ -f "$F/HANDOFF_SELFTEST" ]; then
  in=$(cat)
  case "$in" in
    *'"agent_id"'*) ;;
    *) printf '%s' '{"hookSpecificOutput":{"hookEventName":"PostToolUse","additionalContext":"Handoff hook self-test: the PostToolUse hook is live."}}' ;;
  esac
  exit 0
fi
cat > /dev/null
exit 0
