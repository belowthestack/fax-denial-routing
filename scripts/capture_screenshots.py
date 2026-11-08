"""Take the README screenshots and check the layout at desktop and phone width.

Needs Playwright with Chromium (pip install playwright, then: playwright install chromium).
Serves dashboard/ on a local port, then writes docs/screenshots/*.png. It fails loudly on a
console error, a failed request or any horizontal page scroll.

Usage: python scripts/capture_screenshots.py
"""
from __future__ import annotations

import functools
import http.server
import re
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SHOTS = ROOT / "docs" / "screenshots"


class Quiet(http.server.SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # keep-alive: far fewer sockets for the browser to open

    def log_message(self, *args, **kwargs) -> None:
        return None


# Words that must never appear on the main board or in the side panel (outside the Details section).
BANNED = [r"hash", r"dead.?letter", r"idempot", r"pipeline", r"extract", r"schema", r"\b[CRGQVP]-[A-Z0-9_\-]{3,}\b",
          r"haiku", r"sonnet", r"bedrock", r"claude", r"connectionerror", r"\.pdf", r"\bmodels?\b", r"\brules?\b",
          chr(0x2014), chr(0x2013)]  # the last two are the em dash and the en dash


def plain_scan(pg, where: str, problems: list[str]) -> None:
    """Read the visible text of the board and of an open panel (without Details) and flag jargon."""
    text = pg.evaluate("""() => {
      const board = document.querySelector('#board-root')?.innerText || '';
      const panel = document.querySelector('#panel');
      let p = '';
      if (panel && !panel.hidden) { const c = panel.cloneNode(true); c.querySelectorAll('.details').forEach(n => n.remove()); p = c.innerText; }
      return board + ' ' + p;
    }""")
    for pat in BANNED:
        m = re.search(pat, text, re.I)
        if m:
            problems.append(f"jargon {pat!r} ({m.group(0)!r}) on {where}")


def walk_every_state(pg, problems: list[str]) -> None:
    """Open every card in every state: start, after confirming reviews, approving, and the simulate buttons."""
    def scan_all(label: str) -> None:
        for i in range(pg.locator("article.card[data-open]").count()):
            pg.locator("article.card[data-open]").nth(i).click()
            pg.wait_for_selector("#panel .pbody")
            plain_scan(pg, f"{label} card {i}", problems)
            pg.locator("#panel [data-close]").click()
        plain_scan(pg, f"{label} board", problems)

    scan_all("start")
    for _ in range(3):  # confirm each review item with the value printed on the fax
        card = pg.locator(".col.needs article.card").first
        if card.count() == 0:
            break
        card.click()
        pg.wait_for_selector("#panel [data-field]")
        card_id = pg.locator("#panel [data-confirm]").get_attribute("data-confirm")
        ans = pg.evaluate("(id) => DEMO.patches.review[S.cards[id].doc_id].answers", card_id)
        for k, v in ans.items():
            pg.fill(f"#panel [data-field={k}]", str(v))
        pg.click("#panel [data-confirm]")
        pg.wait_for_selector("#panel [data-approve], #panel .sec .big")
        plain_scan(pg, "after confirm", problems)
        pg.locator("#panel [data-close]").click()
    for btn in ("new", "outage", "new"):
        pg.click(f"[data-demo={btn}]")
        pg.wait_for_function("() => !document.querySelector('.card.temp')", timeout=6000)
        pg.wait_for_timeout(100)
        plain_scan(pg, f"after simulate {btn}", problems)
    pg.click("[data-demo=again]")
    plain_scan(pg, "after send again", problems)
    if pg.locator("[data-retry]").count():
        pg.locator("[data-retry]").first.click()
        pg.wait_for_function("() => !document.querySelector('[data-retry]')", timeout=6000)
    while pg.locator(".col.ready [data-approve]").count():
        pg.locator(".col.ready [data-approve]").first.click()
    scan_all("end")
    done = pg.locator(".col").nth(3).locator("article.card").count()
    total = pg.locator("article.card").count()
    if done != total or total != 10:
        problems.append(f"expected all 10 letters in Done after approving everything, got {done} of {total}")
    pg.click("[data-demo=reset]")
    if pg.locator(".col.needs article.card").count() != 3:
        problems.append("Reset did not restore the starting board")


def serve() -> http.server.ThreadingHTTPServer:
    handler = functools.partial(Quiet, directory=str(ROOT / "dashboard"))
    http.server.ThreadingHTTPServer.daemon_threads = True
    http.server.ThreadingHTTPServer.request_queue_size = 128
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def main() -> None:
    SHOTS.mkdir(parents=True, exist_ok=True)
    srv = serve()
    base = f"http://127.0.0.1:{srv.server_address[1]}/"
    problems: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()

        def page_for(width: int, height: int, scale: float):
            ctx = browser.new_context(viewport={"width": width, "height": height}, device_scale_factor=scale)
            pg = ctx.new_page()
            pg.on("console", lambda m: problems.append(f"console {m.type}: {m.text}") if m.type in ("error", "warning") else None)
            pg.on("requestfailed", lambda r: problems.append(f"request failed: {r.url}") if "fonts.g" not in r.url else None)
            pg.goto(base)
            pg.wait_for_selector(".card")
            return pg

        def no_hscroll(pg, label: str) -> None:
            sw, w = pg.evaluate("[document.documentElement.scrollWidth, window.innerWidth]")
            if sw > w:
                problems.append(f"horizontal scroll at {label}: {sw} > {w}")

        pg = page_for(1440, 900, 2)
        no_hscroll(pg, "1440 board")
        pg.screenshot(path=SHOTS / "board-desktop.png")
        pg.locator(".col.needs .card", has_text="Jayden").click()
        pg.wait_for_selector("#panel .fields")
        no_hscroll(pg, "1440 panel")
        pg.screenshot(path=SHOTS / "card-panel-desktop.png")
        pg.goto(base + "#/how")
        pg.wait_for_selector(".how")
        no_hscroll(pg, "1440 how it works")
        pg.screenshot(path=SHOTS / "how-it-works-desktop.png", full_page=True)

        pw = page_for(1440, 900, 1)
        walk_every_state(pw, problems)

        pm = page_for(390, 844, 2)
        no_hscroll(pm, "390 board")
        pm.screenshot(path=SHOTS / "board-mobile.png", full_page=True)
        pm.locator(".col.needs .card").first.click()
        pm.wait_for_selector("#panel .fields")
        no_hscroll(pm, "390 panel")
        pm.goto(base + "#/how")
        pm.wait_for_selector(".how")
        no_hscroll(pm, "390 how it works")
        browser.close()
    srv.shutdown()
    if problems:
        raise SystemExit("PROBLEMS:\n" + "\n".join(problems))
    print("ok: no console errors, no failed requests, no horizontal scroll at 1440 or 390, no jargon on the board or panel in any state, Reset restores the start")


if __name__ == "__main__":
    main()
