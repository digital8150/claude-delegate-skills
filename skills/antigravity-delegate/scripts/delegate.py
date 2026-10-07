#!/usr/bin/env python3
"""Hand an implementation spec to Antigravity (agy, Gemini worker) and report exactly what happened.

Usage:
  python delegate.py --spec SPEC.md [--cwd DIR] [--model MODEL] [--timeout SEC]

What it does:
  1. Snapshots the files under --cwd (copies small text files so real diffs are possible).
  2. Runs `agy -p` (print mode, auto-approve, stream-json) in --cwd, pointing it at the spec.
  3. Re-scans, and writes a run folder containing:
       report.md       summary: changed/added/deleted files, worker's final message,
                       and what the worker ACTUALLY did (commands run, browser use)
       changes.patch   unified diff of every text file the worker touched
       suspects.md     static scan of added lines for fake-implementation smells
       trace.md        every tool call the worker made, with command output
       final.md        the worker's own final message
       events.jsonl    raw event stream (debugging only)
       live.log        one readable line per worker action, written while it runs
       status.json     heartbeat: state, elapsed, time of last event (read by watch.py)
  4. Prints the run folder path and a short summary to stdout.

While the worker runs, a read-only window (watch.py) tails live.log unless --no-watch.
A worker silent for --idle-timeout seconds is considered hung and killed.

Run folders live under the system temp dir, never inside the project.
This script never re-invokes the worker: fixing its mistakes is the reviewer's (Claude's) job.
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
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import smells  # noqa: E402

FALLBACK_MODEL = "gemini-3.8-flash-high"  # used only if `agy models` cannot be queried


def latest_gemini_model(agy: str) -> str:
    """Pick the highest-versioned gemini model from `agy models`, preferring the -high effort tier.

    Version is compared numerically (3.10 > 3.9). On a version tie, pro beats flash.
    """
    try:
        out = subprocess.run([agy, "models"], capture_output=True, text=True, stdin=subprocess.DEVNULL,
                             encoding="utf-8", errors="replace", timeout=60).stdout
    except Exception:
        return FALLBACK_MODEL
    best, best_key = None, None
    for line in out.splitlines():
        m = re.match(r"(gemini-(\d+(?:\.\d+)*)-([a-z]+)-(high|medium|low))\s", line.strip())
        if not m:
            continue
        ver = tuple(int(x) for x in m.group(2).split("."))
        key = (ver, m.group(4) == "high", m.group(3) == "pro")
        if best_key is None or key > best_key:
            best, best_key = m.group(1), key
    return best or FALLBACK_MODEL


IGNORE_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", ".idea", ".vs",
    "dist", "build", "target", "Library", "Temp", "Logs", "obj", ".next",
    ".opencode", ".agent", ".antigravity", ".gemini", ".agents",
}
MAX_COPY_BYTES = 1_000_000       # per file
MAX_TOTAL_COPY_BYTES = 300_000_000
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
BROWSER_PREFIXES = ("browser_", "open_browser", "capture_browser", "click_browser", "read_browser",
                    "execute_browser", "list_browser")

WORKER_MESSAGE = """\
First read the spec with view_file: {spec}
Implement it exactly as written. Touch only the files it names. Do not refactor, rename, or add anything it does not ask for.

Honesty rules (the work will be inspected line by line, so shortcuts only cost everyone time):
- Everything you build must really work. If a function, endpoint, feature, or (for UI) a button/form/link/toggle cannot be genuinely implemented, do NOT leave a stand-in or stub that pretends. Leave it out and list it under UNIMPLEMENTED.
- No hardcoded/mock/placeholder data, fake loading timers, or "success" messages that are not backed by a real operation, unless the spec explicitly asks for mock data.
- Do not open a browser, take screenshots, or generate images unless the spec asks you to.
- Do not claim anything is tested or verified unless you ran it and saw the output. State only what you actually ran.
- If the spec is ambiguous or contradicts the code, do not guess: stop and say so.

When finished, reply with exactly these sections:
FILES CHANGED: ...
UNIMPLEMENTED / PARTIAL: ... (write "none" only if it is true)
COMMANDS RUN: ... (the real ones, with pass/fail)
DEVIATIONS FROM SPEC: ...
"""

WORKER_MESSAGE_HIGH = """\
First read the plan with view_file: {spec}
The plan states the goal, the architecture direction, and the constraints. You own everything else. Explore the codebase, design the details yourself (signatures, file structure, edge-case handling), and implement them following existing conventions. Do not refactor, rename, or add features beyond what the direction implies.

Honesty rules (the work will be inspected line by line, so shortcuts only cost everyone time):
- Everything you build must really work. If a function, endpoint, feature, or (for UI) a button/form/link/toggle cannot be genuinely implemented, do NOT leave a stand-in or stub that pretends. Leave it out and list it under UNIMPLEMENTED.
- No hardcoded/mock/placeholder data, fake loading timers, or "success" messages that are not backed by a real operation, unless the plan explicitly asks for mock data.
- Do not open a browser, take screenshots, or generate images unless the plan asks you to.
- Do not claim anything is tested or verified unless you ran it and saw the output. State only what you actually ran.
- If the plan is ambiguous or contradicts the code, do not guess: stop and say so.

When finished, reply with exactly these sections:
FILES CHANGED: ...
UNIMPLEMENTED / PARTIAL: ... (write "none" only if it is true)
COMMANDS RUN: ... (the real ones, with pass/fail)
DEVIATIONS FROM PLAN: ...
"""

WORKER_MESSAGE_FULL = """\
First read the direction brief with view_file: {spec}
The brief states the goal and constraints agreed with the user. You own everything else — exploration, design, implementation, and how to verify. Work freely within the direction, follow existing conventions, and make your own calls on anything the brief does not settle.

Honesty rules:
- Everything you build must really work. If something cannot be genuinely implemented, leave it out and list it under UNIMPLEMENTED.
- No hardcoded/mock/placeholder data or fake success messages, unless the brief explicitly asks for mock data.
- Do not claim anything is tested or verified unless you ran it and saw the output.
- If the brief contradicts the code in a way that changes the goal, stop and say so.

When finished, reply with exactly these sections:
FILES CHANGED: ...
UNIMPLEMENTED / PARTIAL: ... (write "none" only if it is true)
COMMANDS RUN: ... (the real ones, with pass/fail)
SUMMARY: ... (what you built, in plain language the user can read)
"""

WORKER_MESSAGE_INVESTIGATE = """\
First read the question with view_file: {spec}
This is an investigation, not an implementation. Do NOT modify, create, or delete any file. No hardcoded experiments that leave artifacts behind. Explore the codebase and answer the question: find the root cause, gather evidence (exact file paths, line references, quotations from the code), and state your confidence and what is still uncertain. If you cannot determine the cause, say so and list what you ruled out.

When finished, reply with exactly these sections:
ROOT CAUSE: ... (your best answer, stated plainly)
EVIDENCE: ... (file paths, line references, code quotations backing it)
UNCERTAINTY: ... (what you could not determine, what you ruled out)
"""


WORKER_MESSAGE_RESUME = """\
Your previous turn in this conversation stopped before you finished. It was cut off by an error or a time limit, not ended by you, so the task is still open.
First read the follow-up note with view_file: {spec}
It says what interrupted you and answers anything that was open. Continue the same task from where you left off under the same instructions and honesty rules as before. Check the current state of the files you were editing before changing them; do not redo finished work or re-explore what you already read.
End with the same final sections the original instructions asked for, covering the whole task, not only this turn.
"""

NON_INTERACTIVE = """
This run is non-interactive: nobody can answer questions or permission prompts, so never use ask_question or similar tools. Decide open points yourself, within the spec, and list those decisions in your final message.
"""


def list_files(root: Path):
    """Relative posix paths of files under root. Prefers git (respects .gitignore)."""
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-co", "--exclude-standard", "-z"],
            capture_output=True, check=True,
        ).stdout.decode("utf-8", "replace")
        files = [f for f in out.split("\0") if f and (root / f).is_file()
                 and not (set(Path(f).parts) & IGNORE_DIRS)]
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


def parse_events(raw: str):
    """Return (final_text, tool_records, result_dict, usage)."""
    tools, result, deltas = [], {}, []
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("event") == "result":
            result = ev.get("result", {})
        elif ev.get("event") == "step_update":
            su = ev.get("step_update", {})
            if su.get("step_type") == "tool" and su.get("state") == "DONE":
                info = su.get("tool_info", {})
                tools.append({
                    "name": su.get("tool_name") or info.get("name", "?"),
                    "params": info.get("parameters", {}),
                    "output": info.get("output", ""),
                    "seconds": su.get("duration_seconds"),
                })
            elif su.get("step_type") == "agent_response" and su.get("text_delta"):
                deltas.append(su["text_delta"])
    final = (result.get("response") or "".join(deltas)).strip()
    return final, tools, result


def short(v, n=300):
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    s = re.sub(r"\s+", " ", s) if n <= 400 else s.replace("\r", "")
    return s if len(s) <= n else s[:n] + f"... [+{len(s) - n} chars]"


def brief_params(params):
    """The one or two parameters that say what a tool call is about (command, path, query...)."""
    if not isinstance(params, dict):
        return short(params, 160)
    keys = [k for k in params if re.search(r"command|path|file|pattern|query|url|glob", k, re.I)]
    picked = [params[k] for k in keys[:2]] or [v for v in params.values() if isinstance(v, str)][:1]
    return short(" ".join(p if isinstance(p, str) else json.dumps(p, ensure_ascii=False) for p in picked), 160)


class EventRenderer:
    """agy stream-json events -> human-readable live.log lines ("TOOL ...", "SAY ...").

    Response text arrives as many small deltas, so it is buffered and emitted as one
    SAY line when the response step finishes or the next tool starts.
    """

    def __init__(self):
        self.text = []

    def flush_text(self):
        body, self.text = "".join(self.text).strip(), []
        if not body:
            return []
        body = body if len(body) <= 1500 else body[:1500] + " ..."
        return ["SAY  " + body.replace("\n", "\n" + " " * 16)]

    def __call__(self, line: str):
        try:
            ev = json.loads(line)
        except ValueError:
            return [f"INFO {short(line, 200)}"] if line.strip() else []
        if ev.get("event") == "init":
            init = ev.get("init", {})
            return [f"INFO model {init.get('model', '?')}, cwd {init.get('cwd', '?')}"]
        if ev.get("event") == "result":
            res = ev.get("result", {})
            return self.flush_text() + [f"DONE status {res.get('status', '?')}"]
        su = ev.get("step_update", {})
        kind, state = su.get("step_type"), su.get("state")
        if kind == "agent_response":
            if su.get("text_delta"):
                self.text.append(su["text_delta"])
            return self.flush_text() if state == "DONE" else []
        if kind == "tool":
            info = su.get("tool_info", {})
            name = su.get("tool_name") or info.get("name", "?")
            if state == "ACTIVE":
                return self.flush_text() + [f"TOOL {name}  {brief_params(info.get('parameters', {}))}"]
            if state == "DONE" and (name == "run_command" or (su.get("duration_seconds") or 0) >= 10):
                # Long or shell steps: show how they ended, since that is where runs go wrong.
                return [f"ok   {name} {su.get('duration_seconds') or 0:.0f}s  {short(info.get('output', ''), 200)}"]
        return []


def kill_tree(proc):
    # agy spawns its own children (shells, language servers); kill the whole tree.
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


def run_live(cmd, root: Path, run_dir: Path, timeout: int, idle_timeout: int, status: dict):
    """Run the worker, streaming every event to disk the moment it arrives.

    events.jsonl gets the raw stream, live.log a readable line per action, status.json a
    heartbeat (watch.py renders these). The worker is killed after `timeout` seconds in
    total, or after `idle_timeout` seconds with no event at all, which is what a hung
    worker looks like. Returns (raw_stdout, exit_code, stderr, outcome).
    """
    events_f = open(run_dir / "events.jsonl", "w", encoding="utf-8")
    live_f = open(run_dir / "live.log", "a", encoding="utf-8")
    stderr_path = run_dir / "stderr.log"
    render, chunks = EventRenderer(), []
    # stdin is closed so the CLI can never block waiting for input.
    with open(stderr_path, "w", encoding="utf-8") as stderr_f:
        # PWD too: shells like Git Bash export it, and the worker trusts it over the real cwd,
        # silently working in whatever directory Claude launched this script from.
        proc = subprocess.Popen(cmd, cwd=root, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=stderr_f, text=True, encoding="utf-8", errors="replace",
                                env={**os.environ, "PWD": str(root)})
        status.update(state="running", pid=proc.pid, last_event=time.time(), tools=0)

        def pump():
            for line in proc.stdout:
                chunks.append(line)
                events_f.write(line)
                events_f.flush()
                status["last_event"] = time.time()
                for out in render(line):
                    status["tools"] += out.startswith("TOOL")
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
    """Pop up a separate read-only console tailing this run (Windows). Elsewhere, print how to."""
    watch = Path(__file__).with_name("watch.py")
    if os.name == "nt":
        try:
            subprocess.Popen([sys.executable, str(watch), str(run_dir), "--hold"],
                             creationflags=subprocess.CREATE_NEW_CONSOLE)
            return
        except OSError:
            pass
    print(f"watch live: python \"{watch}\" \"{run_dir}\"", flush=True)


def write_trace(path: Path, tools):
    lines = ["# Worker tool trace", "",
             "Ground truth for what the worker did. Compare against its claims in final.md.", ""]
    for i, t in enumerate(tools, 1):
        lines.append(f"## {i}. {t['name']}")
        lines.append(f"params: `{short(t['params'], 400)}`")
        if t["output"]:
            lines += ["output:", "```", short(t["output"], 1500), "```"]
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", required=True, help="markdown spec/plan/brief file for the worker")
    ap.add_argument("--cwd", default=".", help="project directory the worker runs in")
    ap.add_argument("--model", default=None,
                    help="see `agy models`; default: highest-versioned gemini model (-high), auto-detected")
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
                    help="resume an earlier worker conversation by ID (printed in a failed run's report) "
                         "instead of starting fresh; the --spec file is sent as the follow-up note")
    ap.add_argument("--no-watch", action="store_true",
                    help="don't open the read-only live-view window")
    args = ap.parse_args()

    root = Path(args.cwd).resolve()
    spec = Path(args.spec).resolve()
    if not spec.is_file():
        sys.exit(f"spec not found: {spec}")
    agy = shutil.which("agy")
    if not agy:
        sys.exit("agy not found on PATH (Antigravity CLI)")
    if not args.model:
        # --model, then $ANTIGRAVITY_DELEGATE_MODEL, then config.json next to SKILL.md, then auto-detect.
        args.model = os.environ.get("ANTIGRAVITY_DELEGATE_MODEL")
    if not args.model:
        try:
            config = Path(__file__).resolve().parent.parent / "config.json"
            args.model = json.loads(config.read_text(encoding="utf-8")).get("model")
        except (OSError, ValueError):
            pass
    if not args.model:
        args.model = latest_gemini_model(agy)

    run_dir = Path(tempfile.gettempdir()) / "antigravity-delegate" / time.strftime("%Y%m%d-%H%M%S")
    before_dir = run_dir / "before"
    run_dir.mkdir(parents=True, exist_ok=True)
    # Keep a copy of the spec next to the run so the reviewer can diff claims against it later.
    spec_copy = run_dir / "spec.md"
    shutil.copy2(spec, spec_copy)

    before = stat_map(root)
    skipped = backup(root, before, before_dir)

    if args.timeout is None:
        args.timeout = {"default": 1800, "high": 3600, "full": 3600}[args.level]
    if args.level == "high":
        worker_message = WORKER_MESSAGE_HIGH
    elif args.level == "full":
        worker_message = WORKER_MESSAGE_FULL
    else:
        worker_message = WORKER_MESSAGE
    if args.mode == "investigate":
        worker_message = WORKER_MESSAGE_INVESTIGATE
    worker_message = worker_message.format(spec=spec_copy)
    if args.session:
        # Resuming: the conversation already holds the original spec and the work so far;
        # the --spec file is the follow-up (what broke, answers to open questions).
        worker_message = WORKER_MESSAGE_RESUME.format(spec=spec_copy)
    worker_message += NON_INTERACTIVE

    cmd = [agy, "-p", worker_message, "--model", args.model,
           "--dangerously-skip-permissions", "--output-format", "stream-json",
           "--add-dir", str(run_dir)]
    if args.session:
        cmd += ["--conversation", args.session]

    status = {"state": "starting", "worker": f"agy {args.model}", "cwd": str(root),
              "started": time.time(), "timeout": args.timeout, "idle_timeout": args.idle_timeout}
    write_status(run_dir, status)
    print(f"run folder: {run_dir}\nlive log: {run_dir / 'live.log'}", flush=True)
    if not args.no_watch:
        open_watch_window(run_dir)

    raw, exit_code, stderr, outcome = run_live(cmd, root, run_dir, args.timeout, args.idle_timeout, status)
    timed_out = outcome == "timeout"
    elapsed = time.time() - status["started"]

    final, tools, result = parse_events(raw)
    found = re.search(r'"conversation_id"\s*:\s*"([^"]+)"', raw)
    session = result.get("conversation_id") or (found.group(1) if found else None)
    resume_hint = (f"resume: if the worker was cut off (API error, timeout, stall, crash) rather than done, "
                   f"continue it with --session {session} --spec <follow-up note> --cwd \"{root}\"")
    (run_dir / "final.md").write_text(final or "(no final message)", encoding="utf-8")
    write_trace(run_dir / "trace.md", tools)
    result_status = result.get("status", "NO RESULT EVENT")
    usage = result.get("usage", {})

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
    patch_text = "".join(patch)
    (run_dir / "changes.patch").write_text(patch_text, encoding="utf-8")

    hits = smells.scan_patch(patch_text)
    (run_dir / "suspects.md").write_text("# Static fake-implementation scan\n\n" + smells.render(hits),
                                         encoding="utf-8")

    # What the worker really did, independent of what it says it did.
    counts = Counter(t["name"] for t in tools)
    commands = [short(t["params"].get("CommandLine", t["params"]), 160) for t in tools if t["name"] == "run_command"]
    browser_used = sorted({n for n in counts if n.startswith(BROWSER_PREFIXES)})
    screenshots = counts.get("capture_browser_screenshot", 0)
    images = counts.get("generate_image", 0)

    lines = [
        "# Delegation report",
        f"- model: {args.model}, session: {session}" + (f" (resumed {args.session})" if args.session else ""),
        f"- cwd: {root}",
        f"- status: {result_status}, exit code: {exit_code}{' (TIMED OUT)' if timed_out else ''}"
        + (f" (STALLED: no worker output for {args.idle_timeout}s, killed)" if outcome == "stalled" else ""),
        f"- elapsed: {elapsed:.0f}s, tool calls: {len(tools)}, tokens: {usage.get('total_tokens', '?')}",
        f"- modified ({len(modified)}): {', '.join(modified) or '-'}",
        f"- added ({len(added)}): {', '.join(added) or '-'}",
        f"- deleted ({len(deleted)}): {', '.join(deleted) or '-'}",
        f"- static smells: {len(hits)} (see suspects.md)",
    ]
    if undiffable:
        lines.append(f"- no text diff available (binary/large): {', '.join(sorted(set(undiffable)))}")
    if skipped:
        lines.append(f"- note: {skipped} pre-existing files were too large to snapshot; their diffs may be missing")
    if result_status != "SUCCESS":
        lines.append(f"- worker result: {short(result, 500)}")
    ok = exit_code == 0 and result_status == "SUCCESS"
    # agy sometimes reports ERROR (API 500) on the closing turn after a complete final message.
    complete = "FILES CHANGED" in final or "ROOT CAUSE" in final
    if not ok and complete and outcome == "done":
        lines.append("- note: the final message is complete (all sections present), so the error came after "
                     "the work ended; review it as a finished run, no resume needed")
    elif not ok and session:
        lines.append(f"- {resume_hint}")
    if stderr.strip() and exit_code != 0:
        lines.append("- stderr: " + ANSI.sub("", stderr.strip())[-500:])
    lines += ["", "## What the worker actually did (from the tool trace)",
              f"- tools: {dict(counts) or 'none'}",
              f"- shell commands run ({len(commands)}): " + (" | ".join(commands) if commands else "NONE"),
              f"- browser tools used: {', '.join(browser_used) or 'NONE'}; screenshots taken: {screenshots}; images generated: {images}",
              "- If the final message says something was tested, verified, or looked at in a browser, "
              "that claim must be backed by an entry here or in trace.md. Otherwise treat it as unverified.",
              "", "## Worker's final message", final or "(none)", "",
              f"Full diff: {run_dir / 'changes.patch'}",
              f"Smell scan: {run_dir / 'suspects.md'}",
              f"Tool trace: {run_dir / 'trace.md'}"]
    (run_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")
    status.update(state=outcome if outcome != "done" else ("done" if ok else "failed"),
                  ended=time.time(), summary=lines[2:9])
    write_status(run_dir, status)

    print(f"run folder: {run_dir}")
    print("\n".join(lines[2:9]))
    print(f"session: {session}")
    if not ok and complete and outcome == "done":
        print("note: error came after a complete final message; treat as finished, no resume needed")
    elif not ok and session:
        print(resume_hint)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
