"""Write the LA-to-region index that index.html carries for its search box.

Every map shows one region, and its search only knows that region's places.
Someone in Leeds who opens the London map and types "Leeds" should be told
which map Leeds is on, so the page carries a small index of every English LA
and the region it belongs to: about 300 names, under 5 KB.

It is written from scopes/<region>.json (the same lists each region's build
runs from) into the block between the LA_REGIONS markers in index.html, so
the page needs no extra request for it.

    python scripts/build_la_regions.py           # rewrite the block
    python scripts/build_la_regions.py --check   # fail if it is out of date
"""
import argparse, json, re, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BEGIN = "/* LA_REGIONS:BEGIN (scripts/build_la_regions.py writes this line) */"
END = "/* LA_REGIONS:END */"


def display_name(name: str) -> str:
    """ONS's "Bristol, City of" as people type it: "Bristol"."""
    return re.sub(r",\s*(City|County) of$", "", str(name).strip())


def build_index(repo: Path = REPO) -> dict:
    """{region slug: [LA name, ...]} from every scope file, names sorted."""
    out = {}
    for f in sorted((repo / "scopes").glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        names = sorted({display_name(n) for n, _code in d.get("lads", []) if str(n).strip()})
        for n in names:
            if "|" in n:
                raise ValueError(f"{f.name}: LA name {n!r} contains '|'")
        if names:
            out[f.stem] = names
    return out


def render(index: dict) -> str:
    """One line of JS: each region's LA names joined by '|'."""
    body = json.dumps({k: "|".join(v) for k, v in sorted(index.items())},
                      ensure_ascii=False, separators=(",", ":"))
    return f"{BEGIN}\nconst PH_LA_REGIONS = {body};\n{END}"


def splice(html: str, block: str) -> str:
    pat = re.compile(re.escape(BEGIN) + r".*?" + re.escape(END), re.S)
    if not pat.search(html):
        raise SystemExit("build_la_regions: the LA_REGIONS markers are missing from index.html")
    return pat.sub(lambda _m: block, html, count=1)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="exit 1 if index.html is out of date")
    args = ap.parse_args()
    page = REPO / "index.html"
    html = page.read_text(encoding="utf-8")
    new = splice(html, render(build_index()))
    if args.check:
        if new != html:
            print("build_la_regions: index.html's LA index is out of date; run scripts/build_la_regions.py")
            return 1
        print("build_la_regions: up to date")
        return 0
    if new != html:
        page.write_text(new, encoding="utf-8")
        print("build_la_regions: index.html updated")
    else:
        print("build_la_regions: no change")
    return 0


if __name__ == "__main__":
    sys.exit(main())
