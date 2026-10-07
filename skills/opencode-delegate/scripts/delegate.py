#!/usr/bin/env python3
"""Hand an implementation spec to opencode (GLM worker) and report exactly what changed.

Usage:
  python delegate.py --spec SPEC.md [--cwd DIR] [--model MODEL#variant] [--timeout SEC]

What it does:
  1. Snapshots the files under --cwd (copies small text files so real diffs are possible).
  2. Runs `opencode run --auto` with the spec attached, in --cwd.
  3. Re-scans, and writes a run folder containing:
       report.md       summary: changed/added/deleted files, worker's final message
       changes.patch   unified diff of every text file the worker touched
       final.md        the worker's own final message
       events.jsonl    raw event stream (for debugging only)
       live.log        one readable line per worker action, written while it runs
       status.json     heartbeat: state, elapsed, time of last event (read by watch.py)
  4. Prints the run folder path and a short summary to stdout.

While the worker runs, a read-only window (watch.py) tails live.log unless --no-watch.
A worker silent for --idle-timeout seconds is considered hung and killed.

Run folders live under the system temp dir, never inside the project.
This script never re-invokes the worker: fixing the worker's mistakes is the
reviewer's (Claude's) job.
"""
import argparse
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

MODEL_ENV = "OPENCODE_DELEGATE_MODEL"
CONFIG_FILE = Path(__file__).resolve().parent.parent / "config.json"


def resolve_model(cli_value):
    """Worker model: --model, then $OPENCODE_DELEGATE_MODEL, then config.json next to SKILL.md.

    None means "don't pass -m": opencode then uses the default model from the user's own
    opencode config, so the skill works with whatever provider each user has set up.
    """
    if cli_value:
        return cli_value
    if os.environ.get(MODEL_ENV):
        return os.environ[MODEL_ENV]
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8")).get("model") or None
    except (OSError, ValueError):
        return None
IGNORE_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", ".idea", ".vs",
    "dist", "build", "target", "Library", "Temp", "Logs", "obj", ".next",
    ".opencode",
}
MAX_COPY_BYTES = 1_000_000       # per file
MAX_TOTAL_COPY_BYTES = 300_000_000
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


# Over-exploration guard. Workers left to "explore" tend to keep reading (often the source of
# third-party libraries) instead of committing to a design: one run spent 30 minutes and 81 of
# 117 tool calls inside external/imgui, edited nothing, then finished in 3 minutes once Claude
# made the decisions for it. So: minutes allowed before the first file edit, per --level.
EXPLORE_LIMIT_MIN = {"default": 8, "high": 12, "full": 15}
EDIT_TOOL = re.compile(r"edit|write|patch|replace|create|apply", re.I)
THIRD_PARTY = re.compile(r"[\\/](external|extern|third[_-]?party|3rdparty|vendor|vendored|deps|node_modules|"
                         r"site-packages|\.cargo|pkg[\\/]mod)[\\/]", re.I)
EXPLORE_RULES = (
    " Explore with purpose: read the files the plan points to first, then only what the next decision needs. "
    "Stay inside this project's own code: do not read the source of third-party libraries or vendored "
    "dependencies (external/, third_party/, vendor/, node_modules/ ...); how this project already calls them, "
    "plus their headers, is enough. When unsure how a library behaves, use its standard documented usage, "
    "note the assumption, and check it by building or running, not by reading its internals. Start editing "
    "early: if you have not changed any file after roughly {limit} minutes, the run is stopped."
)


def list_files(root: Path):
    """Relative posix paths of files under root. Prefers git (respects .gitignore)."""
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-co", "--exclude-standard", "-z"],
            capture_output=True, check=True,
        ).stdout.decode("utf-8", "replace")
        files = [f for f in out.split("\0") if f and (root / f).is_file()]
        return sorted(set(files))
    except Exception:
        pass
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS]
        for name in filenames:
            files.append(Path(dirpath, name).relative_to(root).as_posix())
    return sorted(files)


def stat_map(root: Path):
    result = {}
    for rel in list_files(root):
        try:
            st = (root / rel).stat()
            result[rel] = (st.st_size, st.st_mtime_ns)
        except OSError:
            pass
    return result


def backup(root: Path, snap: dict, dest: Path):
    total, skipped = 0, 0
    for rel, (size, _) in snap.items():
        if size > MAX_COPY_BYTES or total + size > MAX_TOTAL_COPY_BYTES:
            skipped += 1
            continue
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(root / rel, target)
            total += size
        except OSError:
            skipped += 1
    return skipped


def read_text(path: Path):
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if b"\0" in data:
        return None
    return data.decode("utf-8", "replace").splitlines(keepends=True)


def short(v, n=300):
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= n else s[:n] + f"... [+{len(s) - n} chars]"


def brief_params(params):
    """The one or two parameters that say what a tool call is about (command, path, pattern...)."""
    if not isinstance(params, dict):
        return short(params, 160)
    keys = [k for k in params if re.search(r"command|path|file|pattern|query|url|glob", k, re.I)]
    picked = [params[k] for k in keys[:2]] or [v for v in params.values() if isinstance(v, str)][:1]
    return short(" ".join(p if isinstance(p, str) else json.dumps(p, ensure_ascii=False) for p in picked), 160)


def render_event(line: str):
    """One opencode JSON event -> human-readable live.log lines ("TOOL ...", "SAY ...")."""
    try:
        ev = json.loads(line)
    except ValueError:
        return [f"INFO {short(line, 200)}"] if line.strip() else []
    kind, part = ev.get("type"), ev.get("part", {})
    if kind in ("tool", "tool_use"):
        state = part.get("state", {})
        out = [f"TOOL {part.get('tool', '?')}  {brief_params(state.get('input', {}))}"]
        if state.get("status") == "error":
            out.append(f"ERR  {short(state.get('error', ''), 300)}")
        return out
    if kind == "text" and part.get("text", "").strip():
        body = part["text"].strip()
        body = body if len(body) <= 1500 else body[:1500] + " ..."
        return ["SAY  " + body.replace("\n", "\n" + " " * 16)]
    if kind == "error":
        return [f"ERR  {short(ev.get('error', ev), 300)}"]
    return []


def kill_tree(proc):
    # opencode is a .cmd shim around node: killing only the shim would orphan the worker.
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
    else:
        proc.kill()


def write_status(run_dir: Path, status: dict):
    tmp = run_dir / "status.json.tmp"
    tmp.write_text(json.dumps(status), encoding="utf-8")
    try:
        os.replace(tmp, run_dir / "status.json")
    except OSError:
        pass  # watcher had it open this instant (Windows); the next tick rewrites it


def run_live(cmd, root: Path, run_dir: Path, timeout: int, idle_timeout: int, status: dict,
             explore_deadline=None):
    """Run the worker, streaming every event to disk the moment it arrives.

    events.jsonl gets the raw stream, live.log a readable line per action, status.json a
    heartbeat (watch.py renders these). The worker is killed after `timeout` seconds in
    total, after `idle_timeout` seconds with no event at all (hung), or, if it has still
    not edited any file at `explore_deadline` (epoch seconds), as "exploring".
    Returns (raw_stdout, exit_code, stderr, outcome).
    """
    events_f = open(run_dir / "events.jsonl", "a", encoding="utf-8")
    live_f = open(run_dir / "live.log", "a", encoding="utf-8")
    stderr_path = run_dir / "stderr.log"
    chunks = []
    # stdin must be closed: opencode otherwise waits for piped input and hangs forever.
    with open(stderr_path, "w", encoding="utf-8") as stderr_f:
        # PWD too: shells like Git Bash export it, and the worker trusts it over the real cwd,
        # silently working in whatever directory Claude launched this script from.
        proc = subprocess.Popen(cmd, cwd=root, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=stderr_f, text=True, encoding="utf-8", errors="replace",
                                env={**os.environ, "PWD": str(root)})
        status.update(state="running", pid=proc.pid, last_event=time.time())
        for key in ("tools", "edits", "third_party"):
            status.setdefault(key, 0)

        def pump():
            for line in proc.stdout:
                chunks.append(line)
                events_f.write(line)
                events_f.flush()
                status["last_event"] = time.time()
                for out in render_event(line):
                    if out.startswith("TOOL"):
                        status["tools"] += 1
                        if EDIT_TOOL.search(out.split()[1]):
                            status["edits"] += 1
                            status.setdefault("first_edit", time.time())
                        elif THIRD_PARTY.search(out):
                            status["third_party"] += 1
                    live_f.write(f"[{time.strftime('%H:%M:%S')}] {out}\n")
                live_f.flush()

        reader = threading.Thread(target=pump, daemon=True)
        reader.start()
        outcome = "done"
        while proc.poll() is None:
            now = time.time()
            if now - status["started"] > timeout:
                outcome = "timeout"
            elif idle_timeout and now - status["last_event"] > idle_timeout:
                outcome = "stalled"
            elif explore_deadline and not status["edits"] and now > explore_deadline:
                outcome = "exploring"
            if outcome != "done":
                live_f.write(f"[{time.strftime('%H:%M:%S')}] ERR  killing worker: {outcome}\n")
                live_f.flush()
                kill_tree(proc)
                break
            write_status(run_dir, status)
            time.sleep(1)
        proc.wait()
        reader.join(timeout=10)
    events_f.close()
    live_f.close()
    stderr = stderr_path.read_text(encoding="utf-8", errors="replace")
    return "".join(chunks), (proc.returncode if outcome == "done" else -1), stderr, outcome


def open_watch_window(run_dir: Path):
    """Pop up a separate read-only console tailing this run (Windows, macOS). Elsewhere, print how to."""
    watch = Path(__file__).with_name("watch.py")
    if os.name == "nt":
        try:
            subprocess.Popen([sys.executable, str(watch), str(run_dir), "--hold"],
                             creationflags=subprocess.CREATE_NEW_CONSOLE)
            return
        except OSError:
            pass
    elif sys.platform == "darwin":
        # Terminal.app window via AppleScript; first use asks for Automation permission.
        import shlex
        # The command goes in as argv, never spliced into the AppleScript source.
        shell_cmd = " ".join(shlex.quote(a) for a in [sys.executable, str(watch), str(run_dir), "--hold"])
        script = 'on run argv\ntell application "Terminal" to do script (item 1 of argv)\nend run'
        try:
            r = subprocess.run(["/usr/bin/osascript", "-e", script, shell_cmd],
                               capture_output=True, timeout=15)
            if r.returncode == 0:
                return
        except (OSError, subprocess.SubprocessError):
            pass
    print(f"watch live: python \"{watch}\" \"{run_dir}\"", flush=True)


def parse_events(raw: str):
    texts, tools, tokens, errors = [], 0, None, []
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = ev.get("type")
        part = ev.get("part", {})
        if kind == "text":
            texts.append(part.get("text", ""))
        elif kind in ("tool", "tool_use"):
            tools += 1
        elif kind == "step_finish":
            tokens = part.get("tokens", tokens)
        elif kind == "error":
            errors.append(json.dumps(ev.get("error", ev))[:500])
    return "".join(texts).strip(), tools, tokens, errors


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", required=True, help="markdown spec/plan/brief file for the worker")
    ap.add_argument("--cwd", default=".", help="project directory the worker runs in")
    ap.add_argument("--model", default=None,
                    help=f"provider/model#variant (see `opencode models`); default: ${MODEL_ENV}, "
                         "then config.json's \"model\", then opencode's own default model")
    ap.add_argument("--level", choices=["default", "high", "full"], default="default",
                    help="spec detail: default=contract spec, high=big-picture plan, full=direction brief")
    ap.add_argument("--review", choices=["full", "smoke", "none"], default="full",
                    help="review depth after the run: full=line-by-line diff review (default), "
                         "smoke=run verification + read report only, none=skip (informational only)")
    ap.add_argument("--mode", choices=["implement", "investigate"], default="implement",
                    help="implement: worker builds (default). investigate: read-only; worker explores "
                         "the codebase and reports findings/root causes instead of changing files")
    ap.add_argument("--timeout", type=int, default=None,
                    help="seconds before the worker is killed (default: 1800, or 3600 for high/full)")
    ap.add_argument("--idle-timeout", type=int, default=600,
                    help="kill the worker after this many seconds with no event at all (hung); 0 disables")
    ap.add_argument("--session", default=None,
                    help="continue an earlier opencode session (its ID, e.g. from a failed run's events.jsonl) "
                         "instead of starting a new one; the spec file is sent as the follow-up message")
    ap.add_argument("--explore-limit", type=float, default=None,
                    help="minutes the worker may read before its first file edit; then it is stopped and "
                         "handed back so Claude can make the decisions it is stuck on (default: 8/12/15 for "
                         "default/high/full; 0 disables; never applies to --mode investigate)")
    ap.add_argument("--no-watch", action="store_true",
                    help="don't open the read-only live-view window")
    args = ap.parse_args()

    root = Path(args.cwd).resolve()
    spec = Path(args.spec).resolve()
    if not spec.is_file():
        sys.exit(f"spec not found: {spec}")
    oc = shutil.which("opencode")
    if not oc:
        sys.exit("opencode not found on PATH")

    run_dir = Path(tempfile.gettempdir()) / "opencode-delegate" / time.strftime("%Y%m%d-%H%M%S")
    before_dir = run_dir / "before"
    run_dir.mkdir(parents=True, exist_ok=True)

    before = stat_map(root)
    skipped = backup(root, before, before_dir)

    if args.level == "high":
        message = (
            "The attached file is a high-level plan: goal, architecture direction, and constraints. "
            "You own the rest. Explore the codebase, design the details yourself (signatures, file "
            "structure, edge-case handling), and implement them following existing conventions. "
            "Do not refactor, rename, or add features beyond what the direction implies. "
            "If the plan is ambiguous or contradicts the code, do not guess: stop and say so in your final message. "
            "When finished, reply with a short list of files changed and any part you could not complete."
        )
    elif args.level == "full":
        message = (
            "The attached file is a direction brief: goal and constraints agreed with the user. "
            "You own everything else — exploration, design, implementation, and how to verify. "
            "Work freely within the direction, follow existing conventions, and make your own calls "
            "on anything the brief does not settle. When finished, reply with a summary of what you "
            "built, files changed, and anything you left out."
        )
    else:
        message = (
            "Implement the attached spec exactly as written. Touch only the files it names. "
            "Do not refactor, rename, or add anything it does not ask for. "
            "If the spec is ambiguous or contradicts the code, do not guess: stop and say so in your final message. "
            "When finished, reply with a short list of files changed and any spec item you could not complete."
        )
    if args.mode == "investigate":
        message = (
            "This is an investigation, not an implementation. Do NOT modify, create, or delete any file. "
            "Explore the codebase and answer the question in the attached file: find the root cause, "
            "gather evidence (exact file paths, line references, quotations from the code), and state "
            "your confidence and what is still uncertain. If you cannot determine the cause, say so "
            "and list what you ruled out. When finished, reply with: root cause / evidence / uncertainty."
        )
    if args.session:
        message = (
            "Your previous run in this session stopped before finishing (it ended on a session error, not by you). "
            "The attached file answers what was open. Continue the same task from where you left off; do not "
            "re-explore what you already read. " + message
        )
    message += (" This run is non-interactive: never use the question tool; nobody can answer. "
                "Decide open points yourself and note them in your final message.")
    if args.explore_limit is None:
        args.explore_limit = EXPLORE_LIMIT_MIN[args.level]
    if args.mode == "investigate":
        args.explore_limit = 0  # reading is the whole job
    if args.explore_limit:
        message += EXPLORE_RULES.format(limit=f"{args.explore_limit:g}")
    if args.timeout is None:
        args.timeout = {"default": 1800, "high": 3600, "full": 3600}[args.level]
    args.model = resolve_model(args.model)
    cmd = [oc, "run"] + (["-m", args.model] if args.model else []) + ["--auto", "--format", "json"]
    model_label = args.model or "opencode default model"
    cmd += ["--session", args.session] if args.session else ["--title", "delegated-implementation"]
    cmd += ["--file", str(spec), "--", message]
    status = {"state": "starting", "worker": f"opencode {model_label}", "cwd": str(root),
              "started": time.time(), "timeout": args.timeout, "idle_timeout": args.idle_timeout}
    write_status(run_dir, status)
    print(f"run folder: {run_dir}\nlive log: {run_dir / 'live.log'}", flush=True)
    if not args.no_watch:
        open_watch_window(run_dir)

    limit_s = args.explore_limit * 60
    raw, exit_code, stderr, outcome = run_live(
        cmd, root, run_dir, args.timeout, args.idle_timeout, status,
        explore_deadline=status["started"] + limit_s if limit_s else None)
    # No automatic nudge on "exploring": a generic "hurry up" can't make the decision the worker
    # is stuck on. Stop early and hand back to Claude, who writes a decision note and resumes.
    found = re.search(r'"sessionID"\s*:\s*"([^"]+)"', raw)
    timed_out = outcome == "timeout"
    elapsed = time.time() - status["started"]

    final, tool_calls, tokens, errors = parse_events(raw)
    session = found.group(1) if found else args.session
    resume_hint = (f"resume: if the worker was cut off (API/session error, timeout, stall, crash) rather than done, "
                   f"continue it with --session {session} --spec <follow-up note> --cwd \"{root}\"")
    if outcome == "exploring":
        resume_hint = (f"resume: the worker kept reading instead of deciding. Look at what it was reading in "
                       f"live.log, make those decisions yourself in a short note (which API, which layout, "
                       f"which approach), then --session {session} --spec <note> --cwd \"{root}\"")
    (run_dir / "final.md").write_text(final or "(no final message)", encoding="utf-8")

    after = stat_map(root)
    added = sorted(set(after) - set(before))
    deleted = sorted(set(before) - set(after))
    modified = sorted(r for r in set(before) & set(after) if before[r] != after[r])

    patch, undiffable = [], []
    for rel in modified:
        old = read_text(before_dir / rel) if (before_dir / rel).exists() else None
        new = read_text(root / rel)
        if old is None or new is None:
            undiffable.append(rel)
            continue
        if old == new:
            continue
        patch.extend(difflib.unified_diff(old, new, f"a/{rel}", f"b/{rel}"))
    for rel in added:
        new = read_text(root / rel)
        if new is None:
            undiffable.append(rel)
            continue
        patch.extend(difflib.unified_diff([], new, "/dev/null", f"b/{rel}"))
    for rel in deleted:
        old = read_text(before_dir / rel) if (before_dir / rel).exists() else None
        if old is None:
            undiffable.append(rel)
            continue
        patch.extend(difflib.unified_diff(old, [], f"a/{rel}", "/dev/null"))
    (run_dir / "changes.patch").write_text("".join(patch), encoding="utf-8")

    # Keep the pre-run copies only as long as needed for the review.
    lines = [
        "# Delegation report",
        f"- model: {model_label}, session: {session}" + (f" (resumed {args.session})" if args.session else ""),
        f"- cwd: {root}",
        f"- exit code: {exit_code}{' (TIMED OUT)' if timed_out else ''}"
        + (f" (STALLED: no worker output for {args.idle_timeout}s, killed)" if outcome == "stalled" else "")
        + (f" (OVER-EXPLORING: no file edited within {args.explore_limit:g} min, stopped)" if outcome == "exploring" else ""),
        f"- elapsed: {elapsed:.0f}s, tool calls: {tool_calls}, tokens: {tokens}",
        "- exploration: first edit "
        + (f"after {status['first_edit'] - status['started']:.0f}s" if status.get("first_edit") else "never")
        + f", third-party/vendored reads: {status.get('third_party', 0)} of {status.get('tools', 0)} tool calls",
        f"- modified ({len(modified)}): {', '.join(modified) or '-'}",
        f"- added ({len(added)}): {', '.join(added) or '-'}",
        f"- deleted ({len(deleted)}): {', '.join(deleted) or '-'}",
    ]
    if undiffable:
        lines.append(f"- no text diff available (binary/large): {', '.join(sorted(set(undiffable)))}")
    if skipped:
        lines.append(f"- note: {skipped} pre-existing files were too large to snapshot; their diffs may be missing")
    if errors:
        lines.append("- worker errors: " + " | ".join(errors))
    if stderr.strip() and exit_code != 0:
        lines.append("- stderr: " + ANSI.sub("", stderr.strip())[-500:])
    ok = exit_code == 0 and not errors
    if not ok and session:
        lines.append(f"- {resume_hint}")
    lines += ["", "## Worker's final message", final or "(none)", "",
              f"Full diff: {run_dir / 'changes.patch'}"]
    (run_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")
    status.update(state=outcome if outcome != "done" else ("done" if ok else "failed"),
                  ended=time.time(), summary=lines[2:10])
    write_status(run_dir, status)

    print(f"run folder: {run_dir}")
    print("\n".join(lines[2:10]))
    print(f"session: {session}")
    if not ok and session:
        print(resume_hint)
    sys.exit(0 if exit_code == 0 else 1)


if __name__ == "__main__":
    main()
