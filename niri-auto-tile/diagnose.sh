#!/usr/bin/env bash
set -u

PLUGIN_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$PLUGIN_DIR/auto-tile.pid"
CONFIG_FILE="$PLUGIN_DIR/runtime-config.json"

ok=0
fail=0

pass() { printf '[OK]   %s\n' "$*"; ok=$((ok + 1)); }
warn() { printf '[WARN] %s\n' "$*"; }
fail_item() { printf '[FAIL] %s\n' "$*"; fail=$((fail + 1)); }

printf 'Niri Auto-Tile v5 diagnostic (v0.3)\n'
printf 'Plugin directory: %s\n\n' "$PLUGIN_DIR"

for command in noctalia niri python3; do
  if path="$(command -v "$command" 2>/dev/null)"; then
    pass "$command found at $path"
  else
    fail_item "$command was not found in PATH"
  fi
done

if [[ -n "${NIRI_SOCKET:-}" ]]; then
  pass "NIRI_SOCKET is set"
  printf '       %s\n' "$NIRI_SOCKET"
else
  fail_item 'NIRI_SOCKET is empty in this terminal/session'
fi

if [[ -f "$PLUGIN_DIR/service.luau" ]] && grep -q 'noctalia.json.decode' "$PLUGIN_DIR/service.luau"; then
  pass 'v0.2 JSON API fix is installed'
else
  fail_item 'service.luau does not contain the v0.2 JSON API fix'
fi

if [[ -f "$PLUGIN_DIR/auto-tile.py" ]] && grep -q 'center_single_window' "$PLUGIN_DIR/auto-tile.py"; then
  pass 'v0.3 single-window centering support is installed'
else
  fail_item 'auto-tile.py does not contain v0.3 centering support'
fi

if [[ -f "$CONFIG_FILE" ]]; then
  pass 'runtime-config.json exists'
  sed 's/^/       /' "$CONFIG_FILE"
else
  warn 'runtime-config.json does not exist; the service may not have loaded yet'
fi

if [[ -s "$PID_FILE" ]]; then
  pid="$(tr -dc '0-9' < "$PID_FILE")"
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    pass "daemon process is alive (PID $pid)"
    ps -p "$pid" -o pid=,stat=,cmd= | sed 's/^/       /'
  else
    fail_item "PID file exists, but process ${pid:-?} is not alive"
  fi
else
  fail_item 'auto-tile.pid is missing or empty; the Python daemon is not running'
fi

if command -v niri >/dev/null 2>&1; then
  if output="$(niri msg --json workspaces 2>&1)"; then
    pass 'niri msg --json workspaces succeeded'
  else
    fail_item "Niri workspaces IPC failed: $output"
  fi

  if output="$(niri msg --json windows 2>&1)"; then
    pass 'niri msg --json windows succeeded'
  else
    fail_item "Niri windows IPC failed: $output"
  fi
fi

printf '\nResult: %d passed, %d failed.\n' "$ok" "$fail"
if ((fail > 0)); then
  printf 'Hover the bar widget too: the widget shows the daemon state and the latest error in its tooltip.\n'
  exit 1
fi
