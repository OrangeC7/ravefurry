#!/usr/bin/env bash
set -Eeuo pipefail

SERVICE="$(systemctl list-unit-files --type=service --no-legend | awk '{print $1}' | grep -Ei 'raveberry.*\.service$' | head -n1 || true)"
[[ -n "$SERVICE" ]] || { echo "No Raveberry service found."; exit 1; }

TMUX_SESSION="raveberry-newt"

# Stop NEWT tmux session if running
if command -v tmux >/dev/null 2>&1 && tmux has-session -t "$TMUX_SESSION" 2>/dev/null; then
  tmux kill-session -t "$TMUX_SESSION"
  echo "Stopped NEWT tmux session: $TMUX_SESSION"
else
  echo "NEWT tmux session not running."
fi

# Stop Raveberry service
sudo systemctl stop "$SERVICE"
sudo systemctl status --no-pager "$SERVICE"

echo "Done."
