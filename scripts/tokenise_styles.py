"""One-off sweep: put the stylesheet's raw values onto the design tokens.

index.html declared a palette in :root and then ignored it in about half the
stylesheet, and carried a further set of greys in style attributes on the
markup that were not in the palette at all (#888, #666, #ddd). That mattered
beyond tidiness: darkening --ink-3 to meet contrast fixed most of the
interface and would have left every hardcoded #768692 and #888 behind it,
producing a visible mismatch between two greys that are meant to be one.

The type scale had the same problem in a different form: 21 distinct sizes
with no ladder, 137 declarations under 11px.

Scope is deliberately narrow. Only the <style> block and style attributes in
the markup are touched. Colour literals inside JavaScript are left alone:
those are map rendering values (IMD ramps, marker fills, Leaflet styles) that
are passed to a canvas and cannot read a CSS variable.

Run once:

    py scripts/tokenise_styles.py

It reports what it changed and leaves a summary on stdout. Safe to re-run: it
is idempotent, because a value already written as var(--token) no longer
matches any pattern.
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "index.html"

# ── Colour: palette hexes that already have a token ────────────────────────
COLOURS = {
    "#005EB8": "--nhs-blue", "#003087": "--nhs-dark", "#0072CE": "--nhs-bright",
    "#41B6E6": "--nhs-aqua", "#FFB81C": "--nhs-yellow",
    "#DA291C": "--nhs-red",  "#009639": "--nhs-green", "#AE2573": "--nhs-pink",
    "#212B32": "--ink",      "#4C6272": "--ink-2",     "#768692": "--ink-3",
    "#DDE1E4": "--line",     "#E8EDEE": "--line-2",
    "#F4F7F9": "--bg",       "#FBFCFD": "--paper-2",
}

# ── Colour: the loose greys, which had no token at all ─────────────────────
# Mapped to the nearest token that is legible. #888 was 3.54:1 and #aaa was
# 2.32:1, so both were failing wherever they carried words.
LOOSE = {
    "#333": "--ink",   "#555": "--ink-2", "#666": "--ink-2",
    "#888": "--ink-3", "#999": "--ink-3", "#aaa": "--ink-3",
    "#ddd": "--line",  "#eee": "--line-2", "#e5e5e0": "--line-2",
    "#fafafa": "--paper-2", "#f7f9f7": "--paper-2",
}

# ── Type scale ─────────────────────────────────────────────────────────────
TYPE = {
    "8px": "--t-xs", "9px": "--t-xs", "9.5px": "--t-xs",
    "10px": "--t-xs", "10.5px": "--t-xs", "11px": "--t-xs",
    "11.5px": "--t-sm", "12px": "--t-sm",
    "12.5px": "--t-md", "13px": "--t-md", "13.5px": "--t-md",
    "14px": "--t-lg", "15px": "--t-lg",
    "16px": "--t-xl", "17px": "--t-xl", "18px": "--t-xl",
    "19px": "--t-2xl", "20px": "--t-2xl", "22px": "--t-2xl",
    "26px": "--t-3xl", "28px": "--t-3xl",
}

# Selectors whose font-size sets the size of a glyph rather than of text: the
# disclosure triangles and the up/down markers. These are drawn shapes that
# happen to be characters, and pushing them to the 11px floor makes them
# clumsy without making anything more readable. Left exactly as they were.
GLYPH_SELECTORS = (
    ".sb-lbl.collapsible::before",
    ".vcse-tag-group > summary::before",
    ".cat-arrow",
    ".ov-grp-hd .ov-arrow",
    ".cb-grp-hd .a",
    ".cmp-card .cmp-c-row .v.hi::after",
    ".cmp-card .cmp-c-row .v.lo::after",
    ".dir-grp-hd .a",
)


def is_glyph_rule(line: str) -> bool:
    return any(sel in line for sel in GLYPH_SELECTORS)


def main() -> int:
    if not INDEX.exists():
        print(f"missing {INDEX}", file=sys.stderr)
        return 1

    html = INDEX.read_text(encoding="utf-8")
    m = re.search(r"(<style>)(.*?)(</style>)", html, re.DOTALL)
    if not m:
        print("no <style> block found", file=sys.stderr)
        return 1

    head, css, tail = m.group(1), m.group(2), m.group(3)
    tally: Counter[str] = Counter()

    # ── Pass 1: colours in the stylesheet ──────────────────────────────────
    # The :root block itself must keep its literals, or every token would
    # resolve to itself.
    root_end = css.index("}", css.index(":root")) + 1
    root_block, rest = css[:root_end], css[root_end:]

    def sub_colours(text: str, table: dict[str, str]) -> str:
        for literal, token in table.items():
            pattern = re.compile(re.escape(literal) + r"\b", re.IGNORECASE)
            text, n = pattern.subn(f"var({token})", text)
            if n:
                tally[f"colour {literal} -> {token}"] += n
        return text

    rest = sub_colours(rest, COLOURS)
    rest = sub_colours(rest, LOOSE)

    # ── Pass 2: font sizes in the stylesheet ───────────────────────────────
    out_lines = []
    for line in rest.split("\n"):
        if not is_glyph_rule(line):
            def repl(mo: re.Match) -> str:
                size = mo.group(1)
                token = TYPE.get(size)
                if not token:
                    return mo.group(0)
                tally[f"type {size} -> {token}"] += 1
                return f"font-size: var({token})"
            line = re.sub(r"font-size:\s*([\d.]+px)", repl, line)
        else:
            tally["type left as a glyph size"] += 1
        out_lines.append(line)
    rest = "\n".join(out_lines)

    css = root_block + rest
    html = html[: m.start()] + head + css + tail + html[m.end():]

    # ── Pass 3: style attributes on the markup ─────────────────────────────
    # Same tables, applied only inside style="...", so no JavaScript literal
    # is ever touched.
    def sweep_attr(mo: re.Match) -> str:
        body = mo.group(1)
        for literal, token in {**COLOURS, **LOOSE}.items():
            pattern = re.compile(re.escape(literal) + r"\b", re.IGNORECASE)
            body, n = pattern.subn(f"var({token})", body)
            if n:
                tally[f"inline {literal} -> {token}"] += n

        def repl(m2: re.Match) -> str:
            token = TYPE.get(m2.group(1))
            if not token:
                return m2.group(0)
            tally[f"inline type {m2.group(1)} -> {token}"] += 1
            return f"font-size:var({token})"
        body = re.sub(r"font-size:\s*([\d.]+px)", repl, body)
        return f'style="{body}"'

    html = re.sub(r'style="([^"]*)"', sweep_attr, html)

    INDEX.write_text(html, encoding="utf-8")

    colour = sum(v for k, v in tally.items() if k.startswith(("colour", "inline #")))
    inline = sum(v for k, v in tally.items() if k.startswith("inline"))
    type_n = sum(v for k, v in tally.items() if k.startswith("type ") and "glyph" not in k)
    print(f"{colour} colour literals and {type_n} font sizes in the stylesheet")
    print(f"{inline} replacements inside style attributes")
    print(f"{tally['type left as a glyph size']} glyph-size rules left alone\n")
    for key, n in sorted(tally.items(), key=lambda kv: -kv[1]):
        if n > 2:
            print(f"  {n:4d}  {key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
