---
name: opencode-delegate
description: Delegate the code-writing part of a task to a cheaper worker model (whatever model the user runs through the opencode CLI, e.g. GLM) after Claude defines a short contract, then Claude reviews and fixes. Trigger when the user says "delegate to opencode", "opencode/GLM으로 구현", "위임", "opencode한테 시켜", "worker로 구현", or asks for a plan-then-delegate workflow; also for large mechanical implementation work (boilerplate, ports, test suites, repetitive edits).
---

# opencode-delegate

Claude is the architect and reviewer; opencode (running the user's chosen model, typically a cheap one such as GLM) is the builder.

**Rule: the spec is a contract, not an implementation.** It states interfaces, requirements, and constraints, and points at files to imitate — it never contains the code. Delegation only saves money while the spec is far shorter than the code it produces; if specifying a part would require writing it, write that part yourself. And on critical logic, the worker's *approach* is never trusted: if its design is wrong, Claude redesigns and rewrites it. **Claude never sends the worker back to fix its own mistakes: once it has delivered, Claude fixes everything directly.** A run that was cut off before delivering (API/session error, timeout, stall, crash) is different: resume that same session instead of taking over (see Interrupted runs in step 4).

**Delegation levels `default` / `high` / `full`** (`--level`) — spec detail and worker autonomy: `default` — Claude writes a contract spec (interfaces, behaviors, edge cases); the worker implements it. `high` — Claude writes only a big-picture plan; the worker explores the codebase and designs *and* implements the details itself. `full` — Claude agrees the direction with the user (1–2 quick exchanges, then cut) and hands over a short direction brief; the worker owns everything.

**Review depth `full` / `smoke` / `none`** (`--review`): `full` — line-by-line diff review, then Claude fixes directly (the default). `smoke` — run the verification commands and read `report.md` only; skip the full diff read, deep-dive only if something looks wrong. `none` — skip review entirely. Combos: `high --review smoke` (medium work, worker discretion, minimal verification), `default --review smoke` (routine repetitive work), `full --review none` (complete hand-off). Use `full` review unless the user asks otherwise.

**Mode `implement` / `investigate`** (`--mode`): `implement` (default) — worker builds. `--mode investigate` — read-only delegation: the worker explores the codebase and reports its root cause with evidence; Claude spot-checks the cited files itself and delivers the judgment to the user. The worker never modifies files.

**The worker's mistakes are Claude's to fix, never the worker's to redo**; only an interrupted run goes back to the worker, by resuming its session.

## Workflow

### 1. Decide whether to delegate

Delegate when the task is well-defined and mostly mechanical once designed: new modules from a clear interface, multi-file boilerplate, test suites for known behavior, ports/migrations following a pattern, repetitive edits across files, wiring up a designed feature.

Do the work yourself when delegating costs more than it saves: a change you can make in a few lines, work where the real difficulty is figuring out the root cause or the right design (that is your job *before* delegating), or code where subtle correctness matters most (auth, crypto, concurrency, money). If the task is a mix, do the delicate parts yourself and delegate the rest. If the task is diagnosing a problem rather than building (bug hunting, root-cause analysis, codebase questions), that is `--mode investigate`.

### 2. Explore, then define the contract

Read enough to define the contract: the files to touch, the exact interfaces the new code must fit, which existing files already solve a similar problem. The contract must name real paths and real APIs. On tricky logic, state *what* must hold (behavior, edge cases, invariants) — never *how*.

### 3. Write the contract spec

Write the spec to a scratchpad file in the temp directory (not the project):

- **`--level default`**: contract spec per `references/spec-template.md` — interfaces, behaviors, edge cases as requirements (never implementations). Run through the template's pre-send checklist; if it fails, don't delegate.
- **`--level high`**: big-picture plan only — goal, architecture direction, constraints, files allowed to touch, non-goals, verification command. No signatures or detailed behaviors; the worker designs those. One page max. (Less Claude output at the cost of more worker discretion; review is the same.)
- **`--level full`**: direction brief only — goal, scope, constraints agreed with the user. Agree the direction with the user in 1–2 quick exchanges, then cut; no deep exploration, no detailed spec. Write the brief (at most half a page) and delegate.

For large jobs, split into several independent specs that touch **disjoint files**, and run them in parallel (separate background Bash calls). Do not split work that shares files, since concurrent edits collide.

### 4. Run the worker

```bash
python "<skill-dir>/scripts/delegate.py" --spec <spec.md> --cwd <project-dir>
```

`<skill-dir>` is `~/.claude/skills/opencode-delegate`. The worker model is the user's choice, resolved in order: `--model`, the `OPENCODE_DELEGATE_MODEL` environment variable, `"model"` in `<skill-dir>/config.json`, and otherwise opencode's own default model (no `-m` passed). Model IDs look like `provider/model#variant` (`opencode models` lists them). Pass `--model` only if the user names one. If the run fails immediately with a model/provider error, tell the user to set their model in `config.json` rather than guessing one. Default timeout is 30 minutes; override only if asked.

The script snapshots the project, runs `opencode run --auto` with the spec attached, and writes a run folder (under the system temp dir) with `report.md` (files modified/added/deleted, worker's final message), `changes.patch` (real diff), and `final.md`. It never retries on its own; resuming an interrupted run is an explicit decision (below).

**Live view.** The run is not a black box: the script streams the worker's events to disk as they arrive and, on Windows and macOS (Terminal.app), pops up a separate read-only console window (`scripts/watch.py`) showing each tool call and message, with a status bar of elapsed time and time since the last event (yellow after 2 min of silence, red after 5). The user watches the worker there instead of staring at nothing; closing the window does not affect the worker. The first lines of output give the run folder and `live.log` path. When the user asks how the worker is doing, read the tail of `live.log` and `status.json` and summarize it rather than guessing. A worker that emits no event for `--idle-timeout` seconds (default 600; `0` disables) is treated as hung and killed, and the report says STALLED, so a stuck run fails within minutes rather than at the 30-60 minute timeout. Raise it only for specs whose single steps legitimately run silent for longer (a long build). Pass `--no-watch` if the user doesn't want the window; `python scripts/watch.py` with no argument reattaches to the newest run.

**Interrupted runs: resume, don't take over.** Tell *cut off* apart from *done*. A run is cut off when the report shows a non-zero exit, `status: ERROR`, worker errors, TIMED OUT, or STALLED, or when the final message shows it stopped mid-task (for example it tried to ask a question nobody could answer). A run is done when the worker ended its turn with its final summary, even if the work is wrong; that is a review problem, handled in steps 5-6. For a cut-off run:
1. Don't take the task over yourself. The user delegated it to keep this work off Claude, and an infrastructure failure doesn't change that. Don't start a fresh run either: it loses everything the worker already read and did.
2. Read the tail of `live.log` to see where it stopped and why.
3. Write a short follow-up note (not a new spec): what interrupted it, decisions on anything it was unsure about (you are the architect; decide), and what not to redo.
4. Resume the same session: `delegate.py --session <ID> --spec <note> --cwd <same project>` with the same `--level`/`--mode`. The session ID is in `report.md` and in the script's printed output, together with a ready-made resume line.
5. Review the task as a whole afterwards: the resumed run's `changes.patch` covers only what changed after resuming, and the first run's patch covers the rest, so read both.

Resume at most twice per task. If it keeps failing the same way, stop and tell the user what is happening (retry later, change model, or let Claude take over) instead of silently doing the work yourself.

Use `run_in_background: true` for anything likely to take more than a couple of minutes, and you'll be notified when it finishes. The worker runs with auto-approved permissions inside `--cwd`, so make sure `--cwd` is the project the spec is about, and not a parent directory holding unrelated work.

The spec and the files the worker reads are sent to an external model provider. If the code is sensitive in a way the user hasn't cleared, check with them first and keep secrets out of the spec.

### 5. Review as the architect

**Review depth** (`--review`):

- **`full`** (default): treat the output as a pull request from a junior contributor: plausible-looking, possibly wrong. Read `report.md`, then read the actual diff (`changes.patch`) and the touched files. Don't trust the worker's final message as proof; it describes intentions as often as results.
- **`smoke`**: run the verification commands and read `report.md`. Check the touched-file list against the spec and scan for stubs/smells in the report — but skip the line-by-line diff read. Only if something looks wrong (check failed, unexpected files touched, tokens suspiciously low) deep-dive into the diff.
- **`none`**: skip entirely (`--level full` implies this). The run folder is still written; point the user to it.

**`--mode investigate`**: no diff review at all (nothing changed). Spot-check the worker's cited evidence: open 2-3 of the files/lines it referenced and confirm the root cause is real. Then deliver the judgment to the user.

Check, in order (for `--review full`):
1. **Spec conformance**: go item by item through your spec. Anything missing, partial, or stubbed (`TODO`, `pass`, placeholder returns)?
2. **Scope**: files changed outside the spec, renamed or reformatted code, deleted tests, unrequested features. The report lists every touched file, so compare it to the spec's file list.
3. **Correctness**: hallucinated imports/APIs/methods that don't exist in this codebase or library version, wrong edge-case handling, off-by-one, mismatched types, hardcoded values that should be parameters.
4. **Conventions**: matches surrounding naming, style, error handling.
5. **It actually runs**: execute the verification command from the spec (build, tests, lint, type-check). Passing tests the worker wrote prove little; also run or read tests critically.

### 6. Fix it yourself

For every problem found, edit the code directly with the smallest change that makes it right. Keep the worker's acceptable work; don't rewrite for taste. If the worker's approach to critical logic is wrong, redesign and rewrite that logic yourself. Re-run the verification afterward. If patching would be a rewrite, rewrite it and say so. Do not send the worker back to fix its mistakes. (`--review smoke`: fix only what the smoke check exposed. `--review none` / `--mode investigate`: skip this step.)

### 7. Report

Tell the user briefly: what was delegated, what the review found, what you fixed yourself, and the verification result. Be honest about anything unverified or still failing. For `--review none`: relay the worker's summary as-is, with the run folder path — no Claude claims on top. For `--mode investigate`: deliver the verified root cause, the evidence you spot-checked, and remaining uncertainty — you own the judgment, so state it as yours.

## Notes

- Windows: run through Bash/PowerShell as usual. The script handles the `opencode.cmd` shim and closes stdin on the child process (opencode hangs forever waiting for input otherwise, so do not call `opencode run` by hand without `< /dev/null` or the script).
- Pre-existing uncommitted changes are fine: the script diffs against a snapshot taken immediately before the run, not against git HEAD.
- Files in git-ignored locations are not tracked by the change detection, so if the spec asks for output there, inspect it manually.
- To confirm the model and variant exist: `opencode models` (variants are chosen as `provider/model#variant`; an invalid one errors immediately).
