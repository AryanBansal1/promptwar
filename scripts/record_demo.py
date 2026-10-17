"""Record a demo video of the real, deployed Saathi app (real Gemini calls, nothing mocked).

Usage:
    python scripts/record_demo.py --url https://promptwar-7b2k.vercel.app

Needs: pip install playwright==1.48.0 imageio-ffmpeg==0.5.1 && python -m playwright install chromium
Output: demo_output/demo.mp4 (git-ignored, about 1 minute; AI waiting time is cut out).
Uses 5 Gemini calls: Ask, SMS import, schemes, safety, complaint.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import imageio_ffmpeg
from playwright.sync_api import Page, sync_playwright

WIDTH, HEIGHT = 1280, 720
TYPE_DELAY_MS = 35  # brisk but readable typing
PAUSE_S = 1.6  # pause after each visible result
AI_TIMEOUT_MS = 60_000
MAX_SECONDS = 60  # final video: about one minute
CARD_SECONDS = 2.5
SPINNER_KEEP_S = 0.8  # seconds of each AI wait left in the video (the rest is cut)
FONT = "C\\:/Windows/Fonts/segoeuib.ttf"  # escaped for ffmpeg drawtext on Windows

SMS = "Rs 450.00 credited to your A/c XX4321 on 16-Oct-26 from URBAN COMPANY PAYOUT. UPI Ref 629812345678"
PUNJABI_QUESTION = "ਰਾਤ ਨੂੰ ਸਵਾਰੀ ਦੌਰਾਨ ਇੱਕ ਗਾਹਕ ਨੇ ਮੈਨੂੰ ਗਾਲ੍ਹਾਂ ਕੱਢੀਆਂ ਅਤੇ ਧਮਕੀ ਦਿੱਤੀ। ਮੈਂ ਕੀ ਕਰਾਂ?"


@dataclass
class Timeline:
    """Section start times (seconds since recording began) for the captions."""

    started: float = field(default_factory=time.monotonic)
    marks: list[tuple[float, str]] = field(default_factory=list)
    cuts: list[tuple[float, float]] = field(default_factory=list)  # AI waiting periods removed from the video
    gemini: list[str] = field(default_factory=list)

    def mark(self, caption: str) -> None:
        self.marks.append((time.monotonic() - self.started, caption))
        print(f"[{self.marks[-1][0]:6.1f}s] {caption}")


def pause(seconds: float = PAUSE_S) -> None:
    time.sleep(seconds)


def type_slowly(page: Page, selector: str, text: str) -> None:
    page.click(selector)
    page.keyboard.type(text, delay=TYPE_DELAY_MS)


def wait_ai(page: Page, ok_selector: str, area: str, label: str, tl: Timeline) -> bool:
    """Wait for a real AI result or an honest error box; record which happened and cut the wait."""
    waited_from = time.monotonic() - tl.started
    page.wait_for_selector(f"{ok_selector}, {area} .error-box", timeout=AI_TIMEOUT_MS)
    waited_to = time.monotonic() - tl.started
    if waited_to - waited_from > SPINNER_KEEP_S:
        tl.cuts.append((waited_from + SPINNER_KEEP_S, waited_to))
    ok = page.locator(ok_selector).count() > 0
    tl.gemini.append(f"{label}: {'OK' if ok else 'ERROR - ' + page.locator(area + ' .error-box').first.inner_text()}")
    print("   Gemini ->", tl.gemini[-1], flush=True)
    return ok


def scroll_to(page: Page, selector: str) -> None:
    page.locator(selector).first.scroll_into_view_if_needed()
    page.evaluate("window.scrollBy(0, -80)")


def walkthrough(page: Page, url: str, tl: Timeline) -> None:
    today = date.today().isoformat()

    tl.mark("Saathi - first visit")
    page.goto(url, wait_until="networkidle")
    pause(1.5)

    tl.mark("1. My Work - more than one kind of work")
    page.click("#onboarding-start")
    pause(1)
    for selector in (
        "input[name=work_types][value=bike_taxi]",
        "input[name=work_types][value=home_services]",
        "input[name=platforms][value=rapido]",
        "input[name=platforms][value=urban_company]",
    ):
        page.check(selector)
        pause(0.5)
    page.click("#profile-form button[type=submit]")
    pause()

    tl.mark("2. Income and Expenses - one income, one expense")
    page.goto(f"{url}#/ledger")
    pause(1)
    page.select_option("#e-platform", "rapido")
    type_slowly(page, "#e-amount", "600")
    type_slowly(page, "#e-hours", "5")
    page.click("#entry-form button[type=submit]")
    pause(1.5)
    page.click("label.kind-expense")
    type_slowly(page, "#e-amount", "120")
    page.select_option("#e-category", "fuel")
    page.click("#entry-form button[type=submit]")
    page.wait_for_selector("#summary .stat")
    scroll_to(page, "#summary-title")
    pause(2)

    tl.mark("3. Ask Saathi in Hindi - log money by text")
    page.goto(f"{url}#/")
    page.select_option("#lang-select", "hi")
    pause(1.5)
    type_slowly(page, "#ask-input", "aaj Rapido se 150 kamaye aur 50 ka petrol dala")
    pause(0.8)
    page.click("#ask-form button[type=submit]")
    if wait_ai(page, "#confirm-title", "#ask-result", "Ask Saathi (log_transaction)", tl):
        pause()
        page.click("#ask-result .confirm-card button.btn-primary")
        page.wait_for_selector("#ask-result .alert-success")
    pause(2)

    tl.mark("4. Import bank or UPI SMS with AI - check before saving")
    page.goto(f"{url}#/ledger")
    scroll_to(page, "#import-title")
    pause(1)
    page.fill("#import-text", SMS)  # pasted, as a worker would
    pause(1.5)
    page.click("#import-text-form button[type=submit]")
    if wait_ai(page, "#review-panel .review-row, #review-panel .empty", "#import-status", "SMS import", tl):
        scroll_to(page, "#review-title")
        pause(2)
        if page.locator("#review-save").count():
            page.click("#review-save")
            pause(1)
            scroll_to(page, "#entries-title")
    pause()

    tl.mark("5. Schemes - rules in code, explained by AI in Hindi")
    page.goto(f"{url}#/schemes")
    pause(1)
    type_slowly(page, "#s-age", "32")
    type_slowly(page, "#s-income", "14000")
    page.select_option("#s-state", "DL")
    page.check("input[name=has_bank_account][value=true]")
    pause(0.8)
    page.click("#schemes-form button[type=submit]")
    page.wait_for_selector("#scheme-results .scheme")
    scroll_to(page, "#scheme-results-title")
    pause(1.5)
    if wait_ai(page, "#scheme-results .ai-answer", "#scheme-results", "Schemes explanation", tl):
        scroll_to(page, "#scheme-results .ai-answer")
        pause(2)

    tl.mark("6. What documents do I need? - checklist")
    page.locator("#scheme-results .scheme button.btn-primary").first.click()
    page.wait_for_selector("#doc-title")
    pause(1.5)
    page.locator(".check-item input").first.check()
    pause()

    tl.mark("7. Safety chat in Punjabi")
    page.select_option("#lang-select", "pa")
    page.goto(f"{url}#/safety")
    pause(1)
    type_slowly(page, "#safety-input", PUNJABI_QUESTION)
    page.click("#safety-form button[type=submit]")
    if wait_ai(page, "#chat-log .msg-assistant", "#safety-error", "Safety chat", tl):
        scroll_to(page, "#chat-log .msg-assistant")
        pause(2)

    tl.mark("8. Draft a complaint - letter in English and Punjabi")
    draft = page.locator("#chat-log .msg-assistant button.btn-primary")
    if draft.count():
        draft.first.click()  # issue type pre-selected from the chat
    else:
        page.goto(f"{url}#/grievance")
        page.select_option("#g-issue", "harassment")
        page.fill("#g-description", PUNJABI_QUESTION)
    page.wait_for_selector("#grievance-form")
    page.select_option("#g-platform", "rapido")
    page.fill("#g-start", today)
    pause(1.5)
    page.click("#grievance-form button[type=submit]")
    if wait_ai(page, "#grievance-result .letter", "#grievance-result", "Complaint letter", tl):
        scroll_to(page, "#grievance-result .letter")
        pause(2)
        tl.mark("9. Send it - WhatsApp, copy, download, where to send")
        scroll_to(page, "#grievance-result a[href^='https://wa.me']")
        pause(2)

    tl.mark("10. Dark mode for night shifts")
    page.click("#theme-toggle")
    page.evaluate("window.scrollTo(0, 0)")
    pause(2)


def ffmpeg(*args: str) -> None:
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", *args], check=True)


def duration(path: Path) -> float:
    out = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-i", str(path)], capture_output=True, text=True).stderr
    h, m, s = re.search(r"Duration: (\d+):(\d+):([\d.]+)", out).groups()  # type: ignore[union-attr]
    return int(h) * 3600 + int(m) * 60 + float(s)


def drawtext(text: str, size: int, y: str, extra: str = "") -> str:
    safe = text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "’").replace(",", "\\,")
    return f"drawtext=fontfile='{FONT}':text='{safe}':fontsize={size}:fontcolor=0x1F2A44:x=(w-text_w)/2:y={y}{extra}"


def card(path: Path, lines: list[tuple[str, int]], seconds: float) -> None:
    filters = ",".join(drawtext(text, size, f"h/2-{140 - i * 90}") for i, (text, size) in enumerate(lines))
    ffmpeg(
        "-f", "lavfi", "-i", f"color=c=0xFFF8EC:s={WIDTH}x{HEIGHT}:d={seconds}:r=30",
        "-vf", filters, "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path),
    )  # fmt: skip


def build_video(raw: Path, tl: Timeline, out_dir: Path) -> Path:
    raw_len = duration(raw)
    # Captions: each section's name, on a marigold banner, until the next section starts.
    marks = tl.marks + [(raw_len, "")]
    caps = []
    for (start, text), (end, _) in zip(marks, marks[1:], strict=False):
        enable = f":box=1:boxcolor=0xF5A623@0.95:boxborderw=14:enable='between(t,{start:.2f},{end:.2f})'"
        caps.append(drawtext(text, 30, "h-150", enable))
    # Remove AI waiting periods (captions are drawn first, on the original timeline).
    keep_len = raw_len - sum(b - a for a, b in tl.cuts)
    cut = "+".join(f"between(t,{a:.2f},{b:.2f})" for a, b in tl.cuts) or "0"
    budget = MAX_SECONDS - 2 * CARD_SECONDS
    speed = max(1.0, keep_len / budget)
    vf = ",".join(
        [
            "fps=25",  # Playwright records variable frame rate; make it constant before cutting
            *caps,
            f"select='not({cut})'",
            "setpts=N/25/TB",
            f"setpts=PTS/{speed:.4f}",
            "fps=30",
            f"scale={WIDTH}:{HEIGHT}",
        ]
    )
    body = out_dir / "body.mp4"
    ffmpeg("-i", str(raw), "-vf", vf, "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(body))
    intro, end = out_dir / "intro.mp4", out_dir / "end.mp4"
    card(
        intro,
        [("Saathi", 72), ("GenAI companion for gig workers", 40), ("Live app - real Gemini calls", 28)],
        CARD_SECONDS,
    )
    card(
        end,
        [
            ("Saathi", 64),
            ("Income - Safety - Schemes - Documents - Complaints", 30),
            ("6 languages - voice - no login", 28),
        ],
        CARD_SECONDS,
    )
    concat = out_dir / "concat.txt"
    concat.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in (intro, body, end)), encoding="utf-8")
    final = out_dir / "demo.mp4"
    ffmpeg("-f", "concat", "-safe", "0", "-i", str(concat), "-c", "copy", "-movflags", "+faststart", str(final))
    for temp in (intro, end, body, concat):
        temp.unlink()
    print(
        f"raw {raw_len:.1f}s, AI waits cut {raw_len - keep_len:.1f}s, speed x{speed:.2f}, final {duration(final):.1f}s"
    )
    return final


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--out-dir", default="demo_output")
    args = parser.parse_args()
    url = args.url.rstrip("/") + "/"
    out_dir = Path(args.out_dir)
    out_dir.mkdir(exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(
            viewport={"width": WIDTH, "height": HEIGHT},
            record_video_dir=str(out_dir),
            record_video_size={"width": WIDTH, "height": HEIGHT},
            locale="en-IN",
            color_scheme="light",
        )
        page = context.new_page()
        video_path = Path(page.video.path())  # type: ignore[union-attr]  # known up front; written on close
        tl = Timeline()
        try:
            walkthrough(page, url, tl)
        finally:
            context.close()
            browser.close()
            (out_dir / "timeline.json").write_text(
                json.dumps({"marks": tl.marks, "cuts": tl.cuts, "gemini": tl.gemini}, indent=2), encoding="utf-8"
            )
    raw = out_dir / "raw.webm"
    video_path.replace(raw)
    if any("ERROR" in g for g in tl.gemini):
        print("A Gemini call failed during recording; not building the final video:", *tl.gemini, sep="\n  ")
        raise SystemExit(1)
    final = build_video(raw, tl, out_dir)
    print("Gemini calls:", *tl.gemini, sep="\n  ")
    print("Video:", final.resolve())


if __name__ == "__main__":
    main()
