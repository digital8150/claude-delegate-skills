#!/usr/bin/env python3
"""Static "fake implementation" scan over the lines a worker ADDED in a unified diff.

Usage:  python smells.py changes.patch            (prints markdown to stdout)
Also imported by delegate.py.

These are leads for the reviewer, not verdicts. Every hit still has to be read in
context: a `setTimeout` can be a legitimate debounce, `href="#"` can be a skip link.
The point is to make the cheap, mechanical part of the "does this actually work?"
review automatic so Claude spends its attention on the judgment part.
"""
import re
import sys
from collections import defaultdict

# (label, why it matters, regex)
SMELLS = [
    ("dead-handler", "handler that does nothing",
     r"on[cC]lick\s*=\s*\{\s*\(\s*\)\s*=>\s*\{\s*\}\s*\}|onclick\s*=\s*[\"']\s*[\"']|"
     r"on[cC]lick\s*=\s*\{\s*(undefined|null|noop)\s*\}|addEventListener\([^)]*,\s*\(\s*\)\s*=>\s*\{\s*\}\s*\)"),
    ("dead-link", "link/button that goes nowhere",
     r"href\s*=\s*[\"'](#|javascript:[^\"']*)[\"']"),
    ("log-only-handler", "handler that only logs/alerts, i.e. looks wired but isn't",
     r"on[cC]lick\s*=\s*\{?\s*\(?[^)]*\)?\s*=>\s*(console\.\w+|alert)\(|onclick\s*=\s*[\"'](alert|console\.)"),
    ("fake-async", "timer standing in for real work (fake loading / fake success?)",
     r"\bsetTimeout\s*\("),
    ("fake-success", "success message that may not follow a real operation",
     r"(saved|submitted|success(fully)?|sent|done|updated)\s*!?[\"'`]\s*\)?\s*;?\s*$|toast\w*\(.*(success|saved)"),
    ("mock-wording", "mock/dummy/placeholder data or wording",
     r"\b(mock|dummy|fake|stub|hard-?coded|placeholder(?!\s*=)|sample)[A-Za-z_]*\b|lorem ipsum|John Doe|Jane Doe|@example\.(com|org)"),
    ("random-as-data", "random values presented as data",
     r"Math\.random\(\)|random\.(random|randint|choice)\("),
    ("excuse-comment", "comment admitting it is not real",
     r"(?:^|[\s{;,)])(//|#|/\*|<!--)\s*.*\b(TODO|FIXME|XXX|for demo|demo only|in a real (app|world|implementation)|in production|simulate[sd]?|pretend|placeholder|not implemented|stub)\b"),
    ("not-implemented", "explicit stub",
     r"throw new Error\([\"']not implemented|NotImplementedError|raise NotImplemented"),
    ("swallowed-error", "error hidden instead of handled",
     r"catch\s*(\([^)]*\))?\s*\{\s*\}|except[^:\n]*:\s*pass\b|\.catch\(\s*\(\s*\)\s*=>\s*\{\s*\}\s*\)"),
    ("hollow-test", "test that cannot fail or is switched off",
     r"expect\(\s*true\s*\)|assert True\b|\b(xit|xdescribe|it\.skip|test\.skip|describe\.skip)\b|@pytest\.mark\.skip"),
    ("external-asset", "remote image/font/CDN URL: confirm it exists and loads",
     r"https?://(images\.unsplash|source\.unsplash|via\.placeholder|placehold|picsum|placekitten|i\.pravatar|randomuser)[^\s\"')]*"),
]
COMPILED = [(n, why, re.compile(rx)) for n, why, rx in SMELLS]
HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def scan_patch(text: str):
    """Yield (file, new_line_no, label, why, line) for each smelly added line."""
    hits, current, lineno = [], None, 0
    for raw in text.splitlines():
        if raw.startswith("+++ "):
            current = raw[4:].strip()
            current = current[2:] if current.startswith("b/") else current
            continue
        if raw.startswith("--- "):
            continue
        m = HUNK.match(raw)
        if m:
            lineno = int(m.group(1))
            continue
        if raw.startswith("+"):
            body = raw[1:]
            for name, why, rx in COMPILED:
                if rx.search(body):
                    hits.append((current, lineno, name, why, body.strip()[:160]))
            lineno += 1
        elif not raw.startswith("-"):
            lineno += 1
    return hits


def render(hits) -> str:
    if not hits:
        return "No static smells found in added lines. This proves little: a fake can be written cleanly."
    by_label = defaultdict(list)
    for h in hits:
        by_label[h[2]].append(h)
    out = [f"{len(hits)} suspect line(s). Leads to verify in context, not verdicts.", ""]
    for label, items in sorted(by_label.items(), key=lambda kv: -len(kv[1])):
        out.append(f"### {label} ({len(items)}): {items[0][3]}")
        for f, ln, _, _, body in items[:25]:
            out.append(f"- `{f}:{ln}`  {body}")
        if len(items) > 25:
            out.append(f"- ... and {len(items) - 25} more")
        out.append("")
    return "\n".join(out)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print(render(scan_patch(open(sys.argv[1], encoding="utf-8", errors="replace").read())))
