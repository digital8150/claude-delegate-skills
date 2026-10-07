---
name: antigravity-delegate
description: Delegate implementation (backend, scripts, refactors, tests, ports, boilerplate, wiring, UI) to a general-purpose worker, Gemini via the Antigravity CLI (`agy`), after Claude defines a short contract; Claude then reviews adversarially and fixes. Trigger when the user says "antigravity에 위임", "antigravity한테 시켜", "agy로 구현", "gemini한테 시켜", "delegate to antigravity/gemini", "worker로 구현", or asks for a plan-then-delegate workflow with Antigravity; also for large mechanical implementation work (boilerplate, ports, test suites, repetitive edits). Not UI-only — general worker, vision-capable.
---

# antigravity-delegate

Claude plans, the worker (Antigravity `agy`, Gemini) builds, Claude judges and repairs. Same pattern as `opencode-delegate`, with a different worker.

**Rule: the spec is a contract, not an implementation.** It states interfaces, requirements, and constraints, and points at files to imitate — it never contains the code. Delegation only saves money while the spec is far shorter than the code it produces; if specifying a part would require writing it, write that part yourself. The worker reasons through requirements but fakes what nobody specified, and reports success confidently — so review is adversarial and its own summary is never proof. On critical logic, the worker's *approach* is never trusted: if its design is wrong, Claude redesigns and rewrites it. **Claude never sends the worker back to fix its own mistakes: once it has delivered, Claude fixes everything directly.** A run that was cut off before delivering (API/session error, timeout, stall, crash) is different: resume that same session instead of taking over (see Interrupted runs in step 4).

**Delegation levels `default` / `high` / `full`** (`--level`) — spec detail and worker autonomy: `default` — Claude writes a contract spec (interfaces, behaviors, edge cases); the worker implements it. `high` — Claude writes only a big-picture plan; the worker explores the codebase and designs *and* implements the details itself. `full` — Claude agrees the direction with the user (1–2 quick exchanges, then cut) and hands over a short direction brief; the worker owns everything.

**Review depth `full` / `smoke` / `none`** (`--review`): `full` — line-by-line diff review, then Claude fixes directly (the default). `smoke` — run the verification commands and read `report.md`/`suspects.md` only; skip the full diff read, deep-dive only if something looks wrong. `none` — skip review entirely. Combos: `high --review smoke` (medium work, worker discretion, minimal verification), `default --review smoke` (routine repetitive work), `full --review none` (complete hand-off). Use `full` review unless the user asks otherwise.

**Mode `implement` / `investigate`** (`--mode`): `implement` (default) — worker builds. `--mode investigate` — read-only delegation: the worker explores the codebase and reports its root cause with evidence; Claude spot-checks the cited files itself and delivers the judgment to the user. The worker never modifies files.

**The worker's mistakes are Claude's to fix, never the worker's to redo**; only an interrupted run goes back to the worker, by resuming its session.

The worker can see images, which makes it somewhat useful for UI pieces. That is a bonus, not its identity. UI work gets the same contract -> delegate -> review -> fix loop, plus a few extra checks (see `references/review-checklist.md`, UI section), and Claude always makes the final aesthetic call.

## Workflow

### 1. Decide whether to delegate

Delegate when the task is well-defined and mostly mechanical once designed: new modules from a clear interface, multi-file boilerplate, test suites for known behavior, ports and migrations following a pattern, repetitive edits across files, wiring up a designed feature, a screen or components from a clear brief.

Do it yourself when delegating costs more than it saves: a change you can make in a few lines; work where the real difficulty is the root cause or the right design (that is your job *before* delegating); delicate correctness (auth, crypto, concurrency, money); work whose value is in visual polish or interaction feel, since you would end up redoing it. For mixed tasks, do the delicate part yourself and delegate the rest. If the task is diagnosing a problem rather than building (bug hunting, root-cause analysis, codebase questions), that is `--mode investigate`.

### 2. Explore, then define the contract

Read enough to define the contract: the files to touch, the exact interfaces the new code must fit, the real APIs and helpers it must call, existing components and design tokens if UI is involved. The contract must name real paths and real APIs. On tricky logic, state *what* must hold (behavior, edge cases, invariants) — never *how*. If a dependency does not exist yet, either include building it in the contract or explicitly allow labeled mock data in one file.

### 3. Write the contract spec

Write the spec to a file in the scratchpad/temp directory (not the project):

- **`--level default`**: contract spec per `references/spec-template.md` — interfaces, behaviors, edge cases as requirements (never implementations). Add the optional **Behavior contract** section for user-facing interactions and **Visual direction** only when there is visible UI. Run through the template's pre-send checklist; if it fails, don't delegate.
- **`--level high`**: big-picture plan only — goal, architecture direction, constraints, files allowed to touch, non-goals, verification command. No signatures or detailed behaviors; the worker designs those. One page max. (Less Claude output at the cost of more worker discretion; review is the same.)
- **`--level full`**: direction brief only — goal, scope, constraints agreed with the user. Agree the direction with the user in 1–2 quick exchanges, then cut; no deep exploration, no detailed spec. Write the brief (at most half a page) and delegate.

For large jobs, split into several specs that touch **disjoint files** and run them in parallel in separate background Bash calls. Never split work that shares files; concurrent edits collide. Alternatively run specs in sequence, reviewing and fixing each before the next so errors don't compound.

### 4. Run the worker

```bash
python "<skill-dir>/scripts/delegate.py" --spec <spec.md> --cwd <project-dir>
```

`<skill-dir>` is `~/.claude/skills/antigravity-delegate`. The model resolves in order: `--model`, the `ANTIGRAVITY_DELEGATE_MODEL` environment variable, `"model"` in `<skill-dir>/config.json`; with none of those, the script runs `agy models` and picks the highest-versioned Gemini model at the `-high` effort tier (currently `gemini-3.8-flash-high`), so new releases are adopted automatically. Never use `gemini-3.1-pro`; don't pass `--model` unless the user names one. Don't use the Claude models `agy` also lists (you are already the Claude in charge). Default timeout is 30 minutes; override only if asked.

The script snapshots the project, runs `agy -p` with auto-approved permissions, and writes a run folder under the system temp dir: `report.md` (touched files, **what the worker actually did according to the tool trace**, its final message), `changes.patch`, `suspects.md` (static smell scan), `trace.md` (every tool call and command output), `final.md`. It never retries on its own; resuming an interrupted run is an explicit decision (below).

**Live view.** The run is not a black box: the script streams the worker's events to disk as they arrive and, on Windows, pops up a separate read-only console window (`scripts/watch.py`) showing each tool call, shell result, and message, with a status bar of elapsed time and time since the last event (yellow after 2 min of silence, red after 5). The user watches the worker there instead of staring at nothing; closing the window does not affect the worker. The first lines of output give the run folder and `live.log` path. When the user asks how the worker is doing, read the tail of `live.log` and `status.json` and summarize it rather than guessing. A worker that emits no event for `--idle-timeout` seconds (default 600; `0` disables) is treated as hung and killed, and the report says STALLED, so a stuck run fails within minutes rather than at the 30-60 minute timeout. Raise it only for specs whose single steps legitimately run silent for longer (a long build). Pass `--no-watch` if the user doesn't want the window; `python scripts/watch.py` with no argument reattaches to the newest run.

**Interrupted runs: resume, don't take over.** Tell *cut off* apart from *done*. A run is cut off when the report shows a non-zero exit, `status: ERROR`, worker errors, TIMED OUT, or STALLED, or when the final message shows it stopped mid-task (for example it tried to ask a question nobody could answer). A run is done when the worker ended its turn with its final sections, even if the work is wrong; that is a review problem, handled in steps 5-6. For a cut-off run:
1. Don't take the task over yourself. The user delegated it to keep this work off Claude, and an infrastructure failure doesn't change that. Don't start a fresh run either: it loses everything the worker already read and did.
2. Read the tail of `live.log` to see where it stopped and why. (`status: ERROR` can also arrive *after* a complete final message, for example an API 500 on the closing turn; if FILES CHANGED and the other sections are all there, the run is effectively done, so review it instead of resuming.)
3. Write a short follow-up note (not a new spec): what interrupted it, decisions on anything it was unsure about (you are the architect; decide), and what not to redo.
4. Resume the same conversation: `delegate.py --session <ID> --spec <note> --cwd <same project>` with the same `--level`/`--mode`. The session ID is in `report.md` and in the script's printed output, together with a ready-made resume line.
5. Review the task as a whole afterwards: the resumed run's `changes.patch` covers only what changed after resuming, and the first run's patch covers the rest, so read both.

Resume at most twice per task. If it keeps failing the same way, stop and tell the user what is happening (retry later, change model, or let Claude take over) instead of silently doing the work yourself.

Use `run_in_background: true` for anything likely to exceed a couple of minutes. Permissions are auto-approved, so make sure `--cwd` is exactly the project the spec is about, not a parent directory with unrelated work. The spec and files the worker reads go to Google: check with the user before sending sensitive code and keep secrets out of the spec.

### 5. Review as the architect, adversarially

**Review depth** (`--review`):

- **`full`** (default): read `references/review-checklist.md` now; it is the substance of this skill. Treat the output as a pull request from an overconfident junior: plausible, possibly hollow, possibly wrong. In outline:
- **`smoke`**: run the verification commands and read `report.md` + `suspects.md`. Check the touched-file list against the spec and the smell scan leads — but skip the line-by-line diff read. Only if something looks wrong (check failed, unexpected files touched, smells with plausible hits) deep-dive per the `full` path below.
- **`none`**: skip entirely (`--level full` implies this). The run folder (report, trace, patch) is still written; point the user to it.

**`--mode investigate`**: no diff review (nothing changed). Spot-check the worker's cited evidence: open 2-3 of the files/lines it referenced and confirm the root cause is real. Then deliver the judgment to the user.

Checks, in order (for `--review full`):

1. **Evidence first**: `report.md` (scope creep; do the worker's claims match the tool trace?), then `suspects.md`.
2. **Read the actual diff against your contract**, item by item: missing, partial, stubbed, hardcoded, hallucinated APIs or imports, wrong edge cases, convention breaks, files touched outside the spec.
3. **Judge the approach, not just the bugs**: on critical logic, if the worker's design itself is wrong, redesign and rewrite it — do not patch a wrong architecture into looking right.
4. **Run it yourself**: build, tests, lint, type-check, and exercise the real behavior. Check results by observation, not by reading. Ask whether the worker's tests could ever fail.
5. **If there is UI**: also run `scripts/ui_audit.py` and look at the screenshots yourself (see the checklist's UI section).

### 6. Fix it yourself

For each finding, make the smallest correct change. Classify first: **fake** (make it real, or remove it and tell the user it isn't built), **broken**, **off-spec**, **architecture** (redesign and rewrite yourself rather than patch), **off-brief** (visual), **scope** (revert), **ok**. Keep the worker's acceptable work; don't rewrite for taste. Re-run verification after fixing. Do not send the worker back to fix its mistakes. (`--review smoke`: fix only what the smoke check exposed. `--review none` / `--mode investigate`: skip this step.)

### 7. Report to the user

Briefly and bluntly: what was delegated; what the review found, naming fakes and bugs concretely; any worker claims the trace did not support; what you fixed and why; how you verified (commands, screenshots, interactions). State plainly anything unverified or still failing. For `--review none`: relay the worker's SUMMARY as-is, with the run folder path — no Claude claims on top. For `--mode investigate`: deliver the verified root cause, the evidence you spot-checked, and remaining uncertainty — you own the judgment, so state it as yours.

## Notes

- Windows: the script resolves `agy.exe` from PATH and closes stdin on the child. Don't run `agy -p` by hand without redirecting stdin from nothing, or use the script.
- `agy` has browser tools, image generation, and shell access. The worker prompt tells it not to use browser/image tools unless the spec asks, and the tool trace shows whether it did anyway. Never take a "verified in browser" claim at face value; the trace is the ground truth.
- `ui_audit.py` (UI only) needs `pip install playwright` (it drives installed Edge/Chrome, no browser download). If unavailable, use other browser tooling and do the same checks by hand.
- Pre-existing uncommitted changes are fine: diffs are against a snapshot taken just before the run, not git HEAD. Git-ignored locations aren't tracked by change detection, so inspect output there manually.
- `agy models` lists available models; pass the id as `--model`.
