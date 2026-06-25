#!/usr/bin/env bash
# Install the `todo` command and (optionally) a daily auto-expire job.
#
#   ./install.sh [/path/to/To-Do.md] [--gc-agent]
#
# Board path may also be given via $TODO_BOARD. Existing config is left intact.
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"

# find a python3 that has curses (needed for the TUI)
PY=""
for c in python3 /opt/homebrew/bin/python3 /usr/bin/python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c "import curses" 2>/dev/null; then
    PY="$(command -v "$c")"; break
  fi
done
[ -z "$PY" ] && { echo "Need a python3 with the curses module."; exit 1; }

mkdir -p "$HOME/.local/bin" "$HOME/.config/todo"

cat > "$HOME/.local/bin/todo" <<EOF
#!/bin/sh
exec "$PY" "$DIR/todo.py" "\$@"
EOF
chmod +x "$HOME/.local/bin/todo"
echo "installed: ~/.local/bin/todo  (python: $PY)"
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *)
  echo "note: add ~/.local/bin to PATH" ;; esac

# config (only if absent — never clobber an existing one)
BOARD=""
for a in "$@"; do case "$a" in --*) ;; *) BOARD="$a";; esac; done
[ -z "$BOARD" ] && BOARD="${TODO_BOARD:-}"
if [ ! -f "$HOME/.config/todo/config.json" ]; then
  if [ -n "$BOARD" ]; then
    cat > "$HOME/.config/todo/config.json" <<EOF
{
  "board": "$BOARD",
  "retain_done_days": 7,
  "done_lane": "Done"
}
EOF
    echo "wrote ~/.config/todo/config.json -> $BOARD"
  else
    echo "no board set yet — re-run: ./install.sh /path/to/To-Do.md  (or export TODO_BOARD)"
  fi
fi

# optional: daily background job that expires old Done cards even when unused
case " $* " in *" --gc-agent "*)
  PLIST="$HOME/Library/LaunchAgents/com.todo-cli.gc.plist"
  cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.todo-cli.gc</string>
  <key>ProgramArguments</key>
  <array><string>$HOME/.local/bin/todo</string><string>gc</string></array>
  <key>StartInterval</key><integer>86400</integer>
  <key>StandardOutPath</key><string>$HOME/.config/todo/gc.log</string>
  <key>StandardErrorPath</key><string>$HOME/.config/todo/gc.log</string>
</dict></plist>
EOF
  launchctl unload "$PLIST" 2>/dev/null || true
  launchctl load "$PLIST"
  echo "installed daily auto-expire agent: com.todo-cli.gc"
  ;;
esac

echo "done. run: todo"
