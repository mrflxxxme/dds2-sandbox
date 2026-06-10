#!/bin/sh
# DDS2 statusline — Claude Code statusLine command
# Requirements: < 200ms, no network calls, compact one-liner with ANSI colors
# Input: JSON from stdin (Claude Code context)

# ANSI colors
RED='\033[0;31m'
YELLOW='\033[0;33m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
DIM='\033[2m'
RESET='\033[0m'

# Read JSON input once
input=$(cat)

# 1. Context % (pre-calculated by Claude Code)
ctx_remaining=$(printf '%s' "$input" | jq -r '.context_window.remaining_percentage // empty' 2>/dev/null)
if [ -n "$ctx_remaining" ]; then
  ctx_int=$(printf '%.0f' "$ctx_remaining")
  if [ "$ctx_int" -le 20 ]; then
    ctx_color="$RED"
  elif [ "$ctx_int" -le 50 ]; then
    ctx_color="$YELLOW"
  else
    ctx_color="$GREEN"
  fi
  ctx_part=$(printf "${ctx_color}ctx:${ctx_int}%%${RESET}")
else
  ctx_part=""
fi

# 2. Git branch + dirty marker (fast, no network)
project_dir=$(printf '%s' "$input" | jq -r '.workspace.project_dir // empty' 2>/dev/null)
if [ -z "$project_dir" ]; then
  project_dir=$(printf '%s' "$input" | jq -r '.cwd // empty' 2>/dev/null)
fi

branch=""
dirty=""
if [ -n "$project_dir" ] && [ -d "$project_dir/.git" ]; then
  branch=$(git -C "$project_dir" branch --show-current 2>/dev/null)
  dirty_check=$(git -C "$project_dir" status --porcelain 2>/dev/null)
  if [ -n "$dirty_check" ]; then
    dirty="*"
  fi
fi

if [ -n "$branch" ]; then
  if [ "$branch" = "main" ]; then
    branch_part=$(printf "${RED}${branch}${dirty}${RESET}")
  elif [ -n "$dirty" ]; then
    branch_part=$(printf "${YELLOW}${branch}${dirty}${RESET}")
  else
    branch_part=$(printf "${GREEN}${branch}${RESET}")
  fi
else
  branch_part=""
fi

# 3. Model (display_name, abbreviated)
model=$(printf '%s' "$input" | jq -r '.model.display_name // empty' 2>/dev/null)
if [ -n "$model" ]; then
  # Shorten "Claude 3.5 Sonnet" -> "sonnet", "Claude Opus 4.8" -> "opus-4.8", etc.
  model_short=$(printf '%s' "$model" | sed 's/Claude //' | tr '[:upper:]' '[:lower:]' | tr ' ' '-')
  model_part=$(printf "${DIM}${model_short}${RESET}")
else
  model_part=""
fi

# 4. Pending learn count
pending_log="${project_dir}/.claude/.pending-learn.log"
learn_count=0
if [ -f "$pending_log" ]; then
  learn_count=$(wc -l < "$pending_log" | tr -d ' ')
fi
if [ "$learn_count" -gt 0 ]; then
  learn_part=$(printf "${YELLOW}/learn:${learn_count}${RESET}")
else
  learn_part=""
fi

# Assemble output — space-separated non-empty parts
output=""
for part in "$ctx_part" "$branch_part" "$model_part" "$learn_part"; do
  if [ -n "$part" ]; then
    if [ -n "$output" ]; then
      output="${output} ${DIM}|${RESET} ${part}"
    else
      output="$part"
    fi
  fi
done

printf '%b\n' "$output"
