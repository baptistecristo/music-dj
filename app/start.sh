#!/bin/sh
# Launch the music DJ: daemon + overlay. The browser side needs nothing from
# you -- the extension opens its own Apple Music tab.
# For logs, run instead:  python3 -m daemon.main --verbose
#
# The Windows equivalent is start.cmd. Neither is needed once the native host
# is registered (app/host/register.py): the toolbar icon starts both.
set -e
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null 2>&1 || PY=python
command -v "$PY" >/dev/null 2>&1 || {
  echo "Python 3 not found. Install it, or set PYTHON=/path/to/python." >&2
  exit 1
}

# nohup so closing the terminal does not take the music with it, and the
# output goes somewhere findable rather than nowhere at all.
LOG="${TMPDIR:-/tmp}/music-dj.log"
nohup "$PY" -m daemon.main >>"$LOG" 2>&1 &
nohup "$PY" -m overlay.app >>"$LOG" 2>&1 &

echo "music DJ started. Logs: $LOG"
