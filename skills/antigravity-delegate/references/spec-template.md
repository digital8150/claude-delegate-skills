# Spec template for the worker

The worker sees only this file and the repository. Write as if handing the task to a competent engineer who cannot ask questions but **can reason**: you state *what* must hold, the worker works out *how*. Spec length must stay far below the code you expect back — never paste expected code or full-algorithm pseudocode. If a part is too tricky to specify without writing it, implement that part yourself and delegate around it.

The worker fakes what nobody specified (a handler that does nothing, hardcoded data, a "Saved!" message with no save), so behavioral requirements and explicit "do not" lists are the defense — not longer specs. The two optional sections at the end (Behavior contract, Visual direction) are included only when the task needs them; a backend or refactor spec omits both.

## Template

```markdown
# Goal
One or two sentences: what this change achieves and why. (Context helps the worker make
the right small calls; it is not a license to make design calls.)

# Project context
- Language/framework/versions, how to build and test.
- Conventions to follow (naming, error handling, formatting). Point at a file to imitate:
  "Follow the structure of `src/services/UserService.ts`."
- Existing helpers/components/tokens to reuse, with paths. The worker must not invent a parallel one.

# Changes
## 1. `path/to/file.ext` (modify | create)
What the result must satisfy:
- Exact function/class/type signatures (these are contract, cheap to state):
  `export function parseRange(input: string): { lo: number; hi: number }`
- Behavior requirements, including edge cases and their exact handling
  (empty input -> throw `ValueError("empty")`), including empty / loading /
  error states where relevant. State *what* must hold; the worker decides
  the implementation.
- Which existing helpers/APIs/components/tokens to reuse (by import path)
  or avoid. The worker must not invent a parallel one.
- Where in the file the change goes (after `foo()`, in the `imports` block, ...).

## 2. `path/to/other.ext` (create)
...

# Behavior contract (include when users can trigger things: buttons, forms, user-facing endpoints, jobs)
One row per interactive element or trigger. Nothing interactive may exist outside this table.

| Element / trigger | Action | Real effect (state / API / storage / navigation) | Observable proof |
|---|---|---|---|
| "Save" button in `SettingsForm` | click | PUT `/api/settings` with form values; on 200 update store | reload: values persist |
| `POST /api/export` | request | stream CSV of current rows | file contains the rows shown |

Data: say where every displayed or returned value comes from. If real data is not available
and mock data is acceptable, say so explicitly and require it to live in ONE file named
`*.mock.*`; otherwise mock data is forbidden.

# Visual direction (include only when the change has visible UI)
- Intent: the feeling and the audience, in a sentence ("dense, calm, internal ops tool").
- Hard constraints: tokens/colors/fonts, spacing scale, breakpoints that must work (e.g. 390px, 1440px).
- Reference: a file/page in the repo to match, or a described layout.
- Freedom: what the worker may decide (micro-spacing, hover/focus states, icon choice).
- Avoid: generic AI-looking output (gratuitous gradients/glass, emoji icons, centered-everything
  heroes, stock-photo URLs, lorem ipsum, invented stats).

# Do not
- Do not modify any file not listed above.
- Do not refactor, rename, reformat, or "improve" existing code.
- Do not add dependencies / new files / features beyond this spec.
- Do not leave a stand-in for anything that cannot be genuinely implemented: omit it and list it
  under UNIMPLEMENTED in the final message.
- Do not invent data, URLs, API endpoints, or package versions.
- (Add specific traps: "Do not change the public signature of `save()`.")

# Verification
Commands the worker should run and what passing looks like:
- `npm test -- user.spec.ts` -> all pass
- `npx tsc --noEmit` -> no errors
These are evidence for you, not proof: you re-run everything in review.

# If stuck
If the spec is ambiguous or contradicts the code, do not guess. Stop, and describe the
conflict in your final message.
```

## What makes a spec work

- **Requirements over implementations.** "Add a save button" invites a button that says "Saved!" and does nothing — so specify the real effect ("click -> PUT `/api/status` -> store updated"). But a full code listing wastes expensive output. The sweet spot: signature + behavior contract + edge cases + a file to imitate, nothing more.
- **Say where things go.** File paths, insertion points, import names, module boundaries.
- **Name the APIs it must use.** If the project already has a logger, HTTP client, or helper, name it and its import path. If a dependency doesn't exist yet, include creating it or choose a different split.
- **Invariants, not algorithms.** For tricky logic, state what must hold; do not transcribe the algorithm.
- **Every state, not just the happy path.** Empty, loading, error, disabled. Fakes hide in the states nobody specified.
- **State non-goals.** An explicit "do not" list is the best defense against scope creep.
- **Keep each spec small**: one coherent change, a handful of files; UI visual scope to one screen or component family.
- **No secrets.** The spec and files the worker reads go to Google.

## Pre-send checklist

- Is the spec much shorter than the code you expect back? (If not, don't delegate.)
- Is every file path real? (Verify with Glob/ls.)
- Is every function/API it calls confirmed to exist, with the right signature?
- If users can trigger things: is every trigger in the Behavior contract with an observable proof?
- Are the non-goals and the "no mock data" rule explicit?
- Is there a concrete verification command?
- Did implementation code leak into the spec? (Remove it.)
