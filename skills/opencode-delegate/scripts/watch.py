#!/usr/bin/env python3
"""Read-only live view of a delegated worker run.

Usage:
  python watch.py [RUN_DIR] [--hold]

Tails RUN_DIR/live.log (one line per worker action, written by delegate.py as events
arrive) and shows a status bar from RUN_DIR/status.json: elapsed time, time since the
worker's last event, tool-call count. The bar turns yellow, then red, when the worker
goes quiet, so a stalled run is obvious within minutes instead of at the timeout.

Without RUN_DIR it attaches to the newest run of either delegate skill. It never
writes to the run folder and never talks to the worker: closing it changes nothing.
--hold keeps the window open after the run finishes (used for auto-opened windows).
"""
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

QUIET_WARN, QUIET_ALARM = 120, 300  # seconds without a worker event
FINISHED = {"done", "failed", "timeout", "stalled", "exploring"}
COLORS = {"TOOL": "\x1b[36m", "SAY ": "\x1b[0m\x1b[1m", "ERR ": "\x1b[31m", "INFO": "\x1b[90m",
          "DONE": "\x1b[32m", "ok  ": "\x1b[90m"}
RESET, YELLOW, RED, GREEN, DIM = "\x1b[0m", "\x1b[33m", "\x1b[41;97m", "\x1b[32m", "\x1b[90m"


def newest_run():
    base = Path(tempfile.gettempdir())
    runs = [d for name in ("opencode-delegate", "antigravity-delegate")
            for d in (base / name).glob("*") if d.is_dir()]
    return max(runs, key=lambda d: d.stat().st_mtime, default=None)


def read_status(run_dir: Path):
    try:
        return json.loads((run_dir / "status.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def mmss(seconds):
    seconds = max(0, int(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


def color_line(line: str):
    # live.log lines look like "[12:34:56] TOOL read  path/to/file"; continuation lines are indented.
    tag = line[11:15] if line.startswith("[") else ""
    if tag in COLORS:
        return f"{DIM}{line[:11]}{RESET}{COLORS[tag]}{line[11:]}{RESET}"
    return line


def status_bar(st):
    """(color, plain text) for the one-line status bar."""
    if not st:
        return DIM, "waiting for the worker to start..."
    now = time.time()
    elapsed = (st.get("ended") or now) - st["started"]
    quiet = now - st.get("last_event", st["started"])
    head = (f" {st.get('worker', 'worker')} | {mmss(elapsed)} elapsed | {st.get('tools', 0)} tool calls, "
            f"{st.get('edits', 0)} edits ")
    if st["state"] in FINISHED:
        return (GREEN if st["state"] == "done" else RED), f" FINISHED: {st['state']} |{head}"
    idle = st.get("idle_timeout")
    tail = f"| last activity {mmss(quiet)} ago" + (f" (auto-kill at {mmss(idle)}) " if idle else " ")
    if quiet >= QUIET_ALARM:
        return RED, f" QUIET{head}{tail}- possibly stuck "
    if quiet >= QUIET_WARN:
        return YELLOW, f" quiet{head}{tail}"
    return GREEN, f" running{head}{tail}"


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    hold = "--hold" in sys.argv
    if os.name == "nt":
        os.system("")  # enables ANSI escape handling in the Windows console
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    run_dir = Path(args[0]) if args else newest_run()
    if not run_dir or not run_dir.is_dir():
        sys.exit("no run folder found")
    sys.stdout.write(f"\x1b]0;worker watch - {run_dir.name}\x07")
    print(f"{DIM}Read-only view of {run_dir}\nClosing this window does not affect the worker.{RESET}\n")

    log, pos, last_bar = run_dir / "live.log", 0, ""
    try:
        while True:
            st = read_status(run_dir)
            new = ""
            if log.exists():
                with open(log, encoding="utf-8", errors="replace") as f:
                    f.seek(pos)
                    new = f.read()
                    # only consume whole lines; a half-written one is picked up next tick
                    cut = new.rfind("\n") + 1
                    new, pos = new[:cut], pos + len(new[:cut].encode("utf-8"))
            width = shutil.get_terminal_size((100, 20)).columns - 1
            if new:
                sys.stdout.write("\r\x1b[K")
                for line in new.splitlines():
                    print(color_line(line))
            color, text = status_bar(st)
            bar = color + text[:width] + RESET  # one row only, or a carriage return cannot reach its start
            if new or bar != last_bar:
                sys.stdout.write("\r\x1b[K" + bar)
                sys.stdout.flush()
                last_bar = bar
            if st and st["state"] in FINISHED and not new:
                print()
                for line in st.get("summary", []):
                    print(line)
                print(f"\n{DIM}Full report: {run_dir / 'report.md'}{RESET}")
                break
            time.sleep(1)
    except KeyboardInterrupt:
        return
    if hold:
        try:
            input(f"\n{DIM}Run finished. Press Enter to close.{RESET}")
        except (EOFError, KeyboardInterrupt):
            pass


if __name__ == "__main__":
    main()
