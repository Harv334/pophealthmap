#!/usr/bin/env python3
"""Fail if an em dash (U+2014) or en dash (U+2013) appears in user-facing text.

The house style writes ranges as "1 to 2" and never uses either dash. This
checks everything a visitor can be shown: the map page, the four pages around
it, the Ask panel and the generated label files under data/map/. Comments are
removed first (HTML comments, and JavaScript and CSS comments inside the
pages and scripts), so a note to a developer is not a hit, and line numbers
still match the file. Each hit is printed as file:line with the text around it.

Two kinds of code legitimately contain a dash, because they find dashes in
published text in order to replace them:

  * a JavaScript regular expression literal, such as /\\s*[-\\u2013]\\s*/, which is
    skipped automatically: a pattern is never shown to anyone;
  * any other line that matches dashes in order to replace them, such as a
    string passed to split() or new RegExp(). Mark it with a comment holding
    "dash-ok" and say why, and the line is skipped.

Usage: python3 tests/check_dashes.py [repo root]   (exit 1 on any hit)
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else pathlib.Path(__file__).resolve().parents[1])
PAGES = ["index.html", "methodology.html", "accessibility.html", "privacy.html", "404.html"]
# The two characters, and the ways a page or script can spell them: HTML
# entities, and \u escapes inside a JavaScript string.
DASHES = re.compile("[" + chr(0x2013) + chr(0x2014) + r"]|&[mn]dash;|&#821[12];|&#x201[34];|\\u201[34]", re.I)
MARK = "dash-ok"

# A "/" after one of these (or at the start) begins a regular expression
# literal rather than a division.
_RE_AFTER = set("(,=:[!&|?{};+-*%<>~^")
_RE_KEYWORDS = {"return", "typeof", "case", "do", "else", "in", "of", "new", "delete", "void", "throw"}


def _blank(text: str) -> str:
    """The same text with everything but newlines turned into spaces."""
    return re.sub(r"[^\n]", " ", text)


def strip_js(src: str) -> str:
    """JavaScript with comments and regular expression literals blanked out,
    strings and template literals kept, and every newline where it was.

    Template literals can hold ${...} expressions, which can hold strings,
    comments and further template literals, so the scanner keeps a stack:
    "code" with a count of open braces, or "tpl" inside a template's text."""
    out = []
    i, n = 0, len(src)
    prev = ""            # last significant character outside strings
    word = ""            # last identifier, for "return /re/"
    stack = [["code", 0]]
    while i < n:
        c = src[i]
        nxt = src[i + 1] if i + 1 < n else ""
        top = stack[-1]
        if top[0] == "tpl":
            if c == "\\":
                out.append(src[i:i + 2]); i += 2
            elif c == "`":
                out.append(c); i += 1; stack.pop(); prev, word = "`", ""
            elif c == "$" and nxt == "{":
                out.append("${"); i += 2; stack.append(["code", 0]); prev, word = "{", ""
            else:
                out.append(c); i += 1
            continue
        if c == "/" and nxt == "/":
            j = src.find("\n", i)
            j = n if j == -1 else j
            out.append(_blank(src[i:j])); i = j
            continue
        if c == "/" and nxt == "*":
            j = src.find("*/", i + 2)
            j = n if j == -1 else j + 2
            out.append(_blank(src[i:j])); i = j
            continue
        if c == "`":
            out.append(c); i += 1; stack.append(["tpl", 0])
            continue
        if c in "'\"":
            j = i + 1
            while j < n and src[j] != c:
                if src[j] == "\\":
                    j += 1
                elif src[j] == "\n":
                    break
                j += 1
            out.append(src[i:j + 1]); i = j + 1
            prev, word = c, ""
            continue
        if c == "/" and (prev == "" or prev in _RE_AFTER or word in _RE_KEYWORDS):
            j, in_class = i + 1, False
            while j < n and src[j] != "\n":
                if src[j] == "\\":
                    j += 2; continue
                if src[j] == "[":
                    in_class = True
                elif src[j] == "]":
                    in_class = False
                elif src[j] == "/" and not in_class:
                    break
                j += 1
            out.append(_blank(src[i:j + 1])); i = j + 1
            prev, word = "/", ""
            continue
        if c == "{":
            top[1] += 1
        elif c == "}":
            if top[1] == 0 and len(stack) > 1:
                stack.pop()              # end of a template's ${...}
                out.append(c); i += 1
                continue
            top[1] -= 1
        out.append(c)
        if c.isalnum() or c in "_$":
            word = word + c if (prev.isalnum() or prev in "_$") else c
            prev = c
        elif not c.isspace():
            prev, word = c, ""
        i += 1
    return "".join(out)


def strip_css(src: str) -> str:
    return re.sub(r"/\*.*?\*/", lambda m: _blank(m.group(0)), src, flags=re.S)


def strip_html(src: str) -> str:
    """HTML with comments removed, and the JavaScript and CSS inside script
    and style elements stripped as above."""
    src = re.sub(r"<!--.*?-->", lambda m: _blank(m.group(0)), src, flags=re.S)
    def script(m):
        return m.group(1) + strip_js(m.group(2)) + m.group(3)
    src = re.sub(r"(<script\b[^>]*>)(.*?)(</script>)", script, src, flags=re.S | re.I)
    src = re.sub(r"(<style\b[^>]*>)(.*?)(</style>)",
                 lambda m: m.group(1) + strip_css(m.group(2)) + m.group(3), src, flags=re.S | re.I)
    return src


def check(path: pathlib.Path, stripped: str, original: str) -> list[str]:
    hits = []
    orig_lines = original.split("\n")
    for no, line in enumerate(stripped.split("\n"), 1):
        if not DASHES.search(line):
            continue
        if MARK in orig_lines[no - 1]:
            continue
        m = DASHES.search(line)
        ctx = line[max(0, m.start() - 50): m.end() + 50].strip()
        hits.append(f"{path.relative_to(ROOT)}:{no}: {ctx}")
    return hits


def main() -> int:
    files = [ROOT / p for p in PAGES]
    files += sorted((ROOT / "data" / "map").glob("*.js"))
    hits, seen = [], 0
    for f in files:
        if not f.exists():
            continue
        seen += 1
        text = f.read_text(encoding="utf-8")
        stripped = strip_html(text) if f.suffix == ".html" else strip_js(text)
        hits += check(f, stripped, text)
    for h in hits:
        print(h)
    if hits:
        print(f"\n{len(hits)} dash(es) in user-facing text across {seen} files. "
              f"Write ranges as \"1 to 2\" and use commas, colons or full stops instead.")
        return 1
    print(f"No em or en dashes in user-facing text ({seen} files checked).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
