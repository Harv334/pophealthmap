"""Splice the tail of index.html from git HEAD when virtiofs has truncated
the file mid-write.

Symptoms:
  - `wc -c index.html` is less than expected
  - `tail -c 40 index.html` does not end with `</html>`

Usage:
  python3 scripts/heal_index.py            # heal in place
  python3 scripts/heal_index.py --check    # report only, exit 0 if healthy

Strategy:
  1. Load the current (possibly truncated) index.html.
  2. Load the HEAD version from git.
  3. Find a late-in-file anchor line that appears unchanged in both.
  4. Everything in cur before the anchor is kept as-is (your edits).
     Everything in HEAD from the anchor onward is appended (the healthy tail).
  5. Write atomically, preserving line endings.

Anchors are chosen from the final ~5% of the file — the parts least likely
to be edited day-to-day.
"""

from __future__ import annotations
import argparse
import subprocess
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent
INDEX = REPO / "index.html"

# Late-file anchors, tried in order. Each must be a short byte-string that
# is (a) unique inside index.html and (b) very unlikely to change.
#
# We try anchors from LATEST-in-file to earliest. The latest anchor preserves
# the most of cur; if cur was truncated before that anchor, the next (earlier)
# anchor takes over and pulls more of HEAD's tail into the recovered file.
ANCHORS = [
    b"document.addEventListener(\"DOMContentLoaded\", wire);",
    b"})();\n</script>",
    b"</script>\n</body>",
    b'// Fallback: any ward not matched',
    b'// Fallback: any ward not matche',
    b"// DATA LOADING",
    b"// WARD CHOROPLETH",
    b"function wire()",
]


def git_head_bytes() -> bytes:
    return subprocess.check_output(
        ["git", "-C", str(REPO), "show", "HEAD:index.html"]
    )


def is_healthy(buf: bytes) -> bool:
    tail = buf[-40:]
    return b"</html>" in tail


def heal(buf: bytes, head: bytes) -> bytes | None:
    for anchor in ANCHORS:
        idx_cur = buf.rfind(anchor)
        idx_head = head.rfind(anchor)
        if idx_cur < 0 or idx_head < 0:
            continue
        recovered = buf[:idx_cur] + head[idx_head:]
        if is_healthy(recovered):
            return recovered
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="Exit 0 if healthy, 1 if truncated. Do not write.")
    args = ap.parse_args()

    if not INDEX.exists():
        print(f"[heal] {INDEX} does not exist", file=sys.stderr)
        return 2

    cur = INDEX.read_bytes()
    healthy = is_healthy(cur)
    print(f"[heal] index.html is {len(cur):,} bytes — "
          + ("healthy" if healthy else "TRUNCATED"))

    if healthy:
        return 0
    if args.check:
        return 1

    try:
        head = git_head_bytes()
    except subprocess.CalledProcessError as e:
        print(f"[heal] could not read git HEAD:index.html — {e}", file=sys.stderr)
        return 2

    recovered = heal(cur, head)
    if recovered is None:
        print("[heal] could not find a usable anchor in both cur and HEAD. "
              "You'll need to recover manually.", file=sys.stderr)
        return 3

    # Write via a tmp then replace to avoid a second truncation mid-write.
    tmp = INDEX.with_suffix(".html.healing")
    tmp.write_bytes(recovered)
    tmp.replace(INDEX)
    print(f"[heal] recovered to {len(recovered):,} bytes "
          f"(+{len(recovered) - len(cur):,} bytes spliced from HEAD)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
