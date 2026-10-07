# Review checklist: reviewing a Gemini worker's output

Read this fully before reviewing. The stance: **the worker's report is a claim, not evidence.** Treat the output as a pull request from an overconfident junior. The user has seen Google models produce work that looks complete but is hollow, so hunt for hollowness first and polish second. Passing the build means almost nothing.

Failure families to look for (any kind of task, not just UI):

- **구라 구현 (fake implementation)**: code that looks like the feature but isn't. Hardcoded or mock data presented as real, functions returning constants, timers that simulate loading or success, swallowed errors that hide failure, tests that cannot fail, "TODO / in a real app" behind a working-looking surface.
- **Hallucination**: imports, APIs, methods, flags, or package versions that don't exist in this codebase or library version.
- **Scope creep**: files changed outside the spec, renamed or reformatted code, deleted tests, unrequested features.
- **구라 버튼 (fake button)**, for UI: controls that look interactive but aren't wired (empty or log-only handlers, `href="#"`, a toast saying "Saved!" with no save, toggles that change looks but not state, filters that don't filter).

## Part A: every task

### 1. Read the evidence the script produced (cheap, mechanical)

- `report.md`: files touched vs. the spec's file list.
- "What the worker actually did": compare with its final message. It claims tests pass but no matching command is in `trace.md`, or the trace shows failures? That claim is unverified. It claims it verified in a browser but browser tools = NONE? Same. Say so in your report.
- Final message sections: `UNIMPLEMENTED: none` deserves suspicion; prove it. When items are listed, that is the honest behavior; check they match reality.
- `suspects.md`: leads from a regex scan. Open each hit in context. No hits is not a clean bill of health.

### 2. Read the diff yourself against the spec

Go item by item through the spec. Don't skim.
1. **Conformance**: anything missing, partial, stubbed (`TODO`, `pass`, placeholder returns)?
2. **Correctness**: wrong edge-case handling, off-by-one, mismatched types, swallowed errors, hardcoded values that should be parameters, resource leaks, wrong async handling.
3. **Reality of dependencies**: every import, API, endpoint, and helper it calls actually exists here with that signature.
4. **Data origin**: follow every value that is displayed or returned back to its real source. Literal arrays, `Math.random()`, repeated "John Doe" or round numbers are fake data.
5. **Conventions**: naming, style, error handling match the surroundings.
6. **Scope**: compare touched files to the spec's list; revert extras.

### 3. Run it, then try to break it (do not skip, do not delegate to the worker)

- Run the spec's verification commands yourself (build, tests, lint, type-check) and read the real output.
- Exercise the real behavior: call the function/endpoint/CLI with normal input, then with bad input (empty, huge, special characters, failure of a dependency). Fakes tend to "succeed" regardless.
- If a test the worker wrote passes, ask whether it could ever fail. Temporarily break the code under test and see whether the test notices. Tests against a hand-rolled mock of the thing they claim to test prove nothing.
- If the spec had a Behavior contract: go row by row, perform the action, and check the **observable proof** (reload and see if it persisted, watch the request, inspect storage/DB, open the produced file). A change that vanishes on reload is fake.

## Part B: only if the change has UI

### 4. Mechanical audit

`python <skill-dir>/scripts/ui_audit.py <url-or-html> --out <dir> --steps <steps.json>` gives screenshots at desktop and mobile (initial and seeded), console errors, failed requests (hallucinated image/CDN URLs show up here), a reload-persistence check, and every interactive element clicked in a fresh page with the observable effect recorded. Every `NOTHING` row needs a verdict: justified (the already-active "All" filter is) or fake.

**Seed the app with `--steps`** (e.g. `[{"fill":["input","Dune"]},{"press":"Enter"}]`) whenever it starts empty or behind a state. Controls that only exist once data exists (per-row Delete/Toggle, menus, dialogs) are invisible otherwise, and the empty-state screenshot looks fine regardless. The audit only sees what a click changes in the page; it can't tell a real save from a toast, so pair it with the persistence proof from step 3.

Also look in the diff for interactive elements NOT in the Behavior contract (decorative buttons, extra nav links, invented stat cards). Unlisted interactive UI is a red flag by itself.

### 5. Judge the visuals with your own eyes

The worker may have a decent eye, but the final aesthetic call is Claude's. The worker's own praise ("modern, polished UI") is worth nothing.

- **Read the screenshots** (`Read` on the png) at both viewports. Look; don't infer from CSS. Also check one interaction state (hover/focus/open menu/error) with your own browser tooling if available.
- Judge against the spec's Visual direction and the surrounding product: hierarchy, spacing rhythm, alignment, type scale, color restraint and contrast, density, consistency with existing components and tokens.
- Look for AI-slop tells: gradient-everything, glass cards, emoji icons, centered hero-with-three-cards, equal-weight everything, invented metrics, stock image URLs, lorem ipsum.
- Check responsiveness for real: horizontal overflow, clipped text, touch targets, layout at 390px.
- For anything beyond a small fix, use `impeccable` (critique/polish) and `design-taste-frontend` (anti-slop) as your own references. Their output is Claude's judgment and may override the worker's choices.
- Accept what is good. Don't restyle for taste alone; change what is wrong, weak, or off-brief.

## Verdict per item, then fix

Classify each finding: **fake** (make real or remove), **broken** (bug), **off-spec**, **off-brief** (visual), **scope** (revert), **ok**. Rules:
- Fake feature/button: make it real if the spec called for it and the fix is small. Otherwise remove it rather than leave a lie behind, and tell the user it is not built.
- Do the fixes yourself, directly and surgically. Do not send the worker back; it repeats its mistakes and each round trip costs more than the fix.
- Re-run verification after fixing (and the UI audit plus fresh screenshots if UI). Don't assume your fix worked.

## What to tell the user

Be blunt and specific. Include: what was delegated; what was found, naming fakes and bugs concretely ("the Export handler called `console.log` and wrote no file; now it writes the CSV"); what the worker claimed that the trace did not support; what you changed and why; what you verified and how (commands run, interactions performed, screenshots viewed); and anything you could not verify. Do not soften findings, and don't call it done if a spec item remains unproven.
