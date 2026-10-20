#!/usr/bin/env python3
"""Stamp the assembled site with the date its data last changed.

Run by scripts/build_site.sh on the copy in _site, never on the repository's
own files, so a refresh does not have to commit index.html or sitemap.xml:

  - sitemap.xml is written afresh. The home page's lastmod is London's
    manifest last_run, each published region (data/regions/<id>/ with a
    map/wards.js, other than the England view, which asks before it loads)
    gets its own ?r=<id> entry dated by its own manifest, and the static
    pages keep the lastmod given for them in the repository's sitemap.xml,
    which changes only when those pages do.
  - The Dataset structured data in index.html gets the same last_run as its
    dateModified, in place of the starting value typed into the page.

Usage: scripts/stamp_site.py OUT
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from xml.sax.saxutils import escape

SITE = "https://pophealth.uk/"
STATIC = (
    ("methodology.html", "monthly", "0.8"),
    ("accessibility.html", "yearly", "0.4"),
    ("privacy.html", "yearly", "0.4"),
)


def last_run(manifest: Path) -> str:
    try:
        m = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    v = str(m.get("last_run") or "")[:10]
    return v if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v) else ""


def static_dates(sitemap: Path) -> dict[str, str]:
    """The lastmod of each page in an existing sitemap, by its address."""
    try:
        text = sitemap.read_text(encoding="utf-8")
    except OSError:
        return {}
    out = {}
    for loc, mod in re.findall(r"<loc>([^<]+)</loc>\s*<lastmod>([^<]+)</lastmod>", text):
        out[loc.strip()] = mod.strip()
    return out


def regions(out: Path) -> list[tuple[str, str]]:
    base = out / "data" / "regions"
    if not base.is_dir():
        return []
    found = []
    for d in sorted(base.iterdir()):
        if d.name == "england" or not (d / "map" / "wards.js").is_file():
            continue
        found.append((d.name, last_run(d / "meta" / "manifest.json")))
    return found


def build_sitemap(out: Path, given: dict[str, str]) -> str:
    home = last_run(out / "data" / "meta" / "manifest.json")

    def entry(loc, mod, freq, prio):
        lines = ["  <url>", f"    <loc>{escape(loc)}</loc>"]
        if mod:
            lines.append(f"    <lastmod>{mod}</lastmod>")
        lines += [f"    <changefreq>{freq}</changefreq>", f"    <priority>{prio}</priority>", "  </url>"]
        return "\n".join(lines)

    parts = [entry(SITE, home, "monthly", "1.0")]
    for rid, mod in regions(out):
        parts.append(entry(f"{SITE}?r={rid}", mod or home, "monthly", "0.9"))
    for page, freq, prio in STATIC:
        loc = SITE + page
        parts.append(entry(loc, given.get(loc, ""), freq, prio))
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<!-- Written by scripts/stamp_site.py when the site is assembled. -->\n"
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(parts)
        + "\n</urlset>\n"
    )


def stamp_index(index: Path, date: str) -> bool:
    if not date or not index.is_file():
        return False
    text = index.read_text(encoding="utf-8")
    new, n = re.subn(r'("dateModified":\s*")\d{4}-\d{2}-\d{2}(")', r"\g<1>" + date + r"\g<2>", text, count=1)
    if n:
        index.write_text(new, encoding="utf-8")
    return bool(n)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    out = Path(argv[1])
    given = static_dates(out / "sitemap.xml")
    (out / "sitemap.xml").write_text(build_sitemap(out, given), encoding="utf-8")
    date = last_run(out / "data" / "meta" / "manifest.json")
    stamped = stamp_index(out / "index.html", date)
    print(f"stamp_site: sitemap with {len(regions(out))} region(s); dateModified "
          f"{date if stamped else 'left as it was'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
