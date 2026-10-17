"""Theme tokens: WCAG AA contrast in light and dark mode, read from the real stylesheet."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

CSS = (Path(__file__).resolve().parent.parent / "static" / "styles.css").read_text(encoding="utf-8")

# (foreground, background, minimum ratio). 4.5 for text; 3.0 for UI boundaries and focus rings (WCAG 1.4.11).
PAIRS: list[tuple[str, str, float]] = [
    *[("text", bg, 4.5) for bg in ("bg", "surface", "income-bg", "expense-bg", "info-bg")],
    *[("muted", bg, 4.5) for bg in ("bg", "surface")],
    *[("income", bg, 4.5) for bg in ("bg", "surface", "income-bg")],
    *[("expense", bg, 4.5) for bg in ("bg", "surface", "expense-bg")],
    *[("info", bg, 4.5) for bg in ("bg", "surface", "info-bg")],
    ("primary-ink", "primary", 4.5),
    ("primary-ink", "primary-hover", 4.5),
    ("sos-ink", "sos-bg", 4.5),
    ("sos-ink", "sos-bg", 3.0),  # listening mic icon on emergency red
    ("control", "surface", 3.0),
    ("focus", "bg", 3.0),
    ("focus", "surface", 3.0),
    ("primary", "surface", 1.0),  # decorative accent only; never used for text
]


def block(selector: str) -> str:
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", CSS)
    assert match, selector
    return match.group(1)


def tokens(body: str) -> dict[str, str]:
    return dict(re.findall(r"--([\w-]+):\s*(#[0-9A-Fa-f]{6})", body))


LIGHT = tokens(block(":root"))
DARK = tokens(block(':root[data-theme="dark"]'))
DARK_MEDIA = tokens(block(':root:not([data-theme="light"])'))


def luminance(hex_color: str) -> float:
    channels = [int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(a: str, b: str) -> float:
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def test_required_light_tokens_match_the_brief() -> None:
    expected = {
        "bg": "#FFF8EC",
        "surface": "#FFFFFF",
        "border": "#E8DCC4",
        "text": "#1F2A44",
        "primary": "#F5A623",
        "income": "#1B7F4B",
        "expense": "#B3261E",
        "info": "#2B4C9B",
    }
    assert {k: LIGHT[k].upper() for k in expected} == expected


def test_dark_tokens_are_identical_for_system_and_manual_dark_mode() -> None:
    assert DARK == DARK_MEDIA
    assert set(DARK) == set(LIGHT)


def test_meaning_is_kept_in_dark_mode() -> None:
    # green stays income, red stays expense/emergency, marigold stays primary
    assert DARK["primary"].upper() == LIGHT["primary"].upper()
    assert DARK["sos-bg"].upper() == LIGHT["sos-bg"].upper()
    r, g, b = (int(DARK["income"][i : i + 2], 16) for i in (1, 3, 5))
    assert g > r and g > b
    r, g, b = (int(DARK["expense"][i : i + 2], 16) for i in (1, 3, 5))
    assert r > g and r > b


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize(("fg", "bg", "minimum"), PAIRS, ids=[f"{f}-on-{b}" for f, b, _ in PAIRS])
def test_contrast_meets_wcag_aa(theme: str, fg: str, bg: str, minimum: float) -> None:
    palette = LIGHT if theme == "light" else DARK
    ratio = contrast(palette[fg], palette[bg])
    assert ratio >= minimum, f"{theme}: {fg} on {bg} is {ratio:.2f}, needs {minimum}"


def test_no_platform_brand_colours() -> None:
    brand = {"#FC8019", "#E23744", "#F9C933", "#000000", "#276EF1", "#6D3EE0", "#0C831F", "#3D1563", "#CDDC39"}
    used = {c.upper() for c in re.findall(r"#[0-9A-Fa-f]{6}", CSS)}
    assert not used & brand


def test_fonts_cover_every_script_and_body_text_is_at_least_16px() -> None:
    for family in ("Noto Sans", "Noto Sans Devanagari", "Noto Sans Gurmukhi", "Noto Sans Tamil", "Noto Sans Bengali"):
        assert f'font-family: "{family}"' in CSS
    fonts = Path(__file__).resolve().parent.parent / "static" / "fonts"
    for name in ("latin", "devanagari", "gurmukhi", "tamil", "bengali"):
        assert (fonts / f"noto-sans-{name}.woff2").read_bytes()[:4] == b"wOF2"
    body = block("body")
    size = float(re.search(r"font-size:\s*([\d.]+)rem", body).group(1))  # type: ignore[union-attr]
    assert size * 16 >= 16
