<!-- WORKER_MESSAGE -->
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

<!-- WORKER_MESSAGE_HIGH -->
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

<!-- WORKER_MESSAGE_FULL -->
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

<!-- WORKER_MESSAGE_INVESTIGATE -->
First read the question with view_file: {spec}
This is an investigation, not an implementation. Do NOT modify, create, or delete any file. No hardcoded experiments that leave artifacts behind. Explore the codebase and answer the question: find the root cause, gather evidence (exact file paths, line references, quotations from the code), and state your confidence and what is still uncertain. If you cannot determine the cause, say so and list what you ruled out.

When finished, reply with exactly these sections:
ROOT CAUSE: ... (your best answer, stated plainly)
EVIDENCE: ... (file paths, line references, code quotations backing it)
UNCERTAINTY: ... (what you could not determine, what you ruled out)

<!-- WORKER_MESSAGE_RESUME -->
Your previous turn in this conversation stopped before you finished. It was cut off by an error or a time limit, not ended by you, so the task is still open.
First read the follow-up note with view_file: {spec}
It says what interrupted you and answers anything that was open. Continue the same task from where you left off under the same instructions and honesty rules as before. Check the current state of the files you were editing before changing them; do not redo finished work or re-explore what you already read.
End with the same final sections the original instructions asked for, covering the whole task, not only this turn.

<!-- NON_INTERACTIVE -->
This run is non-interactive: nobody can answer questions or permission prompts, so never use ask_question or similar tools. Decide open points yourself, within the spec, and list those decisions in your final message.

<!-- EXPLORE_RULES -->
Explore with purpose: read the files the plan points to first, then only what the next decision needs. Stay inside this project's own code: do not read the source of third-party libraries or vendored dependencies (external/, third_party/, vendor/, node_modules/ ...); how this project already calls them, plus their headers, is enough. When unsure how a library behaves, use its standard documented usage, note the assumption, and check it by building or running, not by reading its internals. Start editing early: the plan's Start here / Decided lists are meant to make long reading unnecessary.
