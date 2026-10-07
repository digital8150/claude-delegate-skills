#!/usr/bin/env python3
"""Black-box UI audit: screenshots + a dead-button detector. Needs `pip install playwright`
(no browser download: it drives the Edge/Chrome already installed on the machine).

Usage:
  python ui_audit.py TARGET --out DIR [--steps steps.json] [--viewports 1440x900,390x844] [--max-elements 60]

TARGET is a URL (http://localhost:5173) or a local .html file.

--steps: a JSON list of setup actions run after load, so that controls which only exist once the app has
data (per-item Delete/Toggle buttons, menus, filled states) are reachable. Without it, an app that starts
empty gets only its empty state audited, which is exactly where fake buttons hide. Actions:
  {"fill": ["#title", "Dune"]}   {"click": "#add"}   {"press": "Enter"}   {"wait": 300}
Example: [{"fill":["input","Dune"]},{"press":"Enter"},{"fill":["input","Emma"]},{"press":"Enter"}]
The click audit and the reload-persistence check both run on the state AFTER the steps.

What it produces in DIR:
  shot-<WxH>.png     full-page screenshot per viewport (Claude must LOOK at these: Read the png)
  shot-<WxH>-seeded.png  same, after --steps (if given)
  audit.md           console errors, failed requests (hallucinated image/CDN URLs show up here),
                     and a per-element table of what happened when each interactive element was clicked
  audit.json         same data, machine-readable

Dead-button logic: for every button / link / role=button / submit input, the page is reloaded fresh,
the element is clicked, and we record whether ANYTHING observable happened: DOM changed, URL changed,
a network request fired, a dialog opened, storage changed. "NOTHING" is a strong lead for a fake
button. It is a lead, not a verdict: a legitimately idle control (already-selected tab, button that
needs a filled form first) also shows NOTHING. Read the code for each NOTHING and decide.
Another trap this cannot see: a click that changes the DOM or shows a toast but does no real work.
That is why you still read the handler and confirm the effect persists (reload, network, storage).
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sys.exit("playwright missing: pip install playwright  (it will use installed Edge/Chrome)")

SELECTOR = ("button, a[href], [role=button], [role=tab], [role=menuitem], summary, "
            "input[type=submit], input[type=button], input[type=checkbox], input[type=radio], "
            "[role=checkbox], [role=switch], [role=radio], [onclick]")


def launch(p):
    last = None
    for channel in ("msedge", "chrome", None):
        try:
            return p.chromium.launch(channel=channel) if channel else p.chromium.launch()
        except Exception as e:  # noqa: BLE001
            last = e
    sys.exit(f"could not launch a browser (install Edge/Chrome or `playwright install chromium`): {last}")


def run_steps(page, steps):
    for st in steps or []:
        if "fill" in st:
            page.fill(st["fill"][0], st["fill"][1])
        elif "click" in st:
            page.click(st["click"])
        elif "press" in st:
            page.keyboard.press(st["press"])
        elif "wait" in st:
            page.wait_for_timeout(st["wait"])
        page.wait_for_timeout(120)


def snapshot(page):
    return page.evaluate("""() => ({
        html: document.documentElement.outerHTML,
        url: location.href,
        ls: JSON.stringify(localStorage), ss: JSON.stringify(sessionStorage), ck: document.cookie,
        scroll: window.scrollY,
    })""")


def describe(page, idx):
    return page.evaluate("""([sel, i]) => {
        const el = document.querySelectorAll(sel)[i];
        if (!el) return null;
        const r = el.getBoundingClientRect();
        const text = (el.innerText || el.value || el.getAttribute('aria-label') || el.title || '').trim().slice(0, 50);
        return {tag: el.tagName.toLowerCase(), text, id: el.id || '', cls: (el.className && el.className.toString().slice(0, 40)) || '',
                href: el.getAttribute('href') || '', disabled: !!el.disabled || el.getAttribute('aria-disabled') === 'true',
                visible: r.width > 0 && r.height > 0, hasInlineHandler: el.hasAttribute('onclick')};
    }""", [SELECTOR, idx])


def click_audit(browser, target, max_elements, steps):
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    page.goto(target, wait_until="networkidle")
    run_steps(page, steps)
    n = min(page.locator(SELECTOR).count(), max_elements)
    ctx.close()
    rows = []
    for i in range(n):
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        requests, dialogs = [], []
        page.on("request", lambda r: requests.append(r.url))
        page.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))
        page.goto(target, wait_until="networkidle")
        run_steps(page, steps)
        info = describe(page, i)
        if not info:
            ctx.close()
            continue
        row = {"index": i, **info, "effect": []}
        if not info["visible"]:
            row["effect"] = ["(not visible, skipped)"]
        elif info["disabled"]:
            row["effect"] = ["(disabled: confirm it is disabled for a real reason)"]
        else:
            requests.clear()
            before = snapshot(page)
            try:
                page.locator(SELECTOR).nth(i).click(timeout=3000)
                page.wait_for_timeout(600)
                after = snapshot(page)
                eff = []
                if hashlib.md5(before["html"].encode()).digest() != hashlib.md5(after["html"].encode()).digest():
                    eff.append("DOM changed")
                if before["url"] != after["url"]:
                    eff.append(f"URL -> {after['url']}")
                if requests:
                    eff.append(f"{len(requests)} request(s): {requests[0][:80]}")
                if dialogs:
                    eff.append(f"dialog: {dialogs[0][:60]}")
                if (before["ls"], before["ss"], before["ck"]) != (after["ls"], after["ss"], after["ck"]):
                    eff.append("storage/cookie changed")
                if before["scroll"] != after["scroll"]:
                    eff.append("scrolled")
                row["effect"] = eff or ["NOTHING"]
            except Exception as e:  # noqa: BLE001
                row["effect"] = [f"click failed: {str(e).splitlines()[0][:100]}"]
        rows.append(row)
        ctx.close()
    return rows


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("target")
    ap.add_argument("--out", required=True)
    ap.add_argument("--viewports", default="1440x900,390x844")
    ap.add_argument("--max-elements", type=int, default=60)
    ap.add_argument("--steps", help="JSON file: setup actions to reach a populated state")
    args = ap.parse_args()

    target = args.target
    if not target.startswith(("http://", "https://", "file://")):
        target = Path(target).resolve().as_uri()
    steps = json.loads(Path(args.steps).read_text(encoding="utf-8")) if args.steps else []
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    console, failed, shots, persisted = [], [], [], None
    with sync_playwright() as p:
        browser = launch(p)
        for vp in args.viewports.split(","):
            w, h = (int(x) for x in vp.lower().split("x"))
            ctx = browser.new_context(viewport={"width": w, "height": h})
            page = ctx.new_page()
            page.on("console", lambda m, vp=vp: m.type in ("error", "warning") and console.append(f"[{vp}] {m.type}: {m.text[:200]}"))
            page.on("pageerror", lambda e, vp=vp: console.append(f"[{vp}] PAGE ERROR: {str(e)[:200]}"))
            page.on("requestfailed", lambda r, vp=vp: failed.append(f"[{vp}] {r.url[:120]} ({r.failure})"))
            page.on("response", lambda r, vp=vp: r.status >= 400 and failed.append(f"[{vp}] {r.status} {r.url[:120]}"))
            page.goto(target, wait_until="networkidle")
            page.wait_for_timeout(500)
            f = out / f"shot-{w}x{h}.png"
            page.screenshot(path=str(f), full_page=True)
            shots.append(str(f))
            if steps:
                run_steps(page, steps)
                fs = out / f"shot-{w}x{h}-seeded.png"
                page.screenshot(path=str(fs), full_page=True)
                shots.append(str(fs))
                if w >= 1000:
                    before_txt = page.evaluate("document.body.innerText")
                    page.reload(wait_until="networkidle")
                    page.wait_for_timeout(300)
                    persisted = page.evaluate("document.body.innerText") == before_txt
            overflow = page.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1")
            if overflow:
                console.append(f"[{vp}] horizontal overflow: page is wider than the viewport")
            ctx.close()
        rows = click_audit(browser, target, args.max_elements, steps)
        browser.close()

    nothing = [r for r in rows if r["effect"] == ["NOTHING"]]
    md = [f"# UI audit: {args.target}", "",
          "Screenshots (Read them yourself, with your own eyes): " + ", ".join(shots), "",
          f"## Interactive elements: {len(rows)} checked, {len(nothing)} did NOTHING when clicked", "",
          "| # | element | text | href | effect |", "|---|---|---|---|---|"]
    for r in rows:
        mark = "**" if r["effect"] == ["NOTHING"] else ""
        md.append(f"| {r['index']} | {r['tag']}{('#' + r['id']) if r['id'] else ''} | {r['text'] or '-'} | {r['href'] or '-'} "
                  f"| {mark}{'; '.join(r['effect'])}{mark} |")
    if persisted is not None:
        md += ["", "## Reload persistence (state after --steps, then reload)",
               "- visible text identical after reload: " + ("yes" if persisted else "**NO: state did not survive reload, "
               "so the data is not really stored (or the app resets by design; check the spec)**")]
    md += ["", f"## Console errors / warnings ({len(console)})"] + [f"- {c}" for c in console[:40]]
    md += ["", f"## Failed requests / HTTP >= 400 ({len(failed)})"] + [f"- {c}" for c in failed[:40]]
    (out / "audit.md").write_text("\n".join(md), encoding="utf-8")
    (out / "audit.json").write_text(json.dumps({"elements": rows, "console": console, "failed": failed, "shots": shots},
                                               ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n".join(md))


if __name__ == "__main__":
    main()
