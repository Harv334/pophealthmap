#!/usr/bin/env python3
"""Build the handover copy of this repository: the map, without the AI panel.

The map published at pophealth.uk has a question panel backed by a Cloudflare
Worker and an API key. That is a running cost and an account that belongs to a
person, so it cannot be handed to somebody else along with the code. Everything
else can, and does: the pipeline, the data, the tests and the site.

This produces that copy. Run it, check the output, then push the result to its
own repository.

    python scripts/build_handover.py ../pophealthmap-handover

What is left out
    worker/                 the Cloudflare Worker behind the panel
    data/map/assistant.js   the browser half of it
    archive/                one-off patch scripts kept for their history
    CNAME                   pophealth.uk belongs to the original site. Two
                            repositories claiming one custom domain is a
                            conflict, and GitHub gives it to whichever
                            published last, so the handover copy carries none
                            and serves from github.io until its owner sets one.
    sitemap.xml robots.txt  both name pophealth.uk and describe that site's
                            indexing, not this one's
    scripts/build_handover.py   this file. It builds a handover copy from the
                            original, which is not a thing the handover copy
                            can do.
    .cache/ __pycache__/    build leftovers, already gitignored

What is edited
    index.html              the <script> tag for assistant.js, the #ai-* CSS,
                            and the tour chapter about the panel

What is NOT edited, and is left for whoever takes it on
    index.html still carries a canonical link, an og:url and a JSON-LD block
    naming pophealth.uk. Those are correct while this is a copy of that site
    and wrong the moment it becomes its own thing, so they are a decision for
    its owner rather than a rewrite done on their behalf. They are listed in
    HANDOVER.md.

Nothing else is touched. The point of a generated copy rather than a
hand-pruned one is that it can be produced again when this repository moves on,
and that the list of differences is this file rather than somebody's memory.
"""
from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent

# Directories and files the handover copy does not carry.
EXCLUDE_DIRS = {".git", ".cache", "__pycache__", "worker", "archive",
                ".pytest_cache", ".venv", "venv"}
EXCLUDE_FILES = {
    "data/map/assistant.js",
    "CNAME",                      # would claim pophealth.uk from the original
    "sitemap.xml",                # names pophealth.uk throughout
    "robots.txt",                 # ditto, and points at that sitemap
    "scripts/build_handover.py",  # builds a handover copy; nothing to build here
}


def strip_index(text: str) -> tuple[str, list[str]]:
    """Take the assistant out of index.html. Returns (text, what changed)."""
    notes: list[str] = []

    tag = '<script src="data/map/assistant.js"></script>\n'
    if tag in text:
        text = text.replace(tag, "")
        notes.append("removed the assistant <script> tag")

    # The #ai-* rules. They style a panel that no longer exists, so they are
    # dead weight rather than a fault, but dead weight is what a handover is
    # meant to be free of.
    css_removed = 0
    out_lines: list[str] = []
    for line in text.split("\n"):
        if re.match(r"\s*(#ai-[a-z-]+|\.ai-[a-z-]+)[\s,{]", line) and line.rstrip().endswith(("}", "{")):
            # only single-line rules are safe to drop this way
            if line.count("{") == line.count("}"):
                css_removed += 1
                continue
        out_lines.append(line)
    text = "\n".join(out_lines)
    if css_removed:
        notes.append(f"removed {css_removed} single-line #ai-/.ai- CSS rules")

    # The tour chapter about the panel. Matched on its own title so a reordered
    # tour does not defeat it, and bounded by the object literal around it.
    m = re.search(r"\n    \{\n      t: 'Ask a question in plain English',", text)
    if m:
        start = m.start()
        depth, i = 0, text.index("{", start)
        j = i
        while j < len(text):
            if text[j] == "{":
                depth += 1
            elif text[j] == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        end = text.index("\n", text.index(",", j))
        text = text[:start] + text[end:]
        notes.append("removed the 'Ask a question in plain English' tour chapter")
    else:
        notes.append("WARNING: the tour chapter was not found; check the tour by hand")

    return text, notes


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    dst = Path(sys.argv[1]).resolve()
    if dst == SRC:
        print("Refusing to build into the source repository.")
        return 1
    if dst.exists() and any(dst.iterdir()) and not (dst / ".git").exists():
        print(f"{dst} is not empty and is not a git repository. "
              f"Delete it or choose another path.")
        return 1

    copied = skipped = 0
    for path in SRC.rglob("*"):
        rel = path.relative_to(SRC)
        parts = set(rel.parts)
        if parts & EXCLUDE_DIRS:
            continue
        if path.is_dir():
            continue
        if rel.as_posix() in EXCLUDE_FILES:
            skipped += 1
            continue
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        copied += 1

    index = dst / "index.html"
    text = index.read_text(encoding="utf-8", newline="")
    text, notes = strip_index(text)
    # newline="" so Windows text mode cannot turn every LF into CRLF and make
    # the first commit a whole-file rewrite.
    with open(index, "w", encoding="utf-8", newline="") as f:
        f.write(text)

    print(f"copied {copied} files, left out {skipped + 1} "
          f"(assistant.js, worker/, archive/)")
    for n in notes:
        print(f"  {n}")

    # The same check the mirror learned to do: the page must not ask for a file
    # this copy does not have. A missing Leaflet left the mirror on its loading
    # screen for a week, with every other file present and correct.
    refs = set()
    for pat in (r'<script[^>]+src="([^"]+)"', r'<link[^>]+href="([^"]+)"',
                r'fetch\(\s*[\'"]([^\'"]+)[\'"]', r'dataUrl\(\s*[\'"]([^\'"]+)[\'"]'):
        for m in re.finditer(pat, text):
            u = m.group(1)
            if re.match(r"^(https?:|//|data:|#|mailto:)", u) or "${" in u:
                continue
            refs.add(u.split("?")[0].split("#")[0])
    missing = sorted(r for r in refs if not (dst / r).exists())
    print(f"  checked {len(refs)} local references from index.html")
    if missing:
        print("\nindex.html asks for files this copy does not have:")
        for r in missing:
            print(f"    {r}")
        return 1

    print(f"\nBuilt at {dst}")
    print("Check it, then: git init; git add -A; git commit; git push")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
