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
    # Tests for the half that is not being handed over. Left in, they would
    # fail on the first run against a repository that has no assistant and no
    # Worker, which is the worst possible first impression: a suite that is
    # red before anything has been changed teaches its new owner to ignore it.
    "tests/pipeline/test_assistant_tools.py",
    "tests/pipeline/test_worker_cap.py",
    "tests/pipeline/test_worker_followup.py",
    # Roughly a third of this one drives the assistant panel, so it cannot pass
    # here either. It is the only exclusion that costs real coverage: the rest
    # of it tests the ward sheet and Query. HANDOVER.md says where to get it
    # back and what to delete from it.
    "tests/browser/test_sheet_query.py",
    # Markers left behind by earlier work. Nothing reads either of them, and a
    # handover should not start with two files whose purpose has to be guessed.
    ".mount_test",
    ".refresh_marker",
    # One-off scripts, kept here for their history and dead weight there. Each
    # was checked for callers first: nothing in the workflow, the pipeline or
    # the docs references any of them. Left in, they are five more files
    # somebody has to open to find out they did not need to.
    "fix_imd.py",                       # one-time IMD repair, already applied
    "cleanup_and_commit.sh",            # a shell helper for this repository
    "scripts/heal_index.py",
    "scripts/check_ward_record.py",     # diagnostics for a fixed bug
    "scripts/check_fingertips_data.py",
    "scripts/tokenise_styles.py",       # one-time CSS migration, already done
    # About the original site's domain, and true of nothing here.
    "DOMAIN.md",
}
# Deliberately kept even though the monthly refresh does not call them:
#   scripts/fetch_fingertips.py, wire_fingertips_ui.py, build_postcode_table.py,
#   build_fonts.py, build_greenspaces.py, reclip_greenspaces.py, fetch_cics.py,
#   build_esol_layers.py and build_esol_v2.py all rebuild a layer or add
#   indicators. They are how you add data, which is the thing this handover is
#   most likely to be asked to do.
#   scripts/reconfigure_to_ons_wd24_lookup.py is imported by fetch_all_data.py.


def strip_readme(text: str) -> tuple[str, list[str]]:
    """Take out the parts of README.md that are not true of the handover copy.

    The README describes the site at pophealth.uk. Most of it is true of any
    copy, but four things are not, and a README that describes features the
    reader cannot find is worse than a short one: it sends them looking for a
    panel that is not there, or a Worker they have no account for.
    """
    notes: list[str] = []

    # The AI panel section, from its heading to the next one.
    start = text.find("\n## The AI panel")
    if start != -1:
        nxt = text.find("\n## ", start + 5)
        text = text[:start] + (text[nxt:] if nxt != -1 else "\n")
        notes.append("removed the AI panel section")

    # The two rows of the contents table that point at files it does not have.
    kept, dropped = [], 0
    for line in text.split("\n"):
        if line.startswith("| `data/map/assistant.js`") or line.startswith("| `worker/`"):
            dropped += 1
            continue
        kept.append(line)
    text = "\n".join(kept)
    if dropped:
        notes.append(f"removed {dropped} contents row(s) for the assistant and the Worker")

    # DOMAIN.md is not carried, and the DNS records it would have explained are
    # written out in the paragraph immediately below this sentence anyway.
    if "See `DOMAIN.md`." in text:
        text = text.replace(" See `DOMAIN.md`.", "")
        notes.append("removed the pointer to DOMAIN.md, which is not carried")

    # Point at HANDOVER.md, which is the page written for this reader.
    marker = "## Handing this over"
    if marker in text:
        text = text.replace(
            marker + "\n\nFor someone who just wants to refresh the map:",
            marker + "\n\n**Read `HANDOVER.md` first.** It covers whether anything is\n"
            "required of you, how the monthly refresh works, and how to add data of\n"
            "your own with a CSV.\n\nThe short version, for refreshing the map:")
        notes.append("pointed the handover section at HANDOVER.md")

    return text, notes


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

    readme = dst / "README.md"
    if readme.exists():
        rtext, rnotes = strip_readme(readme.read_text(encoding="utf-8", newline=""))
        with open(readme, "w", encoding="utf-8", newline="") as f:
            f.write(rtext)
        for n in rnotes:
            print(f"  README.md: {n}")

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
