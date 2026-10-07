# Spec template for the worker

The worker sees only this file and the repository. Write as if handing the task to a competent engineer who cannot ask questions but **can reason**: you state *what* must hold, the worker works out *how*. Spec length must stay far below the code you expect back — never paste expected code or full-algorithm pseudocode. If a part is too tricky to specify without writing it, implement that part yourself and delegate around it.

## Template

```markdown
# Goal
One or two sentences: what this change achieves and why. (Context helps the worker make
the right small calls; it is not a license to make design calls.)

# Project context
- Language/framework/versions, how to build and test.
- Conventions to follow (naming, error handling, formatting). Point at a file to imitate:
  "Follow the structure of `src/services/UserService.ts`."

# Changes
## 1. `path/to/file.ext` (modify | create)
What the result must satisfy:
- Exact function/class/type signatures (these are contract, cheap to state):
  `export function parseRange(input: string): { lo: number; hi: number }`
- Behavior requirements, including edge cases and their exact handling
  (empty input -> throw `ValueError("empty")`). State *what* must hold; the
  worker decides the implementation.
- Which existing helpers/APIs to use (by import path) or avoid.
- Where in the file the change goes (after `foo()`, in the `imports` block, ...).

## 2. `path/to/other.ext` (create)
...

# Do not
- Do not modify any file not listed above.
- Do not refactor, rename, reformat, or "improve" existing code.
- Do not add dependencies / new files / features beyond this spec.
- (Add specific traps: "Do not change the public signature of `save()`.")

# Verification
Commands the worker should run and what passing looks like:
- `npm test -- user.spec.ts` -> all pass
- `npx tsc --noEmit` -> no errors

# If stuck
If the spec is ambiguous or contradicts the code, do not guess. Stop, and describe the
conflict in your final message.
```

## What makes a spec work

- **Requirements over implementations.** State the signature, behaviors, edge cases, and which existing file to imitate — nothing more. "Add a helper to validate emails" invites invention; a full code listing wastes expensive output.
- **Say where things go.** File paths, insertion points, import names, module boundaries.
- **Name the APIs it must use.** If the project already has a logger, HTTP client, or helper, name it and its import path. Otherwise the worker invents its own, or hallucinates one.
- **Invariants, not algorithms.** For tricky logic, state what must hold; do not transcribe the algorithm.
- **State non-goals.** An explicit "do not" list is the best defense against scope creep.
- **Keep each spec small.** One coherent change, at most a handful of files. Bigger work means multiple specs on disjoint files (parallel) or in sequence (each reviewed before the next).
- **No secrets** in the spec. It goes to an external provider.

## Quick pre-send checklist

- Is the spec much shorter than the code you expect back? (If not, don't delegate.)
- Is every file path real? (Verify with Glob/ls.)
- Is every function/API it calls confirmed to exist, with the right signature?
- Are the non-goals explicit?
- Is there a concrete verification command?
- Did implementation code leak into the spec? (Remove it.)
