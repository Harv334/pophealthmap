"""
fetch_all_data.py - NW London Population Health Pipeline (single-file)
======================================================================

Produces the three JSON files consumed by index.html:
    ward_data.json   - ward-level indicators (188 wards, nested shape)
    lsoa_data.json   - LSOA-level IMD + census (33,755 LSOAs)
    pharmacies.json  - pharmacy point data (~540 rows)

Plus it writes the map data files under data/map/ that index.html loads.

------------------------------------------------------------------------------
MANUAL DOWNLOADS  (none are required for a normal run)
------------------------------------------------------------------------------
Everything is either fetched from an open API or committed to the repo. The
only files you can drop in .cache/ are these, and all are optional:

  .cache/imd2025/File_7_IoD2025_All_Ranks_...csv  [only to rebuild]
      Index of Multiple Deprivation 2025 - File 7 (all domains). IMD is static
      between releases, so data/demographics/imd2025.parquet is committed and
      used as-is. Drop the raw CSV here only when a new IoD is published and
      the parquet needs regenerating. See IMD_SOURCE_URL below.

  .cache/iod2025/File_7_...csv, File_8_...xlsx  [only to rebuild]
      The same release's File 7 again (IDACI, IDAOPI, sub-domains) and File 8
      (underlying indicators). Both are downloaded from the release page by
      themselves until data/demographics/iod2025_extra.parquet and
      iod2025_indicators.parquet hold every column; a file dropped here is
      read instead. See IOD_API_URL below.

No cache needed - the script hits these APIs directly (cached between runs):
  - ODS 'ets' extract    (NHS trust sites, which hospitals are derived from)
  - OHID Fingertips      (health outcomes per LAD)
  - data.police.uk       (crime per borough polygon per month)
  - Nomis Census 2021    (topic-summary tables, ~150 MB first run, cached)
  - postcodes.io         (postcode -> LSOA / ward / borough + coordinates)
  - ONS Open Geography   (LSOA 2021 -> ward 2025 best-fit lookup)
  - NHS ODS Data Search  (epraccur GP + edispensary pharmacy registers,
                          both ETag-revalidated so an unchanged file is a 304)
  - DWP Stat-Xplore      (benefit caseloads; only with a free API key in the
                          STAT_XPLORE_API_KEY environment variable, and
                          skipped without one)

------------------------------------------------------------------------------
USAGE
------------------------------------------------------------------------------
  python fetch_all_data.py                      # run all sources + export
  python fetch_all_data.py --only imd gp        # run a subset, then export
  python fetch_all_data.py --skip crime         # skip slow sources
  python fetch_all_data.py --export-only        # skip fetches, just rebuild JSON

Dependencies:
  pip install pandas pyarrow requests pyproj shapely

------------------------------------------------------------------------------
WHY THE ATOMIC WRITES?
------------------------------------------------------------------------------
The Windows workspace mount has a disk-sync quirk where `open(...).write()`
can return before bytes reach disk, producing 2-byte truncated files.
All JSON + Parquet outputs are written via tempfile + fsync + os.replace
to defeat this. If you see empty outputs, that's the bug - never disable
the atomic wrappers.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import sys
import time
import urllib.parse
import zipfile
from html import unescape as html_unescape
from collections import Counter
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

try:
    import pandas as pd
    import requests
except ImportError as e:
    print(f"ERROR: missing dependency ({e.name}). Run: pip install pandas pyarrow requests pyproj shapely")
    sys.exit(1)

# Section headers use box-drawing characters. A Windows console, or any run
# with stdout redirected to a file, defaults to cp1252 and cannot encode them,
# which killed a full run at a log line rather than at anything real. Force
# UTF-8 and degrade gracefully if the stream does not support reconfiguring.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

REPO_ROOT = Path(__file__).resolve().parent
CACHE_DIR = REPO_ROOT / ".cache"
DATA_DIR  = REPO_ROOT / "data"

# ============================================================================
# SCOPE: which local authorities this run builds.
# Each scope is a reviewable file in scopes/: a name, its ONS region code and
# its LAD25 codes and names. This single list is what defines the map's
# footprint. Everything else, the boundaries, the LSOA set, the ward set, the
# facility filters, derives from it.
#
# London is the default and builds exactly what it always has, into the same
# places. Any other scope (--scope north-east, or PH_SCOPE=north-east) writes
# everything it produces under data/regions/<scope>/, intermediates included,
# so a regional build can never overwrite the live London files. Read before
# argparse, because these constants are used at import time.
# ============================================================================
def _scope_arg() -> str:
    argv = sys.argv[1:]
    for i, a in enumerate(argv):
        if a == "--scope" and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith("--scope="):
            return a.split("=", 1)[1]
    return os.environ.get("PH_SCOPE", "london")

SCOPE_ID = _scope_arg().strip().lower()
_scope_file = REPO_ROOT / "scopes" / f"{SCOPE_ID}.json"
if not _scope_file.exists():
    raise SystemExit(f"Unknown scope {SCOPE_ID!r}: no {_scope_file}. "
                     f"Available: {', '.join(sorted(p.stem for p in (REPO_ROOT / 'scopes').glob('*.json')))}")
_scope = json.loads(_scope_file.read_text(encoding="utf-8"))
SCOPE_NAME = _scope["name"]
SCOPE_REGION = _scope.get("region_code", "")
BOROUGHS = [(n, c) for n, c in _scope["lads"]]
SCOPE_LADS = {b[1] for b in BOROUGHS}
LAD_NAMES = {b[1]: b[0] for b in BOROUGHS}
IS_LONDON = SCOPE_ID == "london"
# Log wording only: London's councils are boroughs, elsewhere they are not.
LA_WORD = "borough" if IS_LONDON else "council"

# Where this scope's outputs go. London: the repo root and data/, as before.
# Anywhere else: one folder holding that region's whole build.
NATIONAL_DATA_DIR = REPO_ROOT / "data"
if not IS_LONDON:
    DATA_DIR = REPO_ROOT / "data" / "regions" / SCOPE_ID
OUT_ROOT = REPO_ROOT if IS_LONDON else DATA_DIR

# A scope fingerprint, so caches cannot silently replay a different footprint.
SCOPE_KEY = hashlib.sha256(",".join(sorted(SCOPE_LADS)).encode()).hexdigest()[:10]

# ============================================================================
# LOGGING (no rich dep — plain ANSI colours)
# ============================================================================
def _c(code: str, s: str) -> str:
    return f"\033[{code}m{s}\033[0m"

def info(msg: str) -> None:  print(_c("36", "[..]") + " " + msg)
def ok(msg: str)   -> None:  print(_c("32", "[OK]") + " " + msg)
def warn(msg: str) -> None:  print(_c("33", "[!!]") + " " + msg)
def err(msg: str)  -> None:  print(_c("31", "[ER]") + " " + msg)
def rule(msg: str) -> None:  print("\n" + _c("1;34", f"─── {msg} " + "─" * (60 - len(msg))))

# ============================================================================
# ATOMIC WRITE — defeats the workspace disk-sync bug
# ============================================================================
def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    # Write as bytes so the OS byte count is unambiguous — avoids Windows
    # text-mode translation AND sidesteps any `newline=""` edge cases that
    # could leave `text` partially buffered.
    payload = text.encode("utf-8")
    expected = len(payload)
    with open(tmp, "wb") as f:
        f.write(payload)
        f.flush()
        os.fsync(f.fileno())
    # Verify we actually wrote all bytes. If not, remove the tmp file and
    # raise so callers don't silently commit a half-written JSON.
    actual = tmp.stat().st_size
    if actual != expected:
        try: tmp.unlink()
        except Exception: pass
        raise IOError(
            f"write_atomic: short write to {tmp} "
            f"(wrote {actual} of {expected} bytes)"
        )
    os.replace(tmp, path)

def write_bytes_atomic(path: Path, payload: bytes) -> None:
    """Byte-oriented sibling of write_atomic, for downloaded files we must not decode."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as f:
        f.write(payload)
        f.flush()
        os.fsync(f.fileno())
    actual = tmp.stat().st_size
    if actual != len(payload):
        try: tmp.unlink()
        except Exception: pass
        raise IOError(
            f"write_bytes_atomic: short write to {tmp} "
            f"(wrote {actual} of {len(payload)} bytes)"
        )
    try:
        os.replace(tmp, path)
    except PermissionError:
        # Some cached downloads were dropped in read-only; Windows refuses to
        # replace those. Clear the flag and retry rather than failing the source.
        try:
            os.chmod(path, 0o666)
        except OSError:
            pass
        os.replace(tmp, path)

# ============================================================================
# RUN MANIFEST - data/meta/manifest.json
# ============================================================================
# Records what each source did on the last run. The scheduled refresh workflow
# reads it to name the sources that failed, and the map reads last_run to show
# a freshness date. main() sets _MANIFEST_SOURCE around each source so writes
# attribute themselves without every run_* function having to know its own CLI
# name.
MANIFEST_PATH = DATA_DIR / "meta" / "manifest.json"
_MANIFEST_SOURCE: str | None = None
_MANIFEST_WRITES: dict[str, list[tuple[str, int]]] = {}

def _record_write(path: Path, rows: int) -> None:
    if not _MANIFEST_SOURCE:
        return
    try:
        rel = str(path.relative_to(REPO_ROOT)).replace("\\", "/")
    except ValueError:
        rel = str(path)
    _MANIFEST_WRITES.setdefault(_MANIFEST_SOURCE, []).append((rel, rows))

def write_parquet_guarded(path: Path, df: "pd.DataFrame", *, source: str) -> "pd.DataFrame":
    """
    Write a parquet, refusing to replace a non-empty file with an empty one.

    An empty frame almost always means the fetch failed rather than the real
    world emptying out, and nothing downstream reads it as an error: a 0-row
    parquet has no columns at all, so the builders' `"WD25CD" in df.columns`
    guards read it as "this source has nothing to say" and quietly drop the
    indicators. Keeping the last good file is the safer failure, and the caller
    still sees the warning.
    """
    if len(df) == 0 and path.exists():
        try:
            existing = pd.read_parquet(path)
        except Exception:
            existing = None
        if existing is not None and len(existing) > 0:
            warn(f"{source}: produced 0 rows but {path.name} already holds "
                 f"{len(existing):,}; keeping the existing file")
            _record_write(path, len(existing))
            return existing
    write_parquet_atomic(path, df)
    _record_write(path, len(df))
    return df

# Sources that kept some of last run's figures because this run's came back
# missing or thin. main() reads it to mark the source "partial", which the
# refresh workflow reports in an issue like a failure.
_SOURCE_PARTIAL: dict[str, str] = {}
# Sources whose output was written but failed a check that means part of it is
# wrong or missing for this pack (an LA with none of its figures, an indicator
# at the wrong age). main() marks them "failed" with this message, so the
# refresh workflow raises them, while the rows that passed are still used.
_SOURCE_ERROR: dict[str, str] = {}
# Something worth knowing about a source, for its manifest notes: why one that
# returned no frame had nothing to do (a publisher that does not publish the
# figures for this geography, say) instead of the generic "skipped" note, or,
# for an "ok" source, an optional column the publisher no longer prints. It
# never changes the status.
_SOURCE_NOTE: dict[str, str] = {}
# Sources that ran and had nothing to ship for a reason that is the
# publisher's, not a failure (every new Local Health series withheld in this
# region, say). main() marks them "skipped" with this as the note, which the
# region pack check lets through.
_SOURCE_SKIPPED: dict[str, str] = {}

def carry_forward_thin(path: Path, df: "pd.DataFrame", *, key: str, source: str,
                       requested, cache_file=None,
                       min_share: float = 0.9) -> "pd.DataFrame":
    """
    Keep last run's rows for any requested indicator this run lost or thinned.

    write_parquet_guarded only catches a source that returns nothing. The
    October 2026 refresh showed the other half: Fingertips timed out on some
    indicators and sent a fraction of the rows for others, so six Local Health
    indicators vanished, five fell from 963 MSOAs to 77, and the run still
    reported ok. Here an indicator that has fewer than min_share of the rows
    it had last time is replaced by last time's rows, which carry their own
    period, so the map labels them correctly as older figures.

    `requested` is the set of keys this run asked for, so an indicator that was
    dropped on purpose is not resurrected. `cache_file(key)`, when given, names
    the cached download behind a key; a thin one is deleted so the next run
    asks again rather than rereading the same short file every month.
    """
    if not path.exists():
        return df
    try:
        prev = pd.read_parquet(path)
    except Exception:
        return df
    if prev.empty or key not in prev.columns:
        return df
    prev_n = prev[key].value_counts()
    new_n = (df[key].value_counts() if len(df) and key in df.columns
             else pd.Series(dtype="int64"))
    thin = [k for k, n in prev_n.items()
            if k in requested and new_n.get(k, 0) < n * min_share]
    if not thin:
        return df
    kept = prev[prev[key].isin(thin)]
    fresh = df[~df[key].isin(thin)] if len(df) and key in df.columns else df
    out = pd.concat([fresh, kept], ignore_index=True)
    if cache_file is not None:
        for k in thin:
            try:
                cache_file(k).unlink(missing_ok=True)
            except Exception:
                pass
    shown = ", ".join(str(k) for k in thin[:12]) + (" ..." if len(thin) > 12 else "")
    msg = (f"kept last run's figures for {len(thin)} indicator(s) that came back "
           f"missing or incomplete: {shown}")
    warn(f"{source}: {msg}")
    _SOURCE_PARTIAL[source] = msg
    return out

_FT_FAILS_IN_A_ROW = [0]

def _ft_get(url: str, timeout: int) -> "requests.Response":
    """GET a Fingertips download, retrying twice. One slow answer used to cost
    an indicator for the whole month.

    A 4xx is an answer, not a hiccup, so it is not retried. Five downloads in a
    row failing means Fingertips is down rather than slow, and asking for each
    of the 600 or so remaining indicators three times would run the refresh past
    its two-hour limit and lose every other source with it. So it stops asking,
    and carry_forward_thin keeps last run's figures for what was not fetched."""
    if _FT_FAILS_IN_A_ROW[0] >= 5:
        raise RuntimeError("Fingertips is not answering; not asking again this run")
    last = None
    for wait in (0, 5, 20):
        if wait:
            time.sleep(wait)
        try:
            r = requests.get(url, timeout=timeout)
            r.raise_for_status()
            _FT_FAILS_IN_A_ROW[0] = 0
            return r
        except requests.HTTPError as e:
            if e.response is not None and 400 <= e.response.status_code < 500:
                raise
            last = e
        except Exception as e:
            last = e
    _FT_FAILS_IN_A_ROW[0] += 1
    raise last

def _scrub_nan(obj):
    """Recursively replace NaN/Infinity with None so JSON is browser-parseable."""
    import math
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, dict):
        return {k: _scrub_nan(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_scrub_nan(v) for v in obj]
    return obj

def write_json_atomic(path: Path, data, pretty: bool = False) -> None:
    data = _scrub_nan(data)
    # allow_nan=False so we error loudly if any NaN snuck through (browser JSON.parse rejects NaN)
    if pretty:
        s = json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True, allow_nan=False)
    else:
        s = json.dumps(data, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    write_atomic(path, s)

def write_parquet_atomic(path: Path, df: "pd.DataFrame") -> None:
    """Parquet + fsync + rename. pyarrow is required."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    df.to_parquet(tmp, engine="pyarrow", compression="snappy", index=False)
    # Force sync on the file before replace
    with open(tmp, "rb+") as f:
        os.fsync(f.fileno())
    os.replace(tmp, path)

# ============================================================================
# HTTP helpers — browser-like headers to bypass NHS/gov Cloudflare blocks
# ============================================================================
def browser_session(referer: str | None = None) -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
                  "image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-GB,en;q=0.9",
        # Only what requests can actually decode. It handled gzip and deflate
        # itself and needed the brotli package for `br`, which is not a
        # dependency here, so any server that chose Brotli handed back bytes
        # that r.text turned into mojibake. Nothing crashed: a regex over
        # compressed data simply matches nothing, so a fetch would report that
        # a page listed no links rather than that it could not read the page.
        # Both the London Datastore and gov.uk choose Brotli when offered it.
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    })
    if referer:
        s.headers["Referer"] = referer
    return s

def normalise_postcode(pc: str) -> str:
    return (pc or "").replace(" ", "").upper().strip()

def postcode_area(pc: str) -> str:
    """Leading letters of the outward code: 'W8 5SF' -> 'W', 'HA1 3HP' -> 'HA'."""
    m = re.match(r"^[A-Z]+", normalise_postcode(pc))
    return m.group(0) if m else ""

# Names and addresses in the NHS registers arrive in capitals. str.title() gets
# three things wrong in them: it capitalises after an apostrophe ("St Mary'S"),
# it lower-cases acronyms ("Nhs", "Gp") and it capitalises ordinals ("1St
# Floor"). title_case() is str.title() with those put right.
TITLE_ACRONYMS = {w: w for w in (
    "NHS", "GP", "UK", "CIC", "CIO", "YMCA", "YWCA", "PCN", "ICB", "CCG", "PCT",
    "PMS", "APMS", "LLP", "PLC", "HMP", "UCL", "UCLH", "ENT", "NE", "NW", "SE", "SW",
    "II", "III", "IV",
)}
TITLE_ACRONYMS.update({"GPS": "GPs", "PCNS": "PCNs", "ICBS": "ICBs"})
# Short words with no vowel are usually initials ("RJ's Pharmacy", "Brent
# Central KWH"), except these, which are ordinary abbreviations.
_TITLE_NOT_INITIALS = {
    "DR", "DRS", "MR", "MRS", "MS", "MX", "ST", "STS", "LTD", "RD", "CTR", "HTH",
    "HLTH", "CT", "CL", "STN", "FL", "FLR", "PL", "SQ", "PH", "WM", "GDNS", "GRN",
    "LN", "PK", "YD", "BLVD", "BLDG", "CRT", "WHF", "HWY", "MT", "MKT", "MDDX",
}
_TITLE_PERSON = {"DR", "DRS", "MR", "MRS", "MS", "MISS", "PROF"}
_TITLE_MINOR = {"and", "of", "for", "in", "on", "at", "upon", "with", "to"}
_TITLE_APOS_TAIL = {"s", "t", "d", "ll", "re", "ve", "m"}
_OUTWARD_CODE = re.compile(r"^[A-Z]{1,2}\d{1,2}[A-Z]?$")
_INWARD_CODE = re.compile(r"^\d[A-Z]{2}$")
_ORDINAL = re.compile(r"^(\d+)(ST|ND|RD|TH)$", re.I)


def _title_part(p: str, fixed: dict, *, first: bool, initials: bool,
                after_person: bool, after_minor: bool = False,
                last: bool = False) -> str:
    """One word, or one piece of a hyphenated word, in title case."""
    up = p.upper()
    if up in fixed:
        return fixed[up]
    m = re.match(r"^(.+?)(['’])[Ss]$", p)
    if m:                                   # possessive: recase the stem alone
        return _title_part(m.group(1), fixed, first=first, initials=initials,
                           after_person=after_person, after_minor=after_minor,
                           last=last) + m.group(2) + "s"
    m = _ORDINAL.match(p)
    if m:
        return m.group(1) + m.group(2).lower()
    if initials and p.isalpha() and up not in _TITLE_NOT_INITIALS and (
            (2 <= len(p) <= 4 and not re.search(r"[AEIOUY]", up))
            or (after_person and len(p) == 2)):
        return up
    if not first and not last and (p.lower() in _TITLE_MINOR
                                   or (after_minor and p.lower() == "the")):
        return p.lower()          # "Centre for Health", "Health on the Hill"
    w = p.title()
    # "O'Brien" keeps its capital, "Can'T" does not.
    w = re.sub(r"(['’])([A-Za-z]+)",
               lambda m: m.group(1) + (m.group(2).lower()
                                       if m.group(2).lower() in _TITLE_APOS_TAIL
                                       else m.group(2)), w)
    if re.match(r"^Mc[a-z]{2,}$", w):
        w = "Mc" + w[2].upper() + w[3:]
    return w


def title_case(text: str, *, initials: bool = False, acronyms: dict | None = None) -> str:
    """
    A register name or address line in title case, as a person would write it.

        "ST MARY'S SURGERY"          -> "St Mary's Surgery"
        "NHS WALK-IN CENTRE"         -> "NHS Walk-in Centre"
        "4TH FLOOR, 75 OLIVER ROAD"  -> "4th Floor, 75 Oliver Road"
        "SOHO CENTRE FOR HEALTH"     -> "Soho Centre for Health"
        "MCCARTHY & O'BRIEN"         -> "McCarthy & O'Brien"

    Acronyms keep their capitals, and so does a postcode. With initials=True,
    which suits names but not addresses, a short word with no vowel is taken
    to be initials ("RJ'S PHARMACY" -> "RJ's Pharmacy"), as is a two-letter
    word after Dr, Mr, Mrs or Ms ("DR AA ANSARI" -> "Dr AA Ansari").
    """
    fixed = {**TITLE_ACRONYMS, **(acronyms or {})}
    tokens = str(text or "").split()
    out: list[str] = []
    starts = True               # the next word begins a name or a new phrase
    after_minor = False         # the last word was "at", "for", "on" and so on
    prev = ""
    for n, tok in enumerate(tokens):
        lead, core, trail = re.match(r"^([^A-Za-z0-9]*)(.*?)([^A-Za-z0-9]*)$",
                                     tok).groups()
        up = core.upper()
        if not core:
            out.append(tok)
            after_minor = False
            if tok in ("-", "\u2013", ":", "|"):
                starts = True
            continue
        if re.match(r"^(?:www\.|https?://)", core, re.I) or re.search(
                r"\.(?:com|net|org|co\.uk|org\.uk|nhs\.uk)$", core, re.I):
            word = core.lower()
        elif _OUTWARD_CODE.match(up) or (_INWARD_CODE.match(up)
                                        and _OUTWARD_CODE.match(prev.upper())):
            word = up
        else:
            first = starts or "(" in lead
            after_person = (prev.upper() in _TITLE_PERSON and n + 1 < len(tokens))
            parts = re.split(r"([-/])", core)
            word = "".join(
                p if p in ("-", "/") else
                _title_part(p, fixed, first=first and i == 0, initials=initials,
                            after_person=after_person and len(parts) == 1,
                            after_minor=after_minor and i == 0,
                            last=n + 1 == len(tokens) and len(parts) == 1)
                for i, p in enumerate(parts))
        out.append(lead + word + trail)
        starts = bool(trail) and trail[-1] in ",:;("
        after_minor = not trail and word in _TITLE_MINOR
        prev = core
    return " ".join(out)


def format_uk_phone(raw: str) -> str:
    """
    A UK phone number grouped the usual way, or the input tidied if it is not one.

        "02089852795", "020 89852795"  -> "020 8985 2795"
        "01134960000"                  -> "0113 496 0000"
        "01708797649"                  -> "01708 797649"
        "03001234567"                  -> "0300 123 4567"
        "+44 7700 900123"              -> "07700 900123"
    """
    s = " ".join(str(raw or "").split())
    digits = re.sub(r"[\s().-]", "", s.replace("(0)", ""))
    if digits.startswith("+44"):
        digits = "0" + digits[3:]
    elif digits.startswith("0044"):
        digits = "0" + digits[4:]
    if not re.fullmatch(r"0\d{9,10}", digits):
        return s
    n = digits
    if len(n) == 11:
        if n[1] in "25" or n.startswith("070"):         # 020 7946 0018, 070 personal
            return f"{n[:3]} {n[3:7]} {n[7:]}"
        if n[1] == "1" and "1" in (n[2], n[3]):         # 0113 and 0121 areas
            return f"{n[:4]} {n[4:7]} {n[7:]}"
        if n[1] in "389":                               # 0300, 0800, 0870, 0900
            return f"{n[:4]} {n[4:7]} {n[7:]}"
        return f"{n[:5]} {n[5:]}"                       # 01632 960000, 07700 900123
    if n[1] in "58":                                    # 0800 123456, 0500 123456
        return f"{n[:4]} {n[4:]}"
    return f"{n[:5]} {n[5:]}"                           # 01xxx xxxxx

def _http_json(method: str, url: str, *, source: str, session=None,
               json_body=None, form_body=None, params=None, timeout: int = 60,
               attempts: int = 4):
    """
    GET/POST returning parsed JSON, with exponential backoff. Returns None on a
    404 (a legitimate 'not found' for the endpoints used here). Raises
    RuntimeError naming `source` once the attempts are exhausted.
    """
    sess = session or requests
    delay = 1.0
    last = "no attempt made"
    for i in range(attempts):
        try:
            r = sess.request(method, url, json=json_body, data=form_body,
                             params=params, timeout=timeout)
            if r.status_code == 404:
                return None
            if r.status_code in (429, 500, 502, 503, 504):
                last = f"HTTP {r.status_code}"
            else:
                r.raise_for_status()
                return r.json()
        except Exception as e:                      # network, JSON, 4xx
            last = f"{type(e).__name__}: {e}"
        if i < attempts - 1:
            time.sleep(delay)
            delay *= 2
    raise RuntimeError(
        f"{source}: request failed after {attempts} attempts ({url}): {last}"
    )

# ============================================================================
# NHS ODS bulk extracts - Data Search and Export portal
# ============================================================================
# ODS publishes its bulk organisation extracts (pharmacies, GP practices, ...)
# as headerless, fully quoted, 27-column CSV. The old bulk-zip route under
# files.digital.nhs.uk/assets/ods/current/ now returns 403 for every extract, so
# the Data Search and Export API below is the live source. `report` is the ODS
# extract code: 'edispensary' for pharmacies, 'epraccur' for GP practices.
ODS_EXPORT_URL = "https://www.odsdatasearchandexport.nhs.uk/api/getReport?report={report}"

# The 27-column ODS standard extract layout. Shared by every bulk extract, so
# both epraccur (GP practices) and edispensary (pharmacies) are read with it.
EPRACCUR_HEADER = [
    "OrganisationCode", "Name", "NationalGrouping", "HighLevelHealthGeography",
    "AddressLine1", "AddressLine2", "AddressLine3", "AddressLine4", "AddressLine5",
    "Postcode", "OpenDate", "CloseDate", "StatusCode", "OrganisationSubTypeCode",
    "Commissioner", "JoinProviderDate", "LeftProviderDate", "ContactTelephoneNumber",
    "_n1", "_n2", "_n3", "AmendedRecordIndicator", "_n4",
    "ProviderPurchaser", "_n5", "PrescribingSetting", "_n6",
]
ODS_FIELD_COUNT = len(EPRACCUR_HEADER)      # 27

def _http_bytes(url: str, *, source: str, headers: dict | None = None,
                timeout: int = 180, attempts: int = 4):
    """GET returning the requests.Response, with backoff. Raises naming `source`."""
    delay = 1.0
    last = "no attempt made"
    for i in range(attempts):
        try:
            r = requests.get(url, headers=headers or {}, timeout=timeout)
            if r.status_code in (200, 304):
                return r
            last = f"HTTP {r.status_code}"
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
        if i < attempts - 1:
            time.sleep(delay)
            delay *= 2
    raise RuntimeError(
        f"{source}: download failed after {attempts} attempts ({url}): {last}"
    )

def _validate_ods_extract(raw: bytes, *, source: str, report: str,
                          min_active: int,
                          role_codes: set[str] | None = None,
                          code_prefix: str | None = None,
                          marker: tuple[int, str, int] | None = None,
                          active_by_close_date: bool = False) -> int:
    """
    Check a downloaded ODS extract really is the report we asked for, and return
    its active-row count.

    Every check here exists because the failure it catches is otherwise silent.
    pandas reads a headerless CSV against a fixed name list without complaining
    when the upstream width changes, so a new ODS column would shift every field
    one place and quietly corrupt postcodes. Likewise all ODS reports share this
    layout, so a wrong `report` code returns a perfectly well-formed file of the
    wrong organisations.

    `code_prefix` is the leading character every organisation code should carry,
    where the report has one (pharmacies are all F...; GP practices are spread
    across many letters, so they identify themselves by `marker` instead).
    `marker` is an optional (field index, substring, minimum row count) triple
    naming a value that must appear in the extract for it to be the report we
    asked for.
    """
    try:
        rows = [r for r in csv.reader(io.StringIO(raw.decode("latin-1"), newline="")) if r]
    except csv.Error as e:
        # A CDN error page or a truncated body served as HTTP 200 lands here.
        raise RuntimeError(
            f"{source}: '{report}' extract is not parseable as CSV ({e}); "
            f"first bytes: {raw[:120]!r} ({ODS_EXPORT_URL.format(report=report)})"
        ) from e
    if not rows:
        raise RuntimeError(f"{source}: {report} extract is empty ({ODS_EXPORT_URL.format(report=report)})")

    widths = {len(r) for r in rows}
    if widths != {ODS_FIELD_COUNT}:
        raise RuntimeError(
            f"{source}: {report} extract has {sorted(widths)} fields per row, "
            f"expected {ODS_FIELD_COUNT}. The ODS layout changed - update "
            f"EPRACCUR_HEADER before trusting this file "
            f"({ODS_EXPORT_URL.format(report=report)})"
        )
    if rows[0][0].strip().lower() == "organisationcode":
        raise RuntimeError(
            f"{source}: {report} extract now ships a header row; the pipeline "
            f"reads it headerless ({ODS_EXPORT_URL.format(report=report)})"
        )

    if code_prefix:
        codes = [r[0] for r in rows if r[0]]
        pct_prefix = sum(c.startswith(code_prefix) for c in codes) / max(len(codes), 1)
        if pct_prefix < 0.90:
            raise RuntimeError(
                f"{source}: only {pct_prefix:.0%} of organisation codes in the "
                f"'{report}' extract start with '{code_prefix}' - this looks like a "
                f"different ODS report ({ODS_EXPORT_URL.format(report=report)})"
            )
    if marker:
        idx, needle, min_count = marker
        hits = sum(needle in r[idx] for r in rows if len(r) > idx)
        if hits < min_count:
            raise RuntimeError(
                f"{source}: only {hits:,} rows of the '{report}' extract carry "
                f"'{needle}' in field {idx} (expected at least {min_count:,}) - "
                f"this looks like a different ODS report "
                f"({ODS_EXPORT_URL.format(report=report)})"
            )
    # Not every extract populates the role column: egdpprac leaves it blank on
    # most rows, so dentists identify themselves by code prefix instead.
    if role_codes:
        pct_role = sum(r[13] in role_codes for r in rows) / len(rows)
        if pct_role < 0.90:
            raise RuntimeError(
                f"{source}: only {pct_role:.0%} of rows in the '{report}' extract "
                f"carry role codes {sorted(role_codes)} - this looks like a "
                f"different ODS report ({ODS_EXPORT_URL.format(report=report)})"
            )

    if active_by_close_date:
        # ets leaves the status column blank on every one of its 46,000 rows and
        # marks a closed site with a close date instead. Counting r[12] here
        # finds zero active rows in a perfectly healthy extract, which reads as
        # a truncated download and takes the source down.
        active = sum(not r[11].strip() for r in rows)
    else:
        active = sum(r[12] in ("A", "ACTIVE") for r in rows)
    if active < min_active:
        raise RuntimeError(
            f"{source}: '{report}' extract has only {active:,} active rows "
            f"(expected at least {min_active:,}) - looks truncated "
            f"({ODS_EXPORT_URL.format(report=report)})"
        )
    return active

def fetch_ods_report(report: str, cache: Path, *, source: str,
                     min_active: int,
                     role_codes: set[str] | None = None,
                     code_prefix: str | None = None,
                     marker: tuple[int, str, int] | None = None,
                     active_by_close_date: bool = False) -> Path:
    """
    Refresh a cached ODS extract from the Data Search and Export API.

    The response carries an ETag but no Last-Modified, and the URL has no
    version in it, so the ETag is the only freshness signal available. It is
    kept in a sidecar next to the cache and replayed as If-None-Match, which
    makes a rerun with no upstream change a single cheap 304. A download that
    fails or fails validation leaves an existing cache in place rather than
    taking the whole source down with it.
    """
    url = ODS_EXPORT_URL.format(report=report)
    etag_path = cache.with_name(cache.name + ".etag")
    cached_etag = ""
    if cache.exists() and etag_path.exists():
        try:
            cached_etag = etag_path.read_text(encoding="utf-8").strip()
        except OSError:
            cached_etag = ""

    headers = {"If-None-Match": cached_etag} if cached_etag else {}
    try:
        r = _http_bytes(url, source=source, headers=headers)
        if r.status_code == 304:
            ok(f"{report}: cached copy still current (ETag {cached_etag})")
            return cache
        active = _validate_ods_extract(
            r.content, source=source, report=report, code_prefix=code_prefix,
            role_codes=role_codes, min_active=min_active, marker=marker,
            active_by_close_date=active_by_close_date,
        )
        write_bytes_atomic(cache, r.content)
        etag = r.headers.get("ETag", "")
        if etag:
            write_atomic(etag_path, etag)
        elif etag_path.exists():
            etag_path.unlink()          # no ETag upstream: don't replay a stale one
        ok(f"{report}: downloaded {len(r.content)/1e6:.2f} MB, "
           f"{active:,} active rows")
    except Exception as e:
        if cache.exists():
            warn(f"{source}: refresh failed ({e}); using the cached "
                 f"{cache.name} from {datetime.fromtimestamp(cache.stat().st_mtime, timezone.utc):%Y-%m-%d}")
            return cache
        raise RuntimeError(
            f"{source}: could not download the '{report}' ODS extract and no "
            f"cached copy exists at {cache}. Source: {url}"
        ) from e
    return cache

# ============================================================================
# Postcode lookup - postcodes.io bulk API
# ============================================================================
# Replaces the old ~250 MB ONSPD manual download. postcodes.io is an open API
# over the same ONS Postcode Directory, so the fields line up 1:1 with the
# columns the ONSPD path used to read:
#     codes.lsoa21          -> LSOA21CD
#     codes.admin_district  -> LAD25CD
#     codes.admin_ward      -> WD25CD
# Every postcode handed in is resolved. There used to be a hand-maintained
# allowlist of postcode areas, inherited from the ONSPD files the pipeline once
# read. At 33 local authorities that list is a silent-drop bug waiting to
# happen: miss one area and every facility in it disappears with no error. The
# LAD filter downstream does the real scoping, and results are cached per
# postcode so the extra lookups are paid once.
POSTCODES_IO_BULK       = "https://api.postcodes.io/postcodes"
POSTCODES_IO_TERMINATED = "https://api.postcodes.io/terminated_postcodes/{pc}"
PC_BATCH             = 100    # postcodes.io rejects bulk requests above this
PC_CACHE_PATH        = CACHE_DIR / "postcodes" / "postcodes_io.json"
PC_NEGATIVE_TTL_DAYS = 90     # re-check misses: new postcodes appear quarterly
PC_MIN_RESOLVED      = 0.5    # below this share resolved, assume upstream broke
PC_MASS_FAILURE_MIN  = 20     # ...but only judge the share on a decent sample

_PC_CACHE: dict | None = None
_PC_NEG_STAMP: str = ""

def _days_since(iso_date: str) -> float:
    try:
        d = datetime.fromisoformat(iso_date).date()
    except (TypeError, ValueError):
        return float("inf")
    return (datetime.now(timezone.utc).date() - d).days

def _pc_cache() -> dict:
    """Postcode cache, keyed by space-stripped postcode. None value = a miss."""
    global _PC_CACHE, _PC_NEG_STAMP
    if _PC_CACHE is not None:
        return _PC_CACHE
    entries: dict = {}
    stamp = ""
    if PC_CACHE_PATH.exists():
        try:
            blob = json.loads(PC_CACHE_PATH.read_text(encoding="utf-8"))
            entries = blob.get("postcodes") or {}
            stamp = blob.get("negatives_checked") or ""
        except (OSError, ValueError) as e:
            warn(f"postcodes.io: cache unreadable ({e}); starting a fresh one")
            entries = {}
            stamp = ""
    # Cached misses are dropped periodically. A postcode created since the last
    # ONS release misses once and then starts resolving; a permanent cache entry
    # would hide it forever.
    if _days_since(stamp) > PC_NEGATIVE_TTL_DAYS:
        stale = sum(1 for v in entries.values() if not v)
        if stale:
            info(f"postcodes.io: re-checking {stale:,} cached misses "
                 f"(older than {PC_NEGATIVE_TTL_DAYS} days)")
        entries = {k: v for k, v in entries.items() if v}
        stamp = datetime.now(timezone.utc).date().isoformat()
    _PC_CACHE, _PC_NEG_STAMP = entries, stamp
    return _PC_CACHE

def _pc_cache_save() -> None:
    if _PC_CACHE is None:
        return
    write_json_atomic(PC_CACHE_PATH, {
        "negatives_checked": _PC_NEG_STAMP,
        "postcodes": _PC_CACHE,
    })

def _pc_record(res: dict) -> dict | None:
    """One postcodes.io result -> cache record, or None if it is unusable."""
    codes = res.get("codes") or {}
    lat, lng = res.get("latitude"), res.get("longitude")
    if lat is None or lng is None:
        return None
    return {
        "lat": float(lat),
        "lng": float(lng),
        "lsoa": codes.get("lsoa21") or codes.get("lsoa") or "",
        "lad":  codes.get("admin_district") or "",
        "wd":   codes.get("admin_ward") or "",
        "src":  "live",
    }

def _codes_from_point(lat: float, lng: float) -> tuple[str, str, str] | None:
    """(LSOA21CD, LAD25CD, WD25CD) by point-in-polygon on the local boundaries."""
    try:
        wards_idx = load_boundary_index("wards")
        lsoa_idx  = load_boundary_index("lsoa")
    except (FileNotFoundError, ImportError):
        return None
    wp = wards_idx.find(lng, lat)
    if not wp:
        return None                       # outside the mapped footprint
    lp = lsoa_idx.find(lng, lat) or {}
    return (
        lp.get("LSOA21CD") or lp.get("code") or "",
        wp.get("LAD25CD") or wp.get("LAD24CD") or "",
        wp.get("WD25CD") or wp.get("WD24CD") or "",
    )

def _lookup_terminated(pc: str, *, source: str, session) -> dict | None:
    """
    Retry one postcode against the terminated-postcodes endpoint. That endpoint
    returns a grid reference but no area codes, so LSOA/ward/LAD come from a
    point-in-polygon against the local boundary GeoJSONs.
    """
    payload = _http_json("GET", POSTCODES_IO_TERMINATED.format(pc=pc),
                         source=source, session=session, timeout=30)
    res = (payload or {}).get("result") or {}
    lat, lng = res.get("latitude"), res.get("longitude")
    if lat is None or lng is None:
        return None
    codes = _codes_from_point(float(lat), float(lng))
    if codes is None:
        return None
    lsoa, lad, wd = codes
    return {"lat": float(lat), "lng": float(lng),
            "lsoa": lsoa, "lad": lad, "wd": wd, "src": "terminated"}

def lookup_postcodes(postcodes, *, source: str,
                     owners: dict | None = None) -> dict:
    """
    Resolve postcodes to {postcode_no_spaces: (lat, lng, LSOA21CD, LAD25CD,
    WD25CD)} via the postcodes.io bulk API.

    Results are cached in .cache/postcodes/ keyed by postcode, so reruns are
    free. Postcodes the bulk endpoint does not know are retried individually
    against the terminated-postcodes endpoint. `owners` maps a space-stripped
    postcode to a human label (practice, pharmacy or charity name) so anything
    still unresolved can be logged against the record it belongs to.
    """
    wanted = sorted({normalise_postcode(p) for p in postcodes})
    wanted = [p for p in wanted if p]
    cache = _pc_cache()
    todo = [p for p in wanted if p not in cache]

    if todo:
        info(f"{source}: resolving {len(todo):,} postcodes via postcodes.io "
             f"({len(wanted) - len(todo):,} already cached)")
        sess = requests.Session()
        unmatched: list[str] = []
        for i in range(0, len(todo), PC_BATCH):
            batch = todo[i:i + PC_BATCH]
            payload = _http_json("POST", POSTCODES_IO_BULK, source=source,
                                 session=sess, json_body={"postcodes": batch})
            results = (payload or {}).get("result")
            if not isinstance(results, list) or len(results) != len(batch):
                raise RuntimeError(
                    f"{source}: postcodes.io returned "
                    f"{len(results) if isinstance(results, list) else 'no'} "
                    f"results for a batch of {len(batch)}. API shape changed? "
                    f"({POSTCODES_IO_BULK})"
                )
            for item in results:
                key = normalise_postcode(item.get("query", ""))
                rec = _pc_record(item.get("result") or {}) if item.get("result") else None
                if rec is None:
                    unmatched.append(key)
                else:
                    cache[key] = rec
            time.sleep(0.1)     # be polite to a free public API

        if unmatched:
            info(f"{source}: retrying {len(unmatched):,} unmatched postcodes "
                 "against the terminated-postcodes endpoint")
            revived = 0
            for pc in unmatched:
                rec = _lookup_terminated(pc, source=source, session=sess)
                cache[pc] = rec
                revived += bool(rec)
            if revived:
                ok(f"{source}: {revived:,} of {len(unmatched):,} recovered as "
                   "terminated postcodes (placed by boundary lookup)")

    resolved = sum(1 for p in wanted if cache.get(p))
    # A handful of unresolved postcodes is normal (bad data in the charity
    # register, mostly). Nearly all of them failing is not: postcodes.io answers
    # an unknown postcode with a 200 and a null result, so a bad upstream
    # response looks exactly like "none of these places exist" and would
    # otherwise sail through as an empty result set. Raise before saving the
    # cache, so a bad response is not persisted as negatives and replayed for
    # the whole 90-day TTL.
    if wanted and (resolved == 0 or
                   (len(wanted) >= PC_MASS_FAILURE_MIN
                    and resolved < len(wanted) * PC_MIN_RESOLVED)):
        raise RuntimeError(
            f"{source}: only {resolved:,} of {len(wanted):,} postcodes resolved "
            f"via postcodes.io. Treating this as an upstream failure rather "
            f"than an empty result ({POSTCODES_IO_BULK})"
        )
    if todo:
        _pc_cache_save()

    missing = [p for p in wanted if not cache.get(p)]
    if missing:
        shown = [f"{p} ({owners.get(p, 'unknown')})" if owners else p
                 for p in missing[:10]]
        warn(f"{source}: {len(missing):,} postcodes unresolved: "
             + "; ".join(shown) + (" ..." if len(missing) > 10 else ""))

    out = {}
    for p in wanted:
        r = cache.get(p)
        if r:
            out[p] = (r["lat"], r["lng"], r["lsoa"], r["lad"], r["wd"])
    return out

# ============================================================================
# LSOA -> ward mapping - ONS best-fit lookup (Open Geography Portal)
# ============================================================================
# The official LSOA (2021) -> Electoral Ward (2025) -> LAD (2025) best-fit
# lookup, queried from the ONS ArcGIS feature service. This replaces voting on
# ONSPD postcode centroids and matches the lookup ward_data.json was rebuilt
# against (see scripts/reconfigure_to_ons_wd24_lookup.py, the 2024 vintage).
ONS_LOOKUP_LAYER = "LSOA21_WD25_LAD25_EW_LU_v2"
ONS_LOOKUP_URL = (
    "https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services/"
    f"{ONS_LOOKUP_LAYER}/FeatureServer/0/query"
)
ONS_LOOKUP_CACHE = CACHE_DIR / "ons_lookup" / f"{ONS_LOOKUP_LAYER}_{SCOPE_KEY}.json"
ONS_LOOKUP_PAGE  = 1000        # the service's maxRecordCount

@lru_cache(maxsize=1)
def get_lsoa_ward_lookup() -> dict:
    """{LSOA21CD: (WD25CD, LAD25CD)} for the in-scope boroughs."""
    if ONS_LOOKUP_CACHE.exists():
        try:
            blob = json.loads(ONS_LOOKUP_CACHE.read_text(encoding="utf-8"))
            if blob:
                ok(f"ONS lookup: {len(blob):,} LSOAs (cached)")
                return {k: tuple(v) for k, v in blob.items()}
        except (OSError, ValueError) as e:
            warn(f"ONS lookup: cache unreadable ({e}); refetching")

    info(f"ONS lookup: fetching {ONS_LOOKUP_LAYER} for {len(SCOPE_LADS)} {LA_WORD}s")
    where = "LAD25CD IN (" + ",".join(f"'{c}'" for c in sorted(SCOPE_LADS)) + ")"
    lookup: dict[str, tuple[str, str]] = {}
    offset = 0
    while True:
        payload = _http_json("GET", ONS_LOOKUP_URL, source="ONS LSOA->ward lookup",
                             params={
                                 "where": where,
                                 "outFields": "LSOA21CD,WD25CD,LAD25CD",
                                 "returnGeometry": "false",
                                 "orderByFields": "LSOA21CD",
                                 "resultOffset": offset,
                                 "resultRecordCount": ONS_LOOKUP_PAGE,
                                 "f": "json",
                             }, timeout=120)
        if payload is None or "features" not in payload:
            raise RuntimeError(
                f"ONS LSOA->ward lookup: unexpected response from "
                f"{ONS_LOOKUP_LAYER} (no 'features' key). "
                f"Check the layer still exists: {ONS_LOOKUP_URL}"
            )
        feats = payload["features"]
        for f in feats:
            a = f.get("attributes") or {}
            code = a.get("LSOA21CD")
            if code:
                lookup[code] = (a.get("WD25CD") or "", a.get("LAD25CD") or "")
        if not feats or not payload.get("exceededTransferLimit"):
            break
        offset += len(feats)

    if not lookup:
        raise RuntimeError(
            f"ONS LSOA->ward lookup: {ONS_LOOKUP_LAYER} returned no rows for "
            f"LAD25CD in {sorted(SCOPE_LADS)}. Has the ward vintage moved on?"
        )
    # Every authority in the scope must come back. A mistyped or retired code
    # in scopes/*.json would otherwise build a map with a hole where that
    # authority should be, and nothing downstream would say so.
    missing = sorted(SCOPE_LADS - {lad for _wd, lad in lookup.values()})
    if missing:
        raise RuntimeError(
            f"ONS LSOA->ward lookup: no LSOAs for {', '.join(f'{LAD_NAMES[c]} ({c})' for c in missing)} "
            f"in scope {SCOPE_ID}. Check scopes/{SCOPE_ID}.json against the LAD25 codes."
        )
    write_json_atomic(ONS_LOOKUP_CACHE, {k: list(v) for k, v in lookup.items()})
    ok(f"ONS lookup: {len(lookup):,} LSOAs across {len(SCOPE_LADS)} authorities in {SCOPE_NAME}")
    return lookup

def get_lsoa_to_ward() -> dict:
    """{LSOA21CD: WD25CD}: the mapping used by the ward-level aggregations."""
    return {lc: wd for lc, (wd, _lad) in get_lsoa_ward_lookup().items()}


# The output area (2021) -> ward (May 2025) best-fit lookup. Output areas are
# about a fifth the size of LSOAs, so a ward built from them follows the real
# ward line far more closely: about four in ten LSOAs cross a ward boundary.
# Only the census is published by output area, so only census ward figures use
# it; everything else stays on the LSOA lookup above.
ONS_SERVICES = "https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services"
OA_WARD_LAYERS = ["OA21_WD25_LAD25_EW_LU_v2", "OA21_WD25_LAD25_EW_LU",
                  "OA21_WD25_LAD25_EW_LU_v3"]


def _oa_ward_layer() -> str | None:
    """The ONS OA21 -> WD25 lookup service, by known name or from the list."""
    for name in OA_WARD_LAYERS:
        try:
            r = requests.get(f"{ONS_SERVICES}/{name}/FeatureServer/0?f=json", timeout=60)
            if r.ok and "fields" in r.json():
                return name
        except Exception:
            continue
    try:
        r = requests.get(f"{ONS_SERVICES}?f=json", timeout=120)
        names = [x["name"] for x in r.json().get("services", [])]
        cands = sorted(n for n in names if re.match(r"(?i)^OA21_WD25_LAD25.*_LU", n))
        return cands[-1] if cands else None
    except Exception as e:
        warn(f"OA->ward lookup: could not list ONS services: {e}")
        return None


@lru_cache(maxsize=1)
def get_oa_to_ward() -> dict:
    """{OA21CD: WD25CD} for the in-scope authorities, or {} if ONS does not
    answer, in which case census wards are built from LSOAs as before."""
    cache = CACHE_DIR / "ons_lookup" / f"OA21_WD25_{SCOPE_KEY}.json"
    if cache.exists():
        try:
            blob = json.loads(cache.read_text(encoding="utf-8"))
            if blob:
                return blob
        except (OSError, ValueError):
            pass
    layer = _oa_ward_layer()
    if not layer:
        warn("OA->ward lookup: no ONS service found; census wards use the LSOA lookup")
        return {}
    url = f"{ONS_SERVICES}/{layer}/FeatureServer/0/query"
    where = "LAD25CD IN (" + ",".join(f"'{c}'" for c in sorted(SCOPE_LADS)) + ")"
    out: dict = {}
    offset = 0
    try:
        while True:
            payload = _http_json("GET", url, source="ONS OA->ward lookup", params={
                "where": where, "outFields": "OA21CD,WD25CD",
                "returnGeometry": "false", "orderByFields": "OA21CD",
                "resultOffset": offset, "resultRecordCount": 2000, "f": "json",
            }, timeout=120)
            if payload is None or "features" not in payload:
                raise RuntimeError(f"unexpected response from {layer}")
            feats = payload["features"]
            for f in feats:
                a = f.get("attributes") or {}
                if a.get("OA21CD") and a.get("WD25CD"):
                    out[a["OA21CD"]] = a["WD25CD"]
            if not feats or not payload.get("exceededTransferLimit"):
                break
            offset += len(feats)
    except Exception as e:
        warn(f"OA->ward lookup ({layer}): {e}; census wards use the LSOA lookup")
        return {}
    # A ward set that does not match the LSOA lookup's means the two products
    # are on different vintages; mixing them would split wards.
    wards = set(get_lsoa_to_ward().values())
    if not out or len(set(out.values()) - wards) > len(wards) * 0.01:
        warn(f"OA->ward lookup ({layer}): {len(out):,} rows, wards do not match "
             f"the LSOA lookup; census wards use the LSOA lookup")
        return {}
    write_json_atomic(cache, out)
    ok(f"ONS lookup: {len(out):,} output areas -> {len(set(out.values())):,} wards ({layer})")
    return out

# ============================================================================
# Boundary / point-in-polygon helpers (lazy, only load when needed)
# ============================================================================
@lru_cache(maxsize=4)
def load_boundary_index(kind: str):
    """kind in {'lsoa', 'wards', 'boroughs'}. Returns a PolygonIndex."""
    from shapely.geometry import Point, shape
    from shapely.strtree import STRtree

    path = DATA_DIR / "boundaries" / f"{kind}.geojson"
    if not path.exists():
        raise FileNotFoundError(
            f"Boundary file not found: {path}\n"
            "Run download_boundaries() or place the GeoJSON manually."
        )
    with open(path, encoding="utf-8") as f:
        fc = json.load(f)
    feats = fc["features"]
    geoms = [shape(f["geometry"]) for f in feats]
    props = [f["properties"] for f in feats]
    tree  = STRtree(geoms)

    def find(lng: float, lat: float):
        pt = Point(lng, lat)
        for idx in tree.query(pt):
            if geoms[idx].contains(pt):
                return props[idx]
        return None

    def features_iter():
        from shapely.geometry import mapping
        return [
            {"type": "Feature", "geometry": mapping(g), "properties": p}
            for g, p in zip(geoms, props)
        ]

    # Return a lightweight object so callers have .find and .features
    class _Idx:
        pass
    idx_obj = _Idx()
    idx_obj.find = find
    idx_obj.features = features_iter()
    return idx_obj

def bng_to_wgs84(e: float, n: float) -> tuple[float, float]:
    """British National Grid easting/northing -> (lat, lng) WGS84."""
    from pyproj import Transformer
    global _BNG_TRANSFORMER
    try:
        t = _BNG_TRANSFORMER
    except NameError:
        t = Transformer.from_crs(27700, 4326, always_xy=True)
        _BNG_TRANSFORMER = t
    lng, lat = t.transform(e, n)
    return lat, lng

# ============================================================================
# SOURCE 1: GP practices  (NHS ODS EPRACCUR)
# ============================================================================
# EPRACCUR files more than practices under the GP practice role: special
# allocation schemes (for patients removed from a practice after violence or
# aggression), PCN extended access hubs, GP federation offices, a Covid
# medicines unit and single clinics. None is a practice a resident can join,
# and listed as "the nearest GP practice" they misdirect people and inflate
# the counts. A name that says so is left out, and so is any code in
# data/curated/service_sites.csv marked drop. The practices for people who are
# homeless are real practices and stay, labelled by the same file.
GP_NOT_A_PRACTICE = re.compile(
    r"special allocation|\bSAS\b|\bPCN\b.*\bhub\b|\bEA hub\b|extended access|"
    r"federation|federated|covid|care home service|delivery unit|"
    r"\bgynaecology\b|\bdermatology\b|\blarc clinic\b",
    re.I)
SERVICE_SITES = REPO_ROOT / "data" / "curated" / "service_sites.csv"

def load_service_sites(path=None) -> dict:
    """{(layer, code): {"action": "drop"|"label", "label": str}} from the
    checked list of GP practices and pharmacies that are not what their
    register role says."""
    path = path or SERVICE_SITES
    out = {}
    if not Path(path).exists():
        return out
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            out[(r["layer"].strip(), r["code"].strip().upper())] = {
                "action": r["action"].strip(), "label": r["label"].strip()}
    return out

def gp_practice_verdict(code: str, name: str, sites: dict) -> tuple:
    """("keep", label) or ("drop", reason) for one EPRACCUR record."""
    hit = sites.get(("gp", str(code).upper()))
    if hit and hit["action"] == "label":
        return ("keep", hit["label"])
    if hit and hit["action"] == "drop":
        return ("drop", hit["label"] or "checked list")
    if GP_NOT_A_PRACTICE.search(str(name)):
        return ("drop", "name says it is not a practice")
    return ("keep", "")

def run_gp_practices() -> pd.DataFrame:
    rule("GP practices (NHS ODS EPRACCUR)")
    cache_dir = CACHE_DIR / "gp_practices"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache = cache_dir / "epraccur.csv"
    legacy_zip = cache_dir / "epraccur.zip"

    # GP practices are spread across many organisation-code letters, so unlike
    # pharmacies they cannot be identified by a code prefix. Subtype B/Z plus a
    # floor on RO76 (the ODS role code for a GP practice) pins the report down:
    # the neighbouring extracts either carry no subtype at all (ebranchs,
    # epharmacyhq) or use different subtype values entirely (egpcur).
    try:
        fetch_ods_report(
            "epraccur", cache, source="gp_practices",
            role_codes={"B", "Z"}, min_active=10_000,
            marker=(EPRACCUR_HEADER.index("PrescribingSetting"), "RO76", 5_000),
        )
        src = cache
    except Exception:
        # An install predating this change may still hold the retired zip.
        # Prefer stale-but-real data over failing the whole source.
        if not legacy_zip.exists():
            raise
        warn(f"gp_practices: falling back to the legacy {legacy_zip.name}")
        src = legacy_zip

    if src.suffix == ".zip":
        with zipfile.ZipFile(src) as z:
            with z.open("epraccur.csv") as f:
                df = pd.read_csv(
                    io.TextIOWrapper(f, encoding="latin-1"),
                    header=None, names=EPRACCUR_HEADER,
                    dtype=str, keep_default_na=False,
                )
    else:
        df = pd.read_csv(
            src, header=None, names=EPRACCUR_HEADER,
            dtype=str, keep_default_na=False, encoding="latin-1",
        )

    # Active only. Then filter to actual GP practices (not branches/clinics).
    # Handles three EPRACCUR shipping formats:
    #   Legacy:  StatusCode=A, PrescribingSetting=4 (numeric)
    #   Modern:  StatusCode=ACTIVE, SubType=B + Role codes (RO76 = GP practice)
    # B + RO76 is the canonical NHS ODS definition of a main GP practice.
    df = df[df["StatusCode"].isin(["A", "ACTIVE"])]
    setting = df["PrescribingSetting"].astype(str)
    if setting.str.fullmatch(r"\d+").any():
        df = df[df["PrescribingSetting"] == "4"]
    elif setting.str.contains("RO", na=False).any():
        df = df[
            (df["OrganisationSubTypeCode"] == "B")
            & (setting.str.contains("RO76", na=False))
        ]

    owners = {normalise_postcode(r["Postcode"]): title_case(r["Name"], initials=True)
              for _, r in df.iterrows() if normalise_postcode(r["Postcode"])}
    lookup = lookup_postcodes(owners.keys(), source="gp_practices", owners=owners)

    sites = load_service_sites()
    rows = []
    dropped = 0
    national = []          # every practice kept, in or out of scope (service_access)
    for _, r in df.iterrows():
        pc = normalise_postcode(r["Postcode"])
        hit = lookup.get(pc)
        if not hit:
            continue
        lat, lng, lsoa, lad, wd = hit
        if lad not in SCOPE_LADS:
            if gp_practice_verdict(r["OrganisationCode"], r["Name"], sites)[0] == "keep":
                national.append((r["OrganisationCode"], lat, lng))
            continue
        verdict, label = gp_practice_verdict(r["OrganisationCode"], r["Name"], sites)
        if verdict == "drop":
            dropped += 1
            continue
        national.append((r["OrganisationCode"], lat, lng))
        rows.append({
            "code": r["OrganisationCode"],
            "name": title_case(r["Name"], initials=True),
            "kind": label,
            "addr": ", ".join(filter(None, [
                title_case(r["AddressLine1"]),
                title_case(r["AddressLine2"]),
                title_case(r["AddressLine3"]),
            ])),
            "postcode": r["Postcode"],
            "tel": format_uk_phone(r["ContactTelephoneNumber"]),
            "lat": lat, "lng": lng,
            "LSOA21CD": lsoa, "WD25CD": wd, "LAD25CD": lad,
            "lad": LAD_NAMES.get(lad, ""),
        })
    out = pd.DataFrame(rows)
    save_national_sites("gp", national)
    out_path = DATA_DIR / "healthcare" / "gp_practices.parquet"
    out = write_parquet_guarded(out_path, out, source="gp_practices")
    ok(f"gp_practices: {len(out):,} rows -> {out_path.relative_to(REPO_ROOT)} "
       f"({dropped:,} schemes, hubs and offices left out)")
    return out


# ============================================================================
# SOURCE 2: Pharmacies  (NHS ODS edispensary)
# ============================================================================
# The dispensary extract is every contractor, and two kinds have no counter a
# resident can walk up to: dispensing appliance contractors (role RO94, which
# supply stoma and continence products and do not dispense medicines), and
# distance-selling pharmacies, which NHS rules bar from serving people in
# person. The register does not mark the second kind, so it is caught by name
# here and by the checked list (data/curated/service_sites.csv). The NHSBSA
# consolidated pharmaceutical list does mark them, and joining it is the
# better rule once the pipeline fetches it.
PHARM_APPLIANCE = re.compile(
    r"fittleworth|bullen|stoma|appliance|salts medilink|respond healthcare|"
    r"clinidirect|new health supplies|amcare", re.I)
PHARM_ONLINE = re.compile(
    r"online|www\.|\.com\b|\.co\.uk\b|\bdigital\b|chemist ?2 ?home|clickrx|"
    r"mydirectpharmacy|medicine porter|netscripts|mail my meds|\bdelivery\b", re.I)
PHARM_AIRSIDE = re.compile(r"\bairside\b", re.I)

def pharmacy_verdict(code: str, name: str, address: str, role: str, sites: dict) -> str:
    """"" to keep a dispensary record, or the reason it is left out."""
    hit = sites.get(("pharmacy", str(code).upper()))
    if hit and hit["action"] == "drop":
        return hit["label"] or "checked list"
    if str(role).strip() == "RO94":
        return "dispensing appliance contractor"
    if PHARM_APPLIANCE.search(str(name)):
        return "appliance contractor"
    if PHARM_ONLINE.search(str(name)):
        return "online or delivery pharmacy"
    if PHARM_AIRSIDE.search(str(address)):
        return "airside, past security"
    return ""

def filter_pharmacies(df: pd.DataFrame, sites: dict) -> pd.DataFrame:
    """The dispensary records a resident can walk into, one per address."""
    def addr(r):
        return " ".join(str(r.get(f"AddressLine{i}", "")) for i in range(1, 5))
    keep = df.apply(lambda r: pharmacy_verdict(
        r.get("OrganisationCode", ""), r.get("Name", ""), addr(r),
        r.get("OrganisationSubTypeCode", ""), sites) == "", axis=1)
    df = df[keep] if len(df) else df
    if not len(df):
        return df
    # Two active codes at one address are one pharmacy whose code changed
    # hands without the old one being closed: keep the newer.
    line1 = df["AddressLine1"].str.upper().str.replace(r"[^A-Z0-9]", "", regex=True)
    key = (line1 + "|" + df["Postcode"].map(normalise_postcode)).where(
        line1 != "", "code|" + df["OrganisationCode"])
    df = df.assign(_k=key, _o=df["OpenDate"].astype(str))
    df = df.sort_values("_o").drop_duplicates("_k", keep="last")
    return df.drop(columns=["_k", "_o"])

def run_pharmacies() -> pd.DataFrame:
    rule("Pharmacies (NHS ODS edispensary)")
    cache_dir = CACHE_DIR / "pharmacies"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache = cache_dir / "edispensary.csv"

    # Every pharmacy in the extract is an F-prefixed ODS code with a pharmacy
    # role code (RO182 dispensing contractor, RO94 dispensing appliance
    # contractor). GP practices, the other extract on this endpoint, are
    # Y/M/A-prefixed with role codes B/Z - so these two checks catch a wrong
    # report code, which would otherwise parse perfectly.
    fetch_ods_report(
        "edispensary", cache, source="pharmacies",
        code_prefix="F", role_codes={"RO182", "RO94"},
        min_active=8_000,          # ~11.3k active today; a floor, not a target
    )

    df = pd.read_csv(
        cache, header=None, names=EPRACCUR_HEADER,
        dtype=str, keep_default_na=False, encoding="latin-1",
    )
    df = df[df["StatusCode"].isin(["A", "ACTIVE"])]
    n_active = len(df)
    df = filter_pharmacies(df, load_service_sites())
    ok(f"pharmacies: {n_active - len(df):,} of {n_active:,} active records left out "
       f"(appliance contractors, online or delivery only, airside, duplicates)")

    owners = {normalise_postcode(r.get("Postcode", "")): title_case(r.get("Name"), initials=True)
              for _, r in df.iterrows() if normalise_postcode(r.get("Postcode", ""))}
    lookup = lookup_postcodes(owners.keys(), source="pharmacies", owners=owners)

    rows = []
    national = []          # every pharmacy kept, in or out of scope (service_access)
    for _, r in df.iterrows():
        pc = normalise_postcode(r.get("Postcode", ""))
        hit = lookup.get(pc)
        if not hit:
            continue
        lat, lng, lsoa, lad, wd = hit
        national.append((r.get("OrganisationCode", ""), lat, lng))
        if lad not in SCOPE_LADS:
            continue
        rows.append({
            "code": r.get("OrganisationCode", ""),
            "name": title_case(r.get("Name"), initials=True),
            "addr": ", ".join(filter(None, [
                title_case(r.get("AddressLine1")),
                title_case(r.get("AddressLine2")),
                title_case(r.get("AddressLine4")),
            ])),
            "postcode": r.get("Postcode", ""),
            "tel": format_uk_phone(r.get("ContactTelephoneNumber", "")),
            "lat": lat, "lng": lng,
            "LSOA21CD": lsoa, "WD25CD": wd, "LAD25CD": lad,
        })
    out = pd.DataFrame(rows)
    save_national_sites("pharmacy", national)
    out_path = DATA_DIR / "healthcare" / "pharmacies.parquet"
    out = write_parquet_guarded(out_path, out, source="pharmacies")
    ok(f"pharmacies: {len(out):,} rows -> {out_path.relative_to(REPO_ROOT)}")
    return out

# ============================================================================
# SOURCE 0: Boundaries  (ONS Open Geography Portal)
# ============================================================================
# data/boundaries/*.geojson used to be placed by hand with no generator, which
# made the footprint impossible to change: the ward and LSOA shapes silently
# defined the map's scope regardless of what the borough list said. These are
# now fetched for whatever SCOPE_LADS contains.
#
# Layer names are the short aliases. The portal also lists longer display names
# with parentheses, and those do not resolve as URL segments.
ARCGIS_BASE = ("https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/"
               "services/{layer}/FeatureServer/0/query")
BOUNDARY_LAYERS = {
    # All three are BGC: generalised to 20m and clipped to the coastline.
    #
    # Keeping them at the same generalisation is the point, not a detail. LSOAs
    # were on BSC, which is 200m, so they were drawn ten times coarser than the
    # wards they nest inside: about 8 vertices per LSOA against 32. Shared
    # borders had been simplified independently at different tolerances, so
    # they no longer coincided and LSOA edges visibly cut across ward
    # boundaries. Any future change here should move all three together.
    "wards":    ("WD_MAY_2025_UK_BGC_V2",  "WD25CD,WD25NM,LAD25CD,LAD25NM"),
    "boroughs": ("LAD_MAY_2025_UK_BGC_V2", "LAD25CD,LAD25NM"),
    # No LAD column on the LSOA layer, so it is filtered by code instead.
    "lsoa":     ("Lower_layer_Super_Output_Areas_December_2021_Boundaries_EW_BGC_V5",
                 "LSOA21CD,LSOA21NM"),
    # Same 2021 census vintage and the same BGC generalisation as the LSOAs it
    # is built from, for the reason given above: MSOA edges are LSOA edges, and
    # generalising them at different tolerances makes the shared borders stop
    # coinciding. No LAD column here either, so it is filtered by code.
    "msoa":     ("Middle_layer_Super_Output_Areas_December_2021_Boundaries_EW_BGC_V3",
                 "MSOA21CD,MSOA21NM"),
}
ARCGIS_PAGE = 1000          # under the service's 2000 maxRecordCount

def fetch_arcgis_geojson(layer: str, where: str, out_fields: str, *,
                         source: str) -> dict:
    """Page an ArcGIS FeatureServer layer into one GeoJSON FeatureCollection."""
    feats: list = []
    offset = 0
    while True:
        payload = _http_json("POST", ARCGIS_BASE.format(layer=layer), source=source,
                             form_body={
                                 "where": where, "outFields": out_fields,
                                 "returnGeometry": "true",
                                 # WGS84 throughout. The old committed boroughs
                                 # file was British National Grid while wards and
                                 # LSOAs were WGS84, which is why the crime code
                                 # carried a conversion step.
                                 "outSR": 4326,
                                 "f": "geojson",
                                 "resultOffset": offset,
                                 "resultRecordCount": ARCGIS_PAGE,
                                 "orderByFields": out_fields.split(",")[0],
                             }, timeout=300)
        if payload is None or "features" not in payload:
            raise RuntimeError(
                f"{source}: unexpected response from {layer} (no 'features'). "
                f"Has the layer been renamed? {ARCGIS_BASE.format(layer=layer)}"
            )
        page = payload["features"]
        feats.extend(page)
        if len(page) < ARCGIS_PAGE:
            break
        offset += len(page)
    return {"type": "FeatureCollection", "features": feats}

def run_boundaries() -> pd.DataFrame:
    rule("Boundaries (ONS Open Geography Portal)")
    out_dir = DATA_DIR / "boundaries"
    out_dir.mkdir(parents=True, exist_ok=True)
    lad_in = "LAD25CD IN (" + ",".join(f"'{c}'" for c in sorted(SCOPE_LADS)) + ")"
    counts = {}

    for kind in ("wards", "boroughs"):
        layer, fields = BOUNDARY_LAYERS[kind]
        gj = fetch_arcgis_geojson(layer, lad_in, fields, source="boundaries")
        write_json_atomic(out_dir / f"{kind}.geojson", gj)
        counts[kind] = len(gj["features"])
        ok(f"boundaries: {kind} {counts[kind]:,} features -> "
           f"data/boundaries/{kind}.geojson")

    # The LSOA layer has no LAD column, so scope comes from the ONS best-fit
    # lookup and is applied as chunked IN clauses to keep each URL short.
    codes = sorted(get_lsoa_ward_lookup())
    layer, fields = BOUNDARY_LAYERS["lsoa"]
    feats: list = []
    CHUNK = 200
    for i in range(0, len(codes), CHUNK):
        batch = codes[i:i + CHUNK]
        where = "LSOA21CD IN (" + ",".join(f"'{c}'" for c in batch) + ")"
        feats.extend(fetch_arcgis_geojson(layer, where, fields,
                                          source="boundaries")["features"])
    write_json_atomic(out_dir / "lsoa.geojson",
                      {"type": "FeatureCollection", "features": feats})
    counts["lsoa"] = len(feats)
    ok(f"boundaries: lsoa {len(feats):,} features -> data/boundaries/lsoa.geojson")

    if counts["lsoa"] < len(codes) * 0.95:
        raise RuntimeError(
            f"boundaries: only {counts['lsoa']:,} of {len(codes):,} in-scope "
            f"LSOAs returned geometry. Refusing to ship a map with holes in it."
        )

    counts["msoa"] = fetch_msoa_boundaries(out_dir)

    load_boundary_index.cache_clear()
    return pd.DataFrame([{"kind": k, "features": v} for k, v in counts.items()])


def fetch_msoa_boundaries(out_dir: Path) -> int:
    """
    MSOA boundaries for the in-scope area, written to data/boundaries/msoa.geojson.

    Scope comes from the LSOA set the rest of the pipeline already works to,
    mapped up through the ONS LSOA21 -> MSOA21 lookup. Going via the LSOAs
    rather than asking for a borough's MSOAs directly keeps every geography in
    this repo answering to the same definition of "in scope": an MSOA is
    included exactly when one of its LSOAs is.

    Split out of run_boundaries so it can be run on its own without refetching
    the three files that are already committed.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    lsoa_to_msoa = get_lsoa_to_msoa()
    in_scope = set(get_lsoa_ward_lookup())
    msoa_codes = sorted({m for l, m in lsoa_to_msoa.items() if l in in_scope})
    if not msoa_codes:
        raise RuntimeError("boundaries: no MSOA codes resolved from the LSOA lookup")

    layer, fields = BOUNDARY_LAYERS["msoa"]
    feats: list = []
    CHUNK = 200
    for i in range(0, len(msoa_codes), CHUNK):
        batch = msoa_codes[i:i + CHUNK]
        where = "MSOA21CD IN (" + ",".join(f"'{c}'" for c in batch) + ")"
        feats.extend(fetch_arcgis_geojson(layer, where, fields,
                                          source="boundaries")["features"])

    if len(feats) < len(msoa_codes) * 0.95:
        raise RuntimeError(
            f"boundaries: only {len(feats):,} of {len(msoa_codes):,} in-scope "
            f"MSOAs returned geometry. Refusing to ship a layer with holes in it."
        )

    write_json_atomic(out_dir / "msoa.geojson",
                      {"type": "FeatureCollection", "features": feats})
    ok(f"boundaries: msoa {len(feats):,} features -> data/boundaries/msoa.geojson")
    export_msoa_outline(feats)
    return len(feats)


# ============================================================================
# SOURCE 2b: Dental practices  (NHS ODS egdpprac)
# ============================================================================
# ODS is the base: it is the authoritative register, refreshes monthly and
# covers the whole country, so it scales to any footprint. It only holds
# practices with an NHS contract, though, and the curated file this replaces
# was 445 private to 285 NHS-contracted. Dropping the private ones would delete
# most of the layer and make the map's NHS/private filter meaningless, so any
# curated practice ODS does not know about is carried through unchanged.
CURATED_DENTAL = REPO_ROOT / "data" / "curated" / "dental_practices_curated.json"

# The dental register holds every NHS dental contract, and some of them are
# not a practice a resident can walk into: prison dental services are left
# out, and referral services are labelled so a reader does not mistake them
# for a high street dentist.
DENTAL_PRISON = re.compile(r"\bH\.? ?M\.? ?P\b|\bH ?M PRISON\b|\bPRISON\b|\bYOI\b|young offender", re.I)
DENTAL_KINDS = [
    (re.compile(r"out of hours|\bOOH\b|emergency dental|urgent dental", re.I),
     "Urgent dental care, through NHS 111"),
    (re.compile(r"orthodont", re.I), "Orthodontics only, by referral"),
    (re.compile(r"community dental|special care|salaried dental|mobile dental", re.I),
     "Community dental service, by referral"),
    (re.compile(r"\bhospital\b|dental institute|\bOMFS\b", re.I),
     "Hospital dental service, by referral"),
]
DENTAL_GENERIC = re.compile(r"^\s*(?:the\s+)?dental\s+(?:surgery|practice|clinic|centre)\s*$", re.I)

def dental_verdict(name: str, address: str = ""):
    """None for a record to leave out, else its label ("" for a practice)."""
    if DENTAL_PRISON.search(f"{name} {address}"):
        return None
    for rx, label in DENTAL_KINDS:
        if rx.search(str(name)):
            return label
    return ""

def dental_display_name(name: str, lines: list) -> str:
    """The register name, or the first address line where the name is only
    "Dental Surgery", which tells a reader nothing about where it is."""
    nm = title_case(name, initials=True)
    first = next((l for l in lines if l), "")
    if DENTAL_GENERIC.match(str(name)) and first:
        return f"{nm}, {first}"
    return nm

def run_dentists() -> pd.DataFrame:
    rule("Dental practices (NHS ODS egdpprac)")
    cache_dir = CACHE_DIR / "dentists"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache = cache_dir / "egdpprac.csv"

    # Every dental practice code is V-prefixed, which separates this extract
    # from epraccur (Y/M/A...) and edispensary (F). The role column is blank on
    # most rows here, so it is not used as a discriminator.
    fetch_ods_report(
        "egdpprac", cache, source="dentists",
        code_prefix="V", min_active=5_000,
    )
    df = pd.read_csv(cache, header=None, names=EPRACCUR_HEADER,
                     dtype=str, keep_default_na=False, encoding="latin-1")
    df = df[df["StatusCode"].isin(["A", "ACTIVE"])]

    owners = {normalise_postcode(r["Postcode"]): title_case(r["Name"], initials=True)
              for _, r in df.iterrows() if normalise_postcode(r["Postcode"])}
    lookup = lookup_postcodes(owners.keys(), source="dentists", owners=owners)

    # Keyed by practice code. It used to be keyed by postcode and overwritten,
    # so the practices in one health centre collapsed into whichever came
    # last, sometimes under the building's name.
    by_code: dict[str, dict] = {}
    n_prison = 0
    national = []          # high street practices, in or out of scope (service_access)
    for _, r in df.iterrows():
        pc = normalise_postcode(r["Postcode"])
        hit = lookup.get(pc)
        if not hit:
            continue
        lat, lng, lsoa, lad, wd = hit
        lines = [title_case(r[f"AddressLine{i}"]) for i in range(1, 5)]
        verdict = dental_verdict(r["Name"], " ".join(lines))
        if verdict == "":
            national.append((r["OrganisationCode"], lat, lng))
        if lad not in SCOPE_LADS:
            continue
        if verdict is None:
            n_prison += 1
            continue
        by_code[r["OrganisationCode"]] = {
            "code": r["OrganisationCode"],
            "name": dental_display_name(r["Name"], lines),
            "addr": ", ".join(filter(None, lines)),
            "tel": format_uk_phone(r["ContactTelephoneNumber"]),
            "kind": verdict,
            "postcode": r["Postcode"],
            "lat": lat, "lng": lng,
            "nhs_contracted": True,          # presence in ODS means an NHS contract
            "lad_code": lad,
            "LSOA21CD": lsoa, "WD25CD": wd,
            "source": "ods",
        }
    n_ods = len(by_code)
    ok(f"dentists: {n_prison:,} prison dental services left out")

    # ODS only. The curated overlay used to add private practices ODS does not
    # list, and a nhs_contracted flag to tell the two apart, which is what the
    # map's NHS/private filter ran on. Both are gone: this layer is now exactly
    # the NHS dental register, which is a claim the source can support on its
    # own and which nobody has to maintain by hand.
    #
    # It costs the 215 private practices the overlay contributed.
    # data/curated/dental_practices_curated.json is still in the repo, so
    # restoring the merge is a matter of putting this block back.
    rows = list(by_code.values())

    out = pd.DataFrame(rows)
    save_national_sites("dentist", national)
    out_path = DATA_DIR / "healthcare" / "dental_practices.parquet"
    out = write_parquet_guarded(out_path, out, source="dentists")
    ok(f"dentists: {len(out):,} NHS practices from ODS -> "
       f"{out_path.relative_to(REPO_ROOT)}")
    return out


# ============================================================================
# SOURCE 3: IMD 2025  (MHCLG, LSOA-level, all 7 domains)
# ============================================================================
# IMD 2025 is a one-off publication: the previous release was 2019 and the next
# is years away. Rather than depend on a gov.uk asset URL whose media hash
# rotates every release, the filtered parquet is committed and is the source of
# truth. The raw file is only needed to regenerate it when a new IoD lands.
#
# Source: MHCLG, English Indices of Deprivation 2025, File 7 (All Ranks,
# Scores, Deciles and Population Denominators).
#   Landing page: https://www.gov.uk/government/statistics/english-indices-of-deprivation-2025
#   File 7 as published (the media hash changes each release, so treat the
#   landing page above as authoritative if this 404s):
IMD_SOURCE_URL = (
    "https://assets.publishing.service.gov.uk/media/"
    "691ded56d140bbbaa59a2a7d/"
    "File_7_IoD2025_All_Ranks_Scores_Deciles_Population_Denominators.csv"
)
IMD_LANDING_URL = (
    "https://www.gov.uk/government/statistics/"
    "english-indices-of-deprivation-2025"
)
# Filtering applied to produce data/demographics/imd2025.parquet:
#   rows    - every English LSOA 2021 in File 7 that carries an LSOA code
#             (33,755; no geographic subsetting, since lsoa_data.json is
#             full-England and the scoping happens downstream)
#   columns - 11 of File 7's ~60: LSOA21CD, the overall IMD score, decile and
#             rank, and the seven domain scores. Population denominators and
#             the per-domain ranks and deciles are dropped as unused here;
#             IDACI, IDAOPI, the sub-domains and the denominators they need
#             are kept by run_iod_extra in a parquet of their own.


def iod_norm(s) -> str:
    """A header as the IoD column matching reads it: lower case, with every
    run of punctuation or spaces turned into one space, so "Sub-domain",
    "Sub domain" and "sub-domain " all read "sub domain" and "aged 0-15"
    reads "aged 0 15"."""
    return " ".join(re.sub(r"[^0-9a-z]+", " ", str(s).lower()).split())


def iod_find_cols(columns, *kws, exclude=()) -> list:
    """Every column whose normalised header holds all of kws and none of
    exclude, in the file's own order."""
    want = [iod_norm(k) for k in kws]
    bar = [iod_norm(e) for e in exclude]
    out = []
    for c in columns:
        if not isinstance(c, str):
            continue
        n = iod_norm(c)
        if all(k in n for k in want) and not any(b in n for b in bar):
            out.append(c)
    return out


def iod_find_col(columns, *kws, exclude=()):
    """The first column iod_find_cols finds, or None."""
    hits = iod_find_cols(columns, *kws, exclude=exclude)
    return hits[0] if hits else None


def iod_lookup(columns, alternatives, exclude=()):
    """The first column matching any of the keyword tuples in alternatives,
    tried in order."""
    for kws in alternatives:
        c = iod_find_col(columns, *kws, exclude=exclude)
        if c:
            return c
    return None


# How the IMD and its seven domain scores are found in File 7. Each domain
# lookup rules out the narrower measures that share its words, so the column
# order of a release cannot hand a domain the wrong figure: "income" and
# "score" are also in the IDACI and IDAOPI headers ("Income Deprivation
# Affecting Children Index (IDACI) Score (rate)"), "barriers" in the two
# barriers sub-domains, and so on. Ranks and deciles are never a score.
IOD_DOMAIN_LOOKUPS = {
    "imd_score":         ((("multiple deprivation", "score"), ("imd", "score")), ()),
    "income_score":      ((("income", "score"),), ("affecting", "idaci", "idaopi")),
    "employment_score":  ((("employment", "score"),), ()),
    "education_score":   ((("education", "score"),), ("sub domain", "young people", "adult skills")),
    "health_score":      ((("health", "score"),), ()),
    "crime_score":       ((("crime", "score"),), ()),
    "barriers_score":    ((("barriers", "score"),), ("sub domain", "geographical", "wider")),
    "environment_score": ((("environment", "score"),), ("sub domain", "indoors", "outdoors")),
}


def iod_domain_cols(columns) -> dict:
    """{imd_score / income_score / ...: the File 7 header, or None}."""
    return {key: iod_lookup(columns, alts, exclude=tuple(ex) + ("rank", "decile"))
            for key, (alts, ex) in IOD_DOMAIN_LOOKUPS.items()}


def run_imd2025() -> pd.DataFrame:
    rule("IMD 2025 (MHCLG)")
    out_path = DATA_DIR / "demographics" / "imd2025.parquet"
    cache_dir = CACHE_DIR / "imd2025"
    cache_dir.mkdir(parents=True, exist_ok=True)

    # A raw File 7 in .cache/ means "regenerate" - that is how a new IoD release
    # gets picked up. If both a CSV (File 7, all domains) and an XLSX (File 1,
    # ranks only) are present, pick the larger one.
    candidates = sorted(
        [*cache_dir.glob("*.csv"), *cache_dir.glob("*.xlsx")],
        key=lambda p: p.stat().st_size, reverse=True,
    )
    if not candidates:
        # IMD is national and committed once, in data/demographics. A region
        # reads that copy rather than needing its own.
        national = NATIONAL_DATA_DIR / "demographics" / "imd2025.parquet"
        if not out_path.exists() and national.exists():
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(national.read_bytes())
        if out_path.exists():
            out = pd.read_parquet(out_path)
            ok(f"imd2025: {len(out):,} LSOAs from the committed parquet "
               "(static between releases; no download needed)")
            return out
        raise RuntimeError(
            "imd2025: no committed parquet at "
            f"{out_path} and no raw file in {cache_dir}.\n"
            "IMD 2025 is static between releases and normally ships with the "
            "repo. To rebuild it, download File 7 (All Ranks, Scores, Deciles "
            f"and Population Denominators) from {IMD_LANDING_URL} "
            f"(direct link at time of writing: {IMD_SOURCE_URL}) "
            f"and drop the CSV in {cache_dir}."
        )
    src = candidates[0]
    info(f"imd2025: rebuilding the parquet from {src.name}")

    if src.suffix.lower() in (".xlsx", ".xls"):
        xl = pd.ExcelFile(src)
        name_match = [s for s in xl.sheet_names
                      if "imd" in s.lower() or "iod" in s.lower()]
        non_notes  = [s for s in xl.sheet_names if s.lower() not in ("notes",)]
        tab = (name_match + non_notes + xl.sheet_names)[0]
        df = pd.read_excel(src, sheet_name=tab)
    else:
        df = pd.read_csv(src)

    if df.empty:
        warn("imd2025: empty input")
        return df

    df = df.rename(columns={c: c.strip() for c in df.columns})

    # Matched on normalised headers, and each domain rules out the narrower
    # measures that share its words (IOD_DOMAIN_LOOKUPS): "income" and
    # "score" are in the IDACI header too, so a release that put IDACI first
    # would otherwise hand the Income domain the children's figure.
    code_col   = iod_find_col(df.columns, "lsoa", "code")
    dom        = iod_domain_cols(df.columns)
    score_col  = dom["imd_score"]
    decile_col = (iod_find_col(df.columns, "multiple deprivation", "decile")
                  or iod_find_col(df.columns, "imd", "decile"))
    rank_col   = (iod_find_col(df.columns, "multiple deprivation", "rank", exclude=("decile",))
                  or iod_find_col(df.columns, "imd", "rank", exclude=("decile",)))
    income_col = dom["income_score"]
    emp_col    = dom["employment_score"]
    edu_col    = dom["education_score"]
    health_col = dom["health_score"]
    crime_col  = dom["crime_score"]
    barriers_col = dom["barriers_score"]
    env_col    = dom["environment_score"]

    if not code_col:
        raise RuntimeError(f"imd2025: no LSOA code column found in {src}")

    def num(col):
        if col is None:
            return pd.Series([pd.NA] * len(df))
        return pd.to_numeric(df[col], errors="coerce")

    out = pd.DataFrame({
        "LSOA21CD":       df[code_col].astype(str).str.strip(),
        "imd_score":      num(score_col),
        "imd_decile":     num(decile_col),
        "imd_rank":       num(rank_col),
        "income_score":   num(income_col),
        "employment_score": num(emp_col),
        "education_score":  num(edu_col),
        "health_score":   num(health_col),
        "crime_score":    num(crime_col),
        "barriers_score": num(barriers_col),
        "environment_score": num(env_col),
    }).dropna(subset=["LSOA21CD"])

    # Parquet does not serialise byte-identically across runs, so rewriting an
    # unchanged frame would show up as a modified binary in git on every run of
    # a source that is static by definition. Only write when the data moved.
    if out_path.exists():
        try:
            previous = pd.read_parquet(out_path)
        except Exception:
            previous = None
        if previous is not None and previous.equals(out):
            ok(f"imd2025: {len(out):,} LSOAs, unchanged from the committed "
               "parquet (not rewritten)")
            _record_write(out_path, len(out))
            return out

    out = write_parquet_guarded(out_path, out, source="imd2025")
    ok(f"imd2025: {len(out):,} LSOAs -> {out_path.relative_to(REPO_ROOT)}")
    return out


# ============================================================================
# SOURCE 3a: IoD 2025 beyond the IMD  (MHCLG, LSOA-level)
# ============================================================================
# The IMD parquet keeps the overall index and its seven domains. Two more parts
# of the same release are kept beside it, each in its own parquet and under its
# own manifest key, so a problem with either never marks the IMD itself failed:
#
#   iod_extra       data/demographics/iod2025_extra.parquet, from File 7, the
#                   file the IMD parquet is filtered from. It also carries the
#                   two supplementary indices, IDACI (the share of children
#                   aged 0 to 15 living in income-deprived families) and IDAOPI
#                   (the share of people aged 60 and over who are income
#                   deprived), each as a rate, a rank and a decile; the six
#                   sub-domain scores (children and young people, and adult
#                   skills, under education; geographical and wider barriers;
#                   indoors and outdoors living environment); and the population
#                   denominators, of which the children aged 0 to 15 and the
#                   people aged 60 and over weight IDACI and IDAOPI into wards
#                   and LAs, so those figures are the share of the area's
#                   children (or older people), not of its residents.
#   iod_indicators  data/demographics/iod2025_indicators.parquet, from File 8,
#                   the underlying indicators the domains are built from: for
#                   health, years of potential life lost, the comparative
#                   illness and disability ratio, acute morbidity and mood and
#                   anxiety disorders; for education, Key Stage 2 and Key Stage
#                   4 attainment, staying on after 16, entry to higher
#                   education, adults with no or low qualifications and English
#                   language proficiency; for living environment, housing in
#                   poor condition, houses without central heating, air quality
#                   and road traffic accidents.
#
# Both are static between releases, like the IMD: once the committed parquet
# holds every column it is used as it is, with no download. Until then, and
# whenever a raw file is dropped in .cache/iod2025/, the file is read from the
# release. Nothing is pinned by hand: the files are found on the release page
# through the gov.uk content API by their "File 7" and "File 8" titles (File 7
# falls back to IMD_SOURCE_URL), and every column by the words in its header.
# Where a file or a column is missing, or a column fails a check, the source is
# marked failed in the manifest saying what, the rest is still written, and the
# map leaves out any indicator that has no figures.
#
# Headers. The IDACI header is "Income Deprivation Affecting Children Index
# (IDACI) Score (rate)", as Leicester's IoD 2025 field glossary (built from the
# MHCLG file) gives it in the fact-check notes; the others are matched by the
# words of the 2019 File 7 layout ("... (IDAOPI) Rank (where 1 is most
# deprived)", "Children and Young People Sub-domain Score", "Indoors Sub-domain
# Score", "Dependent Children aged 0-15: mid 2015 (excluding prisoners)",
# "Older population aged 60 and over: ..."), so a changed year or punctuation
# does not matter. File 8 is a workbook with one sheet per domain, as in 2019.
# Its 2025 headers were not seen when this was written (the proxy refused the
# fact-check too), so each indicator is found by the words the technical report
# uses for it and is checked against its own domain before it is kept: a
# column that matches twice, reads on the wrong scale or runs the wrong way is
# reported, never guessed at.
IOD_API_URL = ("https://www.gov.uk/api/content/government/statistics/"
               "english-indices-of-deprivation-2025")
# A direct link, for when the release page moves.
IOD_FILE7_URL_OVERRIDE = os.environ.get("IOD_FILE7_URL", "")
IOD_FILE8_URL_OVERRIDE = os.environ.get("IOD_FILE8_URL", "")
IOD_CACHE = CACHE_DIR / "iod2025"
IOD_EXTRA_PARQUET = "iod2025_extra.parquet"
IOD_INDICATORS_PARQUET = "iod2025_indicators.parquet"

# File 7: (key, keyword tuples tried in order, words that rule a header out,
# kind). The kind says how a figure is checked and stored. "rate" is MHCLG's 0
# to 1 rate, stored as a percentage (idaci_pct), which is how the map shows it.
# "rank" runs from 1, the most deprived LSOA in England. "decile" runs 1 to 10,
# 1 the most deprived tenth. "score" is a sub-domain score as published, where a
# higher score means more deprived.
_IOD_NOT_SCORE = ("rank", "decile")
IOD_FILE7_FIELDS = (
    ("idaci_pct",     (("idaci", "score"), ("affecting children", "score")), _IOD_NOT_SCORE, "rate"),
    ("idaci_rank",    (("idaci", "rank"), ("affecting children", "rank")), ("decile", "score"), "rank"),
    ("idaci_decile",  (("idaci", "decile"), ("affecting children", "decile")), ("score",), "decile"),
    ("idaopi_pct",    (("idaopi", "score"), ("affecting older", "score")), _IOD_NOT_SCORE, "rate"),
    ("idaopi_rank",   (("idaopi", "rank"), ("affecting older", "rank")), ("decile", "score"), "rank"),
    ("idaopi_decile", (("idaopi", "decile"), ("affecting older", "decile")), ("score",), "decile"),
    ("cyp_education_score",  (("children and young people", "score"),), _IOD_NOT_SCORE, "score"),
    ("adult_skills_score",   (("adult skills", "score"),), _IOD_NOT_SCORE, "score"),
    ("geo_barriers_score",   (("geographical barriers", "score"),), _IOD_NOT_SCORE, "score"),
    ("wider_barriers_score", (("wider barriers", "score"),), _IOD_NOT_SCORE, "score"),
    ("indoors_score",        (("indoors", "score"),), _IOD_NOT_SCORE, "score"),
    ("outdoors_score",       (("outdoors", "score"),), _IOD_NOT_SCORE, "score"),
)
# The denominators that weight IDACI and IDAOPI into wards and LAs.
IOD_FILE7_DENOMINATORS = (
    ("iod_pop_children", (("dependent children",), ("children", "aged 0 15")),
     ("score", "rank", "decile")),
    ("iod_pop_older", (("aged 60 and over",), ("older population",)),
     ("score", "rank", "decile")),
)
# Each File 7 measure must rise with the domain it belongs to (a rank or decile
# must fall as its own rate rises), which catches a header matched to the
# wrong column before it reaches the map.
IOD_FILE7_PARENT = {
    "idaci_pct": "income_score", "idaopi_pct": "income_score",
    "cyp_education_score": "education_score", "adult_skills_score": "education_score",
    "geo_barriers_score": "barriers_score", "wider_barriers_score": "barriers_score",
    "indoors_score": "environment_score", "outdoors_score": "environment_score",
}

# File 8: (key, the domain score it is checked against, direction, kind,
# keyword tuples tried in order, words that rule a header out). Direction +1
# means a higher figure is worse, so it must rise with its domain score; -1
# means a higher figure is better (attainment), so it must fall as the domain
# score rises. The map colours each one in its stated direction, so a column
# that runs the other way is left out rather than painted backwards. Kind:
# "share" is a proportion, stored as a percentage whether published from 0 to 1
# or from 0 to 100; "ratio" is a standardised ratio, stored with England at 100
# whether published with England at 1 or at 100; "value" is kept as published.
# Absence (new in 2025) is not taken: it is several measures, primary,
# secondary and persistent, and the brief keeps the set it names.
IOD_FILE8_SPECS = (
    ("iod_ypll",               "health_score",      +1, "value",
     (("years of potential life lost",), ("ypll",)), ()),
    ("iod_cidr",               "health_score",      +1, "ratio",
     (("comparative illness",), ("cidr",)), ()),
    ("iod_acute_morbidity",    "health_score",      +1, "ratio",
     (("acute morbidity",),), ()),
    ("iod_mood_anxiety",       "health_score",      +1, "value",
     (("mood and anxiety",), ("mood", "anxiety"), ("mental health",)), ()),
    ("iod_ks2",                "education_score",   -1, "value",
     (("key stage 2",), ("ks2",)), ()),
    ("iod_ks4",                "education_score",   -1, "value",
     (("key stage 4",), ("ks4",)), ()),
    ("iod_staying_on",         "education_score",   +1, "share",
     (("staying on",),), ()),
    ("iod_he_entry",           "education_score",   +1, "share",
     (("higher education",),), ()),
    # IoD 2025 prints adult skills and English proficiency as one indicator
    # ("Adult skills and English language proficiency indicator"), which this
    # takes; the 2019 workbook had them apart, and iod_english then took the
    # second. The October 2026 header list has no Key Stage 2, Key Stage 4,
    # staying on or central heating indicator either; their entries stay so
    # a later release that prints them is read, and meanwhile the source
    # notes them and the map leaves them out.
    ("iod_adult_quals",        "education_score",   +1, "share",
     (("qualification",), ("adult skills",)), ()),
    ("iod_english",            "education_score",   +1, "share",
     (("english language",), ("speak english",), ("english proficiency",)),
     ("qualification", "adult skills")),
    ("iod_housing_poor",       "environment_score", +1, "share",
     (("poor condition",),), ()),
    ("iod_no_central_heating", "environment_score", +1, "share",
     (("central heating",),), ()),
    ("iod_air_quality",        "environment_score", +1, "value",
     (("air quality",),), ()),
    ("iod_road_accidents",     "environment_score", +1, "value",
     (("road traffic",),), ("noise",)),
)
# A rank, decile or domain score column must never be taken for an indicator,
# and nor must a part of one: the 2019 workbook lists the four pollutants as
# "Nitrogen dioxide (component of air quality indicator)" and so on, beside
# "Air quality indicator" itself. "Sub domain" on its own is not excluded: the
# October 2026 refresh found no Key Stage 2, Key Stage 4, staying on, adult
# skills or English proficiency column, and one possible reason is a header
# that names the sub-domain it belongs to. A sub-domain score still reads
# "... sub domain score", which "domain score" catches. When anything is
# missing the run log lists every header (iod_headers).
IOD_FILE8_EXCLUDE = ("rank", "decile", "domain score", "component")
# Below this many LSOAs with figures a direction check is not attempted.
IOD_CHECK_MIN_N = 200
# The weakest agreement with its domain an indicator may show and still be
# taken to run the stated way (a rank correlation).
IOD_MIN_AGREEMENT = 0.1


def iod_rank_corr(a: "pd.Series", b: "pd.Series", *, min_n: int | None = None):
    """The rank correlation of two LSOA-indexed series over the LSOAs both
    have, or None when too few do. Pearson on ranks, so no scipy."""
    j = pd.concat([a.rename("a"), b.rename("b")], axis=1, join="inner").dropna()
    if len(j) < (IOD_CHECK_MIN_N if min_n is None else min_n):
        return None
    x, y = j["a"].rank(), j["b"].rank()
    if x.std() == 0 or y.std() == 0:
        return None
    return float(x.corr(y))


def iod_scale(s: "pd.Series", kind: str) -> tuple:
    """(series as stored, problem or ""), for one IoD column of the given
    kind (see IOD_FILE7_FIELDS and IOD_FILE8_SPECS)."""
    v = s.dropna()
    if v.empty:
        return s, "has no figures"
    lo, hi = float(v.min()), float(v.max())
    if kind == "rate":
        if lo < 0:
            return s, f"has negative rates (from {lo:g})"
        if hi <= 1.0:
            return s * 100, ""
        if hi <= 100.0:
            return s, ""
        return s, f"reads as a rate but runs to {hi:g}"
    if kind == "share":
        if hi <= 1.0:
            return s * 100, ""
        if hi <= 100.0:
            return s, ""
        return s, f"reads as a share but runs to {hi:g}"
    if kind == "ratio":
        med = float(v.median())
        if 0.2 <= med <= 5:
            return s * 100, ""
        if 20 <= med <= 500:
            return s, ""
        return s, f"reads as a ratio to England but its median is {med:g}"
    if kind == "rank":
        if lo < 1 or (v.round() != v).any():
            return s, "is not a rank from 1"
        return s, ""
    if kind == "decile":
        if not v.isin(range(1, 11)).all():
            return s, "is not a decile from 1 to 10"
        return s, ""
    return s, ""


def _iod_lsoa_series(df: "pd.DataFrame", col: str, codes: "pd.Series") -> "pd.Series":
    s = pd.Series(pd.to_numeric(df[col], errors="coerce").to_numpy(), index=codes.to_numpy())
    return s[~s.index.duplicated()]


def _iod_lsoa_rows(df: "pd.DataFrame") -> tuple:
    """(rows, codes) of a release table: only the rows carrying an English
    LSOA code, so a notes line or a total row is never read as an area."""
    df = df.rename(columns={c: c.strip() for c in df.columns if isinstance(c, str)})
    code_col = iod_find_col(df.columns, "lsoa", "code")
    if not code_col:
        return df, None, None
    codes = df[code_col].astype(str).str.strip()
    keep = codes.str.match(r"^E01\d{6}$").to_numpy()
    return df[keep], codes[keep], code_col


def iod_parse_file7(df: "pd.DataFrame") -> tuple:
    """(frame, problems, matched) from a File 7 table.

    The frame is LSOA21CD plus every IOD_FILE7_FIELDS and denominator column
    found and checked; problems lists, in words, every one that was missing
    or failed a check (empty when all were fine); matched is {key: header}.
    Raises RuntimeError when there is no LSOA code column, since then nothing
    can be read at all."""
    df, codes, code_col = _iod_lsoa_rows(df)
    if code_col is None:
        raise RuntimeError("File 7 has no LSOA code column (headers seen: "
                           + ", ".join(map(str, list(df.columns)[:8])) + ")")
    if not len(codes):
        raise RuntimeError("File 7 has no rows with an English LSOA code")
    dom = iod_domain_cols(df.columns)
    parent = {k: _iod_lsoa_series(df, c, codes) for k, c in dom.items() if c}
    cols: dict = {}
    problems: list = []
    matched: dict = {}
    n_all = codes.nunique()
    for key, alts, exclude, kind in IOD_FILE7_FIELDS:
        col = iod_lookup(df.columns, alts, exclude=exclude)
        if not col:
            problems.append(f"{key}: no File 7 column with " +
                            " or ".join("'" + " ".join(a) + "'" for a in alts))
            continue
        s, why = iod_scale(_iod_lsoa_series(df, col, codes), kind)
        if not why and kind in ("rank", "decile"):
            own = cols.get(key.rsplit("_", 1)[0] + "_pct")
            r = None if own is None else iod_rank_corr(s, own)
            if r is not None and r > -0.5:
                why = f"does not fall as its own rate rises (rank correlation {r:.2f})"
        elif not why and IOD_FILE7_PARENT.get(key) in parent:
            r = iod_rank_corr(s, parent[IOD_FILE7_PARENT[key]])
            if r is not None and r < IOD_MIN_AGREEMENT:
                why = (f"does not rise with {IOD_FILE7_PARENT[key]} "
                       f"(rank correlation {r:.2f}), so it may be the wrong column")
        if why:
            problems.append(f"{key} ('{col}') {why}; not used")
            continue
        if s.notna().sum() < 0.9 * n_all:
            problems.append(f"{key} ('{col}') has figures for only {int(s.notna().sum()):,} "
                            f"of {n_all:,} LSOAs")
        cols[key] = s
        matched[key] = col
    for key, alts, exclude in IOD_FILE7_DENOMINATORS:
        col = iod_lookup(df.columns, alts, exclude=exclude)
        if not col:
            problems.append(f"{key}: no File 7 denominator column with " +
                            " or ".join("'" + " ".join(a) + "'" for a in alts))
            continue
        s = _iod_lsoa_series(df, col, codes)
        if s.dropna().empty or float(s.min()) < 0:
            problems.append(f"{key} ('{col}') is not a population count; not used")
            continue
        cols[key] = s
        matched[key] = col
    out = pd.DataFrame(cols, index=pd.Index(sorted(set(codes)), name="LSOA21CD"))
    return out.reset_index(), problems, matched


def iod_parse_file8(sheets: dict, parent: "pd.DataFrame | None") -> tuple:
    """(frame, problems, matched) from the sheets of a File 8 workbook.

    Each IOD_FILE8_SPECS indicator is looked for across every sheet with an
    LSOA code column. One that no column matches, or that two different
    columns match, is reported and left out, never guessed at; so is one on
    the wrong scale for its kind or running against its own domain score in
    parent (imd2025.parquet). Raises RuntimeError when no sheet has an LSOA
    code column."""
    tables = []
    for name, df in sheets.items():
        rows, codes, code_col = _iod_lsoa_rows(df)
        if code_col is not None and len(codes):
            tables.append((name, rows, codes))
    if not tables:
        raise RuntimeError("no sheet of File 8 has LSOA codes (sheets: "
                           + ", ".join(map(str, sheets)) + ")")
    par: dict = {}
    if parent is not None and not parent.empty and "LSOA21CD" in parent.columns:
        pc = parent["LSOA21CD"].astype(str)
        for c in ("health_score", "education_score", "environment_score"):
            if c in parent.columns:
                s = pd.Series(pd.to_numeric(parent[c], errors="coerce").to_numpy(), index=pc.to_numpy())
                par[c] = s[~s.index.duplicated()]
    n_all = len(set().union(*(set(t[2]) for t in tables)))
    cols: dict = {}
    problems: list = []
    matched: dict = {}
    for key, pkey, sign, kind, alts, exclude in IOD_FILE8_SPECS:
        hits = []
        for kws in alts:
            for name, rows, codes in tables:
                for c in iod_find_cols(rows.columns, *kws,
                                       exclude=tuple(exclude) + IOD_FILE8_EXCLUDE):
                    hits.append((name, c, rows, codes))
            if hits:
                break
        heads = list(dict.fromkeys(iod_norm(h[1]) for h in hits))
        if not hits:
            problems.append(f"{key}: no File 8 column with " +
                            " or ".join("'" + " ".join(a) + "'" for a in alts))
            continue
        if len(heads) > 1:
            problems.append(f"{key}: {len(heads)} File 8 columns match ("
                            + "; ".join(f"'{h[1]}'" for h in hits[:4]) + "), so none is used")
            continue
        name, col, rows, codes = hits[0]
        s, why = iod_scale(_iod_lsoa_series(rows, col, codes), kind)
        if not why and pkey in par:
            r = iod_rank_corr(s, par[pkey])
            if r is not None and r * sign < IOD_MIN_AGREEMENT:
                why = (f"should {'rise' if sign > 0 else 'fall'} as {pkey} rises but its rank "
                       f"correlation with it is {r:.2f}, so its direction is not the one the map states")
        if why:
            problems.append(f"{key} ('{name}': '{col}') {why}; not used")
            continue
        if s.notna().sum() < 0.9 * n_all:
            problems.append(f"{key} ('{col}') has figures for only {int(s.notna().sum()):,} "
                            f"of {n_all:,} LSOAs")
        cols[key] = s
        matched[key] = f"{name}: {col}"
    codes_all = sorted(set().union(*(set(t[2]) for t in tables)))
    out = pd.DataFrame(cols, index=pd.Index(codes_all, name="LSOA21CD"))
    return out.reset_index(), problems, matched


def iod_release_files() -> list:
    """[{"title", "url"}] for every file on the IoD 2025 release page, from
    the gov.uk content API, or from the page itself when that answers nothing."""
    found: dict = {}

    def add(title, url):
        if not isinstance(url, str):
            return
        if url.startswith("/media/"):
            url = "https://assets.publishing.service.gov.uk" + url
        if "assets.publishing.service.gov.uk" not in url:
            return
        if url not in found or (title and not found[url]):
            found[url] = str(title or "")

    sess = browser_session(referer="https://www.gov.uk/")
    try:
        r = sess.get(IOD_API_URL, timeout=60)
        r.raise_for_status()

        def walk(o):
            if isinstance(o, dict):
                add(o.get("title") or o.get("filename") or "", o.get("url"))
                for v in o.values():
                    walk(v)
            elif isinstance(o, list):
                for v in o:
                    walk(v)
        try:
            walk(r.json())
        except ValueError:
            pass
        for u in re.findall(r'https://assets\.publishing\.service\.gov\.uk/[^"\s<>\\]+', r.text):
            add("", u)
    except Exception as e:
        warn(f"iod: could not read the release through the gov.uk content API ({e})")
    if not found:
        try:
            r = sess.get(IMD_LANDING_URL, timeout=60)
            r.raise_for_status()
            for m in re.finditer(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', r.text, re.S):
                add(re.sub(r"<[^>]+>", " ", m.group(2)).strip(), m.group(1))
        except Exception as e:
            warn(f"iod: could not read the release page {IMD_LANDING_URL} ({e})")
    return [{"title": t, "url": u} for u, t in found.items()]


def iod_pick_file(files: list, number: int, exts: tuple, words: tuple = ()):
    """The URL of File <number> among the release's files, by its title or
    its file name ("File 7: all ranks, ...", "File_7_IoD2025_...csv"), in the
    first of exts that it is published in. words are keyword tuples that also
    identify it, for a title that drops the number."""
    pat = re.compile(rf"\bfile {number}\b")

    def named(f):
        t = iod_norm(f.get("title", ""))
        n = iod_norm(str(f.get("url", "")).split("?")[0].rsplit("/", 1)[-1])
        return bool(pat.search(t) or pat.search(n)) or any(
            all(w in t + " " + n for w in ws) for ws in words)

    cands = [f for f in files if named(f)]
    for ext in exts:
        for f in cands:
            if str(f["url"]).lower().split("?")[0].endswith(ext):
                return f["url"]
    return None


def _iod_raw_files(number: int) -> list:
    """Raw copies of File <number> on disk, largest first: in .cache/iod2025/
    by name, and for File 7 also any CSV in the IMD's drop folder."""
    out = []
    pat = re.compile(rf"\bfile {number}\b")
    if IOD_CACHE.is_dir():
        for p in IOD_CACHE.iterdir():
            n = iod_norm(p.name)
            if p.suffix.lower() in (".csv", ".xlsx", ".ods") and (
                    pat.search(n) or (number == 8 and "underlying" in n)):
                out.append(p)
    imd_dir = CACHE_DIR / "imd2025"
    if number == 7 and imd_dir.is_dir():
        out += [p for p in imd_dir.iterdir()
                if p.suffix.lower() == ".csv" or (p.suffix.lower() == ".xlsx" and pat.search(iod_norm(p.name)))]
    return sorted(set(out), key=lambda p: p.stat().st_size, reverse=True)


def _iod_download(number: int, exts: tuple, words: tuple = (), override: str = ""):
    """Download File <number> into .cache/iod2025/, or None (with a warning)."""
    url = override
    if not url:
        url = iod_pick_file(iod_release_files(), number, exts, words)
        if not url and number == 7:
            url = IMD_SOURCE_URL
            info(f"iod: File 7 not found on the release page; trying {url}")
    if not url:
        warn(f"iod: File {number} is not listed on the release ({IOD_API_URL})")
        return None
    name = url.split("?")[0].rsplit("/", 1)[-1] or f"File_{number}"
    if not re.search(rf"\bfile {number}\b", iod_norm(name)):
        name = f"File_{number}_{name}"
    dest = IOD_CACHE / name
    try:
        info(f"iod: downloading {url}")
        r = browser_session(referer="https://www.gov.uk/").get(url, timeout=300)
        r.raise_for_status()
        head = r.content[:200].lstrip().lower()
        if len(r.content) < 10_000 or head.startswith((b"<!doctype", b"<html")):
            raise RuntimeError(f"got {len(r.content):,} bytes that are not the file")
        write_bytes_atomic(dest, r.content)
    except Exception as e:
        warn(f"iod: File {number} download failed ({e})")
        return None
    return dest


def _iod_sheets(src: Path) -> dict:
    """Every table of a release file that has an LSOA code header, read with
    that row as its header: one for a CSV, one per sheet for a workbook."""
    if src.suffix.lower() == ".csv":
        try:
            return {src.stem: pd.read_csv(src, encoding="utf-8-sig", low_memory=False)}
        except UnicodeDecodeError:
            return {src.stem: pd.read_csv(src, encoding="latin-1", low_memory=False)}
    xl = pd.ExcelFile(src)
    out = {}
    for name in xl.sheet_names:
        try:
            top = xl.parse(name, header=None, nrows=15)
        except Exception:
            continue
        hdr = None
        for i, row in enumerate(top.itertuples(index=False)):
            if any(isinstance(v, str) and "lsoa" in iod_norm(v) and "code" in iod_norm(v)
                   for v in row):
                hdr = i
                break
        if hdr is not None:
            out[name] = xl.parse(name, header=hdr)
    return out


def _iod_committed(name: str):
    """The committed parquet for this scope, copied from the national one
    when a region has none of its own yet; None when there is neither."""
    out_path = DATA_DIR / "demographics" / name
    national = NATIONAL_DATA_DIR / "demographics" / name
    if not out_path.exists() and national.exists() and national != out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(national.read_bytes())
    if not out_path.exists():
        return None
    try:
        return pd.read_parquet(out_path)
    except Exception as e:
        warn(f"iod: {out_path.name} unreadable ({e})")
        return None


def _iod_fail(source: str, msg: str) -> None:
    """Mark the source failed in the manifest with msg, keeping its output."""
    warn(f"{source}: {msg}")
    _SOURCE_ERROR[source] = msg if len(msg) <= 2000 else msg[:1997] + "..."


def _iod_write(name: str, out: "pd.DataFrame", source: str) -> "pd.DataFrame":
    """Write the parquet, unless it already holds exactly this (a rewrite of
    unchanged data still shows as a modified binary in git)."""
    out_path = DATA_DIR / "demographics" / name
    if out_path.exists():
        try:
            previous = pd.read_parquet(out_path)
        except Exception:
            previous = None
        if previous is not None and previous.equals(out):
            ok(f"{source}: {len(out):,} LSOAs, unchanged from the committed parquet")
            _record_write(out_path, len(out))
            return out
    out = write_parquet_guarded(out_path, out, source=source)
    ok(f"{source}: {len(out):,} LSOAs -> {out_path.relative_to(REPO_ROOT)}")
    return out


def iod_headers(sheets: dict) -> list:
    """Every header of every sheet, as "sheet: header", for the log."""
    out = []
    for name, df in (sheets or {}).items():
        for c in getattr(df, "columns", []):
            if isinstance(c, str) and c.strip() and not c.startswith("Unnamed"):
                out.append(f"{name}: {c.strip()}")
    return out


def iod_problems_fail(want: list, found: list, problems: list, prev, optional: bool) -> bool:
    """Whether a run's problems mean the source failed.

    For File 7 (optional False) any problem does. For File 8 (optional True)
    an indicator the release does not print is not a failure: the map leaves
    out an indicator with no figures, and the IoD 2025 workbook may simply not
    carry every 2019 indicator. It fails when anything else went wrong (a
    column on the wrong scale, running the wrong way, too thin, or matched
    twice), when it lost an indicator the committed parquet had, or when it
    found fewer than half of them."""
    if not optional:
        return True
    missing = [k for k in want if k not in found]
    had = set(prev.columns) if prev is not None and hasattr(prev, "columns") else set()
    if any(k in had for k in missing):
        return True
    if len(found) * 2 < len(want):
        return True
    return any(": no File 8 column with " not in p for p in problems)


def _iod_run(source: str, number: int, name: str, want: list, exts: tuple,
             words: tuple, override: str, parse, optional: bool = False) -> "pd.DataFrame":
    """The shared body of run_iod_extra and run_iod_indicators: the raw file
    if one is on disk, else the committed parquet when it is complete, else a
    download; then parse(sheets) -> (frame, problems, matched). optional says
    an indicator the file does not print is reported but is not a failure
    (iod_problems_fail)."""
    raw = _iod_raw_files(number)
    prev = _iod_committed(name)
    if not raw:
        if prev is not None and len(prev) and all(k in prev.columns for k in want):
            ok(f"{source}: {len(prev):,} LSOAs from the committed parquet "
               "(static between releases; no download needed)")
            return prev
        got = _iod_download(number, exts, words, override)
        if got is None:
            msg = (f"File {number} of the English Indices of Deprivation 2025 could not be "
                   f"found or downloaded ({IMD_LANDING_URL}), so "
                   + ("the figures stay as last committed" if prev is not None and len(prev)
                      else "there are no figures yet")
                   + f". Download it from the release page into {IOD_CACHE} and rerun "
                   f"`python fetch_all_data.py --only {source}`.")
            if prev is not None and len(prev):
                _iod_fail(source, msg)
                return prev
            raise RuntimeError(msg)
        raw = [got]
    src = raw[0]
    info(f"{source}: reading {src.name}")
    try:
        sheets = _iod_sheets(src)
    except Exception as e:
        raise RuntimeError(f"File {number} ({src.name}) could not be read: {type(e).__name__}: {e}")
    out, problems, matched = parse(sheets)
    for k in want:
        if k in matched:
            info(f"{source}: {k} <- {matched[k]!r}")
    found = [k for k in want if k in out.columns]
    if problems:
        # Every header, so the next look at the log can say why.
        heads = iod_headers(sheets)
        info(f"{source}: File {number} headers: " + " | ".join(heads))
    if not found:
        msg = f"none of the expected columns was found in {src.name}: " + "; ".join(problems[:6])
        if prev is not None and len(prev):
            _iod_fail(source, msg)
            return prev
        raise RuntimeError(msg)
    if problems:
        msg = (f"{len(want) - len(found)} of {len(want)} columns missing or "
               "not used: " + "; ".join(problems))
        if iod_problems_fail(want, found, problems, prev, optional):
            _iod_fail(source, msg)
        else:
            warn(f"{source}: {msg}")
            _SOURCE_NOTE[source] = msg if len(msg) <= 2000 else msg[:1997] + "..."
    out = _iod_write(name, out, source)
    info(f"{source}: {len(found)} of {len(want)} columns")
    return out


def run_iod_extra() -> pd.DataFrame:
    rule("IoD 2025: IDACI, IDAOPI and sub-domains (MHCLG)")
    want = [f[0] for f in IOD_FILE7_FIELDS] + [d[0] for d in IOD_FILE7_DENOMINATORS]

    def parse(sheets):
        if not sheets:
            raise RuntimeError("File 7 has no table with an LSOA code column")
        # A CSV is one table; in a workbook, the sheet that carries IDACI.
        best = max(sheets.values(), key=lambda d: (
            bool(iod_find_col(d.columns, "idaci")), len(d.columns)))
        return iod_parse_file7(best)

    return _iod_run("iod_extra", 7, IOD_EXTRA_PARQUET, want, (".csv", ".xlsx"), (),
                    IOD_FILE7_URL_OVERRIDE, parse)


def run_iod_indicators() -> pd.DataFrame:
    rule("IoD 2025: underlying indicators (MHCLG)")
    want = [s[0] for s in IOD_FILE8_SPECS]
    imd = _read_parquet_opt(DATA_DIR / "demographics" / "imd2025.parquet")
    if imd is None:
        imd = _read_parquet_opt(NATIONAL_DATA_DIR / "demographics" / "imd2025.parquet")
    if imd is None:
        warn("iod_indicators: no imd2025.parquet, so the directions cannot be checked")

    def parse(sheets):
        return iod_parse_file8(sheets, imd)

    return _iod_run("iod_indicators", 8, IOD_INDICATORS_PARQUET, want,
                    (".xlsx", ".csv", ".ods"), (("underlying", "indicators"),),
                    IOD_FILE8_URL_OVERRIDE, parse, optional=True)


# Weighted into wards and LAs by their own File 7 denominators, so a ward's
# IDACI is the share of its children, not an average over its residents.
IOD_WEIGHTS = {
    "idaci_pct":  ("iod_pop_children", "children", "children aged 0 to 15"),
    "idaopi_pct": ("iod_pop_older", "older", "people aged 60 and over"),
}
# Published per LSOA and never averaged: an average of ranks or deciles is not
# the rank or decile of anything.
IOD_LSOA_ONLY = ("idaci_rank", "idaci_decile", "idaopi_rank", "idaopi_decile")


def iod_area_figures(frames, lsoa_to_area: dict, pop_by_lsoa: dict, *,
                     what: str = "ward") -> tuple:
    """({area: {key: value}}, {key: weight}) for the IoD 2025 extra and
    underlying figures, from their LSOAs.

    IDACI and IDAOPI are weighted by the File 7 children aged 0 to 15 and
    people aged 60 and over, which are their own denominators, so an area's
    figure is its income-deprived children over its children. Without those
    columns they fall back to residents, with a warning, and the weight
    returned says "residents" so the map can say so. Everything else is
    weighted by Census 2021 residents, as the IMD domain scores are. Ranks,
    deciles and the denominators themselves are not carried up."""
    out: dict = {}
    used: dict = {}
    for df in frames:
        if df is None or df.empty or "LSOA21CD" not in df.columns:
            continue
        codes = df["LSOA21CD"].astype(str)
        for col in df.columns:
            if col == "LSOA21CD" or col.startswith("iod_pop_") or col in IOD_LSOA_ONLY:
                continue
            vals = pd.to_numeric(df[col], errors="coerce")
            per = {lc: float(v) for lc, v in zip(codes, vals) if pd.notna(v)}
            if not per:
                continue
            wcol, short, words = IOD_WEIGHTS.get(col, (None, "residents", "residents"))
            weights = {}
            if wcol and wcol in df.columns:
                weights = {lc: float(w) for lc, w in zip(codes, pd.to_numeric(df[wcol], errors="coerce"))
                           if pd.notna(w) and w > 0}
            if weights:
                got = lsoa_means_to_areas(per, lsoa_to_area, weights, what=f"{what} {col}",
                                          weight=words, source="File 7")
                used[col] = short
            else:
                if wcol:
                    warn(f"{what} {col}: File 7 gave no {words} for the LSOAs, so it is "
                         "weighted by Census 2021 residents instead")
                got = lsoa_means_to_areas(per, lsoa_to_area, pop_by_lsoa, what=f"{what} {col}")
                used[col] = "residents"
            for area, v in got.items():
                out.setdefault(area, {})[col] = round(v, 2)
    return out, used


# ============================================================================
# SOURCE 3b: Census 2021  (Nomis bulk topic-summary tables, LSOA-level)
# ============================================================================
# We pull ~13 Topic Summary (TS) tables from the Nomis bulk endpoint, extract
# the LSOA-level CSV from each, and compute the per-LSOA metrics the map
# dropdowns expect (census_* keys). No manual download needed - Nomis is a
# public endpoint. First run downloads ~150 MB to .cache/census2021/.
# Column names inside each table vary, so we match by keyword substrings
# rather than exact names, which survives the periodic Nomis renames.
#
# Indicator -> table mapping:
#   census_population              TS001  (residents total)
#   census_under16_pct / working_age TS007 (single year of age), else TS007A
#   census_over65_pct / over85_pct TS007A (five-year age bands)
#   census_non_white_pct           TS021  (ethnic group)
#   census_born_outside_uk_pct     TS004  (country of birth)
#   census_good_health_pct / bad   TS037  (general health)
#   census_disability_any / lot    TS038  (disability, Equality Act)
#   census_provides_unpaid_care_pct / unpaid_care_50h_pct  TS039
#   census_hh_deprived_any_pct / hh_deprived_2plus_pct  TS011  (household
#                                  deprivation dimensions; TS044 is
#                                  accommodation type, which this once read)
#   census_no_car_pct              TS045
#   census_owned_pct / social_rented / private_rented  TS054  (tenure)
#   census_higher_managerial_pct / routine_semi_routine_pct /
#     never_worked_ltu_pct         TS062  (NS-SEC L1-L3, L12+L13, L14)
#   census_unemployed_pct / inactive_ltsick_pct  TS066
#   census_no_qual_pct / level4_qual_pct  TS067
#   census_pensioner_alone_pct / lone_parent_dep_pct / one_person_hh_pct  TS003
#   census_poor_english_pct        TS029  (proficiency in English)
#   census_overcrowded_pct / underoccupied_pct  TS052  (bedrooms)
#   census_eth_*_pct               TS021  (six detailed ethnic groups)

CENSUS_TABLES = [
    "TS001", "TS004", "TS007A", "TS021", "TS037", "TS038", "TS039",
    "TS011", "TS045", "TS054", "TS062", "TS066", "TS067",
    # Household composition, proficiency in English and occupancy rating
    # (bedrooms). Each is published for output areas upwards; TS029's output
    # area file may carry only the main language split, in which case wards
    # and LAs are built from its LSOA counts (see _census_ward_inputs).
    "TS003", "TS029", "TS052",
    # Phase-A expansion. Only tables that ship LSOA CSV + whose contents
    # match our intended indicators remain.
    #   TS025 = Household language (English/Welsh) — household-level.
    #           (Proficiency in English, per person, is TS029 above.)
    #   TS061 = Method of travel to work — matches intent.
    # Dropped (document here):
    #   TS022 (Religion)                — no LSOA CSV
    #   TS024 (Language)                — no LSOA CSV
    #   TS041 — "Number of households" count only, not composition
    #   TS059 — Hours worked, not accommodation type
    #   TS068 — Schoolchild/student indicator, not year of arrival
    "TS025", "TS061",
    # TS007 is age by single year, which ONS publishes for output areas and
    # LSOAs. It is the only way to the usual bands: TS007A's five-year bands
    # put 15 year olds with 16 to 19 year olds, so they give under 15 and
    # 15 to 64, not under 16 and 16 to 64 (see _census_frame).
    "TS007",
    # Note: TS009 was previously requested but its Nomis bulk zip has no
    # LSOA-level sheet. TS007A ("Age by five-year age bands") is the
    # canonical LSOA-granularity age source.
]


def _nomis_urls(tab_id: str) -> list[str]:
    """Nomis has shipped the bulk zips under two URL patterns. Try both."""
    t = tab_id.lower()
    return [
        f"https://www.nomisweb.co.uk/output/census/2021/census2021-{t}.zip",
        f"https://www.nomisweb.co.uk/output/census/2021/{t}-2021-1.zip",
    ]


def _fetch_census_table(tab_id: str, level: str = "lsoa") -> pd.DataFrame | None:
    """Cache-first download + return the LSOA (or OA, or MSOA) CSV as a
    DataFrame. None on failure.

    The Nomis bulk zip for each table carries one CSV per geography; level
    picks which. "oa" and "msoa" are matched on the file ending "-oa.csv" or
    "-msoa.csv" so neither picks up the LSOA file."""
    cache_dir = CACHE_DIR / "census2021"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_zip = cache_dir / f"{tab_id.lower()}.zip"

    need_download = not cache_zip.exists() or cache_zip.stat().st_size < 2048
    if not need_download:
        try:
            with zipfile.ZipFile(cache_zip) as z:
                z.namelist()
        except zipfile.BadZipFile:
            need_download = True

    if need_download:
        sess = browser_session(referer="https://www.nomisweb.co.uk/")
        downloaded = False
        for url in _nomis_urls(tab_id):
            try:
                r = sess.get(url, timeout=180)
                if r.status_code == 200 and len(r.content) > 2048:
                    cache_zip.write_bytes(r.content)
                    info(f"  {tab_id}: downloaded {len(r.content)/1e6:.1f} MB")
                    downloaded = True
                    break
            except requests.RequestException:
                continue
        if not downloaded:
            warn(f"  {tab_id}: all URL patterns failed")
            return None

    try:
        with zipfile.ZipFile(cache_zip) as z:
            names = z.namelist()
            if level in ("oa", "msoa"):
                lsoa_name = next((n for n in names if n.lower().endswith(f"-{level}.csv")), None)
            else:
                lsoa_name = next(
                    (n for n in names if "lsoa" in n.lower() and n.endswith(".csv")),
                    None,
                )
            if not lsoa_name:
                warn(f"  {tab_id}: no {level.upper()} CSV inside zip ({names[:3]}...)")
                return None
            with z.open(lsoa_name) as f:
                return pd.read_csv(f, low_memory=False)
    except (zipfile.BadZipFile, pd.errors.EmptyDataError):
        warn(f"  {tab_id}: zip/CSV corrupt - delete .cache/census2021/{tab_id.lower()}.zip and rerun")
        return None


def _cen_code_col(df: pd.DataFrame) -> str | None:
    return next(
        (c for c in df.columns if c.strip().lower() in
         ("geography code", "lsoa code", "geographycode", "lsoa21cd", "2021 super output area - lower layer")),
        None,
    )


def _cen_find(df: pd.DataFrame, *kws, exclude=()) -> str | None:
    """First column whose lowercased name contains ALL kws and no excludes."""
    for c in df.columns:
        cl = c.lower()
        if all(k.lower() in cl for k in kws) and not any(e.lower() in cl for e in exclude):
            return c
    return None


def _cen_findall(df: pd.DataFrame, *kws, exclude=()) -> list[str]:
    """All columns whose lowercased name contains ALL kws and no excludes,
    with parent/child de-duplication.

    Nomis Census tables use ": " as a hierarchy separator, so the naive
    match for "owned" in TS054 hits BOTH the "Tenure of household: Owned"
    parent AND its "...: Owned: Owns outright" / "...: Owns with a mortgage"
    children. Summing all three double-counts the parent.

    Resolution: when a matched column is a strict descendant of another
    matched column (same prefix followed by ": "), drop the descendant.
    This keeps parent totals and returns leaves only when no parent
    matched the keywords.
    """
    raw = []
    for c in df.columns:
        cl = c.lower()
        if all(k.lower() in cl for k in kws) and not any(e.lower() in cl for e in exclude):
            raw.append(c)
    if len(raw) <= 1:
        return raw
    # Drop any column that is a strict descendant of another matched column.
    # Compared without the "; measures: Value" ending some Nomis files add,
    # which otherwise sits between a parent's name and its children's: the
    # October 2026 refresh counted TS003's one person households three times
    # over (the group and both of its parts), giving wards up to 125%.
    out = []
    for c in raw:
        is_descendant = False
        cl = _cen_label(c)
        for other in raw:
            if other == c:
                continue
            if cl.startswith(_cen_label(other) + ": "):
                is_descendant = True
                break
        if not is_descendant:
            out.append(c)
    return out


def _cen_label(c: str) -> str:
    """A column name without the "; measures: Value" some Nomis files add."""
    return re.sub(r";\s*measures:.*$", "", str(c), flags=re.I).strip()


def _cen_leaf(c: str) -> str:
    """The last segment of a Nomis label, lower case, with a typographic
    minus read as a hyphen: "Occupancy rating for bedrooms: -1" -> "-1"."""
    return _cen_label(c).rsplit(":", 1)[-1].strip().lower().replace("−", "-")


def _cen_leaf_col(df: pd.DataFrame, head: str, parent: str, leaf: str) -> str | None:
    """The column labelled "<head>: <parent...>: <leaf>", exactly three
    segments, whose parent starts with `parent` and whose leaf is `leaf`
    (all compared in lower case)."""
    for c in df.columns:
        parts = [p.strip().lower() for p in _cen_label(c).split(":")]
        if (len(parts) == 3 and parts[0] == head and parts[1].startswith(parent)
                and parts[2] == leaf):
            return c
    return None


def _cen_group_cols(df: pd.DataFrame, *kws) -> list[str]:
    """The columns that make up one group, counted once: its own total when
    the table has one ("...: One-person household: Total"), otherwise the
    group's parent or, failing that, its leaves (_cen_findall)."""
    cols = _cen_findall(df, *kws)
    tot = [c for c in cols if _cen_leaf(c) == "total"]
    return tot[:1] if tot else cols


# The detailed ethnic groups taken from TS021: key -> (parent, leaf).
CENSUS_ETHNIC_DETAIL = {
    "census_eth_gypsy_traveller_pct": ("white", "gypsy or irish traveller"),
    "census_eth_roma_pct":            ("white", "roma"),
    "census_eth_bangladeshi_pct":     ("asian", "bangladeshi"),
    "census_eth_pakistani_pct":       ("asian", "pakistani"),
    "census_eth_black_african_pct":   ("black", "african"),
    "census_eth_black_caribbean_pct": ("black", "caribbean"),
}

# What _census_frame could not build, by level ("lsoa", "msoa", "oa").
_CENSUS_MISSES: dict = {}
# Keys ONS may not publish for output areas: the OA files carry less detail
# (TS029's proficiency bands, and the fact-check could not confirm TS021's
# leaf columns there). Their wards and LAs come from LSOA counts when the OA
# file lacks them, so the OA build is not marked partial for these.
CENSUS_OA_MAY_LACK = ("census_poor_english_pct", "TS029:") + tuple(CENSUS_ETHNIC_DETAIL)


def _cen_pct(df: pd.DataFrame, num_cols: list[str], den_col: str) -> "pd.Series":
    """Compute (sum(numerator) / denominator) * 100, as a float Series."""
    num, den = _cen_counts(df, num_cols, den_col)
    return (num / den.replace(0, pd.NA)) * 100


def _cen_counts(df: pd.DataFrame, num_cols: list[str], den_col: str):
    """The numerator count (the numerator columns summed) and the
    denominator count behind one percentage, as two float Series."""
    num = df[num_cols].apply(pd.to_numeric, errors="coerce").sum(axis=1)
    den = pd.to_numeric(df[den_col], errors="coerce")
    return num.astype(float), den.astype(float)


# Every census percentage is kept with the two counts it was made from, as
# <key>__num and <key>__den, so that larger areas are built by adding up the
# counts and dividing once. Averaging the percentages instead, weighted by
# residents, is wrong for a household measure such as no car or tenure, whose
# base is households: a ward of large families and one of single people have
# the same number of residents but very different numbers of households.
CEN_NUM, CEN_DEN = "__num", "__den"


def _census_split(df: "pd.DataFrame", code_col: str):
    """(figures, counts): the census frame as it has always been written, and
    the count columns on their own, both keyed by code_col."""
    cnt = [c for c in df.columns if c.endswith((CEN_NUM, CEN_DEN))]
    return df.drop(columns=cnt), df[[code_col] + cnt].copy()


def census_group_figures(figs: "pd.DataFrame", counts: "pd.DataFrame | None",
                         code_to_group: dict, code_col: str = "LSOA21CD",
                         stale_out: "list | None" = None) -> dict:
    """Census figures for groups of areas: {group: {key: value}}.

    Each area (output area or LSOA) goes to the group code_to_group gives it.
    A percentage is the group's summed numerator over its summed denominator,
    from the areas that have both counts. census_population is summed. A
    percentage whose counts are not in the counts frame (a parquet written
    before the counts were kept) falls back to the old average of the area
    percentages weighted by residents, with a warning, so a stale file still
    gives figures. The keys that fell back are appended to stale_out when it
    is given, so the caller can record how the figures were made."""
    out: dict = {}
    if figs is None or figs.empty:
        return out
    f = figs.copy()
    f[code_col] = f[code_col].astype(str)
    f["_g"] = f[code_col].map(code_to_group)
    f = f[f["_g"].notna()]
    if f.empty:
        return out
    pop = (pd.to_numeric(f["census_population"], errors="coerce")
           if "census_population" in f.columns else pd.Series(float("nan"), index=f.index))
    if pop.notna().any():
        for g, v in pop[pop > 0].groupby(f["_g"]).sum().items():
            out.setdefault(g, {})["census_population"] = int(round(float(v)))

    c = None
    if counts is not None and not counts.empty:
        c = counts.copy()
        c[code_col] = c[code_col].astype(str)
        c["_g"] = c[code_col].map(code_to_group)
        c = c[c["_g"].notna()]
    stale = []
    for key in [k for k in f.columns if k.endswith("_pct")]:
        nk, dk = key + CEN_NUM, key + CEN_DEN
        if c is not None and nk in c.columns and dk in c.columns:
            num = pd.to_numeric(c[nk], errors="coerce")
            den = pd.to_numeric(c[dk], errors="coerce")
            use = num.notna() & den.notna() & (den > 0)
            if not use.any():
                continue
            n = num[use].groupby(c.loc[use, "_g"]).sum()
            d = den[use].groupby(c.loc[use, "_g"]).sum()
            for g in d.index:
                if d[g] > 0:
                    out.setdefault(g, {})[key] = round(float(n[g]) / float(d[g]) * 100, 2)
        else:
            stale.append(key)
            v = pd.to_numeric(f[key], errors="coerce")
            w = pop.where(pop > 0, 1.0).fillna(1.0)
            use = v.notna()
            if not use.any():
                continue
            n = (v[use] * w[use]).groupby(f.loc[use, "_g"]).sum()
            d = w[use].groupby(f.loc[use, "_g"]).sum()
            for g in d.index:
                if d[g] > 0:
                    out.setdefault(g, {})[key] = round(float(n[g]) / float(d[g]), 2)
    if stale_out is not None:
        stale_out.extend(stale)
    if stale:
        warn(f"census: no counts for {len(stale)} percentages ({', '.join(stale[:3])}...); "
             f"those are averaged by residents until the census source is rerun")
    return out


def run_census2021() -> pd.DataFrame:
    rule("Census 2021 (Nomis bulk, LSOA)")
    _CENSUS_AGE_PROBLEMS.clear()
    out = _census_frame("lsoa")
    if out is None or out.empty:
        return pd.DataFrame()
    partial = []
    if _CENSUS_AGE_PROBLEMS:
        # Usable, with the older bands the map labels honestly, but not what
        # was meant to be built, so the refresh workflow raises it.
        partial.append("under 16 and working age built from TS007A's "
                       "0 to 14 and 15 to 64: " + "; ".join(_CENSUS_AGE_PROBLEMS))
    if _CENSUS_MISSES.get("lsoa"):
        # A table that did not download or a label that did not match: the
        # indicators it feeds are left out of this build (the map offers no
        # indicator without figures), and the refresh workflow raises it.
        partial.append("not built at LSOA level: " + "; ".join(_CENSUS_MISSES["lsoa"]))
    if partial:
        _SOURCE_PARTIAL["census"] = " | ".join(partial)
    # The figures file keeps its shape for everything that reads it; the
    # counts behind each percentage go beside it, for the ward and LA build.
    out, counts = _census_split(out, "LSOA21CD")
    out_path = DATA_DIR / "demographics" / "census2021.parquet"
    out = write_parquet_guarded(out_path, out, source="census")
    write_parquet_guarded(DATA_DIR / "demographics" / "census2021_counts.parquet",
                          counts, source="census")
    ok(f"census2021: {len(out):,} LSOAs x {len(out.columns)-1} indicators -> {out_path.relative_to(REPO_ROOT)}")

    # The same figures as ONS publishes them for MSOAs, from the same zips.
    # An MSOA is whole LSOAs, so these agree with the LSOA figures added up;
    # taking ONS's own file means nothing is derived here.
    msoa = _census_frame("msoa")
    if msoa is not None and not msoa.empty:
        msoa = _census_split(msoa, "LSOA21CD")[0]
        msoa = msoa.rename(columns={"LSOA21CD": "MSOA21CD"})
        want = set(get_lsoa_to_msoa().values())
        msoa = msoa[msoa["MSOA21CD"].isin(want)]
        if len(msoa):
            m_path = DATA_DIR / "demographics" / "census2021_msoa.parquet"
            msoa = write_parquet_guarded(m_path, msoa, source="census")
            ok(f"census2021: {len(msoa):,} MSOAs -> {m_path.relative_to(REPO_ROOT)}")
        else:
            warn("census2021: no in-scope MSOAs in the MSOA files")
    return out


# England's age structure at the 2021 Census as ONS publishes it, in per cent
# of usual residents, for checking the bands built from TS007. 0 to 14 is
# ONS's own figure (TS007A gives 17.42); 0 to 15 adds the 15 year olds, about
# 1.15 points; 65 and over is the headline 18.4.
ONS_ENGLAND_AGE_SHARES = {"0-14": 17.4, "0-15": 18.6, "65+": 18.4}
ONS_AGE_TOLERANCE = 0.1
_CENSUS_AGE_PROBLEMS: list = []


def _census_single_years(t: "pd.DataFrame | None"):
    """(table, {age: column}, total column) for TS007, or None unless every
    age from 0 to 99 and the total are there."""
    if t is None or t.empty:
        return None
    ages: dict = {}
    for c in t.columns:
        cl = c.lower()
        if " to " in cl:
            continue
        if re.search(r"aged under 1 year", cl):
            ages[0] = c
            continue
        m = re.search(r"aged (\d+) years?(?: and over)?(?:;|$)", cl.strip())
        if m:
            ages.setdefault(int(m.group(1)), c)
    total = _cen_find(t, "total")
    if not total or any(a not in ages for a in range(0, 100)) or not _cen_code_col(t):
        return None
    return t, ages, total


def _census_age_check(t7, code_col, ages, total_col, t7a=None) -> "str | None":
    """Why TS007's England totals cannot be trusted, or None. Compares the
    shares aged 0 to 14, 0 to 15 and 65 and over across England's areas (codes
    beginning E) with ONS's headline shares, and the 0 to 14 share with the
    one TS007A gives for the same areas."""
    eng = t7[t7[code_col].astype(str).str.strip().str.startswith("E")]
    if eng.empty:
        return "no English areas in TS007"
    tot = pd.to_numeric(eng[total_col], errors="coerce").sum()
    if not tot:
        return "TS007 England total is zero"
    def share(lo, hi):
        cols = [ages[a] for a in ages if lo <= a <= hi]
        return float(eng[cols].apply(pd.to_numeric, errors="coerce").sum().sum()) / tot * 100
    got = {"0-14": share(0, 14), "0-15": share(0, 15), "65+": share(65, 200)}
    off = [f"{k} {got[k]:.2f}% against ONS {v}%" for k, v in ONS_ENGLAND_AGE_SHARES.items()
           if abs(got[k] - v) > ONS_AGE_TOLERANCE + 0.05]   # ONS rounds to 0.1
    if off:
        return "England " + "; ".join(off)
    if t7a is not None and not t7a.empty:
        cc = _cen_code_col(t7a)
        cols = []
        for kw in ("aged 4 years and under", "aged 5 to 9", "aged 10 to 14"):
            cols += _cen_findall(t7a, kw, exclude=("and over",))
        den = _cen_find(t7a, "total")
        if cc and cols and den:
            e = t7a[t7a[cc].astype(str).str.strip().str.startswith("E")]
            d = pd.to_numeric(e[den], errors="coerce").sum()
            if d:
                a = float(e[cols].apply(pd.to_numeric, errors="coerce").sum().sum()) / d * 100
                if abs(a - got["0-14"]) > ONS_AGE_TOLERANCE:
                    return f"0 to 14 is {got['0-14']:.2f}% in TS007 but {a:.2f}% in TS007A"
    info(f"  TS007: England aged 0 to 15 {got['0-15']:.2f}%, 65 and over {got['65+']:.2f}%, "
         f"within {ONS_AGE_TOLERANCE} points of ONS")
    return None


def _record_census_age_bands(level: str, bands: str) -> None:
    """Note which ages the under 16 and working age figures at this level
    cover, beside the census parquets, for build_indicator_stats."""
    _record_census_level("census2021_age_bands.json", level, bands)


def _record_census_level(name: str, level: str, value: str) -> None:
    """Note one fact about how a census level was built, in a small JSON
    file of {level: value} beside the census parquets."""
    p = DATA_DIR / "demographics" / name
    try:
        rec = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        rec = {}
    rec[level] = value
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(rec, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    except OSError as e:
        warn(f"census2021: could not record {name} ({e})")


def _census_frame(level: str = "lsoa") -> pd.DataFrame | None:
    """Every census indicator for every area of one geography, keyed by a
    column called LSOA21CD whatever the level (the OA build renames it).
    One set of rules for both levels, so an output area and its LSOA are
    computed the same way."""
    # What could not be built at this level, and why: a table that did not
    # download, or columns that did not match. run_census2021 marks the
    # source partial when the LSOA level has any, so the refresh workflow
    # raises it rather than the key quietly going missing from the map.
    misses = _CENSUS_MISSES.setdefault(level, [])
    misses.clear()

    def miss(msg: str) -> None:
        warn(f"  {msg}")
        misses.append(msg)

    tables: dict[str, pd.DataFrame] = {}
    for tab in CENSUS_TABLES:
        df = _fetch_census_table(tab, level)
        if df is not None:
            tables[tab] = df
            info(f"  {tab}: {len(df):,} rows x {len(df.columns)} cols")
        elif tab not in ("TS007",):   # TS007 has its own fallback to TS007A
            misses.append(f"{tab}: no {level.upper()} file")

    if "TS001" not in tables:
        warn("census2021: TS001 (population) missing - can't anchor, skipping")
        return pd.DataFrame()

    t001 = tables["TS001"]
    code_col = _cen_code_col(t001)
    if not code_col:
        warn("census2021: TS001 has no recognisable LSOA code column")
        return pd.DataFrame()

    pop_col = (_cen_find(t001, "observation")
               or _cen_find(t001, "residence type", "total")
               or _cen_find(t001, "total", "residents")
               or _cen_find(t001, "age: total")
               or _cen_find(t001, "age", "total")
               or _cen_find(t001, "total"))
    out = pd.DataFrame({
        "LSOA21CD": t001[code_col].astype(str).str.strip(),
    })
    if pop_col:
        out["census_population"] = pd.to_numeric(t001[pop_col], errors="coerce")

    # Helper: compute one metric from a table and merge onto `out` by LSOA.
    # silent=True suppresses the "column match failed" warning — useful when
    # the caller has an explicit fallback branch right after.
    # expect is the number of numerator columns the keywords must find. A
    # different number means the publisher's labels are not the ones the
    # keywords were written for, and the figure would count the wrong
    # people, so nothing is written and the miss is recorded.
    def attach(key: str, tab: str, num_kw_list: list[tuple], den_kw=("total",),
               den_exclude=(), num_exclude=(), silent: bool = False,
               expect: "int | None" = None):
        nonlocal out
        t = tables.get(tab)
        if t is None:
            return
        cc = _cen_code_col(t)
        if not cc:
            if not silent:
                miss(f"{tab}/{key}: no area code column")
            return
        num_cols: list[str] = []
        for kws in num_kw_list:
            for c in _cen_findall(t, *kws, exclude=num_exclude):
                if c not in num_cols:
                    num_cols.append(c)
        den_col = _cen_find(t, *den_kw, exclude=den_exclude)
        if not num_cols or not den_col:
            if not silent:
                miss(f"{tab}/{key}: num={len(num_cols)} den={den_col} - column match failed")
            return
        if expect is not None and len(num_cols) != expect:
            miss(f"{tab}/{key}: expected {expect} numerator column(s), found "
                 f"{len(num_cols)} ({'; '.join(num_cols[:4])}) - not written")
            return
        num, den = _cen_counts(t, num_cols, den_col)
        merge_counts(key, t[cc], num, den)

    # One percentage from its two counts, merged onto `out` with the counts
    # beside it (see CEN_NUM).
    def merge_counts(key: str, codes, num, den):
        nonlocal out
        num = pd.to_numeric(num, errors="coerce").astype(float)
        den = pd.to_numeric(den, errors="coerce").astype(float)
        df = pd.DataFrame({
            "LSOA21CD": codes.astype(str).str.strip().values,
            key: ((num / den.replace(0, pd.NA)) * 100).values,
            key + CEN_NUM: num.values,
            key + CEN_DEN: den.values,
        })
        out = out.merge(df, on="LSOA21CD", how="left")

    # Age ----------------------------------------------------------------
    # Under 16 and working age (16 to 64) come from TS007, single years of
    # age, when it is there and its England totals agree with ONS's headline
    # shares. Otherwise they come from TS007A's five-year bands, which can
    # only give 0 to 14 and 15 to 64: the old keyword "aged 15 years" never
    # matched its "Aged 15 to 19 years" band, so "under 16" was 0 to 14 for
    # years. Which one was built is written beside the parquet
    # (census2021_age_bands.json) and carried to the map, which labels the
    # two figures by it.
    _age_tab = "TS007A" if "TS007A" in tables else "TS009"
    bands = "0-14"
    single = _census_single_years(tables.get("TS007"))
    if single is not None:
        t7, ages, total_col = single
        cc7 = _cen_code_col(t7)
        under = [ages[a] for a in range(0, 16)]
        work = [ages[a] for a in range(16, 65)]
        problem = _census_age_check(t7, cc7, ages, total_col, tables.get("TS007A"))
        if problem:
            warn(f"census2021: TS007 single years not used ({problem}); "
                 f"under 16 and working age fall back to TS007A's 0 to 14 and 15 to 64")
            _CENSUS_AGE_PROBLEMS.append(problem)
        else:
            n_u, d_u = _cen_counts(t7, under, total_col)
            merge_counts("census_under16_pct", t7[cc7], n_u, d_u)
            n_w, d_w = _cen_counts(t7, work, total_col)
            merge_counts("census_working_age_pct", t7[cc7], n_w, d_w)
            bands = "0-15"
    if bands != "0-15":
        attach("census_under16_pct", _age_tab, [
            ("aged 4 years and under",), ("aged 5 to 9",), ("aged 10 to 14",),
        ], num_exclude=("and over",))
    # Children under 5 (health-need indicator: child immunisation, HV caseload).
    # Only TS009's single-year bands give this directly; on TS007A the
    # youngest band is 0-15, so this stays blank there.
    attach("census_under5_pct", _age_tab, [
        ("aged 4 years and under",),
    ], num_exclude=("and over",))
    attach("census_over65_pct", _age_tab, [
        ("aged 65",), ("aged 70 to 74",), ("aged 75 to 79",),
        ("aged 80 to 84",), ("aged 85 years and over",),
    ])
    # Frail-elderly 85+ (falls, dementia, end-of-life care demand)
    attach("census_over85_pct", _age_tab, [
        ("aged 85 years and over",),
    ])
    # Derived working age (15 to 64 from TS007A's bands) when TS007 did not
    # give 16 to 64 directly.
    if (bands != "0-15" and "census_under16_pct" in out.columns
            and "census_over65_pct" in out.columns):
        wa = 100 - pd.to_numeric(out["census_under16_pct"], errors="coerce") \
                 - pd.to_numeric(out["census_over65_pct"], errors="coerce")
        out["census_working_age_pct"] = wa
        # Both bands come from the same table and base, so the working-age
        # count is that base less the two counts.
        u, o = "census_under16_pct", "census_over65_pct"
        if all(k in out.columns for k in (u + CEN_NUM, u + CEN_DEN, o + CEN_NUM)):
            out["census_working_age_pct" + CEN_NUM] = (
                out[u + CEN_DEN] - out[u + CEN_NUM] - out[o + CEN_NUM])
            out["census_working_age_pct" + CEN_DEN] = out[u + CEN_DEN]

    # TS004 country of birth ---------------------------------------------
    # First attempt matches a "Not born in the UK" column directly; if that
    # column doesn't exist we fall through to the 1 - UK derivation below,
    # so suppress the column-match warning on the first pass.
    attach("census_born_outside_uk_pct", "TS004",
           [("not born in the uk",)], silent=True)
    if "census_born_outside_uk_pct" not in out.columns:
        # Fallback: 1 - UK
        t = tables.get("TS004")
        if t is not None:
            cc = _cen_code_col(t)
            uk_col = _cen_find(t, "united kingdom", exclude=("not",))
            tot_col = _cen_find(t, "total")
            if cc and uk_col and tot_col:
                tot = pd.to_numeric(t[tot_col], errors="coerce")
                merge_counts("census_born_outside_uk_pct", t[cc],
                             tot - pd.to_numeric(t[uk_col], errors="coerce"), tot)

    # TS021 ethnic group -> non-white = 1 - (white/total) -----------------
    # IMPORTANT: the "white" column must be the PARENT "Ethnic group: White"
    # total — NOT a sub-category like "Mixed ... White and Asian" (which is
    # what a naive substring match picks up first in Nomis column order).
    t = tables.get("TS021")
    if t is not None:
        cc = _cen_code_col(t)
        tot_col = _cen_find(t, "total")
        # TS021 has five top-level ethnic-group categories whose Nomis column
        # names start with "Ethnic group: <cat>" and have exactly one colon
        # (sub-categories like "Ethnic group: White: Irish" have two). We match
        # each parent by prefix and use column-count==1 to filter out leaves.
        # Parents of interest (as they appear in Nomis, verbatim):
        #   Ethnic group: White
        #   Ethnic group: Asian, Asian British or Asian Welsh
        #   Ethnic group: Black, Black British, Black Welsh, Caribbean or African
        #   Ethnic group: Mixed or Multiple ethnic groups
        #   Ethnic group: Other ethnic group
        def _parent_col(prefix_kw: str, must_include_all: tuple = ()) -> str | None:
            """Find the top-level TS021 column whose name starts with
            "Ethnic group: <prefix_kw...>" and has exactly one ":".
            must_include_all tightens the match when a prefix could be
            ambiguous (e.g. "white" vs "white and asian")."""
            pk = prefix_kw.lower()
            for c in t.columns:
                cl = c.lower()
                if not cl.startswith("ethnic group:"):
                    continue
                if c.count(":") != 1:
                    continue
                head = cl.split(":", 1)[1].strip()
                if not head.startswith(pk):
                    continue
                if must_include_all and not all(k in cl for k in must_include_all):
                    continue
                return c
            return None

        parents = {
            "census_white_pct": _parent_col("white"),
            "census_asian_pct": _parent_col("asian"),
            "census_black_pct": _parent_col("black"),
            "census_mixed_pct": _parent_col("mixed"),
            "census_other_ethnic_pct": _parent_col("other"),
        }
        if cc and tot_col:
            den = pd.to_numeric(t[tot_col], errors="coerce")
            for key, col in parents.items():
                if not col:
                    miss(f"TS021/{key}: parent column not found")
                    continue
                merge_counts(key, t[cc], pd.to_numeric(t[col], errors="coerce"), den)
            # Derive non-white as 100 - white (kept for back-compat with the UI).
            if parents.get("census_white_pct"):
                merge_counts("census_non_white_pct", t[cc],
                             den - pd.to_numeric(t[parents["census_white_pct"]], errors="coerce"), den)
            # Six of the nineteen detailed groups, each a leaf column with two
            # colons under its parent, matched on the parent and the leaf's
            # own name rather than on column order:
            #   Ethnic group: White: Gypsy or Irish Traveller
            #   Ethnic group: White: Roma                (new in 2021)
            #   Ethnic group: Asian, Asian British or Asian Welsh: Bangladeshi
            #   Ethnic group: Asian, Asian British or Asian Welsh: Pakistani
            #   Ethnic group: Black, Black British, Black Welsh, Caribbean or African: African
            #   Ethnic group: Black, Black British, Black Welsh, Caribbean or African: Caribbean
            # The base is the same total as the five parents. Gypsy or Irish
            # Traveller and Roma counts are small, and ONS perturbs small
            # counts, so the map opens them at ward level.
            for key, (parent, leaf) in CENSUS_ETHNIC_DETAIL.items():
                col = _cen_leaf_col(t, "ethnic group", parent, leaf)
                if not col:
                    miss(f"TS021/{key}: no '{parent}: {leaf}' column")
                    continue
                merge_counts(key, t[cc], pd.to_numeric(t[col], errors="coerce"), den)

    # TS037 general health -----------------------------------------------
    # "Very good health" + "Good health" for good; "Bad health" + "Very bad health" for bad.
    # The `exclude` guards stop "good health" from also catching "very good health" twice
    # (but our dedup in attach() already does that via findall + set-insert).
    attach("census_good_health_pct", "TS037",
           [("very good health",), ("good health",)],
           num_exclude=("fair",))
    attach("census_bad_health_pct", "TS037",
           [("bad health",), ("very bad health",)])

    # TS038 disability ---------------------------------------------------
    attach("census_disability_lot_pct", "TS038",
           [("limited a lot",)])
    attach("census_disability_any_pct", "TS038",
           [("limited a lot",), ("limited a little",)])

    # TS039 unpaid care --------------------------------------------------
    # Easier: pick "provides NO unpaid care" and do 1 - that/total
    t = tables.get("TS039")
    if t is not None:
        cc = _cen_code_col(t)
        none_col = _cen_find(t, "provides no unpaid care") or _cen_find(t, "no unpaid care")
        tot_col = _cen_find(t, "total")
        if cc and none_col and tot_col:
            tot = pd.to_numeric(t[tot_col], errors="coerce")
            merge_counts("census_provides_unpaid_care_pct", t[cc],
                         tot - pd.to_numeric(t[none_col], errors="coerce"), tot)
    # 50 or more hours a week, over the same base (residents aged 5 and
    # over). The hour bands come as parents with finer bands under them
    # ("Provides 20 to 49 hours ...: Provides 20 to 34 hours ..."), and
    # _cen_findall keeps a parent over its children, so a band is never
    # counted twice; expect=1 stops anything else slipping in.
    attach("census_unpaid_care_50h_pct", "TS039", [("50 or more hours",)], expect=1)

    # TS011 household deprivation ----------------------------------------
    # ONS counts a household as deprived in a dimension (education,
    # employment, health and disability, housing) from its other Census
    # answers, and publishes how many dimensions each household is deprived
    # in: "Household is not deprived in any dimension", "Household is
    # deprived in one dimension", and so on up to four. Any dimension is the
    # total less those deprived in none; two or more is two, three and four
    # added up. Until 2026 this read TS044, which is accommodation type, so
    # its columns never matched and no figure was ever written.
    t = tables.get("TS011")
    if t is not None:
        cc = _cen_code_col(t)
        tot_col = _cen_find(t, "total")
        none_col = _cen_find(t, "not deprived in any dimension")
        multi = [c for n in ("two", "three", "four")
                 for c in _cen_findall(t, f"deprived in {n} dimensions")]
        if not (cc and tot_col and none_col):
            miss(f"TS011/census_hh_deprived_any_pct: total={tot_col} none={none_col} - column match failed")
        else:
            tot = pd.to_numeric(t[tot_col], errors="coerce")
            merge_counts("census_hh_deprived_any_pct", t[cc],
                         tot - pd.to_numeric(t[none_col], errors="coerce"), tot)
            if len(multi) != 3:
                miss(f"TS011/census_hh_deprived_2plus_pct: expected the two, three and four "
                     f"dimension columns, found {len(multi)} - not written")
            else:
                n2, d2 = _cen_counts(t, multi, tot_col)
                merge_counts("census_hh_deprived_2plus_pct", t[cc], n2, d2)

    # TS045 car/van ------------------------------------------------------
    attach("census_no_car_pct", "TS045",
           [("no cars or vans",)])

    # TS054 tenure -------------------------------------------------------
    # Column names: "Tenure: Owned: Owns outright"/"Owns with a mortgage"/"Shared ownership"
    #               "Social rented: ..."/"Private rented: ..."/"Lives rent free"
    attach("census_owned_pct", "TS054",
           [("owned",)],
           num_exclude=("shared",))
    attach("census_social_rented_pct", "TS054",
           [("social rented",)])
    attach("census_private_rented_pct", "TS054",
           [("private rented",)])

    # TS062 NS-SEC -------------------------------------------------------
    # The table publishes its groups by their L numbers, each under
    # "National Statistics Socio-economic Classification (NS-SEC): ":
    #   L1, L2 and L3 Higher managerial, administrative and professional
    #   L4, L5 and L6 Lower managerial, administrative and professional
    #   L7 Intermediate occupations
    #   L8 and L9 Small employers and own account workers
    #   L10 and L11 Lower supervisory and technical occupations
    #   L12 Semi-routine occupations
    #   L13 Routine occupations
    #   L14.1 and L14.2 Never worked and long-term unemployed
    #   L15 Full-time students
    # Routine and semi-routine is L12 and L13. The keywords used to include
    # "l7 " and "l8 ", which took in intermediate occupations and small
    # employers too and roughly doubled the figure (London LSOAs averaged
    # 38%); the 7 and 8 of the analytic classes are not these L numbers.
    attach("census_higher_managerial_pct", "TS062",
           [("l1, l2 and l3",), ("higher managerial",)])
    attach("census_routine_semi_routine_pct", "TS062",
           [("routine occupations",)], expect=2)
    # L14.1 and L14.2 are one category in this table, so "never worked" and
    # "long-term unemployed" cannot be separated here.
    attach("census_never_worked_ltu_pct", "TS062", [("never worked",)], expect=1)

    # TS066 economic activity -------------------------------------------
    # First attempt scopes the denominator to "economically active"; if the
    # column schema doesn't match we fall through to the "all categories"
    # total. Suppress the first-pass warning since the fallback is expected.
    attach("census_unemployed_pct", "TS066",
           [("unemployed",)],
           den_kw=("economically active", "total"), den_exclude=(),
           silent=True)
    if "census_unemployed_pct" not in out.columns:
        # Fallback: use "all categories" total
        attach("census_unemployed_pct", "TS066",
               [("unemployed",)])
    # Economically inactive because of long-term sickness or disability,
    # over all residents aged 16 and over (the table's total, the same base
    # as unemployment above). TS066 has no age split, so this cannot be
    # made a 16 to 64 rate. "Sick or disabled" avoids depending on the
    # hyphen in "Long-term".
    attach("census_inactive_ltsick_pct", "TS066", [("sick or disabled",)], expect=1)

    # TS067 qualifications ----------------------------------------------
    attach("census_no_qual_pct", "TS067",
           [("no qualifications",)])
    attach("census_level4_qual_pct", "TS067",
           [("level 4 qualifications",)])

    # TS003 household composition ----------------------------------------
    # Shares of all households. ONS writes "One-person household" with a
    # hyphen and the 2021 tables use 66 and over (State Pension age), so
    # the keywords are "person household" and "aged 66", which match the
    # label with or without the hyphen. The leaves are:
    #   One-person household: Aged 66 years and over / Other
    #   Single family household: All aged 66 years and over
    #   Single family household: ... Lone parent family: With dependent
    #     children / All children non-dependent
    #   Other household types: With dependent children / Other, including
    #     all full-time students and all aged 66 years and over
    # Lone parents living with their own parents fall under other household
    # types, so the lone parent figure is a lower bound.
    # The all households column is "Household composition: Total" in the
    # Nomis file (the October 2026 refresh found no "all households" in it),
    # and a group may carry its own Total, so the denominator is the Total
    # that names no group.
    ts003_den_ex = ("person household", "family", "other household")
    attach("census_pensioner_alone_pct", "TS003", [("person household", "aged 66")],
           den_exclude=ts003_den_ex, expect=1)
    attach("census_lone_parent_dep_pct", "TS003", [("lone parent", "dependent children")],
           num_exclude=("non-dependent",), den_exclude=ts003_den_ex, expect=1)
    t = tables.get("TS003")
    if t is not None:
        cc = _cen_code_col(t)
        den_col = _cen_find(t, "total", exclude=ts003_den_ex)
        one = _cen_group_cols(t, "person household")
        if not (cc and den_col and one):
            miss(f"TS003/census_one_person_hh_pct: num={len(one)} den={den_col} - column match failed")
        else:
            n1, d1 = _cen_counts(t, one, den_col)
            merge_counts("census_one_person_hh_pct", t[cc], n1, d1)

    # TS029 proficiency in English ---------------------------------------
    # Residents aged 3 and over whose main language is not English who
    # "Cannot speak English well" or "Cannot speak English", over the
    # table's total. ONS publishes less detail for output areas, so the OA
    # file may carry only the main language split; then this is missing at
    # OA level and wards and LAs are built from the LSOA counts instead.
    attach("census_poor_english_pct", "TS029", [("cannot speak english",)], expect=2)

    # TS052 occupancy rating for bedrooms --------------------------------
    # The bedrooms a household has against the bedrooms the bedroom standard
    # says it needs: +2 or more, +1, 0, -1, -2 or less. Overcrowded is -1 or
    # less; under-occupied is +2 or more. Matched on the last segment of the
    # label, so "+1" can never be read as "-1". TS052 rather than TS053
    # (rooms), whose room counts came from Valuation Office records.
    t = tables.get("TS052")
    if t is not None:
        cc = _cen_code_col(t)
        den_col = _cen_find(t, "total")
        over = [c for c in t.columns if re.match(r"-1(?!\d)|-2 or (less|fewer)", _cen_leaf(c))]
        under = [c for c in t.columns if re.match(r"\+2 or more", _cen_leaf(c))]
        if not (cc and den_col) or len(over) != 2:
            miss(f"TS052/census_overcrowded_pct: expected the -1 and -2 or less columns, "
                 f"found {len(over)} (total={den_col}) - not written")
        else:
            n_o, d_o = _cen_counts(t, over, den_col)
            merge_counts("census_overcrowded_pct", t[cc], n_o, d_o)
        if not (cc and den_col) or len(under) != 1:
            miss(f"TS052/census_underoccupied_pct: expected one +2 or more column, "
                 f"found {len(under)} (total={den_col}) - not written")
        else:
            n_u, d_u = _cen_counts(t, under, den_col)
            merge_counts("census_underoccupied_pct", t[cc], n_u, d_u)

    # Note on dropped Phase-A tables (documented for future revisit):
    #   TS022 (Religion)    — Nomis bulk zip has no LSOA CSV sheet.
    #   TS024 (Language)    — Nomis bulk zip has no LSOA CSV sheet.
    #   TS041               — LSOA CSV only carries "Number of households"
    #                         (total count), not one-person/lone-parent
    #                         breakdown. That breakdown is TS003, above.
    #   TS059               — LSOA CSV is hours worked, not accommodation
    #                         type; accommodation table isn't shipped at LSOA.
    #   TS068               — LSOA CSV is schoolchild/student indicator,
    #                         not year-of-arrival. Migration-arrival tables
    #                         aren't shipped at LSOA in the bulk zips.
    # If Nomis re-ships these at LSOA in future, restore the attach() calls.

    # TS025 household language (English/Welsh) --------------------------
    # TS025 is household language, as its rows show (proficiency in
    # English, per person, is TS029, above). The bulk LSOA zip ships:
    #   * "All adults in household have English/Welsh as a main language"
    #   * "At least one but not all adults..."
    #   * "No adults in household, but at least one person aged 3-15..."
    #   * "No people in household have English/Welsh as a main language"
    # We expose the top and bottom bands — "all adults English" and
    # "no people English" — as outreach/low-proficiency indicators.
    attach("census_english_hh_all_pct",  "TS025",
           [("all adults in household",)],
           den_kw=("total", "all households"))
    attach("census_english_hh_none_pct", "TS025",
           [("no people in household",)],
           den_kw=("total", "all households"))

    # TS061 method of travel to work ------------------------------------
    # Active travel = walking + bicycle. Car = driving car/van +
    # passenger in car/van.
    attach("census_active_travel_pct", "TS061",
           [("on foot",), ("bicycle",)])
    attach("census_car_to_work_pct",   "TS061",
           [("driving a car",), ("passenger in a car",)])
    attach("census_public_transport_pct","TS061",
           [("underground",), ("train",), ("bus",), ("taxi",)])

    # Clean up + save ----------------------------------------------------
    out = out.dropna(subset=["LSOA21CD"]).drop_duplicates(subset=["LSOA21CD"])

    # Round percentages to 2dp for smaller output
    for col in out.columns:
        if col.endswith("_pct"):
            out[col] = pd.to_numeric(out[col], errors="coerce").round(2)
    if "census_population" in out.columns:
        out["census_population"] = pd.to_numeric(out["census_population"], errors="coerce").astype("Int64")
    _record_census_age_bands(level, bands)
    # Which NS-SEC groups the routine figure at this level counts, so the
    # map can tell a file built since the L7 and L8 fix from one built
    # before it (census_routine_classes in indicators.js).
    _record_census_level("census2021_nssec.json", level,
                         "L12-L13" if "census_routine_semi_routine_pct" in out.columns else "")
    return out


# ============================================================================
# CENSUS 2021 BY OUTPUT AREA
#
# Output areas are the smallest census geography, about 125 households, and
# nest exactly inside 2021 LSOAs. Only the census is published at this level,
# so it is offered for census indicators alone. The figures come from the same
# Nomis bulk zips as the LSOA ones (each zip carries an OA file) and go
# through the same _census_frame rules. Boundaries are ONS Output Areas 2021
# (BGC), written one file per local authority so the map loads only the
# councils being looked at: London has about 26,000 output areas.
# ============================================================================
OA_BOUNDARY_SERVICES = ["Output_Areas_2021_EW_BGC_V2", "Output_Areas_2021_EW_BGC_V3",
                        "Output_Areas_December_2021_Boundaries_EW_BGC"]


def _oa_boundary_service() -> str | None:
    """The first OA 2021 BGC feature service ONS has, discovered from its
    service list if none of the known names answers."""
    for name in OA_BOUNDARY_SERVICES:
        try:
            r = requests.get(f"{ONS_SERVICES}/{name}/FeatureServer/0?f=json", timeout=60)
            if r.ok and "fields" in r.json():
                return name
        except Exception:
            continue
    try:
        r = requests.get(f"{ONS_SERVICES}?f=json", timeout=120)
        names = [x["name"] for x in r.json().get("services", [])]
        cands = sorted(n for n in names if re.match(r"(?i)output_areas?_(december_)?2021.*bgc", n))
        return cands[-1] if cands else None
    except Exception as e:
        warn(f"census_oa: could not list ONS services: {e}")
        return None


def run_census_oa() -> pd.DataFrame:
    rule("Census 2021 by output area (Nomis bulk; ONS OA boundaries)")
    lsoa_lk = get_lsoa_ward_lookup()               # {LSOA21CD: (WD25CD, LAD25CD)}
    lsoas = sorted(lsoa_lk)
    frame = _census_frame("oa")
    if frame is None or frame.empty:
        warn("census_oa: no OA census figures")
        return pd.DataFrame()
    # A key missing at OA level is built for wards and LAs from its LSOA
    # counts instead (_census_ward_inputs). ONS publishes less detail for
    # output areas, so for some keys that is expected; anything else is
    # raised as partial.
    oa_miss = [m for m in _CENSUS_MISSES.get("oa", [])
               if not any(k in m for k in CENSUS_OA_MAY_LACK)]
    if oa_miss:
        _SOURCE_PARTIAL["census_oa"] = ("not built at output area level (wards use "
                                        "LSOAs for these): " + "; ".join(oa_miss))
    frame = frame.rename(columns={"LSOA21CD": "OA21CD"})
    frame, counts = _census_split(frame, "OA21CD")
    frame = frame.set_index("OA21CD")
    keys = [c for c in frame.columns if c.startswith("census_")]
    # Kept for the ward build, which builds census wards from the counts of
    # their output areas (see get_oa_to_ward). Only output areas in scope.
    oa_ward = get_oa_to_ward()
    if oa_ward:
        scope = set(oa_ward)
        scoped = frame[frame.index.isin(scope)].reset_index()
        write_parquet_guarded(DATA_DIR / "demographics" / "census2021_oa.parquet",
                              scoped, source="census_oa")
        write_parquet_guarded(DATA_DIR / "demographics" / "census2021_oa_counts.parquet",
                              counts[counts["OA21CD"].isin(scope)], source="census_oa")

    svc = _oa_boundary_service()
    if not svc:
        warn("census_oa: no ONS output area boundary service found")
        return pd.DataFrame()
    cache = CACHE_DIR / "boundaries" / f"oa21_{SCOPE_KEY}.geojson"
    cache.parent.mkdir(parents=True, exist_ok=True)
    if cache.exists():
        feats = json.loads(cache.read_text(encoding="utf-8"))["features"]
    else:
        feats = []
        url = f"{ONS_SERVICES}/{svc}/FeatureServer/0/query"
        for i in range(0, len(lsoas), 60):
            batch = lsoas[i:i + 60]
            payload = {"where": "LSOA21CD IN (" + ",".join(f"'{c}'" for c in batch) + ")",
                       "outFields": "OA21CD,LSOA21CD", "returnGeometry": "true",
                       "outSR": "4326", "f": "geojson", "resultRecordCount": 2000}
            for attempt in range(3):
                try:
                    r = requests.post(url, data=payload, timeout=180)
                    r.raise_for_status()
                    feats.extend(r.json().get("features", []))
                    break
                except Exception as e:
                    if attempt == 2:
                        warn(f"census_oa: boundary batch {i // 60} failed: {e}")
                    time.sleep(3)
        if len(feats) < len(lsoas) * 3:
            warn(f"census_oa: only {len(feats):,} output areas for {len(lsoas):,} LSOAs; not caching")
        else:
            cache.write_text(json.dumps({"type": "FeatureCollection", "features": feats}), encoding="utf-8")
    if not feats:
        return pd.DataFrame()

    out_dir = MAP_DIR / "oa"
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.json"):
        old.unlink()
    by_lad: dict = {}
    vals_by_key = {k: [] for k in keys}
    for f in feats:
        p = f.get("properties") or {}
        oa, lsoa = p.get("OA21CD"), p.get("LSOA21CD")
        if not oa or lsoa not in lsoa_lk or oa not in frame.index:
            continue
        lad = lsoa_lk[lsoa][1]
        row = frame.loc[oa]
        # One value per entry of index.json's "keys", in that order, null where
        # missing: the key names repeated on every output area were most of
        # the file.
        v = []
        for k in keys:
            x = row.get(k)
            if x is not None and not pd.isna(x):
                v.append(round(float(x), 1) if k != "census_population" else int(x))
                vals_by_key[k].append(float(x))
            else:
                v.append(None)
        by_lad.setdefault(lad, []).append({"type": "Feature",
            "properties": {"c": oa, "l": lsoa, "v": v}, "geometry": f.get("geometry")})

    index = {"lads": {}, "keys": keys, "ranges": {}}
    for lad, fs in by_lad.items():
        xs, ys = [], []
        def walk(c):
            if isinstance(c[0], (int, float)):
                xs.append(c[0]); ys.append(c[1])
            else:
                for cc in c:
                    walk(cc)
        for f in fs:
            if f["geometry"]:
                walk(f["geometry"]["coordinates"])
        index["lads"][lad] = {"n": LAD_NAMES.get(lad, lad), "count": len(fs),
                              "bbox": [round(min(xs), 4), round(min(ys), 4), round(max(xs), 4), round(max(ys), 4)]}
        write_atomic(out_dir / f"{lad}.json", json.dumps(_round_coords(
            {"type": "FeatureCollection", "features": fs}), separators=(",", ":")))
    for k, xs in vals_by_key.items():
        if len(xs) > 10:
            xs = sorted(xs)
            index["ranges"][k] = {"lo": xs[int(len(xs) * 0.05)], "hi": xs[int(len(xs) * 0.95) - 1],
                                  "min": xs[0], "max": xs[-1]}
    write_atomic(out_dir / "index.json", json.dumps(index, separators=(",", ":")))
    n = sum(len(fs) for fs in by_lad.values())
    size = sum(p.stat().st_size for p in out_dir.glob("*.json")) / 1e6
    ok(f"census_oa: {n:,} output areas in {len(by_lad)} {LA_WORD}s, {len(keys)} indicators, "
       f"{size:.1f} MB -> {out_dir.relative_to(REPO_ROOT)}/")
    return pd.DataFrame([{"lad": l, "oas": len(f)} for l, f in by_lad.items()])


# ============================================================================
# SOURCE 3b: NOMIS claimant count (CLA01, NM_162) - monthly LSOA labour-market
# ============================================================================
# UC + legacy JSA combined count. Latest month + 12-month change. Pulled via
# NOMIS API (not bulk zip) because the file refreshes monthly. LSOA-level.
def run_claimant_count() -> pd.DataFrame:
    rule("NOMIS claimant count (CLA01 / NM_162, LSOA monthly)")
    cache_dir = CACHE_DIR / "claimant"
    cache_dir.mkdir(parents=True, exist_ok=True)
    # _v3: bulk queries against NOMIS TYPE298 silently cap at 25,000 rows
    # regardless of RecordLimit= for unregistered users. Switch to explicit
    # LSOA lists for the boroughs in scope, chunked into ~500-code requests.
    cache = cache_dir / f"claimant_{SCOPE_KEY}_latest.csv"

    # The NW London LSOA set comes from the ONS best-fit lookup, which is scoped
    # to SCOPE_LADS.
    try:
        scope_lsoas = set(get_lsoa_ward_lookup())
    except Exception as e:
        warn(f"claimant count: ONS LSOA->ward lookup unavailable ({e}); "
             "cannot scope the request")
        return pd.DataFrame()
    info(f"  LSOA set in scope: {len(scope_lsoas):,} codes")
    if not scope_lsoas:
        warn("claimant count: empty LSOA set in scope; nothing to fetch")
        return pd.DataFrame()

    # NOMIS accepts an explicit comma-separated geography list. Per-request cap
    # (~25k rows unregistered) easily covers 500 LSOAs x 2 months. URL caps at
    # ~8kB so 500 codes * 10 chars = 5000 chars fits comfortably.
    BASE = (
        "https://www.nomisweb.co.uk/api/v01/dataset/NM_162_1.data.csv"
        "?date=latest,latestMINUS12"
        "&gender=0&age=0"
        "&measures=20100"
    )
    CHUNK = 500
    if not cache.exists() or cache.stat().st_size < 4096:
        codes = sorted(scope_lsoas)
        parts: list[bytes] = []
        header: bytes | None = None
        for i in range(0, len(codes), CHUNK):
            batch = codes[i : i + CHUNK]
            url = BASE + "&geography=" + ",".join(batch)
            try:
                r = requests.get(url, timeout=180)
                r.raise_for_status()
                body = r.content
            except Exception as e:
                warn(f"claimant count: chunk {i//CHUNK+1} fetch failed ({e})")
                return pd.DataFrame()
            # Strip header on subsequent chunks so we concatenate cleanly.
            nl = body.find(b"\n")
            if nl < 0:
                continue
            if header is None:
                header = body[: nl + 1]
                parts.append(body)
            else:
                parts.append(body[nl + 1 :])
            info(f"  chunk {i//CHUNK+1}/{(len(codes)+CHUNK-1)//CHUNK}: "
                 f"{len(batch)} codes, {len(body)/1e3:.1f} kB")
        if not parts:
            warn("claimant count: no data from NOMIS")
            return pd.DataFrame()
        cache.write_bytes(b"".join(parts))
        info(f"  total cached: {cache.stat().st_size/1e6:.2f} MB")

    try:
        df = pd.read_csv(cache, dtype=str, low_memory=False)
    except pd.errors.EmptyDataError:
        warn("claimant count: cached CSV empty")
        return pd.DataFrame()

    need = {"GEOGRAPHY_CODE", "DATE", "OBS_VALUE"}
    if not need.issubset(df.columns):
        warn(f"claimant count: unexpected columns {list(df.columns)[:6]}")
        return pd.DataFrame()

    df["OBS_VALUE"] = pd.to_numeric(df["OBS_VALUE"], errors="coerce")
    dates = sorted(df["DATE"].dropna().unique())
    if len(dates) < 1:
        warn("claimant count: no dates in response")
        return pd.DataFrame()
    latest = dates[-1]
    prev   = dates[0] if len(dates) >= 2 else latest
    non_null = int(df["OBS_VALUE"].notna().sum())
    info(f"  latest={latest}  prev={prev}  non-null obs={non_null:,}/{len(df):,}")

    def slice_month(date: str) -> pd.DataFrame:
        # Only MEASURES=20100 (Value) is published at LSOA — take it directly.
        m = df[df["DATE"] == date]
        return (m.groupby("GEOGRAPHY_CODE", as_index=False)["OBS_VALUE"]
                  .first())

    cnt_latest = slice_month(latest).rename(
        columns={"GEOGRAPHY_CODE": "LSOA21CD", "OBS_VALUE": "claimant_count"})
    cnt_prev = slice_month(prev).rename(
        columns={"GEOGRAPHY_CODE": "LSOA21CD", "OBS_VALUE": "claimant_count_yearAgo"})

    out = cnt_latest.merge(cnt_prev, on="LSOA21CD", how="outer")
    out["claimant_yoy_change"] = out["claimant_count"] - out["claimant_count_yearAgo"]
    out["claimant_yoy_pct"]    = (
        (out["claimant_count"] - out["claimant_count_yearAgo"]) /
        out["claimant_count_yearAgo"].replace(0, pd.NA) * 100
    ).round(1)

    # NOMIS doesn't publish "claimant rate" at LSOA. Derive it ourselves using
    # the census 2021 working-age population we already fetched.
    cen_path = DATA_DIR / "demographics" / "census2021.parquet"
    if cen_path.exists():
        try:
            cen = pd.read_parquet(cen_path)
            if {"LSOA21CD", "census_population", "census_working_age_pct"}.issubset(cen.columns):
                wa = pd.DataFrame({
                    "LSOA21CD": cen["LSOA21CD"],
                    "_working_age": (pd.to_numeric(cen["census_population"], errors="coerce")
                                     * pd.to_numeric(cen["census_working_age_pct"], errors="coerce")
                                     / 100.0),
                })
                out = out.merge(wa, on="LSOA21CD", how="left")
                out["claimant_rate_pct"] = (
                    pd.to_numeric(out["claimant_count"], errors="coerce")
                    / out["_working_age"].replace(0, pd.NA) * 100
                ).round(2)
                out = out.drop(columns=["_working_age"])
            else:
                out["claimant_rate_pct"] = pd.NA
        except Exception as e:
            warn(f"claimant rate: census denom unavailable ({e})")
            out["claimant_rate_pct"] = pd.NA
    else:
        out["claimant_rate_pct"] = pd.NA

    out["claimant_month"] = latest
    out = out.drop(columns=["claimant_count_yearAgo"])

    # No scope filter here. The request above only asked NOMIS for LSOAs in
    # scope, so there is nothing out of scope left to remove.

    out_path = DATA_DIR / "economy" / "claimant_count.parquet"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out = write_parquet_guarded(out_path, out, source="claimant")
    ok(f"claimant count: {len(out):,} LSOAs  month={latest}  "
       f"-> {out_path.relative_to(REPO_ROOT)}")
    return out


# ============================================================================
# SOURCE 3b: DWP benefits — RETIRED, and why
# ----------------------------------------------------------------------------
# There is no DWP benefits source here any more. It ran on every scheduled
# refresh, failed on every one, and nothing on the map ever read it.
#
# The five ids it used were simply the wrong datasets. They returned HTTP 200,
# which is what made this look like an upstream outage for so long, but
# NM_210_1 is an annual population survey by occupation, NM_189_1 is the
# Business Register and Employment Survey, and NM_193_1 is the Annual
# Employment Survey. None of them is a benefit. The empty bodies were NOMIS
# correctly reporting that the requested cell does not exist.
#
# Looking up the real ones by name settles it, and not in favour of a fix:
#
#   PIP                 not on NOMIS in any form
#   Universal Credit    not on NOMIS in any form
#   ESA                 NM_12_1 / NM_134_1     last period 2018-11
#   Pension Credit      NM_109_1 / NM_114_1    last period 2018-11
#   Carer's Allowance   NM_116_1               last period 2018-11
#   DLA                 NM_110_1 / NM_115_1    last period 2018-11
#   Attendance Allow.   NM_78_1                last period 2019-02
#
# So the two benefits that matter most for a current picture were never
# available here, and the rest stopped eight years ago on TYPE304/307/312,
# small-area geographies that predate LSOA 2021 and would not join to anything
# else in this repo.
#
# DWP moved this to Stat-Xplore, which is where the live figures are:
#     https://stat-xplore.dwp.gov.uk/webapi/rest/v1/
# It needs a free account and an API key sent as an APIKey header (the endpoint
# answers 401 without one), and it speaks its own query language rather than
# NOMIS's. That is a real piece of work and it needs a key that belongs to a
# person, so it is not something the pipeline can quietly do for itself.
#
# Adding it back means writing a Stat-Xplore client, not repairing this one.
# Deleting the broken version is the honest state in the meantime: a source
# that always fails teaches everyone to ignore the failure list.
#
# That client now exists: "DWP BENEFITS, FROM STAT-XPLORE", further down. It
# stays dormant, each of its sources "skipped" with a note rather than
# failed, until the owner adds a key as the STAT_XPLORE_API_KEY secret.
# ============================================================================

# ============================================================================
# SOURCE 3c: QOF (Quality and Outcomes Framework) - practice-level prevalence
# ----------------------------------------------------------------------------
# NHS Digital publishes a practice-level prevalence CSV each year at
#   https://files.digital.nhs.uk/.../PREVALENCE_<YY><YY>.csv
# Columns (as of 2022-23): PRACTICE_CODE, INDICATOR_CODE, REGISTER, LIST_SIZE,
# PREVALENCE. We pull a curated indicator list, pivot wide, filter to NW
# London practices, and write one row per GP practice.
# ============================================================================
# ---------------------------------------------------------------------------
# Fingertips practice-level QOF prevalence.
# child_area_type_id=7  -> GP practice. We filter to NW London practices
# after the fact by matching ODS codes against gp_practices.parquet.
# Indicator IDs below are the OHID Fingertips IDs for QOF prevalence
# indicators; values are GP-practice-level percentages.
#
# The third column is Fingertips' own name for the id, copied from
# data/outcomes/fingertips_profiles.parquet, which run_fingertips_profiles
# fills from the API's indicator metadata. It is not a description to be
# reworded: run_qof compares it with the name in every download and skips any
# id whose figures turn out to be something else. A wrong id still returns a
# full set of plausible percentages, so the name is the only thing that can
# catch it.
#
# Cancer is not listed: no QOF cancer prevalence id could be confirmed by name.
# ---------------------------------------------------------------------------
QOF_INDICATORS = [
    # (fingertips_id, short_name,              Fingertips' name for the id)
    (   219,           "qof_hypertension_pct",  "Hypertension: QOF prevalence"),
    (   241,           "qof_diabetes_pct",      "Diabetes: QOF prevalence"),
    (   253,           "qof_copd_pct",          "COPD: QOF prevalence"),
    (   273,           "qof_chd_pct",           "CHD: QOF prevalence"),
    (   258,           "qof_ckd_pct",           "CKD: QOF prevalence"),
    (   262,           "qof_heart_failure_pct", "Heart Failure: QOF prevalence"),
    (   848,           "qof_depression_pct",    "Depression: QOF prevalence"),
    ( 90581,           "qof_mental_health_pct", "Mental Health: QOF prevalence"),
    ( 90933,           "qof_asthma_pct",        "Asthma: QOF prevalence"),
    (   247,           "qof_dementia_pct",      "Dementia: QOF prevalence"),
    (   280,           "qof_af_pct",            "Atrial fibrillation: QOF prevalence"),
    (   212,           "qof_stroke_pct",        "Stroke: QOF prevalence"),
    (   200,           "qof_ld_pct",            "Learning disability: QOF prevalence"),
    ( 94136,           "qof_obesity_pct",       "Obesity: QOF prevalence (new definition)"),
    ( 91280,           "qof_smoking_pct",       "Smoking: QOF prevalence"),
]


def _ft_name_mismatch(label: str, actual: str) -> bool:
    """
    True when the name in a Fingertips download is not the indicator asked for.

    Compared on the first fourteen letters with case, spaces and punctuation
    ignored. That passes Fingertips' own small rewordings ("Heart Failure"
    against "Heart failure", a trailing age band) and fails a different
    indicator, whose name differs in its first words.
    """
    stem = re.sub(r"[^a-z]", "", (label or "").lower())[:14]
    return bool(stem) and stem not in re.sub(r"[^a-z]", "", str(actual or "").lower())


def run_qof() -> pd.DataFrame:
    rule("OHID Fingertips QOF (GP-practice prevalence)")
    cache_dir = CACHE_DIR / "qof_fingertips"
    cache_dir.mkdir(parents=True, exist_ok=True)
    AREA_TYPE_PRACTICE = 7  # GP practice

    rows: list[dict] = []
    for ind_id, short, name in QOF_INDICATORS:
        cache = cache_dir / f"ind_{ind_id}_practice.csv"
        if not cache.exists() or cache.stat().st_size < 1024:
            url = (
                f"https://fingertips.phe.org.uk/api/all_data/csv/by_indicator_id"
                f"?indicator_ids={ind_id}"
                f"&child_area_type_id={AREA_TYPE_PRACTICE}"
            )
            try:
                r = requests.get(url, timeout=180)
                r.raise_for_status()
                if len(r.content) < 1024:
                    warn(f"  {short} ({ind_id}): response only {len(r.content)}B - skipping")
                    continue
                cache.write_bytes(r.content)
                info(f"  {short}: {len(r.content)/1e6:.1f} MB")
                time.sleep(1.0)
            except Exception as e:
                warn(f"  {short} ({ind_id}): fetch failed ({e})")
                continue
        try:
            df = pd.read_csv(cache, dtype=str, low_memory=False)
        except pd.errors.EmptyDataError:
            continue
        if df.empty or "Area Code" not in df.columns:
            continue
        # Skipped rather than warned past: a column of some other measure
        # under this name is worse than no column.
        if "Indicator Name" in df.columns and not df["Indicator Name"].empty:
            actual = str(df["Indicator Name"].iloc[0])
            if _ft_name_mismatch(name, actual):
                warn(f"  {short} ({ind_id}): asked for {name!r} but the data is "
                     f"{actual!r}; skipped. Fix the id in QOF_INDICATORS.")
                continue
        # Keep latest period per practice
        if "Time period Sortable" in df.columns:
            df = df.sort_values("Time period Sortable")
        df = df.groupby("Area Code", as_index=False).tail(1)
        for _, row in df.iterrows():
            v = _tofloat(row.get("Value"))
            if v is None:
                continue
            rows.append({"code": row["Area Code"], "short": short,
                         "value": v, "period": row.get("Time period", "")})

    if not rows:
        warn("qof: no Fingertips practice-level rows returned - skipping write")
        return pd.DataFrame()

    raw = pd.DataFrame(rows)
    wide = (raw.pivot_table(index="code", columns="short",
                            values="value", aggfunc="first")
               .reset_index())
    wide.columns.name = None

    # Filter to NW London practice ODS codes
    gps_path = DATA_DIR / "healthcare" / "gp_practices.parquet"
    if gps_path.exists():
        try:
            gps = pd.read_parquet(gps_path)
            if "code" in gps.columns:
                gp_codes = set(gps["code"].dropna().astype(str).str.upper())
                wide["code"] = wide["code"].astype(str).str.upper()
                wide = wide[wide["code"].isin(gp_codes)].copy()
        except Exception as e:
            warn(f"  practice filter skipped ({e})")

    out_path = DATA_DIR / "healthcare" / "qof_prevalence.parquet"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    wide = write_parquet_guarded(out_path, wide, source="qof")
    ok(f"qof: {len(wide):,} practices in scope x {len(QOF_INDICATORS)} indicators "
       f"-> {out_path.relative_to(REPO_ROOT)}")
    return wide




# ============================================================================
# SOURCE 4: OHID Fingertips  (public health outcomes per LAD)
# ============================================================================
# Every id below was checked against Fingertips' own indicator metadata, by
# name, on 10 Aug 2026. Do not add one without doing the same:
#
#   https://fingertips.phe.org.uk/api/indicator_metadata/by_indicator_id?indicator_ids=<id>
#
# The previous table had eleven of fifteen pointing at the wrong indicator, and
# nothing caught it because a wrong indicator still returns plausible numbers.
# Hypertension prevalence published as "smoking prevalence" was 10.8%, which is
# an entirely believable smoking rate. Five more ids did not exist at all and
# returned an empty CSV, which was cached as if it were a valid answer.
#
# Life expectancy and healthy life expectancy carry both sexes in one
# indicator, so the same id appears twice and the Sex column below picks which.
FINGERTIPS_INDICATORS = [
    # id, short_name, description
    (90366, "life_expectancy_male",          "Life expectancy at birth (male)"),
    (90366, "life_expectancy_female",        "Life expectancy at birth (female)"),
    (90362, "healthy_life_expectancy_male",  "Healthy life expectancy at birth (male)"),
    (90362, "healthy_life_expectancy_female","Healthy life expectancy at birth (female)"),
    (92443, "smoking_prevalence_adults",     "Smoking prevalence in adults (18+), current smokers (APS)"),
    (93088, "obesity_adults",                "Overweight including obesity prevalence in adults"),
    (90323, "obesity_year6",                 "Year 6 prevalence of obesity, including severe obesity"),
    (93014, "physical_activity_adults",      "Percentage of physically active adults"),
    (  219, "hypertension_qof",              "Hypertension: QOF prevalence"),
    (  848, "depression_qof",                "Depression: QOF prevalence"),
    (90581, "mental_health_qof",             "Mental health: QOF prevalence"),
    (41001, "suicide_rate",                  "Suicide rate"),
    (93700, "child_poverty_low_income",      "Children in relative low income families (under 16)"),
    (90813, "self_harm_admissions_10_24",    "Hospital admissions as a result of self-harm (10 to 24 years)"),
    (93930, "a_e_attendance_under_5",        "A&E attendances (0 to 4 years)"),
    (30311, "mmr_2_doses_age5",              "Vaccination coverage: MMR two doses (5 years old)"),
    (30314, "flu_vaccination_65plus",        "Vaccination coverage: flu (65 and over)"),
    (93560, "cervical_screening_25_49",      "Cancer screening coverage: cervical (25 to 49)"),
    # Dropped rather than guessed at:
    #   fuel poverty  - DESNZ LILEE is already fetched by run_fuel_poverty, at
    #                   LSOA level, which is finer than anything here.
    #   GP patient satisfaction - no indicator of that name could be confirmed,
    #                   and a plausible-looking guess is what caused this mess.
]

# Fingertips publishes most of these for districts and unitaries (area type
# 501) as well as for upper-tier LAs (502): counties, unitaries, metropolitan
# and London boroughs. run_fingertips asks for 501 first, so a district (E07)
# in a shire county gets its own figure wherever OHID publishes one. Only where
# it does not does the district take its county's 502 figure, found through
# Fingertips' own county-to-district lookup, and the row then records the
# county as the area the figure describes (source_code and source_name), so
# the map can say it is the county's figure rather than present it as the
# district's own. Until April 2026 every district got its county's figure for
# all 18 series under its own name, which put Kent's life expectancy on Thanet
# and Sevenoaks alike.
FT_PARENTS_URL = ("https://fingertips.phe.org.uk/api/parent_to_child_areas"
                  "?child_area_type_id=501&parent_area_type_id=502")

# Councils whose code changed after a boundary change, mapped to the code they
# replaced, from the ONS Code History Database. Fingertips can lag a year or
# more behind a new code, and an LA that matches nothing has no OHID figures at
# all: Barnsley and Sheffield were redrawn in 2024 (E08000016 and E08000019
# became E08000038 and E08000039), and their 49 wards showed none of the 18
# series. The predecessor's figure is the same council's, so it is not a
# county figure and is not marked as one.
CODE_PREDECESSORS = {
    "E08000038": "E08000016",   # Barnsley
    "E08000039": "E08000019",   # Sheffield
}


def ft_codes_to_current(df: "pd.DataFrame") -> "pd.DataFrame":
    """A Fingertips frame with each predecessor code's rows filed under the
    council's current code, where the current code has no rows of its own.
    The Local Health LA series (area type 501) still uses the old Barnsley and
    Sheffield codes, so without this their LA figures were dropped."""
    if df is None or df.empty or "Area Code" not in df.columns:
        return df
    have = set(df["Area Code"].astype(str))
    swap = {old: new for new, old in CODE_PREDECESSORS.items()
            if new not in have and old in have}
    if not swap:
        return df
    df = df.copy()
    df["Area Code"] = df["Area Code"].astype(str).replace(swap)
    return df

@lru_cache(maxsize=1)
def _ft_district_parents() -> dict:
    """District code -> upper-tier (county) code, for districts in scope."""
    cache = CACHE_DIR / "fingertips" / "parents_502_501.json"
    cache.parent.mkdir(parents=True, exist_ok=True)
    data = None
    if cache.exists():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
        except ValueError:
            data = None
    if data is None:
        try:
            data = _ft_get(FT_PARENTS_URL, timeout=60).json()
            cache.write_text(json.dumps(data), encoding="utf-8")
        except Exception as e:
            warn(f"fingertips: county lookup failed ({e}); districts get no figures")
            return {}
    out = {}
    for parent, kids in (data or {}).items():
        for k in kids or []:
            if k in SCOPE_LADS and k != parent:
                out[k] = parent
    return out


def _ft_num(v):
    """A float, or None for a blank, unreadable or NaN cell."""
    f = _tofloat(v)
    return f if f is not None and f == f else None


def _ft_has_value(row) -> bool:
    return _ft_num(row.get("Value")) is not None


def _ft_in_scope(df: "pd.DataFrame", upper: "pd.DataFrame | None" = None,
                 parents: "dict | None" = None,
                 lads: "set | None" = None) -> "pd.DataFrame":
    """One row per in-scope LA, keyed by its own code in "Area Code".

    df is one row per published area (see _ft_pick), normally at area type 501;
    upper is the same indicator at area type 502, or None when df is the only
    download. For each LA the first of these with a figure is used:

      1. its own row in df, then in upper;
      2. the row of the code it replaced (CODE_PREDECESSORS), the same council;
      3. its county's row in upper (or df), for a district OHID does not
         publish on its own.

    "Area Name" keeps the published area's name, so a county's row still says
    the county, and three columns record what the figure describes:
    source_code, source_name and source_kind ("own", "predecessor" or
    "county")."""
    lads = SCOPE_LADS if lads is None else lads
    if parents is None:
        parents = _ft_district_parents()
    frames = [f for f in (df, upper) if f is not None and not f.empty]
    if not frames:
        return pd.DataFrame()

    def first(code):
        hit = None
        for f in frames:
            rows = f[f["Area Code"] == code]
            if rows.empty:
                continue
            r = rows.iloc[-1]
            if _ft_has_value(r):
                return r
            hit = r if hit is None else hit
        return hit

    out = []
    for lad in sorted(lads):
        tries = [(lad, "own")]
        if lad in CODE_PREDECESSORS:
            tries.append((CODE_PREDECESSORS[lad], "predecessor"))
        if parents.get(lad):
            tries.append((parents[lad], "county"))
        chosen, blank = None, None
        for code, kind in tries:
            r = first(code)
            if r is None:
                continue
            if _ft_has_value(r):
                chosen = (r, code, kind)
                break
            blank = blank or (r, code, kind)
        chosen = chosen or blank
        if chosen is None:
            continue
        r, code, kind = chosen
        r = r.copy()
        r["source_code"] = code
        r["source_name"] = str(r.get("Area Name", "") or "")
        r["source_kind"] = kind
        r["Area Code"] = lad
        out.append(r)
    return pd.DataFrame(out).reset_index(drop=True) if out else pd.DataFrame()


# The age each indicator is published for as its headline. Fingertips sends
# every breakdown in one file, by age and by category (deprivation decile,
# ethnic group and so on), and keeping whichever row sorted last put the
# 18 to 64 breakdown of adult smoking on the map as if it were the 18 and over
# figure: intervals ten points wide, and nine of London's 33 values ones OHID
# marks "not published for data quality reasons". Measured from the October
# 2026 data; every id but 92443 already came through at its headline age.
FT_HEADLINE_AGE = {
    90366: "All ages", 90362: "All ages", 92443: "18+ yrs", 93088: "18+ yrs",
    90323: "10-11 yrs", 93014: "19+ yrs", 219: "All ages", 848: "18+ yrs",
    90581: "All ages", 41001: "10+ yrs", 93700: "<16 yrs", 90813: "10-24 yrs",
    93930: "0-4 yrs", 30311: "5 yrs", 30314: "65+ yrs", 93560: "25-49 yrs",
}
# Survey prevalences, whose LA intervals say whether the right breakdown came
# through: across London's boroughs the 18 and over smoking figure has a
# median half-width of about 3 points, physical activity 3.6, the 18 to 64
# breakdown 9.4. Shire districts have smaller samples, so their headline
# figures run wider: adult obesity and smoking came in at 5.0 to 5.1 in the
# October 2026 rebuild of the East and West Midlands, East of England and
# South East. The limit sits between those and the breakdown.
FT_PREVALENCE_IDS = {92443, 93088, 93014}
FT_CI_HALF_WIDTH_MAX = 7.0
# A value whose note says this is a placeholder, not a figure.
FT_WITHHELD_NOTES = ("not published", "cannot be calculated")


def _ft_note(v) -> str:
    """A Fingertips 'Value note' as a clean string ('' for none)."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    s = str(v).strip()
    return "" if s.lower() in ("", "nan", "none") else s


def _ft_blank(s: "pd.Series") -> "pd.Series":
    return s.isna() | (s.astype(str).str.strip().isin(["", "nan", "None"]))


def _ft_headline_age(df: "pd.DataFrame", want: "str | None") -> "str | None":
    """The age to keep: the one asked for, else the only one, else All ages,
    else the broadest open-ended band ("18+ yrs"). None when it cannot tell."""
    if "Age" not in df.columns:
        return None
    ages = [a for a in df["Age"].dropna().astype(str).unique()]
    if want:
        return want
    if len(ages) <= 1:
        return ages[0] if ages else None
    if "All ages" in ages:
        return "All ages"
    plus = sorted((a for a in ages if a.endswith("+ yrs")),
                  key=lambda a: _tofloat(a.split("+")[0]) or 999)
    return plus[0] if plus else None


def _ft_pick(df: "pd.DataFrame", short: str, want_age: "str | None" = None,
             label: str = "") -> "pd.DataFrame":
    """One headline row per area: the right sex, no category breakdown, the
    headline age, and the latest period. A value OHID withholds is blanked.

    Raises ValueError when the headline age is not in the file, so a wrong
    breakdown is never kept in its place."""
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.copy()
    # Fingertips returns Male, Female and Persons rows for the same
    # indicator. The indicator name says what it wants, and everything else
    # wants Persons.
    if "Sex" in df.columns:
        if short.endswith("_male"):
            want = "Male"
        elif short.endswith("_female"):
            want = "Female"
        else:
            want = "Persons"
        sub = df[df["Sex"] == want]
        if sub.empty and want == "Persons":
            sub = df          # some indicators are single-sex by nature
        if sub.empty:
            raise ValueError(f"no '{want}' rows")
        df = sub
    # A row with a Category Type is a breakdown (by deprivation decile, ethnic
    # group and so on), never the area's headline figure.
    if "Category Type" in df.columns:
        df = df[_ft_blank(df["Category Type"])]
    if "Age" in df.columns and not df.empty:
        age = _ft_headline_age(df, want_age)
        if age is None:
            raise ValueError(f"several ages ({', '.join(sorted(df['Age'].dropna().astype(str).unique()))}) "
                             f"and no headline age known")
        sub = df[df["Age"].astype(str) == age]
        if sub.empty:
            raise ValueError(f"headline age {age!r} not in the file "
                             f"(it has {', '.join(sorted(df['Age'].dropna().astype(str).unique()))})")
        df = sub
    if df.empty:
        return df
    if "Time period Sortable" in df.columns:
        df = df.sort_values("Time period Sortable", kind="stable")
    df = df.groupby("Area Code", as_index=False, sort=False).tail(1).copy()
    if "Value note" in df.columns:
        note = df["Value note"].fillna("").astype(str).str.lower()
        withheld = note.apply(lambda n: any(w in n for w in FT_WITHHELD_NOTES))
        df.loc[withheld, "Value"] = None
        for c in ("Lower CI 95.0 limit", "Upper CI 95.0 limit"):
            if c in df.columns:
                df.loc[withheld, c] = None
    return df


def ft_sanity(rows: list, ind_id: int, short: str) -> "str | None":
    """Why one indicator's rows cannot be trusted, or None. Checks the stored
    age is the headline age, and that a survey prevalence has intervals narrow
    enough to be the headline rather than a breakdown."""
    want = FT_HEADLINE_AGE.get(ind_id)
    mine = [r for r in rows if r.get("indicator_short") == short]
    if want:
        bad = sorted({str(r.get("age")) for r in mine
                      if r.get("age") not in (None, "", want)})
        if bad:
            return f"{short}: age {', '.join(bad)} where {want} was expected"
    if ind_id in FT_PREVALENCE_IDS:
        hw = [(r["upper_ci"] - r["lower_ci"]) / 2 for r in mine
              if r.get("upper_ci") is not None and r.get("lower_ci") is not None
              and r.get("value") is not None]
        if len(hw) >= 3:
            med = sorted(hw)[len(hw) // 2] if len(hw) % 2 else \
                (sorted(hw)[len(hw) // 2 - 1] + sorted(hw)[len(hw) // 2]) / 2
            if med > FT_CI_HALF_WIDTH_MAX:
                return (f"{short}: median interval half-width {med:.1f} points, over "
                        f"{FT_CI_HALF_WIDTH_MAX:g}; probably a breakdown, not the headline")
    return None


def _ft_value_with_ci(row) -> tuple:
    """A Fingertips row's value and 95% confidence limits, as floats or None.

    Where the upper limit is below the lower one the row is not a measurement
    but a placeholder, and its value is stored as missing. The City of London's
    adult smoking row comes as value 0 with limits 0 and -1: taken at face
    value it put 0% smoking on the map for every City ward, where the honest
    answer is that there is no published figure.
    """
    value = _ft_num(row.get("Value"))
    lower = _ft_num(row.get("Lower CI 95.0 limit"))
    upper = _ft_num(row.get("Upper CI 95.0 limit"))
    if lower is not None and upper is not None and upper < lower:
        return None, None, None
    return value, lower, upper


def _ft_download(cache: "Path", ind_id: int, area_type: int, short: str,
                 quiet: bool = False) -> "pd.DataFrame | None":
    """One indicator at one area type, cache first. None when there is nothing."""
    if not cache.exists():
        url = (
            f"https://fingertips.phe.org.uk/api/all_data/csv/by_indicator_id"
            f"?indicator_ids={ind_id}"
            f"&child_area_type_id={area_type}"
            f"&parent_area_type_id=15"
        )
        try:
            r = _ft_get(url, timeout=60)
            # A non-existent indicator id answers 200 with a header row and
            # nothing under it. Caching that makes the gap permanent: the
            # file exists, so no rerun ever asks again, and the indicator
            # is simply absent from the map with nothing to say why. Five
            # of them sat like that until someone counted the outputs. At
            # area type 501 an empty answer is normal for a series OHID
            # publishes only for upper-tier LAs, so it is not a warning there.
            if len(r.content.splitlines()) < 2:
                if not quiet:
                    warn(f"fingertips {ind_id} ({short}): no rows returned; "
                         f"not caching. Check the id exists and is published "
                         f"for area type {area_type}.")
                return None
            cache.write_bytes(r.content)
            time.sleep(1.0)  # be polite
        except Exception as e:
            warn(f"fingertips {ind_id} (area type {area_type}): {e}")
            return None
    try:
        df = pd.read_csv(cache, dtype=str, low_memory=False)
    except pd.errors.EmptyDataError:
        return None
    if df.empty or "Area Code" not in df.columns:
        return None
    return df


def run_fingertips() -> pd.DataFrame:
    rule("OHID Fingertips (public health outcomes)")
    cache_dir = CACHE_DIR / "fingertips"
    cache_dir.mkdir(parents=True, exist_ok=True)
    AREA_TYPE_DISTRICT = 501  # Districts and unitaries (post Apr 2023)
    AREA_TYPE_LA = 502        # Upper-tier LAs (post Apr 2023)

    rows: list = []
    problems: list = []
    for ind_id, short, desc in FINGERTIPS_INDICATORS:
        # 502 keeps its old cache name, so a rerun does not fetch it again.
        df_up = _ft_download(cache_dir / f"ind_{ind_id}.csv", ind_id, AREA_TYPE_LA, short)
        df_501 = _ft_download(cache_dir / f"ind_{ind_id}_501.csv", ind_id,
                              AREA_TYPE_DISTRICT, short, quiet=True)
        if df_up is None and df_501 is None:
            continue

        # Check the id is the indicator we think it is. An id that points at
        # the wrong indicator returns a full, valid, plausible set of numbers
        # under our own label, which is the one failure mode nothing else here
        # can catch: hypertension prevalence published as smoking prevalence
        # was 10.8%, and looked entirely reasonable, for months.
        first = df_501 if df_501 is not None else df_up
        if "Indicator Name" in first.columns and not first["Indicator Name"].empty:
            actual = str(first["Indicator Name"].iloc[0])
            if _ft_name_mismatch(desc, actual):
                warn(f"fingertips {ind_id}: asked for {desc!r} but the data is "
                     f"{actual!r}. Verify the id before trusting this column.")
        # Sex, no category breakdown, the headline age and the latest period,
        # chosen explicitly. Keeping whichever row sorted last once produced
        # Female figures for the two _male indicators, and the 18 to 64
        # breakdown of adult smoking in place of the 18 and over figure.
        try:
            pick_501 = _ft_pick(df_501, short, FT_HEADLINE_AGE.get(ind_id))
            pick_up = _ft_pick(df_up, short, FT_HEADLINE_AGE.get(ind_id))
        except ValueError as e:
            msg = f"fingertips {ind_id} ({short}): {e}; skipping"
            warn(msg)
            problems.append(f"{short}: {e}")
            continue
        df = _ft_in_scope(pick_501, pick_up)
        if df.empty:
            continue
        added = []
        for _, row in df.iterrows():
            value, lower_ci, upper_ci = _ft_value_with_ci(row)
            added.append({
                "LAD25CD": row["Area Code"],
                "lad_name": LAD_NAMES.get(row["Area Code"], row.get("Area Name", "")),
                "indicator_id": ind_id,
                "indicator_short": short,
                "indicator_name":  desc,
                "value":    value,
                "lower_ci": lower_ci,
                "upper_ci": upper_ci,
                "period":   row.get("Time period", ""),
                "sex":      row.get("Sex", ""),
                "age":      row.get("Age", ""),
                # The area the figure describes: the LA itself, the code it
                # replaced, or for a district OHID does not publish on its own,
                # its county.
                "source_code": row.get("source_code", row["Area Code"]),
                "source_name": row.get("source_name", ""),
                "source_kind": row.get("source_kind", "own"),
                # OHID's own caveat on the figure: provisional, combined with
                # another area, interpret with caution. Carried through to
                # borough_data.json (ft_notes) so the map can say so.
                "value_note": _ft_note(row.get("Value note")),
            })
        bad = ft_sanity(added, ind_id, short)
        if bad:
            # Kept as rows with no value, so carry_forward_thin does not put
            # last run's figures back, and the run is marked failed below.
            warn(f"fingertips: {bad}; its values are left blank")
            problems.append(bad)
            for r in added:
                r["value"] = r["lower_ci"] = r["upper_ci"] = None
        rows.extend(added)

    out = pd.DataFrame(rows)
    out_path = DATA_DIR / "outcomes" / "fingertips.parquet"
    ft_id = {short: ind_id for ind_id, short, _ in FINGERTIPS_INDICATORS}
    out = carry_forward_thin(out_path, out, key="indicator_short", source="fingertips",
                             requested=set(ft_id),
                             cache_file=lambda k: cache_dir / f"ind_{ft_id[k]}.csv")
    out = write_parquet_guarded(out_path, out, source="fingertips")
    ok(f"fingertips: {len(out):,} rows -> {out_path.relative_to(REPO_ROOT)}")
    if not out.empty and "source_kind" in out.columns:
        cty = out[out["source_kind"] == "county"]
        if len(cty):
            info(f"fingertips: {cty['LAD25CD'].nunique()} district(s) carry their county's "
                 f"figure for at least one series ({len(cty)} values), marked as such")
    # Council codes are checked, not assumed. A council reorganised since the
    # boundaries Fingertips uses (or the other way round) matches nothing and
    # would otherwise just be blank. ONS's Code History Database records which
    # codes replaced which (CODE_PREDECESSORS); an LA still unmatched is an
    # error for this pack, because the map then has none of its OHID figures.
    if not out.empty and "LAD25CD" in out.columns:
        have = set(out.loc[out["value"].notna(), "LAD25CD"].astype(str))
        missing = sorted(l for l in SCOPE_LADS if l not in have)
        if missing:
            names = ", ".join(f"{LAD_NAMES.get(l, l)} ({l})" for l in missing)
            msg = (f"{len(missing)} {LA_WORD}(s) have no Fingertips figure, directly, "
                   f"under a predecessor code or through their county: {names}. "
                   f"Look the codes up in the ONS Code History Database and add them "
                   f"to CODE_PREDECESSORS.")
            warn(f"fingertips: {msg}")
            problems.append(msg)
        else:
            ok(f"fingertips: all {len(SCOPE_LADS)} {LA_WORD}s matched a Fingertips area code")
    if problems:
        _SOURCE_ERROR["fingertips"] = "; ".join(problems)
    return out

# ============================================================================
# SOURCE 4b: OHID Fingertips, the rest of the profiles
# ----------------------------------------------------------------------------
# Fingertips publishes 38 profiles and this project was reading two of them.
# The other 26 that publish at a geography this map can draw are worth having,
# and they are the same API, so this is a list of profile ids rather than a new
# fetcher.
#
# Deliberately NOT a hand-typed list of indicator ids. The comment above
# FINGERTIPS_INDICATORS records what happened last time that was done: eleven of
# fifteen ids pointed at the wrong indicator, and nothing caught it because a
# wrong id still returns a full set of plausible numbers under your own label.
# Typing several hundred more by hand would be the same bet at twenty times the
# stake. So the ids and their names both come from the API, and the name that
# ends up on the map is the publisher's own. Which of a profile's indicators
# are taken is a list of those names (FINGERTIPS_PROFILE_WANTED), matched
# against the profile's metadata again on every run.
#
#   profiles                                   -> the 38 profiles
#   indicator_metadata/by_profile_id?...       -> every indicator id + its name
#   all_data/csv/by_indicator_id?...           -> the figures
#
# Area type 502 is upper-tier local authority, which is a London borough. The
# three profiles that also publish at MSOA are fetched by run_fingertips_msoa
# instead, where they can vary within a borough.
FINGERTIPS_PROFILES = [
    # (profile_id, short prefix, human label for the picker group)
    ( 19, "phof",     "Public Health Outcomes Framework"),
    ( 50, "mh",       "Adult mental health and wellbeing"),
    (139, "diab",     "Diabetes"),
    (105, "cmh",      "Child and maternal health"),
    ( 30, "hp",       "Health protection"),
    (125, "mort",     "Mortality"),
    ( 45, "srh",      "Sexual and reproductive health"),
    (135, "cvd",      "Cardiovascular disease"),
    ( 18, "smok",     "Smoking"),
    (130, "wider",    "Wider determinants of health"),
    ( 87, "alc",      "Alcohol"),
    ( 29, "resp",     "Respiratory disease"),
    ( 91, "suic",     "Suicide prevention"),
    ( 26, "laprof",   "Local authority health profile"),
    ( 84, "dem",      "Dementia"),
    ( 76, "msk",      "Musculoskeletal health"),
    ( 55, "liver",    "Liver disease"),
    ( 65, "hcheck",   "NHS health check"),
    ( 58, "ld",       "Learning disability"),
    (159, "cypmh",    "Children and young people's mental health"),
    ( 86, "tb",       "Tuberculosis"),
    (165, "vis",      "Vision"),
]

# A profile can carry a couple of hundred indicators and most of them are
# breakdowns of a headline. Past this many per profile the list stops being
# something anyone can read, so this is a cap on what is taken.
FINGERTIPS_PER_PROFILE = int(os.environ.get("FINGERTIPS_PER_PROFILE", "40"))

# Which indicators are taken from each profile, by name. This used to be the
# first FINGERTIPS_PER_PROFILE ids in numerical order, which is arbitrary: the
# oldest ids came first, so the Public Health Outcomes Framework gave its B, C,
# D and E sections and never its life expectancy. The list is one row per
# indicator wanted, in the order of preference, with Fingertips' own name as
# the profile's metadata gives it and the id it had when it was checked. An
# id whose name still matches is taken; otherwise the name is looked for in
# the profile, so a renumbered indicator is still found; a name that is not
# there is logged, never guessed at. Every name came from the API, through
# data/outcomes/fingertips_profiles.parquet, so none was typed by hand. The
# map draws nothing from that parquet; what reads it is the QOF name check
# (QOF_INDICATORS, tests/pipeline/test_qof_labels.py), so every QOF
# prevalence it names is on the list, as is every FINGERTIPS_INDICATORS id
# the parquet held.
FINGERTIPS_PROFILE_WANTED = REPO_ROOT / "data" / "curated" / "fingertips_profile_indicators.csv"
# PHOF writes its section code in front of each name ("A01b - Life
# expectancy at birth"), which other profiles leave off.
_FT_NAME_CODE = re.compile(r"^\s*[A-Z]\d{2}[a-z]?(?:\s*\([ivx]+\))?\s*-\s+")


def ft_profile_name_key(name: str) -> str:
    """A Fingertips name as the profile matching compares it: without a
    PHOF section code, lower case, punctuation and spacing ignored."""
    return iod_norm(_FT_NAME_CODE.sub("", str(name or "")))


def ft_profile_wanted(path: "Path | None" = None) -> dict:
    """{profile_id: [(indicator_id or None, name), ...]} in the file's
    order, from FINGERTIPS_PROFILE_WANTED. {} when the file is missing."""
    path = FINGERTIPS_PROFILE_WANTED if path is None else path
    out: dict = {}
    try:
        with open(path, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                try:
                    pid = int(str(row.get("profile_id", "")).strip())
                except ValueError:
                    continue
                name = str(row.get("indicator_name") or "").strip()
                raw = str(row.get("indicator_id") or "").strip()
                if not name:
                    continue
                out.setdefault(pid, []).append((int(raw) if raw.isdigit() else None, name))
    except OSError as e:
        warn(f"fingertips profiles: no list of wanted indicators ({e})")
    return out


def ft_pick_profile_indicators(inds: dict, wanted: list, cap: int) -> tuple:
    """(chosen ids, wanted names not found, [(name, listed id, id found)]).

    inds is the profile's {id: name} from its metadata, wanted the profile's
    rows of FINGERTIPS_PROFILE_WANTED. A listed id is taken when its name in
    the metadata still matches (the first fourteen letters, as
    _ft_name_mismatch compares them, so a small rewording passes); otherwise
    the id whose name is the same once codes, case and punctuation are set
    aside. A name matching nothing, or matching two ids, is reported and
    not taken. At most cap ids, in the order wanted."""
    by_name: dict = {}
    for i, nm in inds.items():
        by_name.setdefault(ft_profile_name_key(nm), []).append(i)
    chosen: list = []
    missing: list = []
    moved: list = []
    for wid, name in wanted:
        hit = None
        if wid is not None and wid in inds and not _ft_name_mismatch(
                _FT_NAME_CODE.sub("", name), _FT_NAME_CODE.sub("", inds[wid])):
            hit = wid
        else:
            ids = by_name.get(ft_profile_name_key(name), [])
            if len(ids) == 1:
                hit = ids[0]
                if wid is not None and hit != wid:
                    moved.append((name, wid, hit))
        if hit is None:
            missing.append(name)
        elif hit not in chosen:
            chosen.append(hit)
    return chosen[:cap], missing, moved


def _ft_profile_indicators(profile_id: int, cache_dir: "Path") -> dict[int, str]:
    """Indicator id -> published name, straight from Fingertips."""
    cache = cache_dir / f"profile_{profile_id}_meta.json"
    if not cache.exists():
        url = ("https://fingertips.phe.org.uk/api/indicator_metadata/by_profile_id"
               f"?profile_id={profile_id}")
        try:
            r = _ft_get(url, timeout=60)
            cache.write_bytes(r.content)
            time.sleep(0.5)
        except Exception as e:
            warn(f"fingertips profile {profile_id}: metadata fetch failed ({e})")
            return {}
    try:
        meta = json.loads(cache.read_text(encoding="utf-8"))
    except Exception as e:
        warn(f"fingertips profile {profile_id}: unreadable metadata ({e})")
        return {}
    out: dict[int, str] = {}
    for ind_id, m in (meta or {}).items():
        try:
            name = (m.get("Descriptive", {}) or {}).get("Name") or ""
        except AttributeError:
            continue
        if name:
            out[int(ind_id)] = name
    return out


def run_fingertips_profiles() -> pd.DataFrame:
    rule("OHID Fingertips (the other profiles, borough level)")
    cache_dir = CACHE_DIR / "fingertips_profiles"
    cache_dir.mkdir(parents=True, exist_ok=True)
    AREA_TYPE_LA = 502

    rows: list = []
    # What this run asked for. A profile whose index could not be read asked
    # for nothing, so its previous indicators are claimed by profile id instead,
    # which lets carry_forward_thin keep them rather than lose the profile.
    requested: set = set()
    unread: set = set()
    wanted_all = ft_profile_wanted()
    not_found: list = []
    for profile_id, prefix, label in FINGERTIPS_PROFILES:
        inds = _ft_profile_indicators(profile_id, cache_dir)
        if not inds:
            unread.add(profile_id)
            continue
        # By name, from the list of wanted indicators, rather than the first
        # ids in numerical order (FINGERTIPS_PROFILE_WANTED).
        wanted = wanted_all.get(profile_id, [])
        if not wanted:
            warn(f"  {label}: no wanted indicators listed for profile {profile_id} in "
                 f"{FINGERTIPS_PROFILE_WANTED.name}; none taken")
            continue
        chosen, missing, moved = ft_pick_profile_indicators(inds, wanted, FINGERTIPS_PER_PROFILE)
        for name, old, new in moved:
            info(f"  {label}: {name!r} is now id {new} (listed as {old})")
        for name in missing:
            warn(f"  {label}: wanted indicator not found in profile {profile_id}'s metadata: {name!r}")
            not_found.append(f"{profile_id}: {name}")
        requested.update(f"{prefix}_{i}" for i in chosen)
        info(f"  {label}: {len(chosen)} of {len(wanted)} wanted indicators found by name "
             f"({len(inds)} published, at most {FINGERTIPS_PER_PROFILE} taken)")
        kept = 0
        for ind_id in chosen:
            cache = cache_dir / f"ind_{ind_id}.csv"
            if not cache.exists():
                url = ("https://fingertips.phe.org.uk/api/all_data/csv/by_indicator_id"
                       f"?indicator_ids={ind_id}"
                       f"&child_area_type_id={AREA_TYPE_LA}"
                       f"&parent_area_type_id=15")
                try:
                    r = _ft_get(url, timeout=60)
                    # Same trap as the older fetcher: an id with nothing behind
                    # it answers 200 with a header row. Caching that makes the
                    # gap permanent.
                    if len(r.content.splitlines()) < 2:
                        continue
                    cache.write_bytes(r.content)
                    time.sleep(0.4)
                except Exception:
                    continue
            try:
                df = pd.read_csv(cache, dtype=str, low_memory=False)
            except Exception:
                continue
            if df.empty or "Area Code" not in df.columns:
                continue
            # Persons, no category breakdown, the headline age and the latest
            # period. The age is known for the ids FT_HEADLINE_AGE lists, and
            # otherwise is the only one, All ages, or the broadest "N+ yrs";
            # an indicator with several ages and none of those is left out
            # rather than shown as whichever breakdown sorted last.
            try:
                df = _ft_pick(df, f"{prefix}_{ind_id}", FT_HEADLINE_AGE.get(ind_id))
            except ValueError as e:
                info(f"  {label}: {ind_id} left out ({e})")
                requested.discard(f"{prefix}_{ind_id}")
                continue
            df = _ft_in_scope(df)
            if df.empty:
                continue
            # The name comes from the API, so the label on the map is the
            # publisher's and cannot disagree with the id it was fetched under.
            name = inds[ind_id]
            for _, row in df.iterrows():
                rows.append({
                    "LAD25CD": row["Area Code"],
                    "lad_name": LAD_NAMES.get(row["Area Code"], row.get("Area Name", "")),
                    "profile_id": profile_id,
                    "profile": label,
                    "indicator_id": ind_id,
                    "indicator_short": f"{prefix}_{ind_id}",
                    "indicator_name": name,
                    "value": _tofloat(row.get("Value")),
                    "period": row.get("Time period", ""),
                    # OHID's note on the value. This column used to be
                    # called "unit", which it never was.
                    "value_note": _ft_note(row.get("Value note")),
                    "age": row.get("Age", ""),
                    "source_code": row.get("source_code", row["Area Code"]),
                    "source_kind": row.get("source_kind", "own"),
                })
            kept += 1
        ok(f"  {label}: {kept} indicators with {SCOPE_NAME} figures")

    out = pd.DataFrame(rows)
    out_path = DATA_DIR / "outcomes" / "fingertips_profiles.parquet"
    if unread and out_path.exists():
        try:
            prev = pd.read_parquet(out_path, columns=["profile_id", "indicator_short"])
            requested.update(prev.loc[prev["profile_id"].isin(unread), "indicator_short"])
        except Exception:
            pass
    out = carry_forward_thin(out_path, out, key="indicator_short",
                             source="fingertips_profiles", requested=requested,
                             cache_file=lambda k: cache_dir / f"ind_{str(k).rsplit('_', 1)[-1]}.csv")
    out = write_parquet_guarded(out_path, out, source="fingertips_profiles")
    n_ind = out["indicator_short"].nunique() if not out.empty else 0
    ok(f"fingertips_profiles: {len(out):,} rows, {n_ind} indicators "
       f"-> {out_path.relative_to(REPO_ROOT)}")
    if not_found:
        info(f"fingertips_profiles: {len(not_found)} wanted indicator(s) not found by name; "
             f"edit data/curated/{FINGERTIPS_PROFILE_WANTED.name} if OHID renamed them")
    return out

# ============================================================================
# SOURCE 5b: Fingertips Local Health  (MSOA level, aggregated to wards)
# ============================================================================
# The indicators above are published for whole boroughs, so every ward in a
# borough carries the same figure. The Local Health profile publishes a
# different set at MSOA level, which is roughly four LSOAs and genuinely varies
# within a borough, and index.html already has a dropdown expecting them.
#
# Those options came from scripts/import_fingertips_csv.py, a one-off that
# patched ward_data.json by hand from a downloaded CSV. The pipeline has
# regenerated that file many times since, so the keys were long gone and all 33
# options selected an indicator that was not there. Fetching them here means
# they survive every rerun.
#
# Ward is not an option: area type 8 exists but returns nothing for these, so
# MSOA is the finest published geography and it has to be bridged to wards.
#
# Low birth weight of live babies (93092) is not in this list but in
# FINGERTIPS_MSOA_EXTRA below, with the five cancer incidence ratios. It was
# taken out in October 2026 on the belief that it had no London MSOA figures,
# but the September 2026 raw snapshot (scripts/fingertips_msoa_raw.csv) holds
# 237 of them for 2018 to 2022, on 2021 MSOA codes. Rather than trust either
# check, the extra source runs per-series checks on every download (coverage,
# withheld values, interval width) and ships a series only if it passes, so
# an empty or withheld series is dropped by the pipeline, never offered as an
# indicator with nothing to draw.
FINGERTIPS_MSOA_AREA_TYPE = 3   # Middle Super Output Area
FINGERTIPS_MSOA_INDICATORS = [
    93465, 93233, 93229, 93227, 93115, 93280, 93089, 93232, 93241, 93240,
    93239, 93283, 93098, 93250, 93252, 93253, 93254, 93255, 93256,
    93480, 93257, 93260, 93259, 93105, 93106, 93231, 93097, 93114, 93219,
    93224, 93107, 93108,
]

LSOA_MSOA_LOOKUP = ("https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/"
                    "services/OA21_LAD23_LSOA21_MSOA21_LEP23_EN_LU/FeatureServer/0/query")
# 2011 MSOAs to 2021 MSOAs. Local Health is still published against 2011 codes
# for its older series; about 2% of MSOAs were split, merged or redrawn in 2021.
MSOA11_MSOA21_LOOKUP = ("https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/"
                        "services/MSOA11_MSOA21_LAD22_EW_LU_v2/FeatureServer/0/query")


@lru_cache(maxsize=1)
def get_lsoa_to_msoa() -> dict:
    """LSOA21CD -> MSOA21CD, from the ONS lookup, cached on disk."""
    cache = CACHE_DIR / "lookups" / f"lsoa21_msoa21_{SCOPE_KEY}.json"
    cache.parent.mkdir(parents=True, exist_ok=True)
    if cache.exists():
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except ValueError:
            warn("lsoa->msoa lookup cache unreadable; refetching")

    codes = sorted(get_lsoa_ward_lookup())
    pairs: dict = {}
    CHUNK = 120  # the IN clause goes in a POST body, but keep it sane
    for i in range(0, len(codes), CHUNK):
        batch = codes[i:i + CHUNK]
        payload = {
            "where": "LSOA21CD IN (" + ",".join(f"'{c}'" for c in batch) + ")",
            "outFields": "LSOA21CD,MSOA21CD",
            "returnGeometry": "false",
            "returnDistinctValues": "true",
            "f": "json",
            "resultRecordCount": 4000,
        }
        r = requests.post(LSOA_MSOA_LOOKUP, data=payload, timeout=180)
        r.raise_for_status()
        for feat in r.json().get("features", []):
            a = feat.get("attributes", {})
            if a.get("LSOA21CD") and a.get("MSOA21CD"):
                pairs[a["LSOA21CD"]] = a["MSOA21CD"]

    if len(pairs) < len(codes) * 0.95:
        raise RuntimeError(
            f"lsoa->msoa: only {len(pairs):,} of {len(codes):,} LSOAs mapped. "
            f"Refusing to cache a partial lookup."
        )
    cache.write_text(json.dumps(pairs), encoding="utf-8")
    ok(f"ONS lookup: {len(pairs):,} LSOAs -> {len(set(pairs.values())):,} MSOAs")
    return pairs


@lru_cache(maxsize=1)
def get_msoa11_population() -> dict:
    """MSOA11CD -> usual residents, Census 2011 (table KS101EW, ONS via Nomis).

    The weight when 2011 MSOAs merged into one 2021 MSOA: their figures are
    combined in proportion to the people living in each, the same rule every
    other aggregation on the site follows. {} if Nomis does not answer, and
    the merge then falls back to equal weights with a warning."""
    cache = CACHE_DIR / "lookups" / "msoa11_population_ks101ew.json"
    cache.parent.mkdir(parents=True, exist_ok=True)
    if cache.exists():
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except ValueError:
            pass
    url = ("https://www.nomisweb.co.uk/api/v01/dataset/NM_144_1.data.csv"
           "?date=latest&geography=TYPE297&rural_urban=0&cell=0&measures=20100"
           "&select=geography_code,obs_value")
    try:
        r = requests.get(url, timeout=180)
        r.raise_for_status()
        df = pd.read_csv(io.StringIO(r.text))
        out = {str(c): float(v) for c, v in zip(df.iloc[:, 0], df.iloc[:, 1]) if str(c).startswith("E02")}
    except Exception as e:
        warn(f"msoa11 population (KS101EW): {e}; merged MSOAs use equal weights")
        return {}
    if len(out) > 6000:
        cache.write_text(json.dumps(out), encoding="utf-8")
    return out


@lru_cache(maxsize=1)
def get_msoa21_to_msoa11() -> dict:
    """MSOA21CD -> [MSOA11CD, ...] for the MSOAs in scope, cached on disk.

    Unchanged and split 2021 MSOAs have one 2011 parent; merged ones have
    several. Returns {} rather than failing: this only fills gaps."""
    cache = CACHE_DIR / "lookups" / f"msoa21_msoa11_{SCOPE_KEY}.json"
    cache.parent.mkdir(parents=True, exist_ok=True)
    if cache.exists():
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except ValueError:
            pass
    codes = sorted(set(get_lsoa_to_msoa().values()))
    out: dict = {}
    try:
        for i in range(0, len(codes), 120):
            batch = codes[i:i + 120]
            payload = {
                "where": "MSOA21CD IN (" + ",".join(f"'{c}'" for c in batch) + ")",
                "outFields": "MSOA11CD,MSOA21CD",
                "returnGeometry": "false",
                "f": "json",
                "resultRecordCount": 4000,
            }
            r = requests.post(MSOA11_MSOA21_LOOKUP, data=payload, timeout=180)
            r.raise_for_status()
            for feat in r.json().get("features", []):
                a = feat.get("attributes", {})
                if a.get("MSOA21CD") and a.get("MSOA11CD"):
                    out.setdefault(a["MSOA21CD"], [])
                    if a["MSOA11CD"] not in out[a["MSOA21CD"]]:
                        out[a["MSOA21CD"]].append(a["MSOA11CD"])
    except Exception as e:
        warn(f"msoa11->msoa21 lookup: {e}; 2011-coded figures will not be bridged")
        return {}
    if len(out) < len(codes) * 0.9:
        warn(f"msoa11->msoa21 lookup: only {len(out):,} of {len(codes):,} MSOAs; not caching")
        return out
    cache.write_text(json.dumps(out), encoding="utf-8")
    ok(f"ONS lookup: {len(out):,} MSOAs (2021) -> 2011 parents")
    return out


# OHID's own test of each small-area figure against England: whether the
# area's 95% confidence interval excludes the England value. Kept as one
# letter. "Better"/"Worse" where the indicator has a direction, "Higher"/
# "Lower" where it does not; anything else (not compared) is left blank.
FT_VS_ENGLAND_COL = "Compared to England value or percentiles"
FT_VS_CODES = {"Better": "B", "Worse": "W", "Similar": "S", "Higher": "H", "Lower": "L"}


def FT_MALE_ID(ind_id: int) -> int:
    """The id the men's figure of a sex-split Local Health series is kept under."""
    return ind_id * 10 + 1


def ft_msoa_drop_bare_zeros(ftm: pd.DataFrame) -> pd.DataFrame:
    """Local Health MSOA rows less any 0 that OHID gives no England
    comparison. Long-term unemployment (93098) counts Jobseeker's Allowance
    claims only, and 309 of London's MSOAs read exactly 0 with no comparison:
    too few claimants to test rather than none, so they are stored as no
    figure. Every other zero in the release carries a comparison."""
    if ftm is None or ftm.empty or "value" not in ftm.columns:
        return ftm
    vs = (ftm["vs_england"].fillna("").astype(str).str.strip()
          if "vs_england" in ftm.columns else pd.Series("", index=ftm.index))
    bare = (pd.to_numeric(ftm["value"], errors="coerce") == 0) & (vs == "")
    return ftm[~bare]


def _ft_msoa_variant(ind_id, df, name, want_msoa, rows, missing_codes,
                     detail: bool = False) -> int:
    """Latest period per MSOA, 2011 codes bridged, rows appended. Returns the
    number of figures bridged from 2011 codes.

    With detail=True (the extra series, FINGERTIPS_MSOA_EXTRA) each row also
    keeps its 95% limits and OHID's note, and a value OHID withholds (its
    note says so, or its limits run backwards, as _ft_value_with_ci reads a
    placeholder) is stored as missing, so ft_msoa_series_check counts it as
    withheld rather than as a figure."""
    bridged = 0
    if "Time period Sortable" in df.columns:
        df = df.sort_values("Time period Sortable").groupby("Area Code", as_index=False).tail(1)
    # 2021 MSOAs with no figure under their own code take their 2011
    # parent's (a split) or their parents' combined in proportion to the people
    # living in each in 2011 (a merge). Marked as estimated.
    lookup = get_msoa21_to_msoa11()
    have = set(df["Area Code"])
    by_code = df.set_index("Area Code")
    extra = []
    for m21 in want_msoa - have:
        parents = [p for p in lookup.get(m21, []) if p in by_code.index and p != m21]
        pairs = [(p, _tofloat(by_code.at[p, "Value"])) for p in parents]
        pairs = [(p, v) for p, v in pairs if v is not None and v == v]
        if pairs:
            pops = get_msoa11_population() if len(pairs) > 1 else {}
            w = [pops.get(p, 0.0) for p, _ in pairs] if pops else [1.0] * len(pairs)
            if not sum(w):
                w = [1.0] * len(pairs)
            row = by_code.loc[pairs[0][0]].copy()
            row["Value"] = repr(sum(v * wi for (_, v), wi in zip(pairs, w)) / sum(w))
            row["_estimated"] = True
            row["Area Code"] = m21
            extra.append(row)
    if extra:
        bridged += len(extra)
        extra_df = pd.DataFrame(extra)
        # A carried figure is an estimate for the new area, so OHID's
        # comparison with England, made for the 2011 area, does not apply.
        if FT_VS_ENGLAND_COL in extra_df.columns:
            extra_df[FT_VS_ENGLAND_COL] = ""
        df = pd.concat([df, extra_df], ignore_index=True)
    df = df[df["Area Code"].isin(want_msoa)]
    if df.empty:
        # Fingertips still publishes these against 2011 MSOA codes, and 39
        # London MSOAs were renumbered in 2021. A total miss means
        # something else changed.
        warn(f"fingertips msoa {ind_id}: no rows matched an in-scope MSOA")
        return bridged

    got = set(df["Area Code"])
    missing_codes |= (want_msoa - got)
    for _, row in df.iterrows():
        rec = {
            "MSOA21CD": row["Area Code"],
            "indicator_id": int(ind_id),
            "indicator_name": name,
            "value": _tofloat(row.get("Value")),
            "period": row.get("Time period", ""),
            "vs_england": FT_VS_CODES.get(str(row.get(FT_VS_ENGLAND_COL, "")).strip(), ""),
            "estimated": bool(row.get("_estimated", False) is True),
        }
        if detail:
            est = rec["estimated"]
            value, lower, upper = _ft_value_with_ci(row)
            note = _ft_note(row.get("Value note"))
            if any(w in note.lower() for w in FT_WITHHELD_NOTES):
                value = lower = upper = None
            # A figure carried from 2011 areas keeps its value, but the
            # limits were for the 2011 area.
            rec.update({"value": rec["value"] if est else value,
                        "lower_ci": None if est else lower,
                        "upper_ci": None if est else upper,
                        "value_note": note})
        rows.append(rec)

    return bridged


def run_fingertips_msoa() -> pd.DataFrame:
    rule("OHID Fingertips Local Health (MSOA)")
    cache_dir = CACHE_DIR / "fingertips_msoa"
    cache_dir.mkdir(parents=True, exist_ok=True)

    want_msoa = set(get_lsoa_to_msoa().values())
    rows: list = []
    missing_codes: set = set()

    bridged_total = 0
    for ind_id in FINGERTIPS_MSOA_INDICATORS:
        # England as the parent, not counties and UAs (502). Under 502,
        # Fingertips returned only the MSOAs it had re-parented since 2023,
        # which for the series last uploaded in 2022 was 77 MSOAs in all of
        # England: whole indicators went missing outside London. Under England
        # every series comes back complete. _p15 so old caches are not reused.
        cache = cache_dir / f"ind_{ind_id}_p15.csv"
        if not cache.exists():
            url = (f"https://fingertips.phe.org.uk/api/all_data/csv/by_indicator_id"
                   f"?indicator_ids={ind_id}"
                   f"&child_area_type_id={FINGERTIPS_MSOA_AREA_TYPE}"
                   f"&parent_area_type_id=15")
            try:
                r = _ft_get(url, timeout=180)
                if len(r.content.splitlines()) < 2:
                    warn(f"fingertips msoa {ind_id}: no rows; not caching")
                    continue
                cache.write_bytes(r.content)
                time.sleep(1.0)
            except Exception as e:
                warn(f"fingertips msoa {ind_id}: {e}")
                continue
        try:
            df = pd.read_csv(cache, dtype=str, low_memory=False)
        except pd.errors.EmptyDataError:
            continue
        if df.empty or "Area Code" not in df.columns:
            continue

        name = str(df["Indicator Name"].iloc[0]) if "Indicator Name" in df.columns else ""
        if "Area Type" in df.columns:
            df = df[df["Area Type"].astype(str).str.contains("MSOA", na=False)]
        # Persons where published. Life expectancy has no Persons figure, only
        # Female and Male, and taking the last row per MSOA mixed the two
        # without saying which: women keep this indicator's id, men get
        # FT_MALE_ID(id), and each says so in its name.
        variants = [(int(ind_id), df, name)]
        if "Sex" in df.columns:
            sexes = set(df["Sex"].dropna().astype(str))
            if "Persons" in sexes:
                variants = [(int(ind_id), df[df["Sex"] == "Persons"], name)]
            elif {"Female", "Male"} <= sexes:
                variants = [(int(ind_id), df[df["Sex"] == "Female"], name + " (women)"),
                            (FT_MALE_ID(int(ind_id)), df[df["Sex"] == "Male"], name + " (men)")]
        for out_id, df, name in variants:
            bridged_total += _ft_msoa_variant(out_id, df, name, want_msoa, rows, missing_codes)

    out = pd.DataFrame(rows)
    out_path = DATA_DIR / "outcomes" / "fingertips_msoa.parquet"
    out = carry_forward_thin(out_path, out, key="indicator_id", source="fingertips_msoa",
                             requested=set(FINGERTIPS_MSOA_INDICATORS),
                             cache_file=lambda k: cache_dir / f"ind_{int(k)}_p15.csv")
    out = write_parquet_guarded(out_path, out, source="fingertips_msoa")
    n_ind = out["indicator_id"].nunique() if not out.empty else 0
    ok(f"fingertips msoa: {len(out):,} rows, {n_ind} indicators across "
       f"{out['MSOA21CD'].nunique() if not out.empty else 0:,} MSOAs "
       f"-> {out_path.relative_to(REPO_ROOT)}")
    if bridged_total:
        info(f"fingertips msoa: {bridged_total:,} figures carried from 2011 MSOA codes to 2021 ones")
    if missing_codes:
        warn(f"fingertips msoa: {len(missing_codes):,} in-scope MSOAs had no figure "
             f"in at least one indicator (suppressed or not published)")
    return out


# ============================================================================
# SOURCE 5c: Fingertips Local Health at LA level
# ============================================================================
# The same Local Health indicators as above, fetched for whole LAs (districts
# and unitary authorities, area type 501). Without them the map's LA figure
# for a Local Health indicator is a population-weighted mean of ward figures,
# which for life expectancy and standardised ratios is not a valid average and
# disagreed with OHID's own LA figure by over two years for Newham. With them,
# borough_data.json carries OHID's figure under the same key (ft_<id>), and the
# map's boroughOvValue prefers it. OHID marks some LA figures (the City of
# London's life expectancy, for one) as not calculable; _ft_value_with_ci
# turns those placeholders into a missing value rather than a zero.
FINGERTIPS_LA_LOWER_TIER = 501   # districts and UAs, April 2023 boundaries


def _ft_la_latest(df: "pd.DataFrame", ind_id: int) -> list:
    """The latest LA figure per area from one Local Health indicator's CSV.

    Returns dicts of LAD25CD, indicator_id, value and period. Persons where
    published; for life expectancy, which has only Female and Male, women keep
    the id and men take FT_MALE_ID(id), as in the MSOA series, so both levels
    share one key.
    """
    if df is None or df.empty or "Area Code" not in df.columns:
        return []
    if "Area Type" in df.columns:
        at = df["Area Type"].astype(str)
        keep = ~at.str.contains("MSOA|England|Region", case=False, na=False)
        df = df[keep]
    variants = [(int(ind_id), df)]
    if "Sex" in df.columns:
        sexes = set(df["Sex"].dropna().astype(str))
        if "Persons" in sexes:
            variants = [(int(ind_id), df[df["Sex"] == "Persons"])]
        elif {"Female", "Male"} <= sexes:
            variants = [(int(ind_id), df[df["Sex"] == "Female"]),
                        (FT_MALE_ID(int(ind_id)), df[df["Sex"] == "Male"])]
    out = []
    for out_id, d in variants:
        if d.empty:
            continue
        if "Time period Sortable" in d.columns:
            d = d.sort_values("Time period Sortable", kind="stable")
        d = d.groupby("Area Code", as_index=False).tail(1)
        for _, row in d.iterrows():
            value, _lo, _hi = _ft_value_with_ci(row)
            out.append({"LAD25CD": str(row["Area Code"]), "indicator_id": int(out_id),
                        "value": value, "period": row.get("Time period", "")})
    return out


def run_fingertips_msoa_la() -> pd.DataFrame:
    rule("OHID Fingertips Local Health (LA)")
    cache_dir = CACHE_DIR / "fingertips_msoa_la"
    cache_dir.mkdir(parents=True, exist_ok=True)
    rows: list = []
    for ind_id in FINGERTIPS_MSOA_INDICATORS:
        cache = cache_dir / f"ind_{ind_id}_la{FINGERTIPS_LA_LOWER_TIER}.csv"
        if not cache.exists():
            url = (f"https://fingertips.phe.org.uk/api/all_data/csv/by_indicator_id"
                   f"?indicator_ids={ind_id}"
                   f"&child_area_type_id={FINGERTIPS_LA_LOWER_TIER}"
                   f"&parent_area_type_id=15")
            try:
                r = _ft_get(url, timeout=120)
                if len(r.content.splitlines()) < 2:
                    warn(f"fingertips local health LA {ind_id}: no rows; not caching")
                    continue
                cache.write_bytes(r.content)
                time.sleep(0.5)
            except Exception as e:
                warn(f"fingertips local health LA {ind_id}: {e}")
                continue
        try:
            df = pd.read_csv(cache, dtype=str, low_memory=False)
        except pd.errors.EmptyDataError:
            continue
        if df.empty or "Area Code" not in df.columns:
            continue
        df = ft_codes_to_current(df)
        df = df[df["Area Code"].isin(SCOPE_LADS)]
        rows.extend(_ft_la_latest(df, ind_id))
    out = pd.DataFrame(rows, columns=["LAD25CD", "indicator_id", "value", "period"])
    out_path = DATA_DIR / "outcomes" / "fingertips_msoa_la.parquet"
    out = carry_forward_thin(out_path, out, key="indicator_id", source="fingertips_msoa_la",
                             requested=set(FINGERTIPS_MSOA_INDICATORS),
                             cache_file=lambda k: cache_dir / f"ind_{int(k)}_la{FINGERTIPS_LA_LOWER_TIER}.csv")
    out = write_parquet_guarded(out_path, out, source="fingertips_msoa_la")
    n_ind = out["indicator_id"].nunique() if not out.empty else 0
    ok(f"fingertips local health LA: {len(out):,} rows, {n_ind} indicators "
       f"-> {out_path.relative_to(REPO_ROOT)}")
    return out


# ============================================================================
# SOURCE 5d: Fingertips Local Health, low birth weight and cancer incidence
# ============================================================================
# Low birth weight of live babies (93092, five years pooled) and the five
# cancer incidence ratios (93234 to 93238, England = 100), at MSOA as Local
# Health publishes them and for whole LAs. They have never been on the map,
# and whether Fingertips still returns MSOA rows for the cancer series could
# not be checked from here, so they are a source of their own,
# fingertips_msoa_extra: a series that does not come back cannot mark the
# established Local Health sources partial or failed, which would stop every
# region pack from publishing.
#
# Each series ships only if ft_msoa_series_check passes it for this scope.
# OHID's own blanks are taken out first: a value its note says is withheld, a
# placeholder whose limits run backwards, and a 0 with no England comparison
# (ft_msoa_drop_bare_zeros). A series is then dropped, with a log line, when
# it has no figures, when its figures cover under FT_MSOA_MIN_COVER of the
# scope's MSOAs, or when its median 95% interval is wider either side than
# FT_MSOA_CI_REL_MAX of the figure. A dropped series writes no rows, so the
# map leaves it out (the no-empty-indicator rule), and indicators.js lists it
# under known_gaps, so a region where OHID publishes too little for it is not
# held back by London having it (scripts/check_region_pack.py). Its LA
# figures ship only beside its MSOA ones.
#
# The id and the words its Fingertips name must hold. The names are OHID's
# (scripts/fingertips_metadata.json); the words allow for the longer form a
# catalogue mirror gives, "Incidence of all cancers, standardised incidence
# ratio", so a rewording does not drop a real series, while a wrong id does.
FINGERTIPS_MSOA_EXTRA = [
    (93092, "Low birth weight of live babies",     ("low birth weight",)),
    (93234, "Cancer incidence: all cancers",       ("incidence", "all cancers")),
    (93235, "Cancer incidence: breast cancer",     ("incidence", "breast")),
    (93236, "Cancer incidence: colorectal cancer", ("incidence", "colorectal")),
    (93237, "Cancer incidence: lung cancer",       ("incidence", "lung")),
    (93238, "Cancer incidence: prostate cancer",   ("incidence", "prostate")),
]
FT_MSOA_EXTRA_PARQUET = "fingertips_msoa_extra.parquet"
FT_MSOA_EXTRA_LA_PARQUET = "fingertips_msoa_extra_la.parquet"
FT_MSOA_EXTRA_CHECKS = "fingertips_msoa_extra_checks.json"
# A series with figures for fewer of the scope's MSOAs than this is not
# shipped. Every established Local Health series covers over 90% of London's
# MSOAs, and long-term claimants, the thinnest, 65% once its bare zeros go.
FT_MSOA_MIN_COVER = 0.5
# A median interval wider either side than the figure itself (a ratio of
# 100 with limits of 0 and 200) cannot tell one MSOA from another.
FT_MSOA_CI_REL_MAX = 1.0
FT_MSOA_EXTRA_COLUMNS = ["MSOA21CD", "indicator_id", "indicator_name", "value", "period",
                         "vs_england", "estimated", "lower_ci", "upper_ci", "value_note"]


def ft_base_id(out_id: int) -> int:
    """The Fingertips id a Local Health series was fetched under: the men's
    figures of a sex-split series are kept under FT_MALE_ID(id), a sixth
    digit on the five-digit id."""
    return int(out_id) // 10 if int(out_id) > 99999 else int(out_id)


def ft_msoa_series_check(rows: list, n_scope: int, *, min_cover: float = FT_MSOA_MIN_COVER,
                         ci_rel_max: float = FT_MSOA_CI_REL_MAX) -> tuple:
    """(ships, why, facts) for one Local Health MSOA series in this scope.

    rows are the series' in-scope rows as _ft_msoa_variant writes them with
    detail=True. A figure counts when it has a value and is not a bare zero
    (a 0 with no England comparison, which ft_msoa_drop_bare_zeros drops);
    the rest are withheld. The series ships when it has figures for at least
    min_cover of the n_scope MSOAs and, where at least three figures carry
    limits, the median half-width of their 95% intervals is at most
    ci_rel_max of the figure."""
    def usable(r):
        v = r.get("value")
        if v is None or v != v:
            return False
        return not (float(v) == 0 and not str(r.get("vs_england") or "").strip())
    figs = [r for r in rows if usable(r)]
    n_rows, n_val = len(rows), len(figs)
    withheld = n_rows - n_val
    cover = n_val / n_scope if n_scope else 0.0
    rel = sorted((float(r["upper_ci"]) - float(r["lower_ci"])) / 2 / abs(float(r["value"]))
                 for r in figs
                 if r.get("lower_ci") is not None and r.get("upper_ci") is not None
                 and r["lower_ci"] == r["lower_ci"] and r["upper_ci"] == r["upper_ci"]
                 and float(r["value"]) != 0)
    med = None
    if len(rel) >= 3:
        m = len(rel) // 2
        med = rel[m] if len(rel) % 2 else (rel[m - 1] + rel[m]) / 2
    facts = {"rows": n_rows, "figures": n_val, "withheld": withheld, "msoas": n_scope,
             "cover": round(cover, 3), "ci_half_width": None if med is None else round(med, 3)}
    if not n_rows:
        return False, "no figures for any MSOA in this area", facts
    if not n_val:
        return False, f"all {n_rows} of its MSOA figures are withheld", facts
    if cover < min_cover:
        return False, (f"figures for only {n_val:,} of the {n_scope:,} MSOAs ({cover:.0%}, under "
                       f"{min_cover:.0%}), with {withheld:,} withheld"), facts
    if med is not None and med > ci_rel_max:
        return False, (f"its median 95% interval runs {med:.0%} of the figure either side "
                       f"(over {ci_rel_max:.0%}), so it cannot tell one MSOA from another"), facts
    return True, (f"figures for {n_val:,} of the {n_scope:,} MSOAs ({cover:.0%}), {withheld:,} withheld"
                  + ("" if med is None else f", median 95% interval {med:.0%} of the figure either side")), facts


def _ft_extra_download(cache: "Path", url: str) -> tuple:
    """(frame or None, error or ""). A frame of None with no error means
    Fingertips answered with no rows, which is not cached, so the next run
    asks again. An error is a download that failed."""
    if not cache.exists():
        try:
            r = _ft_get(url, timeout=180)
        except Exception as e:
            return None, f"{type(e).__name__}: {e}"
        if len(r.content.splitlines()) < 2:
            return None, ""
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(r.content)
        time.sleep(1.0)
    try:
        df = pd.read_csv(cache, dtype=str, low_memory=False)
    except (pd.errors.EmptyDataError, OSError, ValueError):
        return None, ""
    if df.empty or "Area Code" not in df.columns:
        return None, ""
    return df, ""


def _ft_name_has(actual: str, words: tuple) -> bool:
    """Whether a Fingertips name holds every one of words, as normalised."""
    n = iod_norm(actual)
    return all(iod_norm(w) in n for w in words)


def run_fingertips_msoa_extra() -> "pd.DataFrame | None":
    rule("OHID Fingertips Local Health: low birth weight and cancer incidence (MSOA and LA)")
    cache_dir = CACHE_DIR / "fingertips_msoa_extra"
    cache_dir.mkdir(parents=True, exist_ok=True)
    out_dir = DATA_DIR / "outcomes"
    out_dir.mkdir(parents=True, exist_ok=True)
    want_msoa = set(get_lsoa_to_msoa().values())
    rows: list = []
    checks: dict = {}
    errors: list = []
    shipped: set = set()
    failed_ids: set = set()
    names: dict = {}
    try:
        prev_checks = json.loads((out_dir / FT_MSOA_EXTRA_CHECKS).read_text(encoding="utf-8")).get("series") or {}
    except (OSError, ValueError, AttributeError):
        prev_checks = {}
    for ind_id, label, words in FINGERTIPS_MSOA_EXTRA:
        url = (f"https://fingertips.phe.org.uk/api/all_data/csv/by_indicator_id"
               f"?indicator_ids={ind_id}&child_area_type_id={FINGERTIPS_MSOA_AREA_TYPE}"
               f"&parent_area_type_id=15")
        df, err = _ft_extra_download(cache_dir / f"ind_{ind_id}_p15.csv", url)
        if err:
            warn(f"fingertips msoa extra {ind_id} ({label}): download failed ({err})")
            failed_ids |= {ind_id, FT_MALE_ID(ind_id)}
            # A series the last check dropped for OHID's reasons stays dropped
            # on that verdict; one with no verdict yet is a failure (unless
            # last run's figures stand in for it, below).
            before = {k: v for k, v in prev_checks.items() if k in (str(ind_id), str(FT_MALE_ID(ind_id)))
                      and isinstance(v, dict)}
            if before and all(v.get("kind") == "publisher" and not v.get("ships") for v in before.values()):
                for k, v in before.items():
                    checks[k] = dict(v, why=f"{v.get('why', '')} (last run's check; this run could not "
                                            f"download it: {err})")
                continue
            errors.append(({ind_id, FT_MALE_ID(ind_id)}, f"{ind_id} ({label}): {err}"))
            checks[str(ind_id)] = {"ships": False, "kind": "error", "why": f"download failed: {err}"}
            continue
        if df is None:
            why = "Fingertips returned no MSOA rows"
            warn(f"fingertips msoa extra {ind_id} ({label}): {why}; not shipped")
            checks[str(ind_id)] = {"ships": False, "kind": "publisher", "why": why}
            continue
        actual = str(df["Indicator Name"].iloc[0]) if "Indicator Name" in df.columns and len(df) else ""
        if actual and not _ft_name_has(actual, words):
            why = f"the download is {actual!r}, not {label!r}"
            warn(f"fingertips msoa extra {ind_id}: {why}; not shipped. Check the id.")
            errors.append((set(), f"{ind_id}: {why}"))
            checks[str(ind_id)] = {"ships": False, "kind": "name", "why": why}
            continue
        name = actual or label
        if "Area Type" in df.columns:
            df = df[df["Area Type"].astype(str).str.contains("MSOA", na=False)]
        # As run_fingertips_msoa: Persons where published, else women under
        # the id and men under FT_MALE_ID(id). Breast and prostate cancer
        # come for one sex only and are kept as they are.
        variants = [(int(ind_id), df, name)]
        if "Sex" in df.columns:
            sexes = set(df["Sex"].dropna().astype(str))
            if "Persons" in sexes:
                variants = [(int(ind_id), df[df["Sex"] == "Persons"], name)]
            elif {"Female", "Male"} <= sexes:
                variants = [(int(ind_id), df[df["Sex"] == "Female"], name + " (women)"),
                            (FT_MALE_ID(int(ind_id)), df[df["Sex"] == "Male"], name + " (men)")]
        for out_id, d, nm in variants:
            vrows: list = []
            if not d.empty:
                _ft_msoa_variant(out_id, d, nm, want_msoa, vrows, set(), detail=True)
            ships, why, facts = ft_msoa_series_check(vrows, len(want_msoa))
            checks[str(out_id)] = {"ships": ships, "kind": "publisher", "why": why, **facts}
            names[out_id] = nm
            if ships:
                ok(f"fingertips msoa extra {out_id} ({nm}): ships, {why}")
                rows.extend(vrows)
                shipped.add(out_id)
            else:
                warn(f"fingertips msoa extra {out_id} ({nm}): not shipped, {why}")

    out = pd.DataFrame(rows, columns=FT_MSOA_EXTRA_COLUMNS)
    out_path = out_dir / FT_MSOA_EXTRA_PARQUET
    # Last run's rows come back only for a series that this run could not
    # download (or that came back thin). One the checks dropped stays dropped.
    out = carry_forward_thin(out_path, out, key="indicator_id", source="fingertips_msoa_extra",
                             requested=shipped | failed_ids,
                             cache_file=lambda k: cache_dir / f"ind_{int(k)}_p15.csv")
    kept_ids = set(int(i) for i in out["indicator_id"].unique()) if len(out) else set()
    for i in sorted(kept_ids - shipped):
        checks.setdefault(str(i), {})["ships"] = True
        checks[str(i)]["kind"] = "carried"
        checks[str(i)]["why"] = "last run's figures, kept because this run could not download them"
    write_json_atomic(out_dir / FT_MSOA_EXTRA_CHECKS,
                      {"checked": datetime.now(timezone.utc).isoformat(), "series": checks}, pretty=True)

    # OHID's own LA figures, for the series that ship at MSOA only.
    la_rows: list = []
    base_ids = sorted({ft_base_id(i) for i in kept_ids})
    for ind_id in base_ids:
        url = (f"https://fingertips.phe.org.uk/api/all_data/csv/by_indicator_id"
               f"?indicator_ids={ind_id}&child_area_type_id={FINGERTIPS_LA_LOWER_TIER}"
               f"&parent_area_type_id=15")
        df, err = _ft_extra_download(cache_dir / f"ind_{ind_id}_la{FINGERTIPS_LA_LOWER_TIER}.csv", url)
        if err:
            warn(f"fingertips msoa extra LA {ind_id}: download failed ({err}); "
                 "its LA figures are worked out from wards instead")
            continue
        if df is None:
            info(f"fingertips msoa extra LA {ind_id}: no LA rows; its LA figures are worked out from wards")
            continue
        df = ft_codes_to_current(df)
        df = df[df["Area Code"].isin(SCOPE_LADS)]
        la_rows.extend(r for r in _ft_la_latest(df, ind_id) if r["indicator_id"] in kept_ids)
    la = pd.DataFrame(la_rows, columns=["LAD25CD", "indicator_id", "value", "period"])
    la_path = out_dir / FT_MSOA_EXTRA_LA_PARQUET
    la = carry_forward_thin(la_path, la, key="indicator_id", source="fingertips_msoa_extra",
                            requested=kept_ids)
    la = la[la["indicator_id"].isin(kept_ids)] if len(la) else la

    # A download that failed is a failure only when no earlier figures
    # stand in for it; one whose last figures were kept is "partial"
    # (carry_forward_thin). A wrong name is always a failure.
    errors = [msg for ids, msg in errors if not (ids & kept_ids)]
    if out.empty:
        # Nothing ships. Written empty rather than kept, because a series the
        # checks dropped must leave the map, and the old file would put it back.
        write_parquet_atomic(out_path, out)
        write_parquet_atomic(la_path, la.iloc[0:0] if len(la) else la)
        _record_write(out_path, 0)
        if errors:
            raise RuntimeError("no low birth weight or cancer incidence series could be downloaded "
                               "from Fingertips: " + "; ".join(errors))
        msg = ("no series shipped: " + "; ".join(f"{i} {c.get('why', '')}" for i, c in checks.items()))
        warn(f"fingertips_msoa_extra: {msg}")
        _SOURCE_SKIPPED["fingertips_msoa_extra"] = msg[:2000]
        return None
    write_parquet_atomic(out_path, out)
    _record_write(out_path, len(out))
    write_parquet_atomic(la_path, la)
    _record_write(la_path, len(la))
    ok(f"fingertips msoa extra: {len(out):,} rows, {out['indicator_id'].nunique()} series across "
       f"{out['MSOA21CD'].nunique():,} MSOAs -> {out_path.relative_to(REPO_ROOT)}; "
       f"{len(la):,} LA rows -> {la_path.relative_to(REPO_ROOT)}")
    if errors:
        _SOURCE_ERROR["fingertips_msoa_extra"] = "; ".join(errors)[:2000]
    return out


# ============================================================================
# SOURCE 6: ONS datasets found from their landing page
# ============================================================================
# Two ONS releases are read here: the small area income estimates (SAIE) and
# the health state life expectancies for MSOAs. ONS gives each dataset a
# landing page that lists its editions, and every page on the ONS site is also
# JSON at <page>/data: a landing page lists its editions ("datasets"), an
# edition its files ("downloads"), and a file is served at
# /file?uri=<edition>/<file>. So the file is found from the landing page by
# the edition's name and date rather than remembered as an asset URL, which
# would point at one release. Where the landing page does not answer, the
# release's bulletin is read for its related datasets, and then ONS's search
# for the dataset's title; the landing page's HTML links are the last resort.
# Every page that is read and every file that is found is logged, so a first
# run in GitHub Actions shows exactly where each file came from.
ONS_BASE = "https://www.ons.gov.uk"
ONS_SEARCH_API = "https://api.beta.ons.gov.uk/v1/search"
# The last 2011 MSOA code. Every MSOA redrawn at the 2021 Census has a code
# above it, so a file holding none is on the 2011 boundaries.
MSOA_LAST_2011 = "E02006934"


def _ons_get_json(path: str, sess, timeout: int = 60):
    """The JSON behind an ONS page (path, or a full URL), or None."""
    url = path if path.startswith("http") else ONS_BASE + path
    url = url.split("?")[0].rstrip("/") + "/data"
    try:
        r = sess.get(url, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        info(f"ons: {url} did not answer as JSON ({type(e).__name__}: {e})")
        return None


def _ons_title(j) -> str:
    d = (j or {}).get("description") or {}
    return str(d.get("title") or (j or {}).get("title") or "")


def _ons_landing_files(uri: str, sess) -> list:
    """[{"edition", "uri", "file", "url", "release_date"}] for the files of
    every edition on one dataset landing page; [] when it has none."""
    out: list = []
    j = _ons_get_json(uri, sess)
    for ed in ((j or {}).get("datasets") or []):
        euri = str((ed or {}).get("uri") or "").rstrip("/")
        if not euri:
            continue
        ej = _ons_get_json(euri, sess) or {}
        desc = ej.get("description") or {}
        edition = str(desc.get("edition") or euri.rsplit("/", 1)[-1])
        for d in (ej.get("downloads") or []):
            f = str((d or {}).get("file") or "").strip()
            if f:
                out.append({"edition": edition, "uri": euri, "file": f,
                            "url": f"{ONS_BASE}/file?uri={euri}/{f}",
                            "release_date": str(desc.get("releaseDate") or "")})
    if out:
        return out
    # The page itself, for a landing page whose JSON did not list editions.
    try:
        r = sess.get(ONS_BASE + uri, timeout=60)
        r.raise_for_status()
        for href in dict.fromkeys(re.findall(r'href="(/file\?uri=[^"]+)"', r.text)):
            path = href.split("uri=", 1)[1]
            f = path.rsplit("/", 1)[-1]
            euri = path.rsplit("/", 1)[0]
            out.append({"edition": euri.rsplit("/", 1)[-1], "uri": euri, "file": f,
                        "url": ONS_BASE + href.replace("&amp;", "&"), "release_date": ""})
    except Exception as e:
        info(f"ons: {ONS_BASE + uri} did not answer ({type(e).__name__}: {e})")
    return out


def ons_dataset_files(landing: tuple, bulletins: tuple = (), search: str = "",
                      title_words: tuple = ()) -> list:
    """Every file of the first ONS dataset found, from its landing page.

    landing are known landing page paths, tried first. bulletins are paths
    of the release's bulletin, whose related datasets are tried next, and
    search is a title to look for with ONS's search; a page found either way
    is used only if its title holds every one of title_words."""
    sess = browser_session(referer=ONS_BASE + "/")
    tried: set = set()

    def titled(uri):
        if not title_words:
            return True
        t = iod_norm(_ons_title(_ons_get_json(uri, sess)))
        return bool(t) and all(iod_norm(w) in t for w in title_words)

    def attempt(uri, check):
        uri = "/" + str(uri or "").split("ons.gov.uk", 1)[-1].lstrip("/")
        if uri in tried or "/datasets/" not in uri:
            return []
        tried.add(uri)
        if check and not titled(uri):
            info(f"ons: {uri} is not the dataset wanted; skipped")
            return []
        files = _ons_landing_files(uri, sess)
        if files:
            info(f"ons: {len(files)} file(s) on {ONS_BASE}{uri}")
        return files

    for uri in landing:
        files = attempt(uri, False)
        if files:
            return files
    for b in bulletins:
        j = _ons_get_json(b, sess) or {}
        rels = [(rel or {}).get("uri") for rel in (j.get("relatedData") or []) + (j.get("relatedDatasets") or [])]
        if not rels:
            # The bulletin page itself, whose related data section links its
            # datasets; "latest" redirects to the newest edition.
            try:
                r = sess.get(ONS_BASE + b, timeout=60)
                r.raise_for_status()
                rels = list(dict.fromkeys(m.split("?")[0].split("#")[0] for m in
                                          re.findall(r'href="((?:https://www\.ons\.gov\.uk)?/[^"]*/datasets/[^"]+)"', r.text)))
            except Exception as e:
                info(f"ons: {ONS_BASE + b} did not answer ({type(e).__name__}: {e})")
        for rel in rels:
            files = attempt(rel, True)
            if files:
                return files
    if search:
        try:
            r = sess.get(ONS_SEARCH_API, params={"q": search, "content_type": "dataset_landing_page"},
                         timeout=60)
            r.raise_for_status()
            j = r.json()
            items = j.get("items") or j.get("results") or []
        except Exception as e:
            info(f"ons: search for {search!r} did not answer ({type(e).__name__}: {e})")
            items = []
        for it in items:
            files = attempt((it or {}).get("uri") or (it or {}).get("url"), True)
            if files:
                return files
    return []


def ons_year_of(text: str) -> int:
    """The latest year an edition's name or path names, or 0."""
    years = [int(y) for y in re.findall(r"(?:19|20)\d{2}", str(text or ""))]
    return max(years) if years else 0


# Words in a file name that mark it as about the data rather than the data.
_ONS_NOT_DATA = ("technical", "quality", "notes", "methodology", "guidance", "metadata", "lookup", "glossary")


def ons_pick_latest(files: list, exts: tuple, words: tuple = ()) -> "dict | None":
    """The file of the newest edition (by the latest year its name or path
    names, then its release date), in the first of exts it is published in,
    and within one edition a data file before a notes or quality one.
    words, when given, must all be in the file's name or edition (as
    normalised)."""
    def named(f):
        t = iod_norm(f.get("edition", "") + " " + f.get("file", "") + " " + f.get("uri", ""))
        return all(iod_norm(w) in t for w in words)
    cands = [f for f in files if named(f) and str(f.get("file", "")).lower().endswith(exts)]
    if not cands:
        return None
    def rank(f):
        fn = iod_norm(f.get("file", ""))
        return (max(ons_year_of(f.get("edition")), ons_year_of(f.get("uri"))),
                str(f.get("release_date") or ""),
                not any(w in fn for w in _ONS_NOT_DATA),
                -exts.index(next(e for e in exts if f["file"].lower().endswith(e))))
    return max(cands, key=rank)


def _ons_download(f: dict, cache: "Path", source: str) -> "Path | None":
    """Download one ONS file into cache, or None (with a warning)."""
    url = f["url"]
    dest = cache / re.sub(r"[^A-Za-z0-9._-]", "_", f.get("file") or url.rsplit("/", 1)[-1])
    try:
        info(f"{source}: downloading {url} (edition {f.get('edition', '')!r})")
        r = browser_session(referer=ONS_BASE + "/").get(url, timeout=300)
        r.raise_for_status()
        head = r.content[:200].lstrip().lower()
        if len(r.content) < 10_000 or head.startswith((b"<!doctype", b"<html")):
            raise RuntimeError(f"got {len(r.content):,} bytes that are not the file")
        cache.mkdir(parents=True, exist_ok=True)
        if dest.suffix.lower() == ".zip":
            # A zipped release: its largest spreadsheet is the data.
            with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                inner = sorted((i for i in z.infolist() if i.filename.lower().endswith((".xlsx", ".xls", ".csv"))
                                and not any(w in iod_norm(i.filename) for w in _ONS_NOT_DATA)),
                               key=lambda i: i.file_size, reverse=True)
                if not inner:
                    raise RuntimeError(f"{dest.name} holds no spreadsheet")
                dest = cache / re.sub(r"[^A-Za-z0-9._-]", "_", inner[0].filename.rsplit("/", 1)[-1])
                write_bytes_atomic(dest, z.read(inner[0]))
                info(f"{source}: took {inner[0].filename} from the zip")
        else:
            write_bytes_atomic(dest, r.content)
        write_json_atomic(dest.with_suffix(dest.suffix + ".release.json"), f, pretty=True)
    except Exception as e:
        warn(f"{source}: download failed ({type(e).__name__}: {e})")
        return None
    return dest


def ons_code_header(v) -> bool:
    """Whether a header cell names an area code column: "MSOA code",
    "MSOA21CD", "Area code" and the like."""
    n = iod_norm(v)
    return bool(re.search(r"\b(?:msoa|area|geography|lad|local authority|ons)\s*(?:20)?(?:11|21)?\s*(?:codes?|cd)\b"
                          r"|\bmsoa(?:11|21)cd\b|\bareacd\b|^codes?$", n))


def ons_tables(src: "Path") -> list:
    """[(sheet name, the text above its header, frame)] for every table of a
    release file with an area code header: one for a CSV, one per sheet of a
    workbook, each read from its header row."""
    if src.suffix.lower() == ".csv":
        try:
            df = pd.read_csv(src, encoding="utf-8-sig", dtype=str, low_memory=False)
        except UnicodeDecodeError:
            df = pd.read_csv(src, encoding="latin-1", dtype=str, low_memory=False)
        return [(src.stem, "", df)]
    xl = pd.ExcelFile(src)
    out = []
    for name in xl.sheet_names:
        try:
            top = xl.parse(name, header=None, nrows=25, dtype=str)
        except Exception:
            continue
        hdr = None
        for i, row in enumerate(top.itertuples(index=False)):
            if any(isinstance(v, str) and ons_code_header(v) for v in row):
                hdr = i
                break
        if hdr is None:
            continue
        title = " ".join(str(v) for v in top.iloc[:hdr].to_numpy().ravel()
                         if isinstance(v, str) and v.strip())
        out.append((str(name), title, xl.parse(name, header=hdr, dtype=str)))
    return out


def ons_code_col(df: "pd.DataFrame"):
    """The area code column: of the headers that name one, the column
    holding the most MSOA codes, else the most English area codes, so a
    table with both an MSOA and an LA code column reads by MSOA."""
    cands = [c for c in df.columns if isinstance(c, str) and ons_code_header(c)]
    if not cands:
        return None
    def score(c):
        v = df[c].astype(str).str.strip()
        return (int(v.str.match(r"^E02\d{6}$").sum()), int(v.str.match(r"^E0\d{7}$").sum()))
    return max(cands, key=score)


def ons_num(s: "pd.Series") -> "pd.Series":
    """Figures as numbers: thousands separators and pound signs set aside,
    and ONS's markers for a missing figure (":", "[x]", "..") read as none."""
    t = s.astype(str).str.replace(r"[£,\s]", "", regex=True)
    return pd.to_numeric(t.where(~t.str.fullmatch(r"\[?[a-z:.\-]*\]?", case=False)), errors="coerce")


def ons_period(text: str) -> str:
    """The period an edition describes, written as the map writes periods:
    "Financial year ending 2023" as "2022/23", and "2019 to 2023" as
    "2019 - 23", which periodWords reads as "2019 to 2023". "" when the text
    names no period."""
    t = iod_norm(text)
    m = re.search(r"(?:financial year ending|financialyearending|\bfye)\s*(?:march\s*)?((?:19|20)\d{2})", t)
    if m:
        y = int(m.group(1))
        return f"{y - 1}/{str(y)[2:]}"
    m = re.search(r"((?:19|20)\d{2})\s*(?:to|and)\s*((?:19|20)\d{2})", t) or \
        re.search(r"((?:19|20)\d{2})to((?:19|20)\d{2})", str(text or "").lower())
    if m:
        return f"{m.group(1)} - {m.group(2)[2:]}"
    return ""


def _ons_committed(subdir: str, name: str):
    """The committed national parquet for this scope (copied in from the
    national one when a region has none), or None."""
    out_path = DATA_DIR / subdir / name
    national = NATIONAL_DATA_DIR / subdir / name
    if not out_path.exists() and national.exists() and national != out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(national.read_bytes())
    if not out_path.exists():
        return None
    try:
        return pd.read_parquet(out_path)
    except Exception as e:
        warn(f"{name} unreadable ({e})")
        return None


def _ons_write(subdir: str, name: str, out: "pd.DataFrame", source: str, what: str) -> "pd.DataFrame":
    """Write the parquet, unless it already holds exactly this."""
    out_path = DATA_DIR / subdir / name
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        try:
            previous = pd.read_parquet(out_path)
        except Exception:
            previous = None
        if previous is not None and previous.equals(out):
            ok(f"{source}: {len(out):,} {what}, unchanged from the committed parquet")
            _record_write(out_path, len(out))
            return out
    out = write_parquet_guarded(out_path, out, source=source)
    ok(f"{source}: {len(out):,} {what} -> {out_path.relative_to(REPO_ROOT)}")
    return out


def _ons_static_file(source: str, cache: "Path", prev, want: list, override: str,
                     find, how: str):
    """(raw file or None, committed frame or None) for a static ONS release:
    a file dropped in cache wins; else a complete committed parquet is used
    with no download; else the file is found (find() -> file dict) and
    downloaded. Raises, or marks the source failed keeping prev, with what
    to do, when there is no file."""
    raw = sorted((p for p in cache.glob("*") if p.suffix.lower() in (".xlsx", ".xls", ".csv")),
                 key=lambda p: p.stat().st_size, reverse=True) if cache.is_dir() else []
    if raw:
        return raw[0], None
    if prev is not None and len(prev) and all(k in prev.columns for k in want):
        ok(f"{source}: {len(prev):,} rows from the committed parquet "
           "(static between releases; no download needed)")
        return None, prev
    f = {"url": override, "file": override.split("?")[0].rsplit("/", 1)[-1], "edition": ""} if override else find()
    got = _ons_download(f, cache, source) if f else None
    if got is None:
        msg = (f"the {how} file could not be found or downloaded from ONS, so "
               + ("the figures stay as last committed" if prev is not None and len(prev)
                  else "there are no figures yet")
               + f". Download it from the ONS dataset page into {cache} and rerun "
               f"`python fetch_all_data.py --only {source}`.")
        if prev is not None and len(prev):
            _iod_fail(source, msg)
            return None, prev
        raise RuntimeError(msg)
    return got, None


def _ons_release(src: "Path") -> dict:
    """What the download step recorded about a file's edition, or {}."""
    p = src.with_suffix(src.suffix + ".release.json")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _ons_has(text: str, phrases: tuple) -> bool:
    """Whether normalised text holds any of phrases, each as whole words."""
    t = " " + iod_norm(text) + " "
    return any(" " + iod_norm(p) + " " in t for p in phrases)


# ---------------------------------------------------------------------------
# ONS small area income estimates (SAIE), MSOA, financial year ending 2023
# ---------------------------------------------------------------------------
# Modelled mean household income for each MSOA in England and Wales, from the
# Family Resources Survey and Census and administrative data, with 95%
# confidence intervals. The FYE 2023 edition (10 December 2025) is the first
# on the 2021 MSOAs; earlier editions are on 2011 ones and are not used.
# Three of its measures are taken, each a mean in pounds a year: total
# household income, and net household income before and after housing costs,
# which ONS equivalises (adjusts for the size and make-up of the household).
# Static between releases, like the IMD: the committed parquet is used as it
# is once complete. To take a new edition, delete
# data/demographics/saie_msoa.parquet or drop the file in .cache/saie/.
SAIE_LANDING = ("/employmentandlabourmarket/peopleinwork/earningsandworkinghours/datasets/"
                "smallareaincomeestimatesformiddlelayersuperoutputareasenglandandwales",)
SAIE_BULLETINS = ("/peoplepopulationandcommunity/personalandhouseholdfinances/incomeandwealth/"
                  "bulletins/smallareamodelbasedincomeestimates/latest",)
SAIE_SEARCH = "Income estimates for small areas, England and Wales"
SAIE_TITLE_WORDS = ("income", "small areas")
SAIE_URL_OVERRIDE = os.environ.get("SAIE_URL", "")
SAIE_CACHE = CACHE_DIR / "saie"
SAIE_PARQUET = "saie_msoa.parquet"
# (key, phrases that name the measure, phrases that rule a text out). "Net
# annual income" with no housing costs in its name is not taken.
SAIE_MEASURES = (
    ("saie_total_income", ("total annual income", "total household income", "total income"), ("net",)),
    ("saie_net_bhc",      ("before housing costs", "before housing cost"), ("after housing",)),
    ("saie_net_ahc",      ("after housing costs", "after housing cost"), ("before housing",)),
)
SAIE_KEYS = [m[0] for m in SAIE_MEASURES]
# The interval columns kept beside each figure at MSOA, the level ONS
# publishes them for.
CI_LOWER, CI_UPPER = "_lci", "_uci"


def is_interval_key(k: str) -> bool:
    """A 95% limit kept beside a figure (saie_net_bhc_lci), not a figure."""
    return str(k).endswith((CI_LOWER, CI_UPPER))


def _saie_role(h: str) -> str:
    """lower, upper, width (the interval's size, not used) or estimate."""
    t = " " + iod_norm(h) + " "
    if " lower " in t or " lci " in t or " lcl " in t:
        return "lower"
    if " upper " in t or " uci " in t or " ucl " in t:
        return "upper"
    if any(w in t for w in (" interval ", " confidence ", " limit ", " standard error ")):
        return "width"
    return "estimate"


def saie_parse(tables: list) -> tuple:
    """(frame, problems, matched) from the tables of a SAIE workbook.

    A column is a measure's when its header names it, or when the sheet's
    name and title name exactly one measure and the header names none (a
    sheet per measure, whose limits are headed "Lower confidence limit (£)").
    Only rows with an English MSOA code are read. A measure matched by two
    columns, on the wrong scale, or found nowhere is reported and not used;
    so are limits that do not bracket the figure. Raises RuntimeError when no
    table has English MSOA codes, or when the file is on the 2011 MSOAs."""
    found: dict = {k: {"estimate": [], "lower": [], "upper": []} for k in SAIE_KEYS}
    all_codes: set = set()
    for name, title, df in tables:
        code_col = ons_code_col(df)
        if code_col is None:
            continue
        codes = df[code_col].astype(str).str.strip()
        keep = codes.str.match(r"^E02\d{6}$").to_numpy()
        if not keep.any():
            continue
        rows, rcodes = df[keep], codes[keep]
        all_codes |= set(rcodes)
        ctx = name + " " + title
        ctx_m = [k for k, phr, ex in SAIE_MEASURES if _ons_has(ctx, phr) and not _ons_has(ctx, ex)]
        for c in rows.columns:
            if c == code_col or not isinstance(c, str):
                continue
            own = [k for k, phr, ex in SAIE_MEASURES if _ons_has(c, phr) and not _ons_has(c, ex)]
            if len(own) == 1:
                key = own[0]
            elif not own and len(ctx_m) == 1 and not _ons_has(c, ("code", "name", "region", "country",
                                                                 "local authority", "msoa")):
                key = ctx_m[0]
            else:
                continue
            role = _saie_role(c)
            if role == "width":
                continue
            found[key][role].append((name, c, pd.Series(ons_num(rows[c]).to_numpy(), index=rcodes.to_numpy())))
    if not all_codes:
        raise RuntimeError("no table in the file has English MSOA codes (sheets: "
                           + ", ".join(str(t[0]) for t in tables) + ")")
    if not any(c > MSOA_LAST_2011 for c in all_codes):
        raise RuntimeError(f"the file's MSOA codes are all {MSOA_LAST_2011} or below, so it is on the "
                           "2011 MSOAs (an edition before FYE 2023); it is not used")
    idx = pd.Index(sorted(all_codes), name="MSOA21CD")
    cols: dict = {}
    problems: list = []
    matched: dict = {}
    for key in SAIE_KEYS:
        est = found[key]["estimate"]
        if not est:
            problems.append(f"{key}: no column for it in any sheet")
            continue
        if len(est) > 1:
            problems.append(f"{key}: {len(est)} columns match (" + "; ".join(f"'{s}': '{c}'" for s, c, _ in est[:4])
                            + "), so none is used")
            continue
        sheet, col, s = est[0]
        s = s[~s.index.duplicated()].reindex(idx)
        v = s.dropna()
        if v.empty:
            problems.append(f"{key} ('{sheet}': '{col}') has no figures")
            continue
        med = float(v.median())
        if not 5_000 <= med <= 300_000:
            problems.append(f"{key} ('{sheet}': '{col}') has a median of {med:,.0f}, which is not pounds a "
                            "year (a weekly or thousands figure?); not used")
            continue
        if s.notna().sum() < 0.9 * len(idx):
            problems.append(f"{key} ('{sheet}': '{col}') has figures for only {int(s.notna().sum()):,} "
                            f"of {len(idx):,} MSOAs")
        cols[key] = s
        matched[key] = f"{sheet}: {col}"
        lo = [x for x in found[key]["lower"] if x[0] == sheet]
        hi = [x for x in found[key]["upper"] if x[0] == sheet]
        if len(lo) == 1 and len(hi) == 1:
            l = lo[0][2][~lo[0][2].index.duplicated()].reindex(idx)
            u = hi[0][2][~hi[0][2].index.duplicated()].reindex(idx)
            both = s.notna() & l.notna() & u.notna()
            fits = ((l <= s) & (s <= u))[both]
            if both.sum() and fits.mean() >= 0.95:
                cols[key + CI_LOWER], cols[key + CI_UPPER] = l, u
                matched[key + "_ci"] = f"{lo[0][1]} / {hi[0][1]}"
            else:
                problems.append(f"{key}: its limits do not bracket the figure for "
                                f"{(1 - fits.mean()) if both.sum() else 1:.0%} of MSOAs; shown without them")
        else:
            problems.append(f"{key}: no single pair of 95% limits beside it in '{sheet}'; shown without them")
    if "saie_net_bhc" in cols and "saie_net_ahc" in cols:
        b, a = cols["saie_net_bhc"], cols["saie_net_ahc"]
        both = a.notna() & b.notna()
        below = (a[both] <= b[both]).mean() if both.sum() else 1.0
        # Both are equivalised, but on different scales: a single adult's
        # income is divided by 0.67 before housing costs and by 0.58 after, so
        # where housing costs are low (outright owners, say) the after figure
        # can come out higher. The first refresh found it below in 87% of
        # MSOAs. Columns the wrong way round would put it below in few, so
        # only a minority below is taken to mean that.
        if below < 0.5:
            problems.append(f"after housing costs is below before housing costs for only {below:.0%} of "
                            "MSOAs, so the two columns may be the wrong way round; neither is used")
            for k in ("saie_net_bhc", "saie_net_ahc"):
                for sfx in ("", CI_LOWER, CI_UPPER):
                    cols.pop(k + sfx, None)
                    matched.pop(k + sfx, None)
        else:
            info(f"saie: after housing costs is below before housing costs in {below:.0%} of MSOAs")
    out = pd.DataFrame(cols, index=idx).reset_index()
    return out, problems, matched


def run_saie() -> pd.DataFrame:
    rule("ONS small area income estimates (MSOA, financial year ending 2023)")
    source = "saie"
    prev = _ons_committed("demographics", SAIE_PARQUET)

    def find():
        files = ons_dataset_files(SAIE_LANDING, SAIE_BULLETINS, SAIE_SEARCH, SAIE_TITLE_WORDS)
        f = ons_pick_latest(files, (".xlsx", ".xls", ".csv", ".zip"))
        if f:
            info(f"{source}: newest edition is {f['edition']!r}, file {f['file']}")
        else:
            warn(f"{source}: no spreadsheet found on the ONS dataset page ({ONS_BASE}{SAIE_LANDING[0]})")
        return f

    src, kept = _ons_static_file(source, SAIE_CACHE, prev, SAIE_KEYS, SAIE_URL_OVERRIDE, find,
                                 "small area income estimates")
    if src is None:
        return kept
    info(f"{source}: reading {src.name}")
    try:
        tables = ons_tables(src)
    except Exception as e:
        raise RuntimeError(f"{src.name} could not be read: {type(e).__name__}: {e}")
    out, problems, matched = saie_parse(tables)
    for k, v in matched.items():
        info(f"{source}: {k} <- {v!r}")
    found = [k for k in SAIE_KEYS if k in out.columns]
    if not found:
        msg = f"none of the income measures was found in {src.name}: " + "; ".join(problems[:6])
        if prev is not None and len(prev):
            _iod_fail(source, msg)
            return prev
        raise RuntimeError(msg)
    rel = _ons_release(src)
    out["period"] = (ons_period(rel.get("edition", "")) or ons_period(rel.get("uri", ""))
                     or ons_period(src.name) or ons_period(" ".join(t[1] for t in tables)))
    if not out["period"].iloc[0]:
        warn(f"{source}: the edition's period could not be read; the map will say none")
    missing = [k for k in SAIE_KEYS if k not in out.columns]
    if missing:
        _iod_fail(source, f"{len(missing)} of {len(SAIE_KEYS)} measures missing or not used: "
                          + "; ".join(problems))
    elif problems:
        warn(f"{source}: " + "; ".join(problems))
    out = _ons_write("demographics", SAIE_PARQUET, out, source, "MSOAs")
    info(f"{source}: {len(found)} of {len(SAIE_KEYS)} measures, period {out['period'].iloc[0] or 'unknown'}")
    return out


# ---------------------------------------------------------------------------
# ONS healthy and disability-free life expectancy at birth, MSOA, 2019 to 2023
# ---------------------------------------------------------------------------
# "Health state life expectancy estimates for Middle layer Super Output
# Areas, England and Wales: 2019 to 2023": healthy life expectancy (HLE, the
# years expected in good or very good health) and disability-free life
# expectancy (DFLE, the years expected free of an illness or disability that
# limits day-to-day activity), at birth, for women and men, with 95%
# confidence intervals. The health states come from the Census 2021
# questions, so the boundaries should be the 2021 MSOAs; a file whose codes
# are all 2011 ones is refused. ONS says these are not comparable with its
# survey-based LA figures or with the earlier MSOA estimates for 2009 to
# 2013. ONS revised the bulletin once, so the edition the file came from is
# logged and kept with the figures. Static between releases, like SAIE.
HLE_LANDING = ("/peoplepopulationandcommunity/healthandsocialcare/healthandlifeexpectancies/datasets/"
               "healthstatelifeexpectancyestimatesformiddlelayersuperoutputareasenglandandwales",)
HLE_BULLETINS = ("/peoplepopulationandcommunity/healthandsocialcare/healthandlifeexpectancies/bulletins/"
                 "healthstatelifeexpectancyestimatesformiddlelayersuperoutputareasenglandandwales/latest",)
HLE_SEARCH = "Health state life expectancy estimates for Middle layer Super Output Areas"
HLE_TITLE_WORDS = ("life expectancy", "middle layer")
HLE_URL_OVERRIDE = os.environ.get("HLE_MSOA_URL", "")
HLE_CACHE = CACHE_DIR / "hle_msoa"
HLE_PARQUET = "hle_msoa.parquet"
HLE_LA_PARQUET = "hle_msoa_la.parquet"
# (measure, phrases or abbreviations that name it, phrases that rule a header out)
HLE_MEASURES = (
    ("hle",  ("healthy life expectancy", "hle"), ("proportion", "percentage", "disability")),
    ("dfle", ("disability free life expectancy", "disability-free life expectancy", "dfle"),
     ("proportion", "percentage")),
)
HLE_SEXES = (("female", ("female", "females", "women", "f")), ("male", ("male", "males", "men", "m")))
HLE_KEYS = [f"{m}_{s}" for m, _, _ in HLE_MEASURES for s, _ in HLE_SEXES]
# The ages that mean "at birth" in an age column.
_HLE_BIRTH = re.compile(r"^(?:0|00|<1|under 1|less than 1|at birth|birth|0?0 ?(?:to|-) ?0?[14]|aged? 0|0 years?|0 yrs?)$")


def _hle_measure(text: str) -> list:
    return [m for m, phr, ex in HLE_MEASURES if _ons_has(text, phr) and not _ons_has(text, ex)]


def _hle_sex_of(text: str) -> str:
    """female, male or "" for a header or a cell. Female first, since
    "female" holds "male"; single letters only as a whole cell."""
    t = iod_norm(text)
    for sex, words in HLE_SEXES:
        if any((w == t) if len(w) == 1 else (" " + w + " " in " " + t + " ") for w in words):
            return sex
    return ""


def hle_parse(tables: list) -> tuple:
    """(MSOA frame, LA frame, problems, matched) from the tables of the
    health state life expectancy file.

    Long tables (a sex column, perhaps an age column) and wide ones (the sex
    in each header) are both read; with an age column only the at-birth rows
    are kept. MSOA rows are those with an English MSOA code, LA rows those
    with an English LA code (E06, E07, E08, E09), which ONS's own LA figures
    of the same method would carry. A figure matched by two columns, outside
    40 to 90 years, or found nowhere is reported and not used. Raises
    RuntimeError when no table has English MSOA codes, or when the file is
    on the 2011 MSOAs."""
    found: dict = {}
    msoa_codes: set = set()
    for name, title, df in tables:
        code_col = ons_code_col(df)
        if code_col is None:
            continue
        codes = df[code_col].astype(str).str.strip()
        is_msoa = codes.str.match(r"^E02\d{6}$")
        is_la = codes.str.match(r"^E0[6789]\d{6}$")
        if not (is_msoa.any() or is_la.any()):
            continue
        cols = [c for c in df.columns if isinstance(c, str)]
        sex_col = next((c for c in cols if iod_norm(c) in ("sex", "gender")), None)
        age_col = next((c for c in cols if re.match(r"^age(?: group| band| at)?$", iod_norm(c))), None)
        frame = df
        if age_col is not None:
            ages = frame[age_col].astype(str).str.strip().str.lower()
            birth = ages.str.match(_HLE_BIRTH)
            if not birth.any():
                found.setdefault("_age", []).append(f"'{name}' has an age column with no at-birth rows")
                continue
            frame = frame[birth.to_numpy()]
            codes = frame[code_col].astype(str).str.strip()
            is_msoa = codes.str.match(r"^E02\d{6}$")
            is_la = codes.str.match(r"^E0[6789]\d{6}$")
        msoa_codes |= set(codes[is_msoa])
        ctx_m = _hle_measure(name + " " + title)
        groups = [("", frame)]
        if sex_col is not None:
            sx = frame[sex_col].astype(str).map(_hle_sex_of)
            groups = [(s, frame[(sx == s).to_numpy()]) for s, _ in HLE_SEXES]
        for sex_g, g in groups:
            gc = g[code_col].astype(str).str.strip()
            for c in cols:
                if c in (code_col, sex_col, age_col):
                    continue
                own = _hle_measure(c)
                m = own[0] if len(own) == 1 else (ctx_m[0] if not own and len(ctx_m) == 1 else None)
                if m is None:
                    continue
                sex = sex_g or _hle_sex_of(c)
                if not sex:
                    continue
                role = _saie_role(c)
                if role == "width":
                    continue
                vals = pd.Series(ons_num(g[c]).to_numpy(), index=gc.to_numpy())
                found.setdefault((f"{m}_{sex}", role), []).append((name, c, vals))
    problems = list(found.pop("_age", []))
    if not msoa_codes:
        raise RuntimeError("no table in the file has English MSOA codes at birth (sheets: "
                           + ", ".join(str(t[0]) for t in tables) + ")")
    if not any(c > MSOA_LAST_2011 for c in msoa_codes):
        raise RuntimeError(f"the file's MSOA codes are all {MSOA_LAST_2011} or below, so it is on the "
                           "2011 MSOAs; it is not used")
    m_idx = pd.Index(sorted(msoa_codes), name="MSOA21CD")
    m_cols: dict = {}
    l_cols: dict = {}
    matched: dict = {}
    for key in HLE_KEYS:
        est = found.get((key, "estimate"), [])
        heads = list(dict.fromkeys((s, c) for s, c, _ in est))
        if not est:
            problems.append(f"{key}: no column for it in any sheet")
            continue
        if len(heads) > 1:
            problems.append(f"{key}: {len(heads)} columns match (" + "; ".join(f"'{s}': '{c}'" for s, c in heads[:4])
                            + "), so none is used")
            continue
        sheet, col = heads[0]
        s = pd.concat([x[2] for x in est])
        s = s[~s.index.duplicated()]
        v = s[s.index.isin(m_idx)].dropna()
        if v.empty or not 40 <= float(v.median()) <= 90:
            problems.append(f"{key} ('{sheet}': '{col}') " + ("has no MSOA figures" if v.empty else
                            f"has a median of {float(v.median()):.1f}, which is not years of life at birth")
                            + "; not used")
            continue
        sm = s.reindex(m_idx)
        if sm.notna().sum() < 0.9 * len(m_idx):
            problems.append(f"{key} ('{col}') has figures for only {int(sm.notna().sum()):,} of {len(m_idx):,} MSOAs")
        m_cols[key] = sm
        matched[key] = f"{sheet}: {col}"
        la = s[s.index.str.match(r"^E0[6789]\d{6}$")]
        if len(la):
            l_cols[key] = la
        lo = [x for x in found.get((key, "lower"), []) if x[0] == sheet]
        hi = [x for x in found.get((key, "upper"), []) if x[0] == sheet]
        if len({x[1] for x in lo}) == 1 and len({x[1] for x in hi}) == 1:
            l = pd.concat([x[2] for x in lo]); l = l[~l.index.duplicated()].reindex(m_idx)
            u = pd.concat([x[2] for x in hi]); u = u[~u.index.duplicated()].reindex(m_idx)
            both = sm.notna() & l.notna() & u.notna()
            fits = ((l <= sm) & (sm <= u))[both]
            if both.sum() and fits.mean() >= 0.95:
                m_cols[key + CI_LOWER], m_cols[key + CI_UPPER] = l, u
                matched[key + "_ci"] = f"{lo[0][1]} / {hi[0][1]}"
            else:
                problems.append(f"{key}: its limits do not bracket the figure; shown without them")
        else:
            problems.append(f"{key}: no single pair of 95% limits beside it in '{sheet}'; shown without them")
    msoa = pd.DataFrame(m_cols, index=m_idx).reset_index()
    if l_cols:
        la_df = pd.DataFrame(l_cols)
        la_df.index.name = "LAD25CD"
        la_df = la_df.reset_index()
    else:
        la_df = pd.DataFrame(columns=["LAD25CD"])
    return msoa, la_df, problems, matched


def run_hle_msoa() -> pd.DataFrame:
    rule("ONS healthy and disability-free life expectancy at birth (MSOA, 2019 to 2023)")
    source = "hle_msoa"
    prev = _ons_committed("outcomes", HLE_PARQUET)

    def find():
        files = ons_dataset_files(HLE_LANDING, HLE_BULLETINS, HLE_SEARCH, HLE_TITLE_WORDS)
        f = ons_pick_latest(files, (".xlsx", ".xls", ".csv", ".zip"))
        if f:
            info(f"{source}: newest edition is {f['edition']!r}, file {f['file']}")
        else:
            warn(f"{source}: no spreadsheet found on the ONS dataset page ({ONS_BASE}{HLE_LANDING[0]})")
        return f

    src, kept = _ons_static_file(source, HLE_CACHE, prev, HLE_KEYS, HLE_URL_OVERRIDE, find,
                                 "health state life expectancy")
    if src is None:
        return kept
    info(f"{source}: reading {src.name}")
    try:
        tables = ons_tables(src)
    except Exception as e:
        raise RuntimeError(f"{src.name} could not be read: {type(e).__name__}: {e}")
    msoa, la, problems, matched = hle_parse(tables)
    for k, v in matched.items():
        info(f"{source}: {k} <- {v!r}")
    found = [k for k in HLE_KEYS if k in msoa.columns]
    if not found:
        msg = f"none of the life expectancy measures was found in {src.name}: " + "; ".join(problems[:6])
        if prev is not None and len(prev):
            _iod_fail(source, msg)
            return prev
        raise RuntimeError(msg)
    rel = _ons_release(src)
    period = (ons_period(rel.get("edition", "")) or ons_period(rel.get("uri", ""))
              or ons_period(src.name) or ons_period(" ".join(t[1] for t in tables)))
    if not period:
        warn(f"{source}: the edition's period could not be read; the map will say none")
    msoa["period"] = period
    msoa["edition"] = str(rel.get("edition") or rel.get("release_date") or src.name)
    info(f"{source}: edition {msoa['edition'].iloc[0]!r}, period {period}")
    missing = [k for k in HLE_KEYS if k not in msoa.columns]
    if missing:
        _iod_fail(source, f"{len(missing)} of {len(HLE_KEYS)} figures missing or not used: "
                          + "; ".join(problems))
    elif problems:
        warn(f"{source}: " + "; ".join(problems))
    out = _ons_write("outcomes", HLE_PARQUET, msoa, source, "MSOAs")
    if len(la):
        la["period"] = period
        _ons_write("outcomes", HLE_LA_PARQUET, la, source, "LAs (ONS's own LA figures)")
    else:
        info(f"{source}: the file has no LA rows, so LA figures are worked out from wards on the map")
    return out


def _tofloat(v):
    try: return float(v)
    except (TypeError, ValueError): return None


# ============================================================================
# SOURCE 4b: DESNZ sub-regional fuel poverty 2023 (LSOA-level, LILEE)
# ============================================================================
# DESNZ publishes "sub-regional fuel poverty" statistics annually — latest
# dataset (2023 data, published Feb 2025) gives % of households in fuel
# poverty by LSOA under the Low Income Low Energy Efficiency (LILEE)
# definition. That's the canonical small-area cold-homes / excess winter
# deaths proxy. Source page:
#   https://www.gov.uk/government/collections/fuel-poverty-sub-regional-statistics
#
# Point the fetcher at the XLSX of "Table 3 (LSOA)"; column we want is
# "Proportion of households fuel poor (%)".
# URLs rotate on each release, so default may 404 — drop the file manually
# in .cache/fuel_poverty/ and the fetcher will pick it up.
# A pinned asset URL points at one release and DESNZ publish a new one every
# year under a new media id, so this rots on a schedule. The old value was the
# 2023 data published in Feb 2025 and there have been releases since.
#
# gov.uk exposes a content API: any page is also JSON at
# /api/content/<path>, and a statistics release lists its attachments there.
# So the collection is read, the newest sub-regional release picked out of it,
# and its spreadsheet taken from the release's own attachment list. That
# survives a new publication without an edit here.
# The collection is read as HTML and each release as JSON, because that is
# where each one actually answers: /api/content on a collection returns a short
# stub with no children in it, while /api/content on a statistics release lists
# its attachments properly.
FUEL_POVERTY_COLLECTION = ("https://www.gov.uk/government/collections/"
                           "fuel-poverty-sub-regional-statistics")
FUEL_POVERTY_URL_OVERRIDE = os.environ.get("FUEL_POVERTY_URL", "")


def _fuel_poverty_discover() -> tuple[str | None, dict]:
    """The newest DESNZ sub-regional spreadsheet, found rather than remembered,
    and what its release calls itself.

    The second item is {"slug", "title", "url"} for the release the file came
    from. DESNZ name each release after the year it was published and the year
    its figures describe, as in "Sub-regional fuel poverty data 2025 (2023
    data)", and the second of those is the one the map has to show.
    """
    if FUEL_POVERTY_URL_OVERRIDE:
        return FUEL_POVERTY_URL_OVERRIDE, {"url": FUEL_POVERTY_URL_OVERRIDE}
    sess = browser_session(referer="https://www.gov.uk/")
    try:
        r = sess.get(FUEL_POVERTY_COLLECTION, timeout=60)
        r.raise_for_status()
        slugs = re.findall(
            r'/government/statistics/([a-z0-9-]*sub-regional-fuel-poverty[a-z0-9-]*)',
            r.text)
    except Exception as e:
        warn(f"fuel_poverty: could not read the gov.uk collection ({e})")
        return None, {}
    if not slugs:
        warn("fuel_poverty: the collection listed no sub-regional releases")
        return None, {}

    # "data" releases carry the tables; "report" releases carry the commentary.
    #
    # Ordered by the year in the slug, not by where it appears on the page.
    # Document order put 2011-sub-regional-fuel-poverty-data last, so taking the
    # last one fetched a spreadsheet from 2011 and would have quietly replaced
    # current figures with fifteen-year-old ones. The year sits in a different
    # place depending on the release, sometimes leading and sometimes trailing,
    # so this takes the largest four-digit number anywhere in the slug.
    def _slug_year(s: str) -> int:
        # Non-capturing, or findall would return the century rather than the year.
        years = [int(y) for y in re.findall(r"(?:19|20)\d{2}", s)]
        return max(years) if years else 0

    data_first = [s for s in dict.fromkeys(slugs) if "report" not in s]
    ordered = sorted(data_first or list(dict.fromkeys(slugs)),
                     key=_slug_year, reverse=True)
    for slug in ordered:
        try:
            rr = sess.get(f"https://www.gov.uk/api/content/government/statistics/{slug}",
                          timeout=60)
            rr.raise_for_status()
        except Exception:
            continue
        xlsx = re.findall(
            r'"(https://assets\.publishing\.service\.gov\.uk/[^"]+\.xlsx)"', rr.text)
        if xlsx:
            info(f"fuel_poverty: newest release is {slug}")
            try:
                title = str(rr.json().get("title") or "")
            except ValueError:
                title = ""
            return xlsx[0], {"slug": slug, "title": title, "url": xlsx[0]}
    warn("fuel_poverty: no release in the collection had an .xlsx attachment")
    return None, {}


def _fuel_poverty_release_path(xlsx: Path) -> Path:
    """Where the release details for a downloaded spreadsheet are kept."""
    return xlsx.with_suffix(".release.json")


def _xlsx_text(src: Path, sheet: str, nrows: int) -> list[str]:
    """The non-empty cells in the first rows of a sheet, as text."""
    try:
        top = pd.read_excel(src, sheet_name=sheet, header=None, nrows=nrows, dtype=str)
    except Exception:
        return []
    return [str(v).strip() for v in top.to_numpy().ravel()
            if isinstance(v, str) and v.strip()]


_FP_YEAR = r"(?:19|20)\d{2}"

def _fuel_poverty_data_year(release: dict, front: list[str],
                            title_rows: list[str]) -> str:
    """
    The year a DESNZ sub-regional release's figures describe, or "".

    DESNZ publish each year's figures two years later, so a release carries two
    years and the one wanted is easy to mistake for the other. They say which
    is which in the release title, "Sub-regional fuel poverty data 2025 (2023
    data)", and usually again on the spreadsheet's cover sheet. In order:

      1. "(2023 data)" in the release title, the cover sheets or the LSOA
         table's title rows
      2. a release title or slug naming two different years, where the
         earlier is the data year
      3. "2023 data" without the brackets, in the same places as 1
      4. the one year left in the LSOA table's title once any year that
         belongs to the LSOA boundaries or the Census is set aside

    Nothing is worked out from the publication year: if none of these finds a
    year, the answer is "" and the map keeps the year it already states.
    """
    title = str((release or {}).get("title") or "")
    slug = str((release or {}).get("slug") or "")
    texts = [title] + list(front) + list(title_rows)

    for t in texts:
        m = re.search(rf"\(\s*({_FP_YEAR})\s+data\s*\)", t, re.I)
        if m:
            return m.group(1)
    for t in (title, slug):
        years = sorted({int(y) for y in re.findall(rf"(?<!\d)({_FP_YEAR})(?!\d)", t)})
        if len(years) == 2:
            return str(years[0])
    for t in texts:
        m = re.search(rf"(?<!\d)({_FP_YEAR})\s+data\b", t, re.I)
        if m:
            return m.group(1)
    geo = (r"(?:LSOA|LSOAs|lower[\s-]+layer\s+super\s+output\s+areas?|"
           r"super\s+output\s+areas?|census|boundar\w*)")
    left: set[str] = set()
    for t in title_rows:
        t = re.sub(rf"{geo}[\s,(]*{_FP_YEAR}", " ", t, flags=re.I)
        t = re.sub(rf"{_FP_YEAR}[\s)]*{geo}", " ", t, flags=re.I)
        left |= set(re.findall(rf"(?<!\d)({_FP_YEAR})(?!\d)", t))
    return left.pop() if len(left) == 1 else ""


def _fuel_poverty_period(fp) -> str:
    """The data year stored with the fuel poverty figures, or "" if none was."""
    if fp is None or fp.empty or "period" not in fp.columns:
        return ""
    s = fp["period"].fillna("").astype(str).str.strip()
    s = s[s != ""]
    return str(s.mode().iloc[0]) if not s.empty else ""


def run_fuel_poverty() -> pd.DataFrame | None:
    rule("Fuel poverty (DESNZ sub-regional, LSOA)")
    cache_dir = CACHE_DIR / "fuel_poverty"
    cache_dir.mkdir(parents=True, exist_ok=True)

    # Prefer any XLSX the user has dropped in; else discover the current one.
    candidates = sorted(cache_dir.glob("*.xlsx"),
                        key=lambda p: p.stat().st_size, reverse=True)
    if not candidates:
        url, release = _fuel_poverty_discover()
        src = cache_dir / "fuel_poverty_lsoa.xlsx"
        if url:
            info(f"No local cache — downloading {url}")
        try:
            if not url:
                raise RuntimeError("no download URL could be discovered")
            r = browser_session(referer="https://www.gov.uk/").get(url, timeout=120)
            r.raise_for_status()
            src.write_bytes(r.content)
            # Kept beside the file, because a cached spreadsheet is read again
            # on later runs without the release page being asked a second time.
            if release:
                _fuel_poverty_release_path(src).write_text(
                    json.dumps(release), encoding="utf-8")
            candidates = [src]
        except Exception as e:
            warn(f"fuel_poverty download failed: {e}. "
                 f"MANUAL STEP: download the newest sub-regional tables XLSX from "
                 "https://www.gov.uk/government/collections/fuel-poverty-sub-regional-statistics "
                 f"and save it in {cache_dir}/, then re-run "
                 "`python fetch_all_data.py --only fuel_poverty`.")
            return None

    src = candidates[0]
    info(f"fuel_poverty: reading {src.name}")
    xl = pd.ExcelFile(src)

    # Find the LSOA sheet. DESNZ re-labels these every release — in the
    # 2023 data (Feb 2025) Table 4 is LSOA; earlier releases had Table 3.
    # Try hinted sheets first, then fall back to every "Table N" sheet and
    # pick the first one whose header row contains an "LSOA Code" column.
    hinted = [s for s in xl.sheet_names
              if "lsoa" in s.lower() or s.lower().startswith("table 3")]
    all_tables = [s for s in xl.sheet_names if s.lower().startswith("table")]
    # Order: hinted first, then every remaining table sheet.
    lsoa_sheets = hinted + [s for s in all_tables if s not in hinted]
    if not lsoa_sheets:
        warn(f"fuel_poverty: no LSOA-looking sheet in {xl.sheet_names}")
        return None

    # Try each header row until we find the LSOA21CD column.
    df = None
    for sheet in lsoa_sheets:
        for hdr in range(0, 5):
            try:
                tmp = pd.read_excel(src, sheet_name=sheet, header=hdr,
                                    dtype=str)
            except Exception:
                continue
            norm = {c.strip().lower(): c for c in tmp.columns if isinstance(c, str)}
            if any("lsoa" in k and "code" in k for k in norm):
                df = tmp
                break
        if df is not None:
            break
    if df is None:
        warn("fuel_poverty: could not locate LSOA code column")
        return None

    def find_col(*kws, exclude=()):
        for c in df.columns:
            if not isinstance(c, str): continue
            lc = c.lower()
            if (all(k in lc for k in kws)
                    and not any(e in lc for e in exclude)):
                return c
        return None

    code_col = find_col("lsoa", "code")
    # "Proportion of households fuel poor (%)" — or "% of households fuel poor"
    pct_col = (find_col("proportion", "fuel", "poor")
               or find_col("%", "fuel", "poor")
               or find_col("percentage", "fuel", "poor"))
    if not code_col or not pct_col:
        warn(f"fuel_poverty: columns not found (code={code_col!r}, "
             f"pct={pct_col!r}). Columns seen: {list(df.columns)[:10]}")
        return None

    # The year the figures describe, which is two years before the release
    # that publishes them, read from the release rather than typed here.
    release = {}
    try:
        release = json.loads(_fuel_poverty_release_path(src).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    front = []
    for name in xl.sheet_names:
        if name != sheet and not name.lower().startswith("table"):
            front += _xlsx_text(src, name, nrows=20)
    title_rows = _xlsx_text(src, sheet, nrows=hdr) if hdr else []
    period = _fuel_poverty_data_year(release, front, title_rows)
    if period:
        info(f"fuel_poverty: figures are for {period}")
    else:
        warn("fuel_poverty: could not read from the release which year the "
             "figures describe, so none is recorded")

    out = pd.DataFrame({
        "LSOA21CD": df[code_col].astype(str).str.strip(),
        "fuel_poverty_pct": pd.to_numeric(df[pct_col], errors="coerce"),
        "period": period,
    }).dropna(subset=["LSOA21CD"])
    # Recent releases are on the 2021 LSOAs, the same codes as everything else
    # here: the 2024 figures cover all 33,755 of England's. An older release on
    # the 2011 LSOAs would share most codes, and any LSOA it does not know is
    # simply left blank. Rows that are not LSOA codes, such as the sheet's
    # "Back to contents" link, are dropped.
    out = out[out["LSOA21CD"].str.match(r"^[EW]01\d{6}$")]
    out_path = DATA_DIR / "demographics" / "fuel_poverty.parquet"
    out = write_parquet_guarded(out_path, out, source="fuel_poverty")
    ok(f"fuel_poverty: {len(out):,} LSOAs -> "
       f"{out_path.relative_to(REPO_ROOT)}")
    return out


# ============================================================================
# SOURCE 4c: GLA LSOA Atlas — PTAL score (LSOA-level)
# ============================================================================
# PTAL (Public Transport Accessibility Level) is TfL's banded score of how
# well-connected a location is by public transport, published per LSOA in the
# GLA's "LSOA Atlas". Source:
#   https://data.london.gov.uk/dataset/lsoa-atlas
#
# The column is "Public Transport Accessibility Levels (2014);Average Score;",
# and what it holds is the BAND INDEX averaged over the LSOA, not the
# continuous accessibility index. The nine bands 0, 1a, 1b, 2, 3, 4, 5, 6a, 6b
# map onto 0-8, and the observed London values run 0.30 to exactly 8.00, which
# is the cap a band scale has and a continuous one does not.
#
# This field was named ptai_score and documented as "average PTAI score" for
# as long as it existed. PTAI is a different quantity on a different scale —
# it is unbounded and reaches past 40 in the West End — so anything that read
# the name and scaled for PTAI put the whole city in the bottom fifth of its
# ramp. Bigger is still better; the range is 0 to 8.
# The download URL carries a resource id that changes whenever the GLA
# republishes the file, so hardcoding one guarantees it eventually 404s. It did:
# the pinned id stopped resolving and PTAL had been silently skipped ever since,
# because a skipped optional source is not a failure and nothing said so.
#
# The Datastore has an API that lists a dataset's current resources, so the id
# is asked for rather than remembered. PTAL_URL still overrides everything, for
# pinning a specific file or pointing at a local copy.
PTAL_DATASET_API = "https://data.london.gov.uk/api/dataset/lsoa-atlas/"
PTAL_URL_OVERRIDE = os.environ.get("PTAL_URL", "")


def _ptal_discover_url() -> str | None:
    """The current LSOA Atlas CSV, from the Datastore's own resource list."""
    if PTAL_URL_OVERRIDE:
        return PTAL_URL_OVERRIDE
    try:
        r = browser_session(referer="https://data.london.gov.uk/").get(
            PTAL_DATASET_API, timeout=60)
        r.raise_for_status()
        blob = r.text
    except Exception as e:
        warn(f"ptal: could not reach the Datastore API ({e})")
        return None
    # Prefer the current-boundaries file. The dataset also carries an
    # "old-boundaries" CSV, which is the same columns on 2011 LSOAs and would
    # join against about nothing.
    urls = re.findall(r'"(https://data\.london\.gov\.uk/download/[^"]+\.csv)"', blob)
    if not urls:
        warn("ptal: the Datastore API listed no CSV resources")
        return None
    current = [u for u in urls if "old-boundaries" not in u.lower()]
    return (current or urls)[0]


# The ONS lookup from the LSOAs of 2011 to those of 2021, with its change
# indicator: U unchanged, S split, M merged, X a complex redraw. ONS reissues
# these services with a version suffix, as it did the MSOA one (_v2); the
# October 2026 refresh got no rows from the unsuffixed name. So the service is
# found by name, trying the known ones and then the list of ONS services.
LSOA11_LSOA21_LAYERS = ["LSOA11_LSOA21_LAD22_EW_LU_v2", "LSOA11_LSOA21_LAD22_EW_LU",
                        "LSOA11_LSOA21_LAD22_EW_LU_v3"]
LSOA11_LSOA21_FIELDS = ("LSOA11CD", "LSOA21CD", "CHGIND")


def _lsoa11_lsoa21_layer() -> tuple:
    """(service name, {upper-case field: field as ONS spells it}) of the ONS
    LSOA 2011 to 2021 lookup, or (None, {}) with the reasons logged."""
    def fields_of(name):
        r = requests.get(f"{ONS_SERVICES}/{name}/FeatureServer/0?f=json", timeout=60)
        if not r.ok:
            return {}
        j = r.json()
        if j.get("error"):
            return {}
        return {f["name"].upper(): f["name"] for f in j.get("fields", []) if f.get("name")}

    tried = []
    for name in LSOA11_LSOA21_LAYERS:
        try:
            f = fields_of(name)
        except Exception as e:
            tried.append(f"{name} ({type(e).__name__})")
            continue
        if all(k in f for k in LSOA11_LSOA21_FIELDS):
            return name, f
        tried.append(f"{name} ({'no such service' if not f else 'missing fields'})")
    try:
        r = requests.get(f"{ONS_SERVICES}?f=json", timeout=120)
        names = [x["name"] for x in r.json().get("services", [])]
        for name in sorted((n for n in names if re.match(r"(?i)^LSOA11_LSOA21_LAD\d\d_EW_LU", n)),
                           reverse=True):
            if name in LSOA11_LSOA21_LAYERS:
                continue
            f = fields_of(name)
            if all(k in f for k in LSOA11_LSOA21_FIELDS):
                return name, f
            tried.append(f"{name} (missing fields)")
    except Exception as e:
        tried.append(f"service list ({type(e).__name__}: {e})")
    warn("lsoa11->lsoa21 lookup: no ONS service answered: " + "; ".join(tried))
    return None, {}


def get_lsoa11_lsoa21_rows() -> list:
    """[(LSOA11CD, LSOA21CD, CHGIND), ...] for the LSOAs in scope, cached on
    disk. Raises when the lookup cannot be read, because a 2011-coded source
    joined as if it were 2021-coded silently loses every LSOA drawn since."""
    cache = CACHE_DIR / "lookups" / f"lsoa11_lsoa21_{SCOPE_KEY}.json"
    cache.parent.mkdir(parents=True, exist_ok=True)
    if cache.exists():
        try:
            return [tuple(r) for r in json.loads(cache.read_text(encoding="utf-8"))]
        except ValueError:
            pass
    codes = sorted(get_lsoa_ward_lookup())
    layer, fields = _lsoa11_lsoa21_layer()
    if not layer:
        raise RuntimeError("lsoa11->lsoa21 lookup: no ONS service found (see the warning above)")
    f11, f21, fchg = (fields[k] for k in LSOA11_LSOA21_FIELDS)
    url = f"{ONS_SERVICES}/{layer}/FeatureServer/0/query"
    rows: list = []
    seen: set = set()
    for i in range(0, len(codes), 120):
        batch = codes[i:i + 120]
        payload = {
            "where": f"{f21} IN (" + ",".join(f"'{c}'" for c in batch) + ")",
            "outFields": f"{f11},{f21},{fchg}",
            "returnGeometry": "false",
            "f": "json",
            "resultRecordCount": 4000,
        }
        r = requests.post(url, data=payload, timeout=180)
        r.raise_for_status()
        j = r.json()
        # ArcGIS reports a bad query with HTTP 200 and an error body, which
        # read as no rows at all; say what it was instead.
        if j.get("error"):
            raise RuntimeError(f"lsoa11->lsoa21 lookup ({layer}): {j['error']}")
        for feat in j.get("features", []):
            a = feat.get("attributes", {})
            key = (a.get(f11), a.get(f21), (a.get(fchg) or "").strip().upper())
            if key[0] and key[1] and key not in seen:
                seen.add(key)
                rows.append(key)
    have = {r[1] for r in rows}
    if len(have) < len(codes) * 0.99:
        raise RuntimeError(f"lsoa11->lsoa21 lookup: only {len(have):,} of {len(codes):,} "
                           f"LSOAs came back. Refusing to cache a partial lookup.")
    cache.write_text(json.dumps(rows), encoding="utf-8")
    ok(f"ONS lookup: {len(have):,} LSOAs (2021) -> 2011 parents ({layer})")
    return rows


def lsoa11_to_lsoa21_values(values11: dict, rows: list) -> dict:
    """{LSOA21CD: value} from {LSOA11CD: value}, through the ONS lookup rows.

    An unchanged LSOA (U) and each part of a split one (S) take the figure of
    the 2011 LSOA they come from. A merged LSOA (M) takes the mean of its 2011
    parts, and a complex redraw (X) the mean of the 2011 LSOAs it was drawn
    from, which is the best the lookup allows because it carries no weights.
    A 2021 LSOA none of whose parents has a figure is left out."""
    parents: dict = {}
    for p11, c21, _chg in rows:
        parents.setdefault(c21, [])
        if p11 not in parents[c21]:
            parents[c21].append(p11)
    out: dict = {}
    for c21, ps in parents.items():
        vs = [float(values11[p]) for p in ps
              if p in values11 and values11[p] is not None and not pd.isna(values11[p])]
        if vs:
            out[c21] = sum(vs) / len(vs)
    return out


def run_ptal() -> pd.DataFrame | None:
    rule("PTAL (GLA LSOA Atlas, average band score 0-8)")
    cache_dir = CACHE_DIR / "ptal"
    cache_dir.mkdir(parents=True, exist_ok=True)

    candidates = sorted(cache_dir.glob("*.csv"),
                        key=lambda p: p.stat().st_size, reverse=True)
    if not candidates:
        url = _ptal_discover_url()
        src = cache_dir / "lsoa_atlas.csv"
        if url:
            info(f"No local cache — downloading {url}")
        try:
            if not url:
                raise RuntimeError("no download URL could be discovered")
            r = browser_session(referer="https://data.london.gov.uk/").get(
                url, timeout=120)
            r.raise_for_status()
            src.write_bytes(r.content)
            candidates = [src]
        except Exception as e:
            warn(f"PTAL download failed: {e}. "
                 f"MANUAL STEP: download the LSOA Atlas CSV from "
                 "https://data.london.gov.uk/dataset/lsoa-atlas and save it as "
                 f"{cache_dir}/lsoa_atlas.csv, then re-run `python "
                 "fetch_all_data.py --only ptal`.")
            return None

    src = candidates[0]
    info(f"ptal: reading {src.name}")
    # GLA atlas ships with two header rows (category, variable). Read with a
    # simple single-row header and pick the PTAI column by substring.
    df = None
    for enc in ("utf-8", "latin-1", "cp1252"):
        try:
            df = pd.read_csv(src, dtype=str, encoding=enc, low_memory=False)
            break
        except UnicodeDecodeError:
            continue
    if df is None or df.empty:
        warn("ptal: empty or unreadable CSV")
        return None

    def find_col(*kws):
        for c in df.columns:
            if not isinstance(c, str): continue
            lc = c.lower()
            if all(k in lc for k in kws):
                return c
        return None

    code_col = (find_col("lower super output area")
                or find_col("lsoa", "code")
                or find_col("codes"))
    # The Atlas does not use the word PTAI anywhere. Its column is
    #   "Public Transport Accessibility Levels (2014);Average Score;"
    # so matching on "ptai" found nothing and the source has been reporting
    # "no frame" for as long as that has been the heading. The PTAL spellings
    # are tried first, and the old PTAI ones kept in case an older copy of the
    # file is sitting in the cache.
    ptal_col = (find_col("public transport accessibility", "average score")
                or find_col("ptal", "average score")
                or find_col("ptal", "average")
                or find_col("average", "ptai")
                or find_col("ptai", "score")
                or find_col("ptai"))
    if not code_col or not ptal_col:
        warn(f"ptal: columns not found (code={code_col!r}, "
             f"ptal={ptal_col!r}). Columns seen: {list(df.columns)[:10]}")
        return None

    out11 = pd.DataFrame({
        "LSOA11CD": df[code_col].astype(str).str.strip(),
        "ptal_score": pd.to_numeric(df[ptal_col], errors="coerce"),
    }).dropna(subset=["LSOA11CD", "ptal_score"])
    # Filter to well-formed E01 LSOA codes.
    out11 = out11[out11["LSOA11CD"].str.startswith("E01")]
    # The Atlas uses the LSOAs of 2011. Joined as if they were 2021 codes, the
    # 176 retired codes matched nothing and the 335 LSOAs drawn in 2021 (the
    # Royal Docks, Stratford, Wembley Park, Colindale) had no figure at all.
    # Each 2021 LSOA takes the figure of the 2011 area it was drawn from.
    conv = lsoa11_to_lsoa21_values(dict(zip(out11["LSOA11CD"], out11["ptal_score"])),
                                   get_lsoa11_lsoa21_rows())
    out = pd.DataFrame({"LSOA21CD": list(conv), "ptal_score": [round(v, 2) for v in conv.values()]})
    in_scope = set(get_lsoa_ward_lookup())
    n_have = len(set(out["LSOA21CD"]) & in_scope)
    info(f"ptal: {len(out11):,} LSOAs of 2011 -> {n_have:,} of {len(in_scope):,} in-scope LSOAs of 2021")
    # A band index cannot exceed 8 (6b). Anything above it means the Atlas has
    # switched this column to the continuous PTAI, which is a different scale
    # and would need a different range in the map — better to say so than to
    # publish it under a name that promises 0-8.
    over = out[out["ptal_score"] > 8]
    if len(over):
        warn(f"ptal: {len(over):,} LSOAs score above 8, which the band scale "
             f"cannot reach (max seen {out['ptal_score'].max():.1f}). The "
             f"Atlas column may now hold continuous PTAI.")
    out_path = DATA_DIR / "demographics" / "ptal.parquet"
    out = write_parquet_guarded(out_path, out, source="ptal")
    ok(f"ptal: {len(out):,} LSOAs, band score "
       f"{out['ptal_score'].min():.1f}-{out['ptal_score'].max():.1f} -> "
       f"{out_path.relative_to(REPO_ROOT)}")
    return out


# ============================================================================
# SOURCE 5: Police.uk street crime  (polygon queries per borough per month)
# ============================================================================
# The API caps a single polygon-month at 10,000 crimes and answers a request
# over that cap with a 503 and an empty body. Central London boroughs are over
# it: an eight-borough NWL run stayed under by luck (Westminster peaked at
# 8,819), but London-wide it bites, and the failure is silent. A dropped month
# looks exactly like a quiet month, so the map would under-report crime in the
# busiest boroughs with nothing in the output to say so.
#
# So a refused polygon is halved and retried rather than skipped. The halves
# tile the original exactly, so nothing is missed at the seam, and crimes are
# deduplicated by id because a point on the cut line can come back in both.
POLICE_MAX_SPLIT_DEPTH = 4   # 16 pieces; ample for a 10k cap at borough size
POLICE_MAX_POLY_POINTS = 180 # keep the poly parameter to a sane length


def _police_poly_param(geom) -> str:
    """Shapely polygon to police.uk's 'lat,lng:lat,lng:...' parameter."""
    ring = list(geom.exterior.coords)
    # Thin out until short enough. Boundaries carry far more precision than a
    # crime-point query needs.
    tol = 0.0002
    while len(ring) > POLICE_MAX_POLY_POINTS and tol < 0.05:
        simplified = geom.simplify(tol, preserve_topology=True)
        if simplified.is_empty or simplified.geom_type != "Polygon":
            break
        ring = list(simplified.exterior.coords)
        tol *= 2
    if len(ring) > POLICE_MAX_POLY_POINTS:
        step = len(ring) // POLICE_MAX_POLY_POINTS + 1
        ring = ring[::step] + [ring[-1]]
    return ":".join(f"{lat:.5f},{lng:.5f}" for lng, lat in ring)


def _police_halve(geom) -> list:
    """Split a polygon in two along its longer axis. The pieces tile it."""
    from shapely.geometry import box
    minx, miny, maxx, maxy = geom.bounds
    if (maxx - minx) >= (maxy - miny):
        mid = (minx + maxx) / 2
        cuts = [box(minx, miny, mid, maxy), box(mid, miny, maxx, maxy)]
    else:
        mid = (miny + maxy) / 2
        cuts = [box(minx, miny, maxx, mid), box(minx, mid, maxx, maxy)]
    pieces = []
    for cut in cuts:
        part = geom.intersection(cut)
        if part.is_empty:
            continue
        # An intersection can come back as a MultiPolygon or a collection;
        # keep the polygonal parts and drop stray lines and points.
        geoms = getattr(part, "geoms", [part])
        for g in geoms:
            if g.geom_type == "Polygon" and not g.is_empty and g.area > 0:
                pieces.append(g)
    return pieces


def _police_request(poly_str: str, ym: str):
    """One call. Returns (crimes, None) or (None, reason)."""
    try:
        r = requests.post(
            "https://data.police.uk/api/crimes-street/all-crime",
            data={"poly": poly_str, "date": ym}, timeout=90,
        )
    except requests.RequestException as e:
        return None, f"network: {type(e).__name__}"
    if r.status_code == 503:
        # The documented response for "more than 10,000 crimes".
        return None, "over_cap"
    if r.status_code != 200:
        return None, f"http {r.status_code}"
    try:
        return json.loads(r.content.decode("utf-8")), None
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None, "unparseable body"


def _police_fetch_area(geom, ym: str, cache_dir: pathlib.Path, stem: str,
                       depth: int = 0):
    """Fetch one polygon-month, halving the polygon if the API refuses it.

    Returns (crimes, failures). A failure is only recorded when splitting has
    not helped, so the caller can tell a genuinely lost area from a big one.
    """
    cache = cache_dir / f"{stem}__{ym}.json"
    # Files of 3 bytes or fewer are "[]" or an empty body from an earlier bad
    # fetch. Retry those rather than trusting them.
    if cache.exists() and cache.stat().st_size > 3:
        try:
            return json.loads(cache.read_text(encoding="utf-8")), []
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass  # corrupt cache, refetch

    data, reason = _police_request(_police_poly_param(geom), ym)
    if data is not None:
        cache.write_text(json.dumps(data), encoding="utf-8")
        time.sleep(0.3)
        return data, []

    if reason == "over_cap" and depth < POLICE_MAX_SPLIT_DEPTH:
        pieces = _police_halve(geom)
        if len(pieces) > 1:
            merged, failures, seen = [], [], set()
            for i, piece in enumerate(pieces):
                sub, sub_fail = _police_fetch_area(
                    piece, ym, cache_dir, f"{stem}__h{depth}{i}", depth + 1)
                failures.extend(sub_fail)
                for c in sub:
                    cid = c.get("id")
                    # Halves share an edge, so a crime on the cut line can
                    # come back twice. Keep ids once; keep id-less rows as-is
                    # rather than guessing they are duplicates.
                    if cid is None:
                        merged.append(c)
                    elif cid not in seen:
                        seen.add(cid)
                        merged.append(c)
            return merged, failures

    return [], [(stem, ym, reason, depth)]


# ── The bulk archive ─────────────────────────────────────────────────────────
# police.uk publishes every force's street-level crime as one zip
# (data/archive/latest.zip: the last 36 months, one CSV per force per month),
# with coordinates, month and crime type on every row. Reading that is one
# download and a few minutes of pandas. The polygon API above costs one request
# per council per month, more when a council is over the 10,000 cap, and on a
# region of large rural councils it ran past two hours (East of England never
# finished). Every scope uses the archive, London included since East of
# England proved it (530,387 crimes in 80 seconds); the API is the fallback if
# the archive cannot be read, and PH_CRIME_SOURCE=archive|api overrides.
POLICE_ARCHIVE_URL = "https://data.police.uk/data/archive/latest.zip"

# The archive spells crime types out; the API, and everything downstream of
# this source, uses its slugs.
POLICE_TYPE_SLUG = {
    "Anti-social behaviour": "anti-social-behaviour",
    "Bicycle theft": "bicycle-theft",
    "Burglary": "burglary",
    "Criminal damage and arson": "criminal-damage-arson",
    "Drugs": "drugs",
    "Other crime": "other-crime",
    "Other theft": "other-theft",
    "Possession of weapons": "possession-of-weapons",
    "Public order": "public-order",
    "Robbery": "robbery",
    "Shoplifting": "shoplifting",
    "Theft from the person": "theft-from-the-person",
    "Vehicle crime": "vehicle-crime",
    "Violence and sexual offences": "violent-crime",
}


def _police_from_archive(months_back: int) -> pd.DataFrame:
    """Street crime for the scope from the bulk archive, in the API's shape."""
    import shapely
    import tempfile
    from shapely.geometry import shape
    from shapely.strtree import STRtree

    # Outside .cache on purpose: it is well over a gigabyte, and .cache is
    # saved between runs.
    zpath = Path(os.environ.get("RUNNER_TEMP") or tempfile.gettempdir()) / "police_latest.zip"
    if not zpath.exists() or zpath.stat().st_size < 1_000_000:
        info(f"police.uk archive: downloading {POLICE_ARCHIVE_URL}")
        with requests.get(POLICE_ARCHIVE_URL, stream=True, timeout=120) as r:
            r.raise_for_status()
            tmp = zpath.with_suffix(".part")
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 20):
                    f.write(chunk)
            tmp.replace(zpath)
    info(f"police.uk archive: {zpath.stat().st_size / 1e9:.2f} GB")

    # Councils in scope, their outline box, and indexes for the joins.
    def index(kind):
        path = DATA_DIR / "boundaries" / f"{kind}.geojson"
        fc = json.loads(path.read_text(encoding="utf-8"))
        geoms = [shape(f["geometry"]) for f in fc["features"]]
        geoms = [g if g.is_valid else g.buffer(0) for g in geoms]
        return geoms, [f["properties"] for f in fc["features"]]

    b_geoms, b_props = index("boroughs")
    keep = [i for i, p in enumerate(b_props)
            if (p.get("LAD25CD") or p.get("LAD24CD") or p.get("code")) in SCOPE_LADS]
    b_geoms = [b_geoms[i] for i in keep]
    b_props = [b_props[i] for i in keep]
    minx, miny, maxx, maxy = shapely.union_all(b_geoms).bounds

    with zipfile.ZipFile(zpath) as z:
        street = [n for n in z.namelist() if n.endswith("-street.csv")]
        months = sorted({n.split("/")[0] for n in street}, reverse=True)[:months_back]
        info(f"police.uk archive: months {months[-1]} to {months[0]}")
        frames = []
        for n in street:
            if n.split("/")[0] not in months:
                continue
            with z.open(n) as fh:
                df = pd.read_csv(fh, usecols=["Month", "Longitude", "Latitude",
                                              "Location", "Crime type"],
                                 dtype={"Month": str, "Location": str, "Crime type": str})
            df = df.dropna(subset=["Longitude", "Latitude"])
            df = df[df.Longitude.between(minx, maxx) & df.Latitude.between(miny, maxy)]
            if len(df):
                frames.append(df)
    crimes = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if crimes.empty:
        raise RuntimeError("police.uk archive: no crimes inside the scope's box")

    pts = shapely.points(crimes.Longitude.to_numpy(), crimes.Latitude.to_numpy())

    def join(geoms, props, key_fn):
        """First polygon each point falls in -> key, or ''."""
        tree = STRtree(geoms)
        pi, gi = tree.query(pts, predicate="within")
        out = [""] * len(pts)
        for p, g in zip(pi.tolist(), gi.tolist()):
            if not out[p]:
                out[p] = key_fn(props[g])
        return out

    lad = join(b_geoms, b_props, lambda p: p.get("LAD25CD") or p.get("LAD24CD") or p.get("code") or "")
    crimes["LAD25CD"] = lad
    crimes = crimes[crimes.LAD25CD != ""].reset_index(drop=True)
    pts = shapely.points(crimes.Longitude.to_numpy(), crimes.Latitude.to_numpy())
    w_geoms, w_props = index("wards")
    l_geoms, l_props = index("lsoa")
    crimes["WD25CD"] = join(w_geoms, w_props, lambda p: p.get("WD25CD") or p.get("WD24CD") or "")
    crimes["LSOA21CD"] = join(l_geoms, l_props, lambda p: p.get("code") or p.get("LSOA21CD") or "")
    name_of = {(p.get("LAD25CD") or p.get("LAD24CD") or p.get("code")):
               (p.get("LAD25NM") or p.get("name") or "") for p in b_props}
    return pd.DataFrame({
        "category": crimes["Crime type"].map(lambda t: POLICE_TYPE_SLUG.get(
            t, re.sub(r"[^a-z]+", "-", str(t).lower()).strip("-"))),
        "lat": crimes.Latitude.astype(float),
        "lng": crimes.Longitude.astype(float),
        "month": crimes.Month.astype(str),
        "street_name": crimes.Location.fillna(""),
        "LSOA21CD": crimes.LSOA21CD,
        "WD25CD": crimes.WD25CD,
        "LAD25CD": crimes.LAD25CD,
        "borough_name": crimes.LAD25CD.map(name_of).fillna(""),
    })


def run_police_crime(months_back: int = 12) -> pd.DataFrame:
    rule(f"Police.uk crime (last {months_back} months)")
    mode = os.environ.get("PH_CRIME_SOURCE") or "archive"
    if mode == "archive":
        try:
            out = _police_from_archive(months_back)
            out_path = DATA_DIR / "crime" / "police_uk_crime.parquet"
            out = write_parquet_guarded(out_path, out, source="police_uk_crime")
            ok(f"police_uk_crime: {len(out):,} crimes from the bulk archive "
               f"-> {out_path.relative_to(REPO_ROOT)}")
            return out
        except Exception as e:
            warn(f"police.uk archive failed ({type(e).__name__}: {e}); "
                 f"falling back to the polygon API")
    cache_dir = CACHE_DIR / "police_uk_crime"
    cache_dir.mkdir(parents=True, exist_ok=True)

    boroughs_idx = load_boundary_index("boroughs")

    # Build polygon strings (subsampled) per borough. MultiPolygons iterate
    # every sub-polygon so detached landmasses are kept (e.g. Hounslow's main
    # body is polygon index 7; coordinates[0][0] was a 4-point island).
    polys = []
    for feat in boroughs_idx.features:
        p = feat["properties"]
        lad = p.get("LAD25CD") or p.get("LAD24CD") or p.get("code") or ""
        name = p.get("LAD25NM") or p.get("name") or ""
        if lad not in SCOPE_LADS:
            continue
        geom = feat["geometry"]
        if geom["type"] == "MultiPolygon":
            rings = [poly[0] for poly in geom["coordinates"]]
        else:
            rings = [geom["coordinates"][0]]
        # Boundaries are WGS84 since run_boundaries started fetching them with
        # outSR=4326. They used to be British National Grid here (and only
        # here, wards and LSOAs were already WGS84), which is why this step
        # converted. GeoJSON positions are [lng, lat]; police.uk wants lat,lng.
        # Subsampled because the poly is sent as a URL-ish parameter.
        from shapely.geometry import Polygon
        for idx, ring in enumerate(rings):
            if len(ring) < 4:
                continue  # degenerate, skip
            geom = Polygon(ring)
            if not geom.is_valid:
                geom = geom.buffer(0)  # fix self-intersections
            if geom.is_empty or geom.geom_type != "Polygon":
                continue
            polys.append((name, lad, idx, len(rings), geom))

    # 12 months lagged by 2 (publication delay)
    today = pd.Timestamp.now("UTC").normalize()
    months = [(today - pd.DateOffset(months=i)).strftime("%Y-%m")
              for i in range(2, 2 + months_back)]

    all_crimes: list = []
    failures: list = []
    attempted = 0
    for name, lad, idx, total_rings, geom in polys:
        for ym in months:
            # Keep the existing single-polygon cache layout so the months
            # already on disk are still hits; MultiPolygons add a __p{idx}
            # suffix so each sub-polygon caches independently.
            stem = lad if total_rings == 1 else f"{lad}__p{idx}"
            attempted += 1
            data, fails = _police_fetch_area(geom, ym, cache_dir, stem)
            failures.extend(fails)
            for c in data:
                c["_borough_name"] = name
                c["_borough_code"] = lad
                c["_month"] = ym
            all_crimes.extend(data)

    if failures:
        by_reason: dict = {}
        for _stem, _ym, reason, _depth in failures:
            by_reason[reason] = by_reason.get(reason, 0) + 1
        detail = ", ".join(f"{r} x{n}" for r, n in sorted(by_reason.items()))
        share = len(failures) / max(attempted, 1)
        # A handful of bad hits is normal over 400+ requests. A large share is
        # a broken run, and a broken run that returns a small number quietly is
        # worse than one that stops, because the map cannot tell the difference
        # between little crime and little data.
        if share > 0.25:
            raise RuntimeError(
                f"police.uk: {len(failures)} of {attempted} polygon-months "
                f"failed ({share:.0%}): {detail}. Refusing to write a "
                f"partial crime file over the previous one.")
        warn(f"police.uk: {len(failures)} of {attempted} polygon-months "
             f"could not be fetched ({detail}). Those areas are absent from "
             f"this run.")

    # Join to ward / LSOA via point-in-polygon
    wards_idx = load_boundary_index("wards")
    lsoa_idx  = load_boundary_index("lsoa")

    rows = []
    for c in all_crimes:
        loc = c.get("location") or {}
        try:
            lat = float(loc.get("latitude"))
            lng = float(loc.get("longitude"))
        except (TypeError, ValueError):
            continue
        wp = wards_idx.find(lng, lat) or {}
        lp = lsoa_idx.find(lng, lat) or {}
        rows.append({
            "category": c.get("category", ""),
            "lat": lat, "lng": lng,
            "month": c.get("_month", ""),
            "street_name": (loc.get("street") or {}).get("name", ""),
            "LSOA21CD": lp.get("code") or lp.get("LSOA21CD") or "",
            "WD25CD":   wp.get("WD25CD") or wp.get("WD24CD") or "",
            "LAD25CD":  c.get("_borough_code", ""),
            "borough_name": c.get("_borough_name", ""),
        })
    out = pd.DataFrame(rows)
    out_path = DATA_DIR / "crime" / "police_uk_crime.parquet"
    out = write_parquet_guarded(out_path, out, source="police_uk_crime")
    ok(f"police_uk_crime: {len(out):,} crimes -> {out_path.relative_to(REPO_ROOT)}")
    return out


# ============================================================================
# SOURCE 6: Hospitals  (ODS 'ets' NHS trust sites extract)
# ============================================================================
# This used to read a Hospital.csv that somebody had to download by hand from
# nhs.uk. That page no longer offers the file, so the instruction had rotted;
# and the layer it fed was never wired up anyway, so the real map was twenty
# hand-typed records, one of which was in Hertfordshire. Both problems go away
# by deriving the layer from a register the pipeline already knows how to read.
#
# ODS has no "hospital" concept. The nearest thing is RO198, NHS TRUST SITE,
# which is the 'ets' bulk extract: 46,000 rows nationally, and a trust site is
# as often a CAMHS team, a dermatology clinic or an admin office as a hospital.
# So the hospital has to be recovered from the records filed at it, which is
# what the grouping below does. The rule was checked against every major London
# hospital: 37 of 37 are found, none missed.
HOSP_TOKEN = re.compile(r"\bHOSP(?:ITAL)?S?\b|\bINFIRMARY\b")
# "Old Hospital Close" and "Hospital Bridge Road" are streets. Without this they
# arrive as hospitals with one record each.
HOSP_STREET = re.compile(r"\b(?:CLOSE|ROAD|RD|STREET|LANE|WAY|AVENUE|AVE|DRIVE|"
                         r"GARDENS|GROVE|TERRACE|WALK|CRESCENT)$")
# Words that name the department that filed the record, not the site it sits in.
HOSP_NOISE = (r"UTC|A&E|AE|CAMHS|MHLD|OPD|OUT-?PATIENTS?|IN-?PATIENTS?|WARD|"
              r"CLINIC|UNIT|DEPT|DEPARTMENT|SERVICES?|TEAM|NHST|NHS|TRUST|FDN|"
              r"FOUNDATION|ELECTIVE|SURGICAL|HUB|SITE|CENTRE|CENTER")

def _hosp_clean(name: str) -> str:
    """A site record's name with the reporting department stripped off it.

    "ST GEORGE'S HOSPITAL (LANESBOROUGH WING)" -> "ST GEORGE'S HOSPITAL"
    "UTC WEST MID HOSPITAL"                    -> "WEST MID HOSPITAL"
    "GOSH INPATIENTS AT THE PORTLAND HOSPITAL" -> "THE PORTLAND HOSPITAL"
    """
    s = " ".join(str(name).upper().split())
    m = re.match(r"^.*?\b(?:AT|OUTREACH:?)\s+(.*\bHOSP\w*\b.*)$", s)
    if m:
        s = m.group(1)
    s = re.split(r"[_(]", s)[0]
    s = re.split(r"\s+[-:]\s+", s)[0]
    s = re.sub(r"\s+@\s+.*$", "", s)
    s = " ".join(s.split())
    prev = None
    while prev != s:                    # department words cling to both ends
        prev = s
        s = re.sub(rf"^(?:{HOSP_NOISE})\b\s*", "", s).strip()
        s = re.sub(rf"\s*\b(?:{HOSP_NOISE})$", "", s).strip()
    return s

def _hosp_canon(name: str) -> str:
    """A key that is equal for two spellings of one hospital."""
    s = _hosp_clean(name).replace("'", "").replace("&", " AND ")
    s = re.sub(r"\bST\.", "ST", s)
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    s = re.sub(rf"\b(?:{HOSP_NOISE})\b", " ", s)
    s = re.sub(r"\bHOSPITALS?\b|\bINFIRMARY\b|\bTHE\b|\bOF\b|\bAND\b", " ", s)
    return " ".join(s.split())

# ODS holds names in capitals, so they have to be recased to sit next to the
# rest of the map. str.title alone gives "1St Ave", "Bmi" and "St Mary'S".
HOSP_ACRONYMS = {"NHS", "BMI", "UCLH", "GOSH", "CDC", "UTC", "HMP", "RNOH",
                 "PTS", "II", "III", "VII", "QEII", "UHCW", "CNTW"}

def _hosp_title(name: str) -> str:
    return title_case(name, initials=True, acronyms={a: a for a in HOSP_ACRONYMS})

# The register says only that a trust has a site there. It lists private
# hospitals where a trust buys care, teams and offices with "hospital" in their
# name, and sites that have closed, so a hand-checked list decides what each
# record is in the regions it covers (London and the North East so far):
# whether to keep it, the name a resident knows it by, its type and whether it
# has a 24-hour A&E (yes, no, or eye for an eye A&E only).
HOSPITAL_SITES = REPO_ROOT / "data" / "curated" / "hospital_sites.csv"
HOSP_TYPES = {"NHS hospital", "NHS specialist hospital", "NHS community hospital",
              "NHS mental health hospital", "NHS day hospital", "Private hospital",
              "Listed on the NHS register of trust sites"}
# A record that the list does not cover keeps the register's own claim.
HOSP_UNCHECKED = "Listed on the NHS register of trust sites"
# Outside the checked regions: brands that are always independent, and words
# that mark a team or a service rather than a building.
HOSP_PRIVATE = re.compile(r"\b(?:BMI|SPIRE|NUFFIELD|RAMSAY|CYGNET|PRIORY|CIRCLE|HCA|"
                          r"SCHOEN|PRIVATE|INDEPENDENT)\b", re.I)
HOSP_OUTPOST = re.compile(r"HOSPITAL\s*(?:AT|@)\s*HOME|VIRTUAL WARD|\bTEAM\b|"
                          r"\bLIAISON\b|COMM HOSP WARDS|^PRIVATE HOSPITALS?$", re.I)

def _hosp_site_key(postcode: str, name: str) -> tuple:
    return (normalise_postcode(postcode), " ".join(str(name).lower().split()))

def load_hospital_sites(path=None) -> dict:
    """The checked list, keyed by (postcode without spaces, register name)."""
    path = path or HOSPITAL_SITES
    out = {}
    if not Path(path).exists():
        return out
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            keep = r["keep"].strip().lower() == "yes"
            if keep and r["type"] not in HOSP_TYPES:
                raise ValueError(f"hospital_sites.csv: unknown type {r['type']!r} "
                                 f"for {r['register_name']}")
            out[_hosp_site_key(r["postcode"], r["register_name"])] = {
                "keep": keep, "name": r["display_name"].strip(),
                "type": r["type"].strip(), "ae": r["ae"].strip().lower(),
            }
    return out

def hosp_blob_row(r) -> dict:
    """One record of data/map/hospitals.js. ae is true for a 24-hour A&E, false
    for none, "eye" for an eye A&E only, and absent where nobody has checked."""
    pc = normalise_postcode(r["postcode"])
    # The postcode is the only address ODS gives, and it is the popup's
    # address line, so it is spaced the way a person writes it.
    row = {"n": r["name"], "a": f"{pc[:-3]} {pc[-3:]}" if len(pc) > 3 else pc,
           "lat": round(float(r["lat"]), 6), "lng": round(float(r["lng"]), 6),
           "t": r["type"]}
    ae = str(r.get("ae") or "").strip().lower()
    if ae in ("yes", "no"):
        row["ae"] = ae == "yes"
    elif ae == "eye":
        row["ae"] = "eye"
    return row

def apply_hospital_sites(rows: list, sites: dict) -> list:
    """Each register record as the checked list describes it.

    rows carry "name" (as _hosp_title gives it) and "postcode". A record the
    list covers is dropped or renamed and typed from it. One it does not cover
    is matched on its postcode alone when the list has exactly one entry
    there, since the register respells names between releases. Anything else
    keeps the register's claim, loses records whose name marks a team or a
    service, and is called a private hospital when it carries a private brand.
    """
    by_pc: dict = {}
    for (pc, _), v in sites.items():
        by_pc.setdefault(pc, []).append(v)
    out = []
    for r in rows:
        hit = sites.get(_hosp_site_key(r["postcode"], r["name"]))
        if hit is None and len(by_pc.get(normalise_postcode(r["postcode"]), [])) == 1:
            hit = by_pc[normalise_postcode(r["postcode"])][0]
        r = dict(r)
        if hit is not None:
            if not hit["keep"]:
                continue
            r["name"] = hit["name"] or r["name"]
            r["type"] = hit["type"]
            r["ae"] = hit["ae"]
        else:
            if HOSP_OUTPOST.search(r["name"]):
                continue
            r["type"] = "Private hospital" if HOSP_PRIVATE.search(r["name"]) else HOSP_UNCHECKED
            r["ae"] = ""
        out.append(r)
    return out

def run_hospitals() -> pd.DataFrame | None:
    rule("Hospitals (ODS NHS trust sites)")
    cache = CACHE_DIR / "hospitals" / "ets.csv"
    cache.parent.mkdir(parents=True, exist_ok=True)
    # ~35,000 active nationally. The floor is a truncation check, not a target.
    fetch_ods_report("ets", cache, source="hospitals", min_active=20_000,
                     active_by_close_date=True)

    rows = [r for r in csv.reader(io.StringIO(
        cache.read_bytes().decode("latin-1"), newline="")) if r]
    df = pd.DataFrame(rows, columns=EPRACCUR_HEADER)
    # No status column in this extract: a site is open until it has a close date.
    df = df[df["CloseDate"].str.strip() == ""]

    named = df[df["Name"].str.upper().str.contains(HOSP_TOKEN, regex=True, na=False)]
    named = named[~named["Name"].str.upper().str.strip()
                  .str.contains(HOSP_STREET, regex=True, na=False)]
    named = named.assign(_pc=named["Postcode"].str.replace(" ", "").str.upper())

    # One candidate site per postcode.
    sites: dict[str, dict] = {}
    for pc, g in named.groupby("_pc"):
        recs = [(n, _hosp_clean(n), _hosp_canon(n)) for n in g["Name"]]
        recs = [r for r in recs if r[1] and HOSP_TOKEN.search(r[1]) and r[2]]
        if not recs:
            continue
        key = Counter(r[2] for r in recs).most_common(1)[0][0]
        # The shortest raw name at a postcode is the site itself; the longer
        # ones are departments that added their own name to it.
        best = min((r for r in recs if r[2] == key), key=lambda r: len(r[0]))
        sites[pc] = {"key": key, "name": best[1], "n": len(g)}

    # Barts is filed at both EC1A 7BE and EC1A 9DS, King George at three
    # postcodes. Same name in the same postcode area is one hospital.
    grouped: dict[tuple, list] = {}
    for pc, s in sites.items():
        area = re.match(r"^[A-Z]+", pc)
        grouped.setdefault((s["key"], area.group() if area else pc), []).append((pc, s))

    chosen = {}
    for _, members in grouped.items():
        pc, s = max(members, key=lambda kv: kv[1]["n"])
        chosen[pc] = s["name"]

    # Postcode is the only geography in the extract, so it carries the pin.
    owners = {normalise_postcode(pc): nm for pc, nm in chosen.items()}
    lookup = lookup_postcodes(owners.keys(), source="hospitals", owners=owners)

    out_rows = []
    for pc, name in chosen.items():
        npc = normalise_postcode(pc)
        hit = lookup.get(npc)
        if not hit:
            continue
        lat, lng, lsoa, lad, wd = hit
        if lat is None or lng is None or lad not in SCOPE_LADS:
            continue      # the extract is national; the map is London
        out_rows.append({
            "name": _hosp_title(name),
            "addr": "", "postcode": npc, "lat": lat, "lng": lng,
            # Set from data/curated/hospital_sites.csv just below.
            "type": HOSP_UNCHECKED, "ae": "",
            "LSOA21CD": lsoa, "WD25CD": wd, "LAD25CD": lad,
        })
    n_before = len(out_rows)
    out_rows = apply_hospital_sites(out_rows, load_hospital_sites())
    ok(f"hospitals: checked list kept {len(out_rows):,} of {n_before:,} register sites")

    out = pd.DataFrame(out_rows).sort_values("name") if out_rows else pd.DataFrame()
    out_path = DATA_DIR / "healthcare" / "hospitals.parquet"
    out = write_parquet_guarded(out_path, out, source="hospitals")
    ok(f"hospitals: {len(named):,} hospital-named records -> {len(sites):,} "
       f"postcodes -> {len(out):,} {SCOPE_NAME} sites")
    return out


# ============================================================================
# SOURCE 7: VCSE - Charity Commission for England & Wales (bulk extract)
# ============================================================================
CCEW_URLS = {
    "charity": [
        "https://ccewuksprdoneregsadata1.blob.core.windows.net/data/json/publicextract.charity.zip",
    ],
    "classification": [
        "https://ccewuksprdoneregsadata1.blob.core.windows.net/data/json/publicextract.charity_classification.zip",
    ],
    "area_of_operation": [
        "https://ccewuksprdoneregsadata1.blob.core.windows.net/data/json/publicextract.charity_area_of_operation.zip",
    ],
}

# Classification codes confirmed against the April 2026 bulk extract:
# 17 'What' codes (101-117), 10 'How' codes (301-310), 7 'Who' codes (201-207).
CCEW_WHAT_GROUPS = {
    101: "general_purposes",
    102: "education",
    103: "health",
    104: "disability",
    105: "poverty",
    106: "overseas_aid",
    107: "housing",
    108: "religion",
    109: "arts_culture",
    110: "amateur_sport",
    111: "animals",
    112: "environment",
    113: "community_economic",
    114: "armed_forces",
    115: "human_rights",
    116: "recreation",
    117: "other_charitable",
}
CCEW_HOW_GROUPS = {
    301: "grants_individuals",
    302: "grants_organisations",
    303: "other_finance",
    304: "human_resources",
    305: "buildings_facilities",
    306: "services",
    307: "advocacy_info",
    308: "research",
    309: "umbrella_body",
    310: "other_activities",
}
CCEW_WHO_GROUPS = {
    201: "children_youth",
    202: "older_people",
    203: "disability",
    204: "ethnic_racial_origin",
    205: "other_charities",
    206: "other_defined_groups",
    207: "general_public",
}


def _ccew_zip(kind):
    cache = CACHE_DIR / "ccew"
    cache.mkdir(parents=True, exist_ok=True)
    for z in cache.glob("publicextract.charity*.zip"):
        nm = z.name.lower()
        if kind == "charity" and "classif" not in nm and "area" not in nm:
            return z
        if kind == "classification" and "classif" in nm:
            return z
        if kind == "area_of_operation" and ("area_of_operation" in nm or "area-of-operation" in nm):
            return z
    for z in cache.glob("*.zip"):
        nm = z.name.lower()
        if kind == "charity" and "charity" in nm and "classif" not in nm and "area" not in nm:
            return z
        if kind == "classification" and "classif" in nm:
            return z
        if kind == "area_of_operation" and ("area_of_operation" in nm or "area-of-operation" in nm):
            return z
    for url in CCEW_URLS.get(kind, []):
        try:
            sess = browser_session(referer="https://register-of-charities.charitycommission.gov.uk/")
            info(f"CCEW: fetching {kind} <- {url}")
            r = sess.get(url, timeout=180, stream=True)
            r.raise_for_status()
            dst = cache / Path(url).name
            with open(dst, "wb") as f:
                for chunk in r.iter_content(65536):
                    if chunk:
                        f.write(chunk)
            if dst.stat().st_size > 100_000:
                ok(f"CCEW: saved {dst.name} ({dst.stat().st_size/1e6:.1f} MB)")
                return dst
        except Exception as e:
            warn(f"CCEW auto-download failed for {kind}: {type(e).__name__}: {e}")
    return None


def _ccew_read_json(zpath):
    """Read the single JSON file inside a CCEW bulk-extract zip.
    CCEW extracts have a UTF-8 BOM, so decode with utf-8-sig."""
    with zipfile.ZipFile(zpath) as z:
        members = [m for m in z.namelist() if m.lower().endswith(".json")]
        if not members:
            raise RuntimeError(f"no .json inside {zpath.name}")
        with z.open(members[0]) as raw:
            return json.load(io.TextIOWrapper(raw, encoding="utf-8-sig"))


def _ccew_income_band(inc):
    try:
        v = float(inc)
    except (TypeError, ValueError):
        return "unknown"
    if v <= 0:        return "zero"
    if v < 10_000:    return "micro"
    if v < 100_000:   return "small"
    if v < 1_000_000: return "medium"
    return "large"


# Map of lowercase AOO area names -> LAD25CD in scope. Keys cover the exact strings
# that appear in the Charity Commission area_of_operation table.
# Charity Commission area-of-operation strings, derived from the scope list so
# a borough cannot be missed by hand. Both "and" and "&" spellings are
# accepted, plus the "City of X" forms the register uses, and a startup check
# below fails loudly if a borough matches nothing rather than silently
# dropping its charities.
def _aoo_variants(name: str) -> set[str]:
    n = name.lower()
    out = {n, n.replace(" and ", " & ")}
    out.add("city of " + n)
    if n.startswith("city of "):
        out.add(n[len("city of "):])
    return out

AOO_NAMES = {}
for _nm, _code in BOROUGHS:
    for _v in _aoo_variants(_nm):
        AOO_NAMES[_v] = _code
LONDON_WIDE_AOO = {"throughout london", "london", "greater london"}


def run_charities():
    """Place-based VCSE fetch.

    Filter rule: a charity is kept iff its declared area of operation covers
    one or more boroughs in scope, OR it declares London-wide operation
    (Greater London Region row). HQ postcode is used for map-pin placement
    only, not for gating, so a charity headquartered outside London is still
    kept if it operates here: it gets no pin, but is listed in the ward and
    LSOA panel as a service provider.

    The borough names matched against are derived from BOROUGHS, so widening
    the scope widens this automatically. Verified against the extract: all 33
    boroughs match at least 595 area-of-operation rows, and no London-looking
    description fails to match.
    """
    rule("VCSE (Charity Commission bulk extract)")
    main_zip  = _ccew_zip("charity")
    class_zip = _ccew_zip("classification")
    area_zip  = _ccew_zip("area_of_operation")
    if main_zip is None:
        warn("No CCEW charity extract found. Download the JSON bulk extract zips from "
             "https://register-of-charities.charitycommission.gov.uk/register/full-register-download "
             "and drop them into .cache/ccew/")
        return None

    # ---- Pass 1: read area-of-operation, compute `covers` per org number ----
    if area_zip is None:
        err("CCEW: missing area-of-operation extract — cannot filter by coverage")
        return None
    info(f"CCEW: reading area-of-operation extract {area_zip.name}")
    area_rows = _ccew_read_json(area_zip)
    info(f"CCEW: {len(area_rows):,} area-of-operation rows")

    ALL_SCOPE_LADS = sorted(set(AOO_NAMES.values()))
    covers_map: dict[int, set[str]] = {}
    areas_map:  dict[int, list[dict]] = {}
    scope_map:  dict[int, str] = {}   # "explicit" | "london_wide"
    for r in area_rows:
        try:
            num = int(r.get("organisation_number") or 0)
        except (TypeError, ValueError):
            continue
        if not num:
            continue
        desc  = (r.get("geographic_area_description") or "").strip()
        gtype = (r.get("geographic_area_type") or "").strip()
        if desc:
            areas_map.setdefault(num, []).append({"type": gtype, "area": desc})
        key = desc.lower()
        hit_local = AOO_NAMES.get(key)
        if hit_local:
            covers_map.setdefault(num, set()).add(hit_local)
            scope_map[num] = "explicit"
            continue
        if gtype.lower() == "region" and key in LONDON_WIDE_AOO:
            # A London-wide declaration covers every borough in scope
            covers_map.setdefault(num, set()).update(ALL_SCOPE_LADS)
            if scope_map.get(num) != "explicit":
                scope_map[num] = "london_wide"

    info(f"CCEW: {len(covers_map):,} charities cover at least one {LA_WORD} in scope "
         f"(explicit: {sum(1 for v in scope_map.values() if v == 'explicit'):,}, "
         f"{SCOPE_NAME}-wide only: {sum(1 for v in scope_map.values() if v == 'london_wide'):,})")

    # ---- Pass 2: read main extract, keep only charities in covers_map ----
    info(f"CCEW: reading main extract {main_zip.name}")
    main_rows = _ccew_read_json(main_zip)
    info(f"CCEW: {len(main_rows):,} charity rows in extract")

    def _ccew_keep(r: dict) -> int:
        """Charity number if this row is a registered, non-subsidiary charity
        covering a borough in scope; 0 otherwise. Used both to pre-collect postcodes and to
        filter the main pass, so the two stay in step."""
        if (r.get("charity_registration_status") or "").lower() != "registered":
            return 0
        try:
            if int(r.get("linked_charity_number") or 0) > 0:
                return 0
            num = int(r.get("organisation_number")
                      or r.get("registered_charity_number") or 0)
        except (TypeError, ValueError):
            return 0
        return num if num and num in covers_map else 0

    # Resolve HQ postcodes for the charities we are keeping. lookup_postcodes()
    # An HQ outside the scope is resolved but then dropped by the LAD filter.
    ccew_owners: dict[str, str] = {}
    for r in main_rows:
        if not _ccew_keep(r):
            continue
        pc = normalise_postcode(r.get("charity_contact_postcode") or "")
        if pc:
            ccew_owners[pc] = (r.get("charity_name") or "").strip()
    lookup = lookup_postcodes(ccew_owners.keys(), source="charities",
                              owners=ccew_owners)

    charities: dict[int, dict] = {}
    skipped_removed = skipped_linked = skipped_no_coverage = 0
    no_geocode = hq_outside_nwl = 0
    for r in main_rows:
        status = (r.get("charity_registration_status") or "").lower()
        if status != "registered":
            skipped_removed += 1
            continue
        try:
            linked = int(r.get("linked_charity_number") or 0)
        except (TypeError, ValueError):
            linked = 0
        if linked > 0:
            skipped_linked += 1
            continue
        try:
            num = int(r.get("organisation_number") or r.get("registered_charity_number") or 0)
        except (TypeError, ValueError):
            continue
        if not num:
            continue
        if num not in covers_map:
            skipped_no_coverage += 1
            continue

        pc = normalise_postcode(r.get("charity_contact_postcode") or "")
        lat = lng = lsoa = lad = wd = None
        if pc:
            hit = lookup.get(pc)
            if hit:
                lat, lng, lsoa, lad, wd = hit
                if lad not in SCOPE_LADS:
                    hq_outside_nwl += 1
            else:
                no_geocode += 1
        else:
            no_geocode += 1

        addr_parts = []
        for i in range(1, 6):
            v = (r.get(f"charity_contact_address{i}") or "").strip()
            if v:
                addr_parts.append(v)
        addr = ", ".join(addr_parts)
        charities[num] = {
            "num": num,
            "name": (r.get("charity_name") or "").strip(),
            "addr": addr, "postcode": pc or "",
            "lat": lat, "lng": lng,
            "LSOA21CD": lsoa, "WD25CD": wd, "LAD25CD": lad,
            "hq_in_nwl": bool(lad and lad in SCOPE_LADS),
            "income": r.get("latest_income"),
            "income_band": _ccew_income_band(r.get("latest_income")),
            # Whether the latest accounts are in, for the Near list's rule
            # (charity_serves_area).
            "reporting_status": (r.get("charity_reporting_status") or "").strip(),
            "acc_end": (r.get("latest_acc_fin_period_end_date") or "")[:10],
            "website": (r.get("charity_contact_web") or "").strip(),
            "activities": (r.get("charity_activities") or "").strip()[:500],
            "registered": (r.get("date_of_registration") or "")[:10],
            "covers": sorted(covers_map[num]),
            "scope":  scope_map.get(num, "explicit"),
            "areas":  areas_map.get(num, []),
            "what_codes": [], "what_tags": [], "what_desc": [],
            "how_codes":  [], "how_tags":  [], "how_desc":  [],
            "who_codes":  [], "who_tags":  [], "who_desc":  [],
        }

    pinned = sum(1 for c in charities.values() if c["lat"] is not None and c["hq_in_nwl"])
    info(
        f"CCEW: kept {len(charities):,} charities serving the scope "
        f"(skipped: {skipped_removed:,} removed, {skipped_linked:,} subsidiary, "
        f"{skipped_no_coverage:,} no coverage in scope) "
        f"- pinnable on map: {pinned:,}, HQ outside scope: {hq_outside_nwl:,}, no geocode: {no_geocode:,}"
    )

    # ---- Pass 3: attach classification rows ----
    if class_zip is not None:
        info(f"CCEW: reading classification extract {class_zip.name}")
        cls_rows = _ccew_read_json(class_zip)
        attached = 0
        for r in cls_rows:
            try:
                num = int(r.get("organisation_number") or 0)
            except (TypeError, ValueError):
                continue
            ch = charities.get(num)
            if not ch:
                continue
            try:
                code = int(r.get("classification_code") or 0)
            except (TypeError, ValueError):
                code = 0
            ctype = (r.get("classification_type") or "").lower()
            desc  = (r.get("classification_description") or "").strip()
            if "what" in ctype:
                ch["what_codes"].append(code); ch["what_desc"].append(desc)
                tag = CCEW_WHAT_GROUPS.get(code)
                if tag and tag not in ch["what_tags"]:
                    ch["what_tags"].append(tag)
            elif "how" in ctype:
                ch["how_codes"].append(code); ch["how_desc"].append(desc)
                tag = CCEW_HOW_GROUPS.get(code)
                if tag and tag not in ch["how_tags"]:
                    ch["how_tags"].append(tag)
            elif "who" in ctype:
                ch["who_codes"].append(code); ch["who_desc"].append(desc)
                tag = CCEW_WHO_GROUPS.get(code)
                if tag and tag not in ch["who_tags"]:
                    ch["who_tags"].append(tag)
            attached += 1
        ok(f"CCEW: attached {attached:,} classification rows")

    rows = list(charities.values())
    for r in rows:
        for k in ("what_codes","what_tags","what_desc","how_codes","how_tags",
                  "how_desc","who_codes","who_tags","who_desc","areas","covers"):
            r[k] = json.dumps(r[k], ensure_ascii=False) if r.get(k) else "[]"
    out = pd.DataFrame(rows)
    out_path = DATA_DIR / "vcse" / "charities.parquet"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out = write_parquet_guarded(out_path, out, source="vcse")
    ok(f"vcse: {len(out):,} rows -> {out_path.relative_to(REPO_ROOT)}")
    return out


# ============================================================================
# EXPORT: build the 3 JSON files the map consumes
# ============================================================================
def _read_parquet_opt(path: Path):
    if not path.exists():
        warn(f"missing (skipped): {path.relative_to(REPO_ROOT)}")
        return None
    return pd.read_parquet(path)

def _census_ward_inputs():
    """(figures, counts, area -> ward lookup) for the census ward build:
    output areas through ONS's OA->ward best fit when both answer, otherwise
    LSOAs through the LSOA->ward lookup. Frames are keyed by LSOA21CD
    whichever level they hold; either may be None."""
    demo = DATA_DIR / "demographics"
    cen_oa = _read_parquet_opt(demo / "census2021_oa.parquet")
    oa_to_ward = get_oa_to_ward() if cen_oa is not None and not cen_oa.empty else {}
    if oa_to_ward:
        cnt = _read_parquet_opt(demo / "census2021_oa_counts.parquet")
        info(f"census -> wards from {len(cen_oa):,} output areas (ONS OA->ward best fit)")
        return _census_add_lsoa_only(
            cen_oa.rename(columns={"OA21CD": "LSOA21CD"}),
            None if cnt is None else cnt.rename(columns={"OA21CD": "LSOA21CD"}),
            oa_to_ward)
    return (_read_parquet_opt(demo / "census2021.parquet"),
            _read_parquet_opt(demo / "census2021_counts.parquet"),
            get_lsoa_to_ward())


def _census_add_lsoa_only(figs, counts, oa_to_ward):
    """The output area inputs, plus LSOA rows for any percentage the LSOA
    counts hold and the output area counts do not.

    ONS publishes less detail for output areas (TS029's proficiency bands
    may be missing there), so a key can exist for LSOAs only. Its wards and
    LAs are then built from the counts of the LSOAs that best fit them,
    rather than left blank. The LSOA rows carry only those keys and no
    population, so nothing built from output areas is counted twice, and an
    LSOA code never equals an output area code, so one lookup serves both."""
    if counts is None or counts.empty:
        return figs, counts, oa_to_ward
    demo = DATA_DIR / "demographics"
    l_cnt = _read_parquet_opt(demo / "census2021_counts.parquet")
    if l_cnt is None or l_cnt.empty or "LSOA21CD" not in l_cnt.columns:
        return figs, counts, oa_to_ward

    def has(frame, k):
        n, d = k + CEN_NUM, k + CEN_DEN
        return (n in frame.columns and d in frame.columns
                and bool((pd.to_numeric(frame[n], errors="coerce").notna()
                          & pd.to_numeric(frame[d], errors="coerce").gt(0)).any()))
    keys = sorted({c[:-len(CEN_NUM)] for c in l_cnt.columns if c.endswith(CEN_NUM)})
    only = [k for k in keys if has(l_cnt, k) and not has(counts, k)]
    if not only:
        return figs, counts, oa_to_ward
    lsoa_to_ward = get_lsoa_to_ward()
    extra = l_cnt[["LSOA21CD"] + [c for k in only for c in (k + CEN_NUM, k + CEN_DEN)]].copy()
    extra["LSOA21CD"] = extra["LSOA21CD"].astype(str)
    extra = extra[extra["LSOA21CD"].isin(lsoa_to_ward)]
    xf = pd.DataFrame({"LSOA21CD": extra["LSOA21CD"].values})
    for k in only:
        n = pd.to_numeric(extra[k + CEN_NUM], errors="coerce").values
        d = pd.to_numeric(extra[k + CEN_DEN], errors="coerce").values
        xf[k] = n / pd.Series(d).replace(0, float("nan")).values * 100
    info(f"census -> wards: {', '.join(only)} from LSOA counts (not in the output area files)")
    figs = pd.concat([figs.drop(columns=[k for k in only if k in figs.columns]), xf],
                     ignore_index=True)
    counts = pd.concat([counts.drop(columns=[c for k in only for c in (k + CEN_NUM, k + CEN_DEN)
                                             if c in counts.columns]),
                        extra.reset_index(drop=True)], ignore_index=True)
    lookup = dict(oa_to_ward)
    lookup.update(lsoa_to_ward)
    return figs, counts, lookup


def _ward_to_lad() -> dict:
    """{WD25CD: LAD25CD}, from the ward boundaries, then the LSOA lookup."""
    out = {wd: lad for wd, lad in get_lsoa_ward_lookup().values()}
    gj = DATA_DIR / "boundaries" / "wards.geojson"
    if gj.exists():
        for feat in json.loads(gj.read_text(encoding="utf-8")).get("features", []):
            p = feat.get("properties", {})
            wd = p.get("WD25CD") or p.get("WD24CD")
            lad = p.get("LAD25CD") or p.get("LAD24CD")
            if wd and lad:
                out[wd] = lad
    return out


def census_la_figures() -> dict:
    """Census percentages for each LA, {LAD25CD: {key: pct}}: the summed
    counts of the same output areas (or LSOAs) its wards are built from, so
    an LA figure is its wards added up rather than their percentages
    averaged. Population is left to the map, which sums the wards.

    Only percentages built from counts are written. One whose counts are
    missing (a stale parquet) is left out, so the map works it out from the
    wards and its card says so, rather than the LA file holding an average
    that the site would describe as summed counts."""
    cen_src, cnt_src, lookup = _census_ward_inputs()
    if cen_src is None or cen_src.empty:
        return {}
    w2l = _ward_to_lad()
    to_la = {code: w2l[wd] for code, wd in lookup.items() if wd in w2l}
    stale: list = []
    figs = census_group_figures(cen_src, cnt_src, to_la, stale_out=stale)
    drop = set(stale) | {"census_population"}
    for vals in figs.values():
        for key in drop:
            vals.pop(key, None)
    return {la: vals for la, vals in figs.items() if vals}


# A ward figure built from LSOAs (or from MSOAs through their LSOAs) is only
# given when the LSOAs with a figure hold at least this share of the ward's
# residents. Below it the figure describes a corner of the ward, not the ward.
WARD_MIN_COVER = 0.8


def census_pop_by_lsoa(cen: "pd.DataFrame | None") -> dict:
    """{LSOA21CD: Census 2021 residents}. Raises when there are none, because
    every residents-weighted ward figure would otherwise weigh each LSOA as 1
    while the site says it follows where people live."""
    if cen is None or cen.empty or "census_population" not in cen.columns:
        raise RuntimeError(
            "census2021.parquet has no census_population column, so ward "
            "figures cannot be weighted by residents. Run the census source first.")
    pops = dict(zip(cen["LSOA21CD"].astype(str),
                    pd.to_numeric(cen["census_population"], errors="coerce").fillna(0)))
    if not any(p > 0 for p in pops.values()):
        raise RuntimeError("census2021.parquet: every census_population is zero or blank")
    return pops


def bridge_to_wards(per_lsoa: dict, lsoa_to_ward: dict, pop_by_lsoa: dict, *,
                    min_cover: float = WARD_MIN_COVER) -> tuple[dict, dict]:
    """Residents-weighted ward means of LSOA values, and how much of each ward
    they cover.

    per_lsoa is {LSOA21CD: value} for the LSOAs that have a figure. Returns
    ({ward: mean}, {ward: cover}), where cover is the share of the ward's
    Census residents (every LSOA the lookup puts in it) living in an LSOA
    with a figure. A ward under min_cover has a cover but no mean.
    """
    if not pop_by_lsoa:
        raise RuntimeError("bridge_to_wards: no LSOA populations to weight by")
    total: dict = {}
    for lc, wd in lsoa_to_ward.items():
        if wd:
            total[wd] = total.get(wd, 0.0) + float(pop_by_lsoa.get(lc, 0) or 0)
    num: dict = {}
    den: dict = {}
    for lc, v in per_lsoa.items():
        wd = lsoa_to_ward.get(lc)
        if not wd or v is None or pd.isna(v):
            continue
        w = float(pop_by_lsoa.get(lc, 0) or 0)
        if w <= 0:
            continue
        num[wd] = num.get(wd, 0.0) + float(v) * w
        den[wd] = den.get(wd, 0.0) + w
    vals: dict = {}
    cover: dict = {}
    for wd, d in den.items():
        t = total.get(wd, 0.0)
        c = d / t if t > 0 else 0.0
        cover[wd] = min(c, 1.0)
        if c >= min_cover:
            vals[wd] = num[wd] / d
    return vals, cover


# Above this share of the LSOAs with a figure having no Census population (and
# more than one of them), the population table and the lookup are out of step,
# and the build stops.
LSOA_NO_POP_MAX = 0.01


def lsoa_means_to_areas(per_lsoa: dict, lsoa_to_area: dict, weight_by_lsoa: dict, *,
                        what: str = "ward figures", weight: str = "residents",
                        source: str = "Census 2021") -> dict:
    """{area: weighted mean} of LSOA values, each LSOA weighted by
    weight_by_lsoa (Census 2021 residents, or households for a household
    measure, or a publisher's own denominator, which source names for the
    messages). lsoa_to_area is the LSOA -> ward (or LA) lookup.

    There is no fallback to a weight of 1. With no weights at all the build
    stops, because every figure would silently become a plain average of
    LSOAs while the site says it follows where people live. An LSOA that has
    a figure but no weight (zero, or missing from the weight table) gets no
    say, as in bridge_to_wards: a weighted mean has nothing to weigh it by,
    and every LSOA in England has residents, so a missing one means a code
    the population table does not know. A ward whose only LSOAs with figures
    are like that gets no figure. The build warns with how many there were,
    and stops when they are more than LSOA_NO_POP_MAX of the LSOAs with
    figures (a single one is always let through), because then the table
    and the lookup do not match.
    """
    if not weight_by_lsoa or not any((w or 0) > 0 for w in weight_by_lsoa.values()):
        raise RuntimeError(
            f"{what}: no {source} {weight} to weight the LSOAs by. "
            + ("Run the census source first." if source == "Census 2021" else ""))
    num: dict = {}
    den: dict = {}
    n_used = 0
    no_weight: list = []
    for lc, v in per_lsoa.items():
        area = lsoa_to_area.get(lc)
        if not area or v is None or pd.isna(v):
            continue
        n_used += 1
        w = float(weight_by_lsoa.get(lc, 0) or 0)
        if w <= 0:
            no_weight.append(lc)
            continue
        num[area] = num.get(area, 0.0) + float(v) * w
        den[area] = den.get(area, 0.0) + w
    if no_weight:
        msg = (f"{what}: {len(no_weight):,} of {n_used:,} LSOAs with a figure have no "
               f"{source} {weight} (for example {', '.join(sorted(no_weight)[:3])}), so they "
               "are left out of the weighted average")
        if len(no_weight) > max(1, n_used * LSOA_NO_POP_MAX):
            raise RuntimeError(msg + f". The {source} figures and the lookup do not match.")
        warn(msg)
    return {a: num[a] / d for a, d in den.items() if d > 0}


def lsoa_sums_to_areas(per_lsoa: dict, lsoa_to_area: dict) -> dict:
    """{area: total} of LSOA counts. A count is added up, never averaged."""
    out: dict = {}
    for lc, v in per_lsoa.items():
        area = lsoa_to_area.get(lc)
        if not area or v is None or pd.isna(v):
            continue
        out[area] = out.get(area, 0.0) + float(v)
    return out


def custom_is_count(col: str) -> bool:
    """A custom column that is a count, so it is added up into wards rather
    than averaged (data/custom/README.md). The map's laSums uses the same
    _count rule for LAs."""
    return str(col).endswith("_count")


def census_households_by_lsoa(cnt: "pd.DataFrame | None") -> dict:
    """{LSOA21CD: households at the 2021 Census}, the base of the car or van
    table, which counts every household. {} when the counts are missing.
    Custom area in the browser weights by the same number
    (lsoa_census_counts.json), so a ward and an area built from the same
    LSOAs agree."""
    col = "census_no_car_pct__den"
    if cnt is None or cnt.empty or col not in cnt.columns or "LSOA21CD" not in cnt.columns:
        return {}
    hh = dict(zip(cnt["LSOA21CD"].astype(str),
                  pd.to_numeric(cnt[col], errors="coerce").fillna(0)))
    return hh if any(v > 0 for v in hh.values()) else {}


def fuel_poverty_by_area(fp: "pd.DataFrame | None", lsoa_to_area: dict,
                         households: dict, pop_by_lsoa: dict, *,
                         what: str = "fuel poverty") -> dict:
    """Fuel poverty for each ward or LA, {area: pct}. It is a share of
    households, so each LSOA is weighted by its households at the 2021
    Census. That is the area's fuel-poor households over its households, as
    near as the published LSOA shares allow (DESNZ's own household counts
    are not kept). Without the household counts it falls back to residents,
    with a warning."""
    if fp is None or fp.empty or "fuel_poverty_pct" not in fp.columns:
        return {}
    per_lsoa = {str(lc): float(v) for lc, v in zip(fp["LSOA21CD"], fp["fuel_poverty_pct"])
                if pd.notna(v)}
    if households:
        return lsoa_means_to_areas(per_lsoa, lsoa_to_area, households,
                                   what=what, weight="households")
    warn(f"{what}: no Census household counts (census2021_counts.parquet), "
         "so it is weighted by residents instead of households")
    return lsoa_means_to_areas(per_lsoa, lsoa_to_area, pop_by_lsoa, what=what)


def _read_static_opt(subdir: str, name: str):
    """A static national parquet (SAIE, health state life expectancy): this
    scope's copy, else the national one, else None, without a warning, since
    a build before the source first runs simply has none."""
    for base in (DATA_DIR, NATIONAL_DATA_DIR):
        p = base / subdir / name
        if p.exists():
            try:
                return pd.read_parquet(p)
            except Exception as e:
                warn(f"{name} unreadable ({e})")
                return None
    return None


def saie_weights(pop_by_lsoa: dict, *, what: str = "income") -> tuple:
    """({LSOA21CD: weight}, "households" or "residents") for the income
    figures, which are means over households: households at the 2021 Census,
    or residents with a warning when the household counts are missing.
    ({}, "") when there are neither."""
    hh = census_households_by_lsoa(_read_parquet_opt(DATA_DIR / "demographics" / "census2021_counts.parquet"))
    if hh:
        return hh, "households"
    if pop_by_lsoa:
        warn(f"{what}: no Census household counts (census2021_counts.parquet), so it is "
             "weighted by residents instead of households")
        return pop_by_lsoa, "residents"
    warn(f"{what}: no Census households or residents to weight by; no figures made")
    return {}, ""


def msoa_figures_to_areas(frame: "pd.DataFrame", keys, lsoa_to_msoa: dict, lsoa_to_area: dict,
                          weights: dict, *, min_cover: float = WARD_MIN_COVER) -> tuple:
    """({area: {key: value}}, {key: (values, cover)}) for figures published
    per MSOA (frame keyed by MSOA21CD), placed onto areas through the LSOAs:
    each LSOA takes its MSOA's figure and an area is the weighted mean of its
    LSOAs, as bridge_to_wards makes the Local Health ward figures. MSOAs do
    not nest in wards, so a ward over two MSOAs blends them by where its
    weight (households or residents) lies; they do nest in LAs, so an LA's
    figure is its MSOAs weighted by theirs. An area under min_cover has no
    figure. {} when there is nothing to weight by."""
    out: dict = {}
    covers: dict = {}
    if frame is None or frame.empty or "MSOA21CD" not in frame.columns or not weights:
        return out, covers
    by = frame.drop_duplicates("MSOA21CD").set_index(frame.drop_duplicates("MSOA21CD")["MSOA21CD"].astype(str))
    for key in keys:
        if key not in by.columns:
            continue
        per_msoa = {m: float(v) for m, v in pd.to_numeric(by[key], errors="coerce").items() if pd.notna(v)}
        per_lsoa = {lc: per_msoa[ms] for lc, ms in lsoa_to_msoa.items() if ms in per_msoa}
        if not per_lsoa:
            continue
        vals, cover = bridge_to_wards(per_lsoa, lsoa_to_area, weights, min_cover=min_cover)
        for a, v in vals.items():
            out.setdefault(a, {})[key] = v
        covers[key] = (vals, cover)
    return out, covers


def record_cover(wards: dict, cover: dict, key: str, vals: dict) -> int:
    """Write a ward's coverage for one indicator into ward["cover"][key], as a
    whole percentage, where it has a figure that rests on less than all of its
    residents (one that rounds to 100 is left out). Returns how many."""
    n = 0
    for wd, c in cover.items():
        pct = round(100 * c)
        w = wards.get(wd)
        if w is None:
            continue
        if wd in vals and pct < 100:
            w.setdefault("cover", {})[key] = pct
            n += 1
        elif "cover" in w:
            w["cover"].pop(key, None)
    return n


# A ward whose LSOAs hold under 80% or over 125% of its Census residents is
# marked: its deprivation and health figures cover a noticeably different
# patch from the ward.
WARD_FIT_LOW, WARD_FIT_HIGH = 0.8, 1.25


def ward_lsoa_fit(wards: dict, lsoa_to_ward: dict, pop_by_lsoa: dict) -> dict:
    """{ward: residents of the LSOAs the lookup gives it / its own Census
    residents}, to two places, for wards with a Census population. The
    ward's own count comes from output areas, which follow its boundary much
    more closely than LSOAs do."""
    lsoa_pop: dict = {}
    for lc, wd in lsoa_to_ward.items():
        if wd:
            lsoa_pop[wd] = lsoa_pop.get(wd, 0.0) + float(pop_by_lsoa.get(lc, 0) or 0)
    out = {}
    for wd, w in wards.items():
        cen = (w.get("indicators") or {}).get("census_population")
        if not cen or cen <= 0:
            continue
        out[wd] = round(lsoa_pop.get(wd, 0.0) / float(cen), 2)
    return out


def ward_fit_is_off(ratio) -> bool:
    """Outside the band. A ward with no LSOA at all (0, several of the City's)
    has no LSOA-built figures to mark."""
    return bool(ratio) and (ratio < WARD_FIT_LOW or ratio > WARD_FIT_HIGH)


def record_lsoa_fit(wards: dict, fit: dict) -> int:
    """ward["lsoa_fit"] for each ward outside the band, removed elsewhere.
    Returns how many are marked."""
    n = 0
    for wd, w in wards.items():
        r = fit.get(wd)
        if ward_fit_is_off(r):
            w["lsoa_fit"] = r
            n += 1
        else:
            w.pop("lsoa_fit", None)
    return n


# police.uk categories left out of the crime total (see build_ward_data).
CRIME_TOTAL_EXCLUDES = ("anti-social-behaviour",)


def crime_total_by_ward(crime):
    """Crimes per ward, not counting anti-social behaviour. A ward with only
    anti-social behaviour on record has 0 crimes rather than no figure."""
    wards = crime["WD25CD"].dropna().unique()
    if "category" in crime.columns:
        crime = crime[~crime["category"].astype(str).isin(CRIME_TOTAL_EXCLUDES)]
    return crime.groupby("WD25CD").size().reindex(wards, fill_value=0)


def local_health_msoa_frame() -> "pd.DataFrame | None":
    """The Local Health MSOA figures in one frame: fingertips_msoa.parquet
    and the series of FINGERTIPS_MSOA_EXTRA that passed their checks, with
    bare zeros dropped (ft_msoa_drop_bare_zeros). None when neither has
    rows. A series in both keeps the established file's rows."""
    outc = DATA_DIR / "outcomes"
    frames = [_read_parquet_opt(outc / "fingertips_msoa.parquet")]
    if (outc / FT_MSOA_EXTRA_PARQUET).exists():
        frames.append(pd.read_parquet(outc / FT_MSOA_EXTRA_PARQUET))
    frames = [f for f in frames if f is not None and not f.empty and "indicator_id" in f.columns]
    if not frames:
        return None
    ftm = frames[0] if len(frames) == 1 else pd.concat(frames, ignore_index=True)
    if len(frames) > 1:
        ftm = ftm.drop_duplicates(["MSOA21CD", "indicator_id"], keep="first")
    return ft_msoa_drop_bare_zeros(ftm)


def local_health_la_frame() -> "pd.DataFrame | None":
    """OHID's own LA figures for the Local Health series, established and
    extra, in one frame (see local_health_msoa_frame). None when neither
    file has rows."""
    outc = DATA_DIR / "outcomes"
    frames = [_read_parquet_opt(outc / "fingertips_msoa_la.parquet")]
    if (outc / FT_MSOA_EXTRA_LA_PARQUET).exists():
        frames.append(pd.read_parquet(outc / FT_MSOA_EXTRA_LA_PARQUET))
    frames = [f for f in frames if f is not None and not f.empty]
    if not frames:
        return None
    lh = frames[0] if len(frames) == 1 else pd.concat(frames, ignore_index=True)
    return lh.drop_duplicates(["LAD25CD", "indicator_id"], keep="first") if len(frames) > 1 else lh


def build_ward_data() -> dict:
    """Wrapped shape expected by index.html loadData():
         {wards: {WD25CD: {name, lad, indicators:{...}}}, metadata: {...}}
    """
    wards: dict[str, dict] = {}
    sources: dict[str, str] = {}

    # Seed ward shells from the boundaries GeoJSON so every ward has name+lad
    # even if no indicator source covers it.
    wards_gj = DATA_DIR / "boundaries" / "wards.geojson"
    if wards_gj.exists():
        gj = json.loads(wards_gj.read_text(encoding="utf-8"))
        # Same disambiguation as the map blob below, for the same reason: eleven
        # ward names are shared by two boroughs, and anything keyed by name
        # rather than code silently picks one of the pair. Kept identical to the
        # blob so the two files always agree on what a ward is called.
        _counts: dict = {}
        for feat in gj.get("features", []):
            _nm = (feat.get("properties", {}).get("WD25NM")
                   or feat.get("properties", {}).get("WD24NM") or "")
            _counts[_nm] = _counts.get(_nm, 0) + 1
        for feat in gj.get("features", []):
            p = feat.get("properties", {})
            code = p.get("WD25CD") or p.get("WD24CD")
            if not code:
                continue
            nm = p.get("WD25NM") or p.get("WD24NM") or ""
            lad = p.get("LAD25NM") or p.get("LAD24NM") or ""
            if _counts.get(nm, 0) > 1 and lad:
                nm = f"{nm} ({lad})"
            wards[code] = {
                "name": nm,
                "lad":  lad,
                "lad_code": p.get("LAD25CD") or p.get("LAD24CD") or "",
                "indicators": {},
            }

    def _get(code):
        wards.setdefault(code, {"name": "", "lad": "", "indicators": {}})
        return wards[code]

    gps = _read_parquet_opt(DATA_DIR / "healthcare" / "gp_practices.parquet")
    # Practices, pharmacies and dentists are listed per ward but not counted
    # into indicators: a count of points the site places in a ward is its own
    # arithmetic, not a published figure, and the map only presents published
    # figures as indicators.
    if gps is not None and "WD25CD" in gps.columns:
        # Named GP list per ward (for ward-profile download)
        for wd, grp in gps.groupby("WD25CD"):
            if not wd:
                continue
            _get(wd)["gp_list"] = [
                {"name": str(r.get("name", "") or ""),
                 "addr": str(r.get("addr", "") or ""),
                 "postcode": str(r.get("postcode", "") or ""),
                 "tel": str(r.get("tel", "") or "")}
                for r in grp.to_dict("records")
            ]
        sources["gp"] = "NHS Digital ODS"

    pharm = _read_parquet_opt(DATA_DIR / "healthcare" / "pharmacies.parquet")
    if pharm is not None and "WD25CD" in pharm.columns:
        # Named pharmacy list per ward
        for wd, grp in pharm.groupby("WD25CD"):
            if not wd:
                continue
            _get(wd)["pharmacy_list"] = [
                {"name": str(r.get("name", "") or ""),
                 "addr": str(r.get("addr", "") or ""),
                 "postcode": str(r.get("postcode", "") or "")}
                for r in grp.to_dict("records")
            ]
        sources["pharmacy"] = "NHS ODS (edispensary)"

    # Dental practices are credited as a source (the reports list them) but,
    # like GP practices and pharmacies, not counted into a ward indicator.
    dent = _read_parquet_opt(DATA_DIR / "healthcare" / "dental_practices.parquet")
    if dent is not None:
        sources["dental"] = "NHS ODS (egdpprac), NHS contracted practices only"

    crime = _read_parquet_opt(DATA_DIR / "crime" / "police_uk_crime.parquet")
    if crime is not None and "WD25CD" in crime.columns:
        # Anti-social behaviour is not a crime, forces record it differently
        # (a fifth of Met records, almost none of the City of London Police's)
        # and it has its own indicator, so the total, and the rate the page
        # works out from it, leave it out.
        for wd, n in crime_total_by_ward(crime).items():
            if wd:
                _get(wd)["indicators"]["crime_total"] = int(n)
        # Per-category crime breakdown (violence, theft, drugs, etc.)
        if "category" in crime.columns:
            for (wd, cat), n in crime.groupby(["WD25CD", "category"]).size().items():
                if not wd or not cat:
                    continue
                w = _get(wd)
                w.setdefault("crime_by_category", {})[str(cat)] = int(n)
        sources["crime"] = "police.uk"
        # The window the counts cover, read off the data rather than restated
        # in the page, so the label moves with every refresh.
        if "month" in crime.columns:
            mo = crime["month"].dropna().astype(str)
            mo = mo[mo != ""]
            if not mo.empty:
                sources["_crime_first_month"] = mo.min()
                sources["_crime_last_month"] = mo.max()
                sources["_crime_months"] = str(mo.nunique())

    ft = _read_parquet_opt(DATA_DIR / "outcomes" / "fingertips.parquet")
    if ft is not None:
        sources["health"] = "OHID Fingertips"
        # Fingertips is per-LAD. Join on LAD25CD (stored on each ward from the
        # boundaries GeoJSON) - avoids brittle name matching.
        lad_ind: dict = {}
        for _, row in ft.iterrows():
            lad = row["LAD25CD"]
            lad_ind.setdefault(lad, {})[row["indicator_short"]] = row["value"]
        for w in wards.values():
            lc = w.get("lad_code", "")
            if lc and lc in lad_ind:
                for k, v in lad_ind[lc].items():
                    if v is not None:
                        w["indicators"][f"ft_{k}"] = v

    # --- Fingertips Local Health: MSOA -> ward, via the LSOA bridge --------
    # An MSOA is a group of whole LSOAs, and a ward is a best-fit group of
    # LSOAs, so the two are joined through the LSOAs they share rather than
    # directly. Each LSOA carries its MSOA's value and contributes its Census
    # 2021 residents as weight: OHID publishes rates, not the counts behind
    # them, so a weighted average is the best available. A ward straddling two
    # MSOAs gets a blend in proportion to where its people are, not to how
    # much land falls in each.
    #
    # The weights used to be looked up in imd2025.parquet, which has no
    # population column, so every LSOA silently weighed 1 while the site said
    # the blend followed residents. bridge_to_wards now refuses to run without
    # populations. Where OHID withholds an MSOA's figure, the ward's figure
    # rests on its other LSOAs only, so the share of residents covered is
    # recorded, and a ward under WARD_MIN_COVER gets no figure (see
    # bridge_to_wards).
    ftm = local_health_msoa_frame()
    if ftm is not None and not ftm.empty:
        sources["health_msoa"] = "OHID Fingertips (Local Health, MSOA)"
        lsoa_to_ward = get_lsoa_to_ward()
        lsoa_to_msoa = get_lsoa_to_msoa()
        cen_pop = _read_parquet_opt(DATA_DIR / "demographics" / "census2021.parquet")
        pop_by_lsoa = census_pop_by_lsoa(cen_pop)

        by_msoa: dict = {}
        for _, row in ftm.iterrows():
            v = row["value"]
            if v is None or (isinstance(v, float) and pd.isna(v)):
                continue
            by_msoa.setdefault(str(row["MSOA21CD"]), {})[int(row["indicator_id"])] = float(v)

        n_filled = n_part = n_dropped = 0
        for ind_id in sorted({i for vals in by_msoa.values() for i in vals}):
            per_lsoa = {lc: by_msoa[ms][ind_id] for lc, ms in lsoa_to_msoa.items()
                        if ind_id in by_msoa.get(ms, {})}
            vals, cover = bridge_to_wards(per_lsoa, lsoa_to_ward, pop_by_lsoa)
            key = f"ft_{ind_id}"
            for wd, v in vals.items():
                _get(wd)["indicators"][key] = round(v, 4)
                n_filled += 1
            n_part += record_cover(wards, cover, key, vals)
            n_dropped += sum(1 for wd in cover if wd not in vals)
        info(f"fingertips msoa -> {n_filled:,} ward values, {n_part:,} partly covered, "
             f"{n_dropped:,} left blank under {WARD_MIN_COVER:.0%} of residents")

    # --- Census 2021: per output area (or LSOA) -> per ward, from the counts
    # Output areas when ONS's OA->ward best-fit lookup answers: they are a
    # fifth the size of LSOAs, so the ward follows its real boundary much more
    # closely. Otherwise LSOAs through the LSOA->ward lookup, as before. Each
    # ward percentage is its areas' numerator counts summed over their
    # denominator counts summed (see census_group_figures), so a household
    # measure is a share of the ward's households, not of its residents.
    # census_method in the metadata records which way they were made, so the
    # map's cards describe the figures that are shipped: "counts" when every
    # percentage was built from counts, "weighted" when any fell back to the
    # residents-weighted average of a stale parquet.
    census_method = ""
    cen_src, cnt_src, cen_lookup = _census_ward_inputs()
    if cen_src is not None and not cen_src.empty:
        sources["census"] = "ONS Census 2021 (Nomis)"
        cen_stale: list = []
        for wd, vals in census_group_figures(cen_src, cnt_src, cen_lookup,
                                             stale_out=cen_stale).items():
            if wd in wards:
                wards[wd]["indicators"].update(vals)
        census_method = "weighted" if cen_stale else "counts"

    # Everything below is LSOA, so the LSOA lookup and the LSOA populations.
    # Without the populations pop_by_lsoa is empty, and the first weighted
    # figure below stops the build (lsoa_means_to_areas) rather than
    # averaging LSOAs as if each held the same number of people.
    cen = _read_parquet_opt(DATA_DIR / "demographics" / "census2021.parquet")
    lsoa_to_ward = get_lsoa_to_ward()
    pop_by_lsoa = ({} if cen is None or cen.empty or "census_population" not in cen.columns
                   else dict(zip(cen["LSOA21CD"].astype(str),
                                 pd.to_numeric(cen["census_population"], errors="coerce").fillna(0))))

    def _agg_to_wards(df, value_col, ward_key, min_cover=None, ndigits=2, total=False):
        """Residents-weighted ward means of an LSOA column, or with
        total=True its ward sums (counts)."""
        if df is None or df.empty or value_col not in df.columns:
            return
        if total:
            per_lsoa = {str(lc): v for lc, v in zip(df["LSOA21CD"], df[value_col]) if pd.notna(v)}
            for wd, v in lsoa_sums_to_areas(per_lsoa, lsoa_to_ward).items():
                if wd in wards:
                    wards[wd]["indicators"][ward_key] = round(v, ndigits)
            return
        if min_cover is not None:
            # A source that leaves LSOAs out (PTAL's 2011 codes did) would
            # otherwise give a ward the figure of whichever LSOAs are left.
            per_lsoa = {str(lc): float(v) for lc, v in zip(df["LSOA21CD"], df[value_col])
                        if pd.notna(v)}
            vals, cover = bridge_to_wards(per_lsoa, lsoa_to_ward, pop_by_lsoa,
                                          min_cover=min_cover)
            for wd, v in vals.items():
                if wd in wards:
                    wards[wd]["indicators"][ward_key] = round(v, 2)
            record_cover(wards, cover, ward_key, vals)
            return
        per_lsoa = {str(lc): v for lc, v in zip(df["LSOA21CD"], df[value_col]) if pd.notna(v)}
        for wd, v in lsoa_means_to_areas(per_lsoa, lsoa_to_ward, pop_by_lsoa,
                                         what=f"ward {ward_key}").items():
            if wd in wards:
                wards[wd]["indicators"][ward_key] = round(v, ndigits)

    fp = _read_parquet_opt(DATA_DIR / "demographics" / "fuel_poverty.parquet")
    # --- IMD: LSOA scores rolled up to wards, population weighted ------------
    # These were missing from every pipeline-built ward_data.json. The committed
    # file had them only because a one-off script wrote them in years ago, so
    # regenerating the file silently dropped the map's headline deprivation
    # measure. Population weighting matches how MHCLG aggregates IMD: a ward's
    # score is its LSOAs' scores weighted by the people living in them, not a
    # plain average of areas.
    imd_ward = _read_parquet_opt(DATA_DIR / "demographics" / "imd2025.parquet")
    if imd_ward is not None and not imd_ward.empty:
        sources["imd"] = "MHCLG Indices of Deprivation 2025"
        for col in ("imd_score", "income_score", "employment_score",
                    "education_score", "health_score", "crime_score",
                    "barriers_score", "environment_score"):
            if col in imd_ward.columns:
                # Income and employment are shares from 0 to 1, which the page
                # shows as percentages: two places would leave 27 distinct
                # employment figures across 684 wards, so they keep three, as
                # MHCLG publishes them.
                _agg_to_wards(imd_ward, col, col,
                              ndigits=3 if col in ("income_score", "employment_score") else 2)
        # An average of deciles is not itself a decile, so it is named for what
        # it is. The per-ward Core20 counts below remain the categorical view.
        if "imd_decile" in imd_ward.columns:
            _agg_to_wards(imd_ward, "imd_decile", "imd_decile_mean")

    # --- IoD 2025 beyond the IMD: IDACI, IDAOPI, sub-domains, indicators -----
    # The same LSOA-to-ward rule as the IMD, except that IDACI and IDAOPI are
    # weighted by their own File 7 denominators (children aged 0 to 15, people
    # aged 60 and over), so a ward's figure is the share of its children or
    # older people. iod_weights in the metadata records which weight was used,
    # so the map's card describes the figures shipped. Ranks and deciles stay
    # LSOA-only.
    iod_frames = [_read_parquet_opt(DATA_DIR / "demographics" / n)
                  for n in (IOD_EXTRA_PARQUET, IOD_INDICATORS_PARQUET)
                  if (DATA_DIR / "demographics" / n).exists()]
    iod_weights_used: dict = {}
    if any(f is not None and not f.empty for f in iod_frames):
        sources["iod"] = "MHCLG English Indices of Deprivation 2025 (Files 7 and 8)"
        iod_vals, iod_weights_used = iod_area_figures(iod_frames, lsoa_to_ward, pop_by_lsoa,
                                                      what="ward")
        for wd, vals in iod_vals.items():
            if wd in wards:
                wards[wd]["indicators"].update(vals)

    # Wards whose LSOAs hold noticeably more or fewer people than the ward
    # (Farringdon Without's hold 3.45 times its residents), so the map can say
    # that their LSOA-built figures describe a different patch.
    if pop_by_lsoa:
        n_fit = record_lsoa_fit(wards, ward_lsoa_fit(wards, lsoa_to_ward, pop_by_lsoa))
        info(f"ward LSOA fit: {n_fit} wards outside {WARD_FIT_LOW:.0%} to {WARD_FIT_HIGH:.0%} of their residents")

    # Fuel poverty is a share of households, so it is weighted by households,
    # as Custom area weights it (fuel_poverty_by_area).
    for wd, v in fuel_poverty_by_area(
            fp, lsoa_to_ward,
            census_households_by_lsoa(_read_parquet_opt(DATA_DIR / "demographics" / "census2021_counts.parquet")),
            pop_by_lsoa, what="ward fuel_poverty_pct").items():
        if wd in wards:
            wards[wd]["indicators"]["fuel_poverty_pct"] = round(v, 2)
    if fp is not None:
        sources["fuel_poverty"] = "DESNZ sub-regional fuel poverty (LILEE)"
        sources["_fuel_poverty_year"] = _fuel_poverty_period(fp)

    # --- ONS figures published for MSOAs: income, and healthy and
    # disability-free life expectancy. As with Local Health, each LSOA takes
    # its MSOA's figure and the ward is the weighted mean of its LSOAs, with
    # no figure under WARD_MIN_COVER. Income is a mean over households, so it
    # is weighted by households (Census 2021); life expectancy by residents,
    # as the Local Health life expectancy is. The intervals stay at MSOA.
    saie_weight = ""
    saie = _read_static_opt("demographics", SAIE_PARQUET)
    hle = _read_static_opt("outcomes", HLE_PARQUET)
    if (saie is not None and not saie.empty) or (hle is not None and not hle.empty):
        lsoa_to_msoa = get_lsoa_to_msoa()
        if saie is not None and not saie.empty:
            sources["saie"] = "ONS small area income estimates (MSOA)"
            weights, saie_weight = saie_weights(pop_by_lsoa, what="ward income")
            vals, covers = msoa_figures_to_areas(saie, SAIE_KEYS, lsoa_to_msoa, lsoa_to_ward, weights)
            for wd, rec in vals.items():
                if wd in wards:
                    wards[wd]["indicators"].update({k: round(v) for k, v in rec.items()})
            for key, (kv, cover) in covers.items():
                record_cover(wards, cover, key, kv)
        if hle is not None and not hle.empty and pop_by_lsoa:
            sources["hle_msoa"] = "ONS health state life expectancy (MSOA)"
            vals, covers = msoa_figures_to_areas(hle, HLE_KEYS, lsoa_to_msoa, lsoa_to_ward, pop_by_lsoa)
            for wd, rec in vals.items():
                if wd in wards:
                    wards[wd]["indicators"].update({k: round(v, 2) for k, v in rec.items()})
            for key, (kv, cover) in covers.items():
                record_cover(wards, cover, key, kv)

    pt = _read_parquet_opt(DATA_DIR / "demographics" / "ptal.parquet")
    # A ward with figures for under four in five of its residents is left
    # blank rather than given the figure of a corner of it.
    _agg_to_wards(pt, "ptal_score", "ptal_score", min_cover=WARD_MIN_COVER)
    if pt is not None:
        sources["ptal"] = "GLA LSOA Atlas (average PTAL band score, 0-8)"

    # --- Air quality + transport access: per-LSOA -> per-ward ---------------
    # Both are population weighted, including the two counts. A ward's
    # "stations within 1 km" is not the sum of its LSOAs' counts — the same
    # station is within 1 km of several of them — so summing would multiply the
    # network. The population-weighted mean answers the question that can be
    # answered: what the ward's average resident has within that distance.
    aq = _read_parquet_opt(DATA_DIR / "environment" / "air_quality_lsoa.parquet")
    if aq is not None and not aq.empty:
        sources["air_quality"] = "Defra UK-AIR PCM 1 km background model"
        for col in ("no2_ugm3", "pm25_ugm3", "pm10_ugm3"):
            if col in aq.columns:
                _agg_to_wards(aq, col, col)

    # Your own figures. LSOA rows roll up to wards on the same population
    # weighting as everything else; ward rows are already ward-level and are
    # written straight on. Columns ending _count are summed, because a count
    # that gets averaged looks plausible and is wrong. The map then adds the
    # ward sums up for each LA (laSums), so an LA total is a total too.
    for level in ("lsoa", "ward"):
        cf = _read_parquet_opt(CUSTOM_DIR / f"custom_{level}.parquet")
        if cf is None or cf.empty:
            continue
        sources["custom"] = "Your own figures (data/custom)"
        code_col = "LSOA21CD" if level == "lsoa" else "WD25CD"
        for col in [c for c in cf.columns if c != code_col]:
            if level == "lsoa":
                _agg_to_wards(cf, col, col, total=custom_is_count(col),
                              ndigits=4 if custom_is_count(col) else 2)
            else:
                for _, row in cf.iterrows():
                    wd = str(row[code_col])
                    v = row.get(col)
                    if wd in wards and pd.notna(v):
                        wards[wd]["indicators"][col] = round(float(v), 4)

    tfl = _read_parquet_opt(DATA_DIR / "environment" / "tfl_transport_lsoa.parquet")
    if tfl is not None and not tfl.empty:
        sources["tfl"] = ("TfL Unified API (StopPoint register)" if IS_LONDON
                          else "DfT NaPTAN (national stops register)")
        for col in ("rail_station_dist_m", "rail_stations_1km",
                    "bus_stop_dist_m", "bus_stops_800m"):
            if col in tfl.columns:
                _agg_to_wards(tfl, col, col)

    # --- Claimant count: counts SUM, rates pop-weighted MEAN ----------------
    cl = _read_parquet_opt(DATA_DIR / "economy" / "claimant_count.parquet")
    if cl is not None and not cl.empty:
        sources["claimant"] = f"NOMIS CLA01 (UC + JSA, {cl['claimant_month'].iloc[0]})"
        # raw count + YoY change → straight sum
        for raw_col, ward_key in [("claimant_count", "claimant_count"),
                                   ("claimant_yoy_change", "claimant_yoy_change")]:
            agg = {}
            for _, row in cl.iterrows():
                lc = str(row["LSOA21CD"])
                wd = lsoa_to_ward.get(lc)
                v = row.get(raw_col)
                if not wd or pd.isna(v):
                    continue
                agg[wd] = agg.get(wd, 0) + int(v)
            for wd, w in wards.items():
                if wd in agg:
                    w["indicators"][ward_key] = int(agg[wd])
        # rate / yoy pct → pop-weighted mean
        _agg_to_wards(cl, "claimant_rate_pct", "claimant_rate_pct")
        _agg_to_wards(cl, "claimant_yoy_pct",  "claimant_yoy_pct")
        # also push the month label through metadata
        try:
            wards_mo = str(cl["claimant_month"].dropna().iloc[0])
        except Exception:
            wards_mo = ""
        if wards_mo:
            sources["_claimant_month"] = wards_mo

    # --- Distances to services, population estimates, claimants aged 18 to
    # 24, road casualties and care home beds, from the LSOAs that best fit
    # each ward: counts added up before dividing, distances averaged by
    # residents (small_area_figures).
    sa_vals, sa_notes = small_area_figures(lsoa_to_ward, pop_by_lsoa, what="ward")
    for wd, vals in sa_vals.items():
        if wd in wards:
            wards[wd]["indicators"].update(vals)
    if sa_vals:
        n_by: dict = {}
        for wd, vals in sa_vals.items():
            if wd in wards:
                for k in vals:
                    n_by[k] = n_by.get(k, 0) + 1
        info("small-area additions -> wards: " + ", ".join(f"{k} {n:,}" for k, n in sorted(n_by.items())))
    for key, name in (("service_access_origin", "service_access"), ("sape_year", "sape"),
                      ("claimant_18_24_month", "claimant_youth"), ("road_ksi_years", "road_casualties"),
                      ("care_homes_as_at", "care_homes")):
        if key in sa_notes:
            sources[name] = {"service_access": "NHS ODS registers (distances measured here)",
                             "sape": "ONS mid-year population estimates (SAPE)",
                             "claimant_youth": "NOMIS NM_162_1, claimants aged 18 to 24",
                             "road_casualties": "DfT STATS19 road casualties",
                             "care_homes": "CQC care directory"}[name]
    for name in (sa_notes.get("dwp") or {}):
        sources[name] = DWP_SOURCE_NAMES.get(name, "DWP Stat-Xplore")

    # --- Core20: how many of a ward's LSOAs, and residents, are Core20 -------
    # NHS England's Core20PLUS5 defines Core20 for LSOAs: the most deprived
    # fifth of England's, deprivation deciles 1 and 2. There is no such thing
    # as a Core20 ward. is_core20 only means that at least one of the ward's
    # LSOAs is Core20, so the site shows the count and the share of residents
    # living in Core20 LSOAs (core20_pop_pct) rather than a ward badge.
    # The deciles are the 2025 index, while NHS England's own Core20 lists may
    # still use the 2019 one, so counts can differ from theirs.
    imd = _read_parquet_opt(DATA_DIR / "demographics" / "imd2025.parquet")
    if imd is not None and "imd_decile" in imd.columns:
        # Reuse the LSOA->ward map built for census; rebuild if census was absent.
        if "lsoa_to_ward" not in locals():
            lsoa_to_ward = get_lsoa_to_ward()
        core20_wards: set = set()
        n_core20_lsoas: dict = {}
        n_ward_lsoas: dict = {}
        pop_core20: dict = {}
        pop_all: dict = {}
        for _, r in imd.iterrows():
            d = r.get("imd_decile")
            lc = str(r.get("LSOA21CD") or "")
            wd = lsoa_to_ward.get(lc)
            if not wd:
                continue
            n_ward_lsoas[wd] = n_ward_lsoas.get(wd, 0) + 1
            p = float(pop_by_lsoa.get(lc, 0) or 0)
            pop_all[wd] = pop_all.get(wd, 0.0) + p
            if pd.notna(d) and int(d) in (1, 2):
                core20_wards.add(wd)
                n_core20_lsoas[wd] = n_core20_lsoas.get(wd, 0) + 1
                pop_core20[wd] = pop_core20.get(wd, 0.0) + p
        for wd, w in wards.items():
            w["is_core20"] = wd in core20_wards
            if wd in n_ward_lsoas:
                w["indicators"]["core20_lsoa_count"] = n_core20_lsoas.get(wd, 0)
                w["indicators"]["total_lsoa_count"] = n_ward_lsoas[wd]
                if pop_all.get(wd, 0) > 0:
                    w["core20_pop_pct"] = round(100 * pop_core20.get(wd, 0.0) / pop_all[wd], 1)
        sources["core20"] = "IMD2025 deciles 1-2 per LSOA"

    # Only the scope's own wards. A practice or charity just over the scope's
    # edge carries the code of a ward outside it, and _get() then made a blank
    # record for that ward: 87 of them in the first North East build. London
    # never showed it (its edge is the region's edge), so London is unchanged.
    wards = {wd: w for wd, w in wards.items() if w.get("name")}

    return {
        "wards": wards,
        "metadata": {
            "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "sources": sources,
            "claimant_period": _month_label(sources.get("_claimant_month", "")),
            "crime_period": _month_range_label(sources.get("_crime_first_month", ""),
                                               sources.get("_crime_last_month", "")),
            # The year DESNZ say the fuel poverty figures describe.
            "fuel_poverty_period": sources.get("_fuel_poverty_year", ""),
            # Provenance for the LSOA -> ward attribution behind every
            # LSOA-derived ward indicator above.
            "lsoa_to_ward_lookup":
                "ONS LSOA (2021) -> Electoral Ward (2025) -> LAD (2025) best-fit",
            "lsoa_to_ward_lookup_year": "2025",
            # How the Census ward percentages were made (see above).
            "census_method": census_method,
            # What each IoD 2025 ward and LA figure is weighted by:
            # "children" or "older" (File 7's own denominators) for IDACI
            # and IDAOPI, "residents" (Census 2021) for the rest, or for
            # those two when File 7 gave no denominators.
            "iod_weights": iod_weights_used,
            # For the small-area additions: where the distances are measured
            # from ("pwc", ONS's population-weighted centroids, "centroid",
            # the middle of each LSOA's shape, or "mixed"), the year of the
            # population estimates, the claimant month, the road casualty
            # years and the date of the CQC file.
            "small_area": sa_notes,
            # What the ward income figures are weighted by: "households"
            # (Census 2021), or "residents" when the household counts were
            # missing.
            "saie_weight": saie_weight,
        },
    }

def _month_label(ym: str) -> str:
    """'2026-08' -> 'August 2026'. Anything else comes back unchanged."""
    try:
        return datetime.strptime(ym, "%Y-%m").strftime("%B %Y")
    except (TypeError, ValueError):
        return ym or ""


def _month_range_label(first: str, last: str) -> str:
    """'2025-08', '2026-07' -> 'Aug 2025 to Jul 2026'."""
    try:
        f = datetime.strptime(first, "%Y-%m").strftime("%b %Y")
        l = datetime.strptime(last, "%Y-%m").strftime("%b %Y")
    except (TypeError, ValueError):
        return ""
    return f if f == l else f"{f} to {l}"


def local_health_la_values(lh: "pd.DataFrame") -> dict:
    """{LAD25CD: {ft_<id>: value}} from fingertips_msoa_la.parquet, missing
    values (OHID's 'cannot be calculated') left out rather than written."""
    out: dict = {}
    for _, row in lh.iterrows():
        v = row.get("value")
        if v is None or (isinstance(v, float) and pd.isna(v)):
            continue
        lad = str(row.get("LAD25CD") or "")
        if not lad:
            continue
        out.setdefault(lad, {})[f"ft_{int(row['indicator_id'])}"] = float(v)
    return out


def ft_note_check(lad: str, short: str, note: str) -> None:
    """Fail the build when a value OHID says it withholds reaches the LA file.
    _ft_pick blanks those, so this is the guard on that guard."""
    low = note.lower()
    if any(w in low for w in FT_WITHHELD_NOTES):
        raise RuntimeError(f"borough_data: {lad} {short} has a value although OHID's "
                           f"note says {note!r}. It should have been left blank.")


def build_borough_data() -> dict:
    """Indicators at borough level, which is the level they are published at.

    The main Fingertips series is released per upper-tier local authority. The
    pipeline has been copying each borough's value onto every ward inside it,
    so a borough of twenty wards showed the same life expectancy twenty times
    and the map implied a ward-level figure that does not exist. The site's own
    caveats say so; this gives the numbers a level where they are simply true
    instead of apologising for them at ward level.

    The Fingertips values are the published figure for that local authority,
    with nothing aggregated or split. Two sets are derived: the Census
    percentages, each the summed counts of the LA's output areas (see
    census_la_figures), and fuel poverty, from the LA's LSOAs weighted by
    households (fuel_poverty_by_area). The map would otherwise average both
    from ward figures, weighted by residents.

    Flat records keyed by LAD25CD, matching msoa_data.json and lsoa_data.json.
    Names come from data/map/boroughs.js, which the browser already loads.
    """
    out: dict = {}
    ft = _read_parquet_opt(DATA_DIR / "outcomes" / "fingertips.parquet")
    if ft is None or ft.empty:
        warn("borough_data: no fingertips parquet")
        ft = pd.DataFrame(columns=["LAD25CD", "indicator_short", "value"])

    for _, row in ft.iterrows():
        v = row["value"]
        if v is None or (isinstance(v, float) and pd.isna(v)):
            continue
        lad = str(row["LAD25CD"])
        if not lad:
            continue
        # Same key the ward copies carry, so one overlay can be drawn at
        # either level without a second name for the same indicator.
        out.setdefault(lad, {})[f"ft_{row['indicator_short']}"] = float(v)
        # A district's figure that is its county's says so, with the county's
        # code and name, so the map can label it as the county's rather than
        # present it as the district's own. Every LA with figures carries
        # ft_from, empty when all of them are its own, so the map knows the
        # record says where each figure is from; a file without it predates
        # the 501 fetch, when every district's figure was its county's.
        ff = out[lad].setdefault("ft_from", {})
        if str(row.get("source_kind", "") or "") == "county":
            ff[f"ft_{row['indicator_short']}"] = [
                str(row.get("source_code", "") or ""), str(row.get("source_name", "") or "")]
        # OHID's note on the figure, where it has one, so the map can mark a
        # provisional, combined or doubtful figure as such.
        note = _ft_note(row.get("value_note")) if "value_note" in ft.columns else ""
        if note:
            ft_note_check(lad, row["indicator_short"], note)
            out[lad].setdefault("ft_notes", {})[f"ft_{row['indicator_short']}"] = note

    # OHID's own LA figures for the Local Health indicators, under the key
    # the MSOA and ward figures carry (ft_<id>), so the map shows OHID's LA
    # figure rather than its estimate from wards (run_fingertips_msoa_la).
    lh = local_health_la_frame()
    if lh is not None and not lh.empty:
        out_la_lh = local_health_la_values(lh)
        for lad, vals in out_la_lh.items():
            out.setdefault(lad, {}).update(vals)

    n_vals = sum(len(v) for v in out.values())
    # Census percentages for each LA, from the summed counts of its output
    # areas. The map would otherwise average its wards' percentages by
    # residents, which is wrong for household measures.
    cen = census_la_figures()
    for lad, vals in cen.items():
        out.setdefault(lad, {}).update(vals)
    n_cen = sum(len(v) for v in cen.values())
    # Fuel poverty for each LA from all of its LSOAs, weighted by households
    # as the wards are. The map would otherwise average the ward figures by
    # residents.
    fp = _read_parquet_opt(DATA_DIR / "demographics" / "fuel_poverty.parquet")
    if fp is not None and not fp.empty:
        demo = DATA_DIR / "demographics"
        cen_l = _read_parquet_opt(demo / "census2021.parquet")
        pops = ({} if cen_l is None or cen_l.empty or "census_population" not in cen_l.columns
                else dict(zip(cen_l["LSOA21CD"].astype(str),
                              pd.to_numeric(cen_l["census_population"], errors="coerce").fillna(0))))
        to_la = {lc: lad for lc, (_wd, lad) in get_lsoa_ward_lookup().items() if lad}
        for lad, v in fuel_poverty_by_area(
                fp, to_la, census_households_by_lsoa(_read_parquet_opt(demo / "census2021_counts.parquet")),
                pops, what=f"{LA_WORD} fuel_poverty_pct").items():
            out.setdefault(lad, {})["fuel_poverty_pct"] = round(v, 2)
    # The IoD 2025 extra and underlying figures for each LA from all of its
    # LSOAs, which nest exactly in LAs, weighted as the wards are
    # (iod_area_figures): IDACI and IDAOPI by their own denominators, the rest
    # by residents. The map would otherwise average the ward figures by
    # residents, which is wrong for IDACI and IDAOPI.
    demo = DATA_DIR / "demographics"
    iod_frames = [_read_parquet_opt(demo / n) for n in (IOD_EXTRA_PARQUET, IOD_INDICATORS_PARQUET)
                  if (demo / n).exists()]
    if any(f is not None and not f.empty for f in iod_frames):
        cen_l = _read_parquet_opt(demo / "census2021.parquet")
        pops = ({} if cen_l is None or cen_l.empty or "census_population" not in cen_l.columns
                else dict(zip(cen_l["LSOA21CD"].astype(str),
                              pd.to_numeric(cen_l["census_population"], errors="coerce").fillna(0))))
        to_la = {lc: lad for lc, (_wd, lad) in get_lsoa_ward_lookup().items() if lad}
        iod_la, _ = iod_area_figures(iod_frames, to_la, pops, what=LA_WORD)
        for lad, vals in iod_la.items():
            out.setdefault(lad, {}).update(vals)
    # The small-area additions for each LA from all of its LSOAs, made as the
    # ward figures are (small_area_figures): counts added up before dividing,
    # distances averaged by residents.
    cen_sa = _read_parquet_opt(demo / "census2021.parquet")
    pops_sa = ({} if cen_sa is None or cen_sa.empty or "census_population" not in cen_sa.columns
               else dict(zip(cen_sa["LSOA21CD"].astype(str),
                             pd.to_numeric(cen_sa["census_population"], errors="coerce").fillna(0))))
    to_la_sa = {lc: lad for lc, (_wd, lad) in get_lsoa_ward_lookup().items() if lad}
    sa_la, _ = small_area_figures(to_la_sa, pops_sa, what=LA_WORD)
    for lad, vals in sa_la.items():
        if lad in SCOPE_LADS or lad in out:
            out.setdefault(lad, {}).update(vals)
    # ONS income for each LA from all of its MSOAs, which nest in LAs,
    # weighted by households as the wards are (msoa_figures_to_areas). ONS
    # publishes no LA figure in this release, and the map would otherwise
    # average the wards by residents.
    saie = _read_static_opt("demographics", SAIE_PARQUET)
    if saie is not None and not saie.empty:
        cen_l = _read_parquet_opt(demo / "census2021.parquet")
        pops = ({} if cen_l is None or cen_l.empty or "census_population" not in cen_l.columns
                else dict(zip(cen_l["LSOA21CD"].astype(str),
                              pd.to_numeric(cen_l["census_population"], errors="coerce").fillna(0))))
        to_la = {lc: lad for lc, (_wd, lad) in get_lsoa_ward_lookup().items() if lad}
        weights, _ = saie_weights(pops, what=f"{LA_WORD} income")
        vals, _ = msoa_figures_to_areas(saie, SAIE_KEYS, get_lsoa_to_msoa(), to_la, weights)
        for lad, rec in vals.items():
            out.setdefault(lad, {}).update({k: round(v) for k, v in rec.items()})
    # Healthy and disability-free life expectancy: ONS's own LA figures where
    # the MSOA release carries them, under the same keys. Without them the map
    # works an LA figure out from its wards and says it is an estimate, as for
    # the Local Health life expectancy.
    hle_la = _read_static_opt("outcomes", HLE_LA_PARQUET)
    if hle_la is not None and not hle_la.empty and "LAD25CD" in hle_la.columns:
        hle_la = ft_codes_to_current(hle_la.rename(columns={"LAD25CD": "Area Code"})).rename(
            columns={"Area Code": "LAD25CD"})
        for rec in hle_la.to_dict("records"):
            lad = str(rec.get("LAD25CD") or "")
            if lad not in SCOPE_LADS:
                continue
            for k in HLE_KEYS:
                v = rec.get(k)
                if v is not None and pd.notna(v):
                    out.setdefault(lad, {})[k] = round(float(v), 2)
    info(f"borough_data: {len(out):,} {LA_WORD}s, {n_vals:,} published values, "
         f"{n_cen:,} Census figures from summed counts")
    return out


def build_msoa_data() -> dict:
    """Indicators at MSOA level, which is the level they are published at.

    The Fingertips Local Health set is released per MSOA. Until now the
    pipeline read it, population-weighted it down onto wards through the LSOA
    bridge, and discarded the MSOA detail: 1,002 published figures per
    indicator became 704 derived ones, and the geography the publisher
    actually used never reached the map. The ward figures stay exactly as they
    were, because a ward map is what most people want; this adds the
    underlying level rather than replacing anything.

    Nothing is aggregated or split here. Every value is the published figure
    for that MSOA, so this file is the only one in the export with no
    derivation in it at all.

    Records are flat and keyed by MSOA21CD, mirroring lsoa_data.json rather
    than ward_data.json's nested shape: index.html reads both through
    (record.indicators || record), and an MSOA carries no name or parent worth
    storing. Names come from data/map/msoa_boundaries.json, the same way LSOA
    names come from LSOA_IMD.
    """
    out: dict = {}
    # Census 2021 as ONS publishes it for MSOAs (see run_census2021).
    cen = _read_parquet_opt(DATA_DIR / "demographics" / "census2021_msoa.parquet")
    if cen is not None and not cen.empty:
        cols = [c for c in cen.columns if c != "MSOA21CD"]
        for _, row in cen.iterrows():
            rec = out.setdefault(str(row["MSOA21CD"]), {})
            for col in cols:
                v = row[col]
                if pd.notna(v):
                    rec[col] = int(v) if col == "census_population" else round(float(v), 4)
    ftm = local_health_msoa_frame()
    if ftm is None or ftm.empty:
        warn("msoa_data: no fingertips_msoa parquet")
        ftm = pd.DataFrame(columns=["MSOA21CD", "indicator_id", "value"])

    for _, row in ftm.iterrows():
        v = row["value"]
        if v is None or (isinstance(v, float) and pd.isna(v)):
            continue
        code = str(row["MSOA21CD"])
        # Same key as the ward-level copy carries, so one overlay can be drawn
        # at either level without a second name for the same indicator.
        out.setdefault(code, {})[f"ft_{int(row['indicator_id'])}"] = round(float(v), 4)
        vs = row.get("vs_england") if hasattr(row, "get") else None
        if isinstance(vs, str) and vs:
            out[code][f"ft_{int(row['indicator_id'])}_vs"] = vs
        # Carried from 2011 codes, so an estimate for the 2021 area.
        if bool(row.get("estimated", False)) is True:
            out[code][f"ft_{int(row['indicator_id'])}_est"] = 1

    # ONS's income estimates and health state life expectancies, as
    # published for each MSOA, each with its 95% limits (<key>_lci and
    # <key>_uci) where ONS gives them. Both files are national, so only this
    # scope's MSOAs are written. Income is in whole pounds.
    statics = [(_read_static_opt("demographics", SAIE_PARQUET), SAIE_KEYS, 0),
               (_read_static_opt("outcomes", HLE_PARQUET), HLE_KEYS, 2)]
    if any(f is not None and not f.empty for f, _, _ in statics):
        try:
            scope_msoa = set(get_lsoa_to_msoa().values())
        except Exception as e:
            warn(f"msoa_data: no LSOA to MSOA lookup ({e}); income and health state life expectancy left out")
            scope_msoa = set()
        for frame, keys, nd in statics:
            if frame is None or frame.empty or "MSOA21CD" not in frame.columns:
                continue
            cols = [c for k in keys for c in (k, k + CI_LOWER, k + CI_UPPER) if c in frame.columns]
            for rec in frame[["MSOA21CD"] + cols].to_dict("records"):
                code = str(rec["MSOA21CD"])
                if code not in scope_msoa:
                    continue
                for c in cols:
                    v = rec[c]
                    if v is not None and pd.notna(v):
                        out.setdefault(code, {})[c] = int(round(float(v))) if nd == 0 else round(float(v), nd)

    n_vals = sum(len(v) for v in out.values())
    info(f"msoa_data: {len(out):,} MSOAs, {n_vals:,} published values, none derived")
    return out


def build_lsoa_data() -> dict:
    out: dict[str, dict] = {}
    imd = _read_parquet_opt(DATA_DIR / "demographics" / "imd2025.parquet")
    if imd is not None:
        for _, row in imd.iterrows():
            code = row["LSOA21CD"]
            if not code:
                continue
            rec = {}
            for col in imd.columns:
                if col == "LSOA21CD":
                    continue
                v = row[col]
                rec[col] = (int(v) if col in ("imd_decile", "imd_rank") and pd.notna(v)
                            else (None if pd.isna(v) else v))
            out[code] = rec

    # Merge census 2021 LSOA-level fields onto the same dict. Only merge onto
    # LSOAs that already exist (scoped to NW London by the IMD step); don't
    # add E&W LSOAs outside our scope.
    cen = _read_parquet_opt(DATA_DIR / "demographics" / "census2021.parquet")
    if cen is not None and not cen.empty:
        for _, row in cen.iterrows():
            code = str(row["LSOA21CD"])
            if not code or code not in out:
                continue
            rec = out[code]
            for col in cen.columns:
                if col == "LSOA21CD":
                    continue
                v = row[col]
                if pd.isna(v):
                    continue
                if col == "census_population":
                    rec[col] = int(v)
                else:
                    rec[col] = float(v)

    # Fuel poverty (DESNZ LILEE) — one field per LSOA
    fp = _read_parquet_opt(DATA_DIR / "demographics" / "fuel_poverty.parquet")
    if fp is not None and not fp.empty:
        for _, row in fp.iterrows():
            code = str(row["LSOA21CD"])
            v = row.get("fuel_poverty_pct")
            if code in out and pd.notna(v):
                out[code]["fuel_poverty_pct"] = round(float(v), 2)

    # IoD 2025 beyond the IMD: IDACI, IDAOPI and the sub-domains (File 7) and
    # the underlying indicators (File 8), each LSOA's own published figure.
    # The File 7 population denominators only weight the ward and LA figures,
    # so they are not written here.
    for iod_name in (IOD_EXTRA_PARQUET, IOD_INDICATORS_PARQUET):
        iod_path = DATA_DIR / "demographics" / iod_name
        idf = _read_parquet_opt(iod_path) if iod_path.exists() else None
        if idf is None or idf.empty or "LSOA21CD" not in idf.columns:
            continue
        icols = [c for c in idf.columns if c != "LSOA21CD" and not c.startswith("iod_pop_")]
        for rec in idf[["LSOA21CD"] + icols].to_dict("records"):
            code = str(rec["LSOA21CD"])
            if code not in out:
                continue
            for c in icols:
                v = rec[c]
                if v is None or pd.isna(v):
                    continue
                out[code][c] = (int(round(float(v))) if c.endswith(("_rank", "_decile"))
                                else round(float(v), 3))

    # PTAL (GLA LSOA Atlas, average band score 0-8)
    pt = _read_parquet_opt(DATA_DIR / "demographics" / "ptal.parquet")
    if pt is not None and not pt.empty:
        for _, row in pt.iterrows():
            code = str(row["LSOA21CD"])
            v = row.get("ptal_score")
            if code in out and pd.notna(v):
                out[code]["ptal_score"] = round(float(v), 2)

    # Air quality (Defra UK-AIR PCM) and transport access (TfL StopPoint).
    # Both are natively LSOA-level here: the pipeline builds them per LSOA and
    # the ward figures are derived from these, not the other way round.
    for parquet_name, cols in (
        ("air_quality_lsoa.parquet",
         ("no2_ugm3", "pm25_ugm3", "pm10_ugm3")),
        ("tfl_transport_lsoa.parquet",
         ("rail_station_dist_m", "rail_stations_1km",
          "bus_stop_dist_m", "bus_stops_800m")),
    ):
        src_df = _read_parquet_opt(DATA_DIR / "environment" / parquet_name)
        if src_df is None or src_df.empty:
            continue
        present = [c for c in cols if c in src_df.columns]
        for _, row in src_df.iterrows():
            code = str(row["LSOA21CD"])
            if code not in out:
                continue
            for col in present:
                v = row.get(col)
                if pd.notna(v):
                    # The two counts are counts, so they stay integers rather
                    # than rendering as "3.0 stations".
                    out[code][col] = (int(v) if col.endswith(("_1km", "_800m"))
                                      else round(float(v), 2))

    # Your own figures, LSOA level. Ward-level custom columns are not written
    # here: they belong to a ward and spreading one ward's number across its
    # neighbourhoods would invent detail the file does not have.
    cf = _read_parquet_opt(CUSTOM_DIR / "custom_lsoa.parquet")
    if cf is not None and not cf.empty:
        for _, row in cf.iterrows():
            code = str(row["LSOA21CD"])
            if code not in out:
                continue
            for col in [c for c in cf.columns if c != "LSOA21CD"]:
                v = row.get(col)
                if pd.notna(v):
                    out[code][col] = round(float(v), 4)

    # Green and blue space access (Defra, via run_greenblue)
    #
    # Only the fields the map actually offers are carried, matching how
    # vcse_data.json is trimmed to what is read; the parquet's other 31 columns
    # stay out of the payload.
    #
    # Coverage is all 4,994 LSOAs in scope. It was 1,313 until the source was
    # rebuilt: the parquet came from a one-off script scoped to the eight North
    # West London boroughs, and stayed that size after the map went
    # London-wide, so these seven overlays worked on a quarter of the map and
    # looked like missing data everywhere else. run_greenblue takes its scope
    # from the same LSOA lookup as the rest of the pipeline, so it cannot fall
    # behind the footprint again.
    GB_FIELDS = ("gb_total_uprn", "gb_commitment_pct", "blue_commitment_pct",
                 "green_commitment_pct", "green_doorstep_pct",
                 "green_local_pct", "green_neighbourhood_pct")
    gb = _read_parquet_opt(DATA_DIR / "environment" / "greenblue_lsoa.parquet")
    if gb is not None and not gb.empty:
        gb_cols = [c for c in GB_FIELDS if c in gb.columns]
        for _, row in gb.iterrows():
            code = str(row["LSOA21CD"])
            if code not in out:
                continue
            for col in gb_cols:
                v = row.get(col)
                if pd.notna(v):
                    out[code][col] = round(float(v), 2)

    # Claimant count (NOMIS CLA01)
    cl = _read_parquet_opt(DATA_DIR / "economy" / "claimant_count.parquet")
    if cl is not None and not cl.empty:
        cl_cols = [c for c in ("claimant_count", "claimant_rate_pct",
                               "claimant_yoy_change", "claimant_yoy_pct")
                   if c in cl.columns]
        for _, row in cl.iterrows():
            code = str(row["LSOA21CD"])
            if code not in out:
                continue
            for c in cl_cols:
                v = row.get(c)
                if pd.isna(v):
                    continue
                out[code][c] = (int(v) if c in ("claimant_count", "claimant_yoy_change")
                                else round(float(v), 2))

    # Distances to services, population estimates and police.uk crime for
    # each LSOA (small_area_lsoa_values). The crime keys are the ward ones,
    # counted from the same points.
    pops_l = ({} if cen is None or cen.empty or "census_population" not in cen.columns
              else dict(zip(cen["LSOA21CD"].astype(str),
                            pd.to_numeric(cen["census_population"], errors="coerce").fillna(0))))
    n_sa: dict = {}
    for code, vals in small_area_lsoa_values(pops_l).items():
        if code in out:
            out[code].update(vals)
            for k in vals:
                n_sa[k] = n_sa.get(k, 0) + 1
    if n_sa:
        info("small-area additions -> LSOAs: " + ", ".join(f"{k} {n:,}" for k, n in sorted(n_sa.items())))

    # Scope to the LSOAs the map can actually render. The IMD parquet is
    # national, so without this the file carries all 33,755 English LSOAs:
    # 41 MB downloaded by every visitor, of which 3.5% is ever looked up.
    # It also silently corrupted a statistic. _nwlDomainMean() in index.html
    # averages over Object.values(LSOA_DATA), so an unscoped file made the
    # the comparison mean an England mean. Scoping makes that function mean what its
    # name says.
    try:
        scope = set(get_lsoa_ward_lookup())
    except Exception as e:
        warn(f"lsoa_data: scope lookup unavailable ({e}); emitting unscoped")
        scope = set()
    if scope:
        kept = {k: v for k, v in out.items() if k in scope}
        if kept:
            info(f"lsoa_data: scoped {len(out):,} -> {len(kept):,} LSOAs")
            out = kept
        else:
            warn("lsoa_data: scope filter matched nothing; emitting unscoped")
    return out

def build_vcse_json():
    df = _read_parquet_opt(DATA_DIR / "vcse" / "charities.parquet")
    if df is None:
        return []
    list_cols = ["what_codes","what_tags","what_desc","how_codes","how_tags",
                 "how_desc","who_codes","who_tags","who_desc","areas","covers"]
    for c in list_cols:
        if c in df.columns:
            df[c] = df[c].apply(lambda v: json.loads(v) if isinstance(v, str) and v else [])
    # Only the fields index.html actually reads. Six former keys were dropped
    # after auditing every property access in the map against this file:
    #   act (activities), ar (areas), lsoa (LSOA21CD) - read nowhere at all
    #   reg (registered)  - the only .reg read is cicPopupHtml, which consumes
    #                       cics.json, not this file
    #   pc  (postcode)    - dropped as a full postcode. Small charities carry
    #                       only the postcode sector in pc instead of an
    #                       address; see _generalise_small_charity
    #   ward (WD25CD)     - every .ward read is a CSS class, an LSOA_IMD
    #                       property, or a GP record
    # Together they were 37.9% of a 9.7 MB payload that every visitor downloads.
    # Adding a field back means adding it here and reading it in index.html.
    #
    # The *_desc labels went too: they repeated in English, on every record,
    # what the *_tags codes already say, and the page holds the code to label
    # table once (VCSE_TAG_LABELS). And a region-wide charity's covers list
    # (every LA code in the scope, for about 5,800 records) is left out, as
    # its scope says the same; the page counts the LAs on its map instead.
    # Together that took the London file from 13.6 MB to about 7 MB.
    keep = ["num","name","addr","lat","lng","LAD25CD",
            "hq_in_nwl","covers","scope",
            "income","income_band","website",
            "what_tags","how_tags","who_tags"]
    cols = [c for c in keep if c in df.columns]
    out = df[cols].rename(columns={
        "name": "n", "addr": "a",
        "LAD25CD": "lad",
        "hq_in_nwl": "hq", "covers": "cv", "scope": "sc",
        "income": "inc", "income_band": "ib",
        "website": "w",
        "what_tags": "wt", "what_desc": "wd",
        "how_tags":  "ht", "how_desc":  "hd",
        "who_tags":  "ot", "who_desc":  "od",
    })
    records = out.to_dict(orient="records")
    pcs = df["postcode"].tolist() if "postcode" in df.columns else [None] * len(records)
    sts = df["reporting_status"].tolist() if "reporting_status" in df.columns else [None] * len(records)
    ends = df["acc_end"].tolist() if "acc_end" in df.columns else [None] * len(records)
    n = 0
    for rec, pc, st, end in zip(records, pcs, sts, ends):
        if rec.get("sc") == "london_wide":
            rec.pop("cv", None)
        apply_charity_flags(rec, st, end)
        n += _generalise_small_charity(rec, pc)
    info(f"vcse: {n:,} of {len(records):,} charities shown by area only "
         f"(income under \u00a3{SMALL_CHARITY_INCOME:,} or not filed)")
    info(f"vcse: {sum(1 for r in records if r.get('nr')):,} serve the area by the Near list's rule, "
         f"{sum(1 for r in records if r.get('np')):,} not pinned (council office or land only)")
    return records


# Which charities the Near list, the ward and LSOA reports and the Directory's
# nearest-first view show. The register holds every charity that names an area,
# and near a postcode that meant parochial church councils, school PTAs and
# grant funds, which hide the charities a resident might go to for help. The
# rest stay searchable in the Directory and on the Charities tab.
#
# A charity counts as serving the area when its latest accounts show income
# above zero and are not overdue, it says it provides services or advice
# (Charity Commission "how" codes 306 and 307), and its name is not that of a
# parochial church council, a school or its parents' association, a masonic
# lodge, an almshouse or a land or recreation ground trust.
CHARITY_NOT_LOCAL_HELP = re.compile(
    r"\b(PAROCHIAL CHURCH COUNCIL|P\.?C\.?C|SCHOOLS?|P\.?T\.?A|"
    r"PARENTS?[’']? ?(?:AND|&)? ?(?:TEACHERS?|STAFF)|PARENT TEACHER|"
    r"PARENTS[’']? (?:ASSOCIATION|ASSN)|HOME[- ]SCHOOL|MASONIC|LODGE|FREEMASONS?|"
    r"ALMS ?HOUSES?|LAND TRUST|RECREATION GROUNDS?|PLAYING FIELDS?)\b", re.I)
CHARITY_HELP_HOW = {"services", "advocacy_info"}
# A council acting as trustee gives its own office as the contact address, so
# the pin lands on the town hall, kilometres from the park it holds.
CHARITY_COUNCIL_ADDR = re.compile(
    r"\b(LONDON BOROUGH OF|L\.?B\.? OF|ROYAL BOROUGH OF|CITY OF LONDON CORPORATION|"
    r"(?:BOROUGH|CITY|COUNTY|DISTRICT|METROPOLITAN BOROUGH) COUNCIL|COUNCIL OF THE)\b", re.I)
CHARITY_COUNCIL_OFFICE = re.compile(r"\b(TOWN HALL|CIVIC CENTRE|CIVIC OFFICES?|COUNCIL OFFICES?|"
                                    r"GUILDHALL|PO BOX)\b", re.I)
CHARITY_LAND_NAME = re.compile(r"\b(PARK|FIELDS?|GREEN|GROUNDS?|COMMON|ALLOTMENTS?|PLAYGROUND|"
                               r"GARDENS?|RECREATION|OPEN SPACE|WOODS?|MEADOWS?|HEATH)\b", re.I)


def charity_accounts_overdue(status, acc_end, today=None) -> bool:
    """Whether the register says the latest accounts are late. A status of
    'overdue' or 'double default' says so; failing that, a financial year that
    ended over three years ago is taken as late."""
    s = str(status or "").lower()
    if "overdue" in s or "default" in s:
        return True
    d = str(acc_end or "")[:10]
    if len(d) == 10:
        try:
            end = datetime.strptime(d, "%Y-%m-%d").date()
        except ValueError:
            return False
        today = today or datetime.now(timezone.utc).date()
        return (today - end).days > 3 * 365
    return False


def charity_serves_area(name, income, how_tags, status=None, acc_end=None) -> bool:
    """The rule above: true for a charity the Near list should show."""
    try:
        inc = float(income)
    except (TypeError, ValueError):
        return False
    if pd.isna(inc) or inc <= 0:
        return False
    if charity_accounts_overdue(status, acc_end):
        return False
    if not (set(how_tags or []) & CHARITY_HELP_HOW):
        return False
    return not CHARITY_NOT_LOCAL_HELP.search(str(name or ""))


def charity_no_pin(name, addr, how_tags) -> bool:
    """True when the register's address does not show where the charity is:
    a council's office given as the contact for a charity it is trustee of,
    or a charity whose only activity is holding land, such as a park, which
    the Parks layer already shows where it really is."""
    a = str(addr or "")
    if CHARITY_COUNCIL_ADDR.search(a) and CHARITY_COUNCIL_OFFICE.search(a):
        return True
    return list(how_tags or []) == ["buildings_facilities"] and bool(CHARITY_LAND_NAME.search(str(name or "")))


def apply_charity_flags(rec: dict, status=None, acc_end=None) -> None:
    """Set nr (serves the area, see charity_serves_area) and np (do not pin,
    see charity_no_pin) on one vcse_data.json record, in place. Run before
    _generalise_small_charity, which blanks the address."""
    rec["nr"] = charity_serves_area(rec.get("n"), rec.get("inc"), rec.get("ht"), status, acc_end)
    if charity_no_pin(rec.get("n"), rec.get("a"), rec.get("ht")):
        rec["np"] = True
    else:
        rec.pop("np", None)


# A small charity's contact address on the register is often a trustee's home.
# Below this income, or with no income filed, the map shows where a charity is
# only to the postcode sector and its borough: no street address, and the pin
# moved to three decimal places (about 110 m by 70 m in London) so it does not
# land on one front door. Larger charities nearly always give an office, so
# theirs are shown as registered.
SMALL_CHARITY_INCOME = 100_000


def _postcode_sector(pc) -> str:
    """'E16AN' or 'E1 6AN' -> 'E1 6'. Empty when it does not look like a postcode."""
    s = str(pc or "").upper().replace(" ", "")
    return f"{s[:-3]} {s[-3]}" if 5 <= len(s) <= 7 else ""


def _generalise_small_charity(rec: dict, postcode) -> int:
    """Strip a small charity's record to an area in place. Returns 1 if it did."""
    inc = rec.get("inc")
    if isinstance(inc, (int, float)) and not pd.isna(inc) and inc >= SMALL_CHARITY_INCOME:
        return 0
    rec["a"] = ""
    rec["pc"] = _postcode_sector(postcode)
    for k in ("lat", "lng"):
        v = rec.get(k)
        if isinstance(v, float) and not pd.isna(v):
            rec[k] = round(v, 3)
    return 1


def build_pharmacies_json() -> list:
    pharm = _read_parquet_opt(DATA_DIR / "healthcare" / "pharmacies.parquet")
    if pharm is None:
        return []
    keep = ["name", "addr", "postcode", "tel", "lat", "lng",
            "LAD25CD", "LSOA21CD", "WD25CD"]
    cols = [c for c in keep if c in pharm.columns]
    df = pharm[cols].rename(columns={
        "name": "n", "addr": "a", "postcode": "pc",
        "LAD25CD": "lad", "LSOA21CD": "lsoa", "WD25CD": "ward",
    })
    return df.to_dict(orient="records")

MAP_DIR = DATA_DIR / "map"

# index.html's data globals and the file each one lives in. index.html loads
# these as classic <script src> tags, in this order, before the main block.
# Keep this in step with those tags: a mismatch means the global is undefined
# and the map renders empty rather than erroring.
MAP_BLOBS = {
    "GJ":         "wards.js",
    "GPS":        "gp_practices.js",
    "HOSP":       "hospitals.js",
    "LSOA_IMD":   "lsoa_imd.js",
    "BOROUGH_GJ": "boroughs.js",
    "PH_IND_STATS": "indicators.js",
    "PH_CUSTOM":     "custom.js",
}

def _read_geojson_opt(path: Path):
    """Load a boundary GeoJSON, or None with a warning if it is not there."""
    if not path.exists():
        warn(f"missing (skipped): {path.relative_to(REPO_ROOT)}")
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        warn(f"{path.name}: unreadable ({e})")
        return None

COORD_DECIMALS = 5  # ~0.7m of longitude at London's latitude


def _round_coords(obj, nd: int = COORD_DECIMALS):
    """
    Trim coordinate precision in anything shipped to the browser.

    ONS returns coordinates at full float precision, seventeen significant
    digits, which is sub-nanometre on a map whose finest pixel is metres. It is
    most of the file: at 20m generalisation the LSOA blob is 6.5MB at full
    precision and 3.6MB at five decimal places, and no point moves more than
    0.32m. That saving is what pays for the extra detail.

    Only coordinate arrays are touched. Indicator values keep their own
    precision, because rounding a rate and rounding a position are different
    decisions and only one of them is being made here.
    """
    if isinstance(obj, dict):
        return {k: (_round_floats(v, nd) if k == "coordinates" else _round_coords(v, nd))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [_round_coords(v, nd) for v in obj]
    return obj


def _round_floats(obj, nd: int):
    if isinstance(obj, float):
        return round(obj, nd)
    if isinstance(obj, list):
        return [_round_floats(v, nd) for v in obj]
    return obj


def write_map_blob(name: str, payload, description: str) -> None:
    """
    Write one of index.html's data globals to data/map/<name>.js.

    These are classic scripts loaded in order before the app, so the global is
    defined by the time any code reads it. That matters: every consumer in
    index.html reads these at parse time, several before the map object even
    exists, so they cannot become async fetches without restructuring the whole
    initialisation sequence.
    """
    if name not in MAP_BLOBS:
        raise RuntimeError(
            f"write_map_blob: unknown global {name!r}. Add it to MAP_BLOBS and "
            f"add a matching <script src> tag in index.html, or the global will "
            f"be undefined at parse time."
        )
    MAP_DIR.mkdir(parents=True, exist_ok=True)
    body = json.dumps(_round_coords(_scrub_nan(payload)),
                      separators=(",", ":"), ensure_ascii=False)
    write_atomic(MAP_DIR / MAP_BLOBS[name], (
        f"// {description}\n"
        f"// Loaded by index.html as a classic script before the main block,\n"
        f"// so this global is defined by the time any code reads it.\n"
        f"var {name} = {body};\n"
    ))

# Indicators read from a register as it stood on the day it was fetched, so
# their only date is that day. Keyed by the manifest's source name.
REGISTER_KEYS = {
    "tfl":        ("rail_station_dist_m", "rail_stations_1km",
                   "bus_stop_dist_m", "bus_stops_800m"),
    "naptan":     ("rail_station_dist_m", "rail_stations_1km",
                   "bus_stop_dist_m", "bus_stops_800m"),
    "service_access": ("gp_dist_km", "pharmacy_dist_km", "dentist_dist_km"),
}


def _indicator_periods() -> dict[str, str]:
    """
    Indicator key -> the period its figures cover, from the source itself.

    Fingertips states a period on every row, and it differs a great deal from
    one indicator to the next: the Local Health admissions pool 2016/17 to
    2020/21 while the borough QOF figures are 2024/25. Saying "latest" for all
    of them reads as current to anyone who does not go and check.

    Each indicator takes the period most of its areas carry. Fuel poverty takes
    the year its DESNZ release says the figures describe (run_fuel_poverty
    stores it with the figures). A register has no
    period, only the date it was read, which comes from the manifest and only
    from a run that succeeded: a failed fetch leaves the previous file in
    place, and dating it today would be wrong.
    """
    out: dict[str, str] = {}

    def _modal(df, key_col, prefix):
        if df is None or df.empty or "period" not in df.columns:
            return
        df = df[df["period"].fillna("").astype(str).str.strip() != ""]
        for key, s in df.groupby(key_col)["period"]:
            out[f"{prefix}{key}"] = str(s.astype(str).mode().iloc[0]).strip()

    msoa = local_health_msoa_frame()
    if msoa is not None and "indicator_id" in msoa.columns:
        _modal(msoa.assign(indicator_id=msoa["indicator_id"].astype(int)),
               "indicator_id", "ft_")
    boro = _read_parquet_opt(DATA_DIR / "outcomes" / "fingertips.parquet")
    if boro is not None and "indicator_short" in boro.columns:
        _modal(boro, "indicator_short", "ft_")
    fp_year = _fuel_poverty_period(
        _read_parquet_opt(DATA_DIR / "demographics" / "fuel_poverty.parquet"))
    if fp_year:
        out["fuel_poverty_pct"] = fp_year
    out.update(small_area_periods())
    # The ONS MSOA releases carry the period their edition names.
    for subdir, name, keys in (("demographics", SAIE_PARQUET, SAIE_KEYS),
                               ("outcomes", HLE_PARQUET, HLE_KEYS)):
        f = _read_static_opt(subdir, name)
        if f is None or f.empty or "period" not in f.columns:
            continue
        per = f["period"].fillna("").astype(str).str.strip()
        per = per[per != ""]
        if per.empty:
            continue
        for k in keys:
            if k in f.columns:
                out[k] = str(per.mode().iloc[0])

    try:
        sources = json.loads(MANIFEST_PATH.read_text(encoding="utf-8")).get("sources") or {}
    except (OSError, ValueError):
        sources = {}
    for src, keys in REGISTER_KEYS.items():
        rec = sources.get(src) or {}
        if rec.get("status") != "ok" or not rec.get("fetched_at"):
            continue
        try:
            when = datetime.fromisoformat(str(rec["fetched_at"])).strftime("%b %Y")
        except ValueError:
            continue
        for k in keys:
            out[k] = f"as at {when}"
    return out


def build_indicator_stats(ward_data: dict, lsoa_data: dict,
                          msoa_data: dict, borough_data: dict) -> dict:
    """
    Facts about every indicator, measured from the payloads just written.

    This exists because the map's indicator registry is hand-written in
    index.html — 125 entries of OV_CFG, each needing a min, a max and a
    polarity — and ovColor() paints an indicator grey if its entry is missing.
    That is a hard gate: adding the 600 published Fingertips indicators, or QOF
    at LSOA, would mean hand-writing 600 more. It does not scale, and the
    hand-written half is where the mistakes live: ptal_score was given the
    range of a different measure entirely, and nothing noticed because a range
    is just two numbers that look plausible.

    Three of the four things an entry needs can simply be measured, so they are
    measured here rather than typed:

      lv    which levels actually carry this indicator. Derived from presence
            in the payloads, so it cannot drift from the data the way a
            hand-declared list does.
      p5/p95 a sane colour range, the same 5th-95th percentile rule
            getOvRange() already applies at run time, but computed once over
            the whole payload rather than per session.
      n/d   how many areas hold a value, and how many DISTINCT values there
            are. This is the honest answer to "will this indicator actually
            shade the map at this level". A borough series written onto every
            ward has 704 areas and 33 distinct values, and that ratio is the
            difference between a real choropleth and 33 blocks of colour. It
            is not derivable from the label, and it is the single most useful
            thing to know before choosing.

    The fourth, polarity, is genuinely semantic and cannot be measured — but it
    does not have to be guessed either, because Fingertips publishes it. Where
    scripts/fingertips_metadata.json has an entry, its wh flag is carried
    through, which is how the 600 can arrive without anyone deciding by hand
    which direction is bad.

      yr    the period the figures cover, where the source says so (see
            _indicator_periods). The map shows it beside every legend in
            place of the hand-typed one, which is written once and is wrong
            from the first refresh that moves the source on.

    Deliberately emits FACTS ONLY, no labels, groups or descriptions. Those
    stay hand-written in index.html where they are edited. Parsing JS object
    literals out of a 12,000-line HTML file to round-trip them would be a
    fragile way to gain nothing.
    """
    import statistics

    LEVELS = [
        ("ward",    ward_data.get("wards") or {}, True),
        ("lsoa",    lsoa_data or {},              False),
        ("msoa",    msoa_data or {},              False),
        ("borough", borough_data or {},           False),
    ]
    # Keys that are identifiers or labels rather than measurements. Percentiles
    # over a postcode are meaningless and a range over one would be worse.
    SKIP = {"name", "code", "lad", "lad_name", "borough", "ward", "lsoa_name",
            "msoa_name", "ward_code", "pop"}

    stats: dict[str, dict] = {}
    for level, payload, nested in LEVELS:
        buckets: dict[str, list] = {}
        for rec in payload.values():
            inds = (rec.get("indicators") if nested else rec) or {}
            if not isinstance(inds, dict):
                continue
            for k, v in inds.items():
                # A 95% limit rides beside its figure and is not one itself.
                if k in SKIP or k.startswith("_") or is_interval_key(k):
                    continue
                if isinstance(v, bool) or not isinstance(v, (int, float)):
                    continue
                if v != v:            # NaN
                    continue
                buckets.setdefault(k, []).append(float(v))
        for k, vals in buckets.items():
            if len(vals) < 2:
                continue
            rec = stats.setdefault(k, {"lv": {}})
            s = sorted(vals)
            rec["lv"][level] = {
                "n": len(s),
                "d": len(set(s)),
                # Percentiles, not min/max: one outlying ward should not spend
                # the whole colour ramp, which is the rule the run-time
                # fallback already follows.
                "p5":  round(s[int(len(s) * 0.05)], 4),
                "p95": round(s[min(len(s) - 1, int(len(s) * 0.95))], 4),
            }

    # Polarity from the publisher, for anything Fingertips describes.
    ft_meta = REPO_ROOT / "scripts" / "fingertips_metadata.json"
    carried = 0
    if ft_meta.exists():
        try:
            for row in json.loads(ft_meta.read_text(encoding="utf-8")):
                wh = row.get("wh")
                if wh is None:
                    continue
                for key in (f"ft_{row.get('indicator_id')}",):
                    if key in stats:
                        stats[key]["wh"] = bool(wh)
                        carried += 1
        except (OSError, ValueError) as e:
            warn(f"indicator stats: could not read fingertips metadata ({e})")

    dated = 0
    for key, period in _indicator_periods().items():
        if key in stats and period:
            stats[key]["yr"] = period
            dated += 1

    info(f"indicator stats: {len(stats):,} indicators measured across "
         f"{sum(1 for _ in LEVELS)} levels; polarity carried for {carried}; "
         f"period carried for {dated}")
    age_bands = census_age_bands()
    ft_bad = ft_age_mismatches()
    gaps = local_health_known_gaps()
    if gaps:
        info(f"indicator stats: {len(gaps)} Local Health series not shipped here, listed as known gaps: "
             + ", ".join(gaps))
    out = {
        "generated": datetime.now(timezone.utc).isoformat(),
        # How the Census ward figures were made, for the methodology page,
        # which reads this file rather than the much larger ward file.
        "census_method": ((ward_data or {}).get("metadata") or {}).get("census_method", ""),
        # "counts" when the LA file carries Census percentages (each from
        # summed counts, see census_la_figures); otherwise the map averages
        # the wards by population.
        "census_la_method": "counts" if any(
            k.startswith("census_") and k.endswith("_pct")
            for rec in (borough_data or {}).values() if isinstance(rec, dict)
            for k in (rec.get("indicators") or rec)) else "",
        # Which ages census_under16_pct and census_working_age_pct cover:
        # "0-15" when they were built from single years of age (TS007), so
        # the map labels them under 16 and 16 to 64. Anything else means the
        # five-year bands of TS007A, which give 0 to 14 and 15 to 64, and the
        # map says so.
        "census_age_bands": age_bands,
        # "L12-L13" when census_routine_semi_routine_pct counts NS-SEC L12
        # and L13 only at every level. Files built before that fix also
        # counted L7 and L8 to L9, about twice the true share, and the map
        # leaves the indicator out until this says the figures were rebuilt.
        "census_routine_classes": census_routine_classes(),
        # The LA Fingertips series whose stored age is not the headline age
        # (FT_HEADLINE_AGE). The map leaves these out; ft_age_checked says the
        # check ran, so a file written before it existed is treated with care.
        "ft_age_checked": ft_bad is not None,
        "ft_age_mismatch": sorted(ft_bad or []),
        # Local Health series that this scope does not ship because OHID
        # publishes too little of them here (ft_msoa_series_check), so the
        # region pack check does not hold a region back for lacking a series
        # London has (scripts/check_region_pack.py). A series that failed to
        # download is not listed: that is a failure, not a gap. Written only
        # when there are some, so a build with none keeps the file as it was.
        "known_gaps": gaps,
        "stats": stats,
    }
    if not gaps:
        out.pop("known_gaps")
    return out


def local_health_known_gaps() -> list:
    """ft_ keys of the FINGERTIPS_MSOA_EXTRA series this scope's last run
    checked and did not ship, for the publisher's reasons."""
    try:
        rec = json.loads((DATA_DIR / "outcomes" / FT_MSOA_EXTRA_CHECKS).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    series = (rec or {}).get("series") or {}
    return sorted(f"ft_{i}" for i, c in series.items()
                  if isinstance(c, dict) and not c.get("ships") and c.get("kind") == "publisher")


def census_age_bands() -> str:
    """'0-15' when every census level on disk built its under 16 and working
    age bands from single years of age, else '0-14' (see _census_frame)."""
    p = DATA_DIR / "demographics" / "census2021_age_bands.json"
    try:
        rec = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "0-14"
    vals = {v for v in (rec or {}).values() if isinstance(v, str)}
    return "0-15" if vals == {"0-15"} else "0-14"


def census_routine_classes() -> str:
    """'L12-L13' when every census level on disk built the routine and
    semi-routine figure from NS-SEC L12 and L13 alone, else ''. A level
    built before that fix (it also counted L7 and L8 to L9) has no entry in
    census2021_nssec.json; the levels that ran are the ones the age bands
    record lists, since _census_frame writes both."""
    demo = DATA_DIR / "demographics"
    try:
        ran = json.loads((demo / "census2021_age_bands.json").read_text(encoding="utf-8"))
        rec = json.loads((demo / "census2021_nssec.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    levels = [lv for lv, v in (ran or {}).items() if isinstance(v, str)]
    return "L12-L13" if levels and all((rec or {}).get(lv) == "L12-L13" for lv in levels) else ""


def ft_age_mismatches() -> "list | None":
    """ft_ keys whose stored age is not the headline age, or None when the
    Fingertips file has no age column to check."""
    ft = _read_parquet_opt(DATA_DIR / "outcomes" / "fingertips.parquet")
    if ft is None or ft.empty or "age" not in ft.columns:
        return None
    bad = []
    for (ind_id, short), g in ft.groupby(["indicator_id", "indicator_short"]):
        want = FT_HEADLINE_AGE.get(int(ind_id))
        if not want:
            continue
        ages = {str(a) for a in g["age"].dropna() if str(a)}
        if ages and ages != {want}:
            bad.append(f"ft_{short}")
    return bad


def export_msoa_outline(feats: list) -> None:
    """
    The MSOA outline layer index.html lazy-loads, at data/map/msoa_boundaries.json.

    Not a MAP_BLOBS entry, and deliberately not a <script src>. The map blobs
    are classic scripts parsed on every page load because their globals are
    read at parse time. This layer is off by default and is only outlines, so
    paying about a megabyte on every visit for something most visits never
    switch on would be the wrong trade. It is fetched the first time the box is
    ticked and cached from then on.

    Properties are the code and the ONS name only. MSOA21NM is already of the
    form "Brent 001", so the borough is in the name, and no borough is derived
    here: an MSOA's LSOAs can sit in more than one, and a modal answer stated as
    a fact would be wrong for exactly the cases anyone would want it for.
    """
    MAP_DIR.mkdir(parents=True, exist_ok=True)
    slim = []
    for f in feats:
        p = f.get("properties", {})
        slim.append({
            "type": "Feature",
            "geometry": f.get("geometry"),
            "properties": {"code": p.get("MSOA21CD", ""), "name": p.get("MSOA21NM", "")},
        })
    payload = _round_coords(_scrub_nan({"type": "FeatureCollection", "features": slim}))
    path = MAP_DIR / "msoa_boundaries.json"
    write_atomic(path, json.dumps(payload, separators=(",", ":"), ensure_ascii=False))
    kb = path.stat().st_size / 1024
    ok(f"map blob: {len(slim):,} MSOAs -> data/map/msoa_boundaries.json ({kb:,.0f} kB)")


def export_map_blobs() -> None:
    """Regenerate the map data files the pipeline owns."""
    index_path = REPO_ROOT / "index.html"
    if not index_path.exists():
        warn(f"index.html not found at {index_path}; skipping map blob export")
        return

    gps = _read_parquet_opt(DATA_DIR / "healthcare" / "gp_practices.parquet")
    if gps is not None:
        cols = [c for c in ["name", "addr", "lat", "lng", "postcode", "code",
                            "ward", "lad", "tel", "patients", "kind"] if c in gps.columns]
        gp_records = (gps[cols]
                      .rename(columns={"name": "n", "addr": "a", "postcode": "pc"})
                      .to_dict(orient="records"))
        # kind is set only for the few practices with a label (the practices
        # for people who are homeless), so it is left off every other record.
        for rec in gp_records:
            if not rec.get("kind"):
                rec.pop("kind", None)

        # Splice QOF prevalence onto each practice as a compact `qof` dict.
        qof = _read_parquet_opt(DATA_DIR / "healthcare" / "qof_prevalence.parquet")
        if qof is not None and not qof.empty and "code" in qof.columns:
            qof_cols = [c for c in qof.columns
                        if c.startswith("qof_") and c.endswith("_pct")]
            qof_by_code: dict[str, dict] = {}
            for _, row in qof.iterrows():
                code = str(row["code"]).upper().strip()
                d: dict[str, float] = {}
                for c in qof_cols:
                    v = row.get(c)
                    if pd.notna(v):
                        short = c[4:-4]  # strip 'qof_' and '_pct'
                        d[short] = round(float(v), 2)
                if d:
                    qof_by_code[code] = d
            for rec in gp_records:
                c = str(rec.get("code") or "").upper().strip()
                if c in qof_by_code:
                    rec["qof"] = qof_by_code[c]
            ok(f"spliced QOF onto {sum(1 for r in gp_records if 'qof' in r):,} practices")

        write_map_blob("GPS", gp_records,
                       "GP practices. Emitted by fetch_all_data.py from "
                       "data/healthcare/gp_practices.parquet, with QOF prevalence attached.")
        ok(f"map blob: {len(gp_records):,} GP practices -> "
           f"data/map/{MAP_BLOBS['GPS']}")

    # Ward and borough outlines, straight from the fetched boundaries so the
    # map's shapes and the pipeline's scope can never disagree.
    bdir = DATA_DIR / "boundaries"
    wards_gj = _read_geojson_opt(bdir / "wards.geojson")
    if wards_gj:
        # Ward names are not unique across London. Eleven are shared by two
        # boroughs: Kilburn is in both Brent and Camden, Village is both
        # Wimbledon Village in Merton and a ward in Barking and Dagenham, and
        # so on. index.html keys its ward lookup by this name, so a duplicate
        # silently masks one of the pair and clicking that ward shows the other
        # borough's figures. Two wards at opposite ends of the deprivation
        # range sharing a name is exactly the case that looks plausible and is
        # wrong.
        #
        # Shared names get their borough appended, which is the convention the
        # app already expects ("Kilburn (Brent)"). Unique names are left alone,
        # so 693 of the 704 read exactly as before.
        name_counts: dict = {}
        for f in wards_gj["features"]:
            nm = f.get("properties", {}).get("WD25NM", "")
            name_counts[nm] = name_counts.get(nm, 0) + 1
        for f in wards_gj["features"]:
            p = f.get("properties", {})
            nm, lad = p.get("WD25NM", ""), p.get("LAD25NM", "")
            if name_counts.get(nm, 0) > 1 and lad:
                nm = f"{nm} ({lad})"
            # index.html reads WD24NM/WD24CD/LAD; keep those names so the app
            # does not need touching for a boundary-vintage change.
            f["properties"] = {
                "WD24NM": nm, "WD24CD": p.get("WD25CD", ""),
                "LAD": lad,
            }
        shared = sum(1 for n, c in name_counts.items() if c > 1)
        if shared:
            info(f"  {shared} ward name(s) shared by more than one {LA_WORD}; "
                 f"disambiguated with the {LA_WORD} name")
        write_map_blob("GJ", wards_gj,
                       "Ward boundaries. ONS WD_MAY_2025_UK_BGC_V2, scoped to the borough list.")
        ok(f"map blob: {len(wards_gj['features']):,} wards -> data/map/{MAP_BLOBS['GJ']}")

    bor_gj = _read_geojson_opt(bdir / "boroughs.geojson")
    if bor_gj:
        for f in bor_gj["features"]:
            p = f.get("properties", {})
            f["properties"] = {"name": p.get("LAD25NM", ""), "LAD25CD": p.get("LAD25CD", "")}
        write_map_blob("BOROUGH_GJ", bor_gj,
                       "Borough outlines. ONS LAD_MAY_2025_UK_BGC_V2, scoped to the borough list.")
        ok(f"map blob: {len(bor_gj['features']):,} {LA_WORD}s -> data/map/{MAP_BLOBS['BOROUGH_GJ']}")

    # LSOA shapes carrying the IMD figures and their ward, which is what the
    # LSOA choropleth and the ward drill-down both read.
    lsoa_gj = _read_geojson_opt(bdir / "lsoa.geojson")
    imd = _read_parquet_opt(DATA_DIR / "demographics" / "imd2025.parquet")
    if lsoa_gj and imd is not None:
        imd_by_code = {}
        for _, row in imd.iterrows():
            imd_by_code[str(row["LSOA21CD"])] = (
                None if pd.isna(row.get("imd_decile")) else int(row["imd_decile"]),
                None if pd.isna(row.get("imd_rank")) else int(row["imd_rank"]),
            )
        try:
            lookup = get_lsoa_ward_lookup()
        except Exception as e:
            warn(f"map blob: LSOA ward lookup unavailable ({e})")
            lookup = {}
        ward_names, borough_names = {}, {}
        for f in (wards_gj or {"features": []})["features"]:
            p = f["properties"]
            ward_names[p["WD24CD"]] = p["WD24NM"]
            borough_names[p["WD24CD"]] = p["LAD"]
        kept = []
        for f in lsoa_gj["features"]:
            p = f.get("properties", {})
            code = p.get("LSOA21CD", "")
            wd = (lookup.get(code) or ("", ""))[0]
            dec, rank = imd_by_code.get(code, (None, None))
            f["properties"] = {
                "code": code, "name": p.get("LSOA21NM", ""),
                "ward": ward_names.get(wd, ""), "ward_code": wd,
                "borough": borough_names.get(wd, ""),
                "imd_decile": dec, "imd_rank": rank,
            }
            kept.append(f)
        lsoa_gj["features"] = kept
        write_map_blob("LSOA_IMD", lsoa_gj,
                       "LSOA boundaries with IMD 2025 decile and rank, and their ward.")
        ok(f"map blob: {len(kept):,} LSOAs -> data/map/{MAP_BLOBS['LSOA_IMD']}")

    # HOSP used to be excluded here, because data/map/hospitals.js was twenty
    # hand-typed records and run_hospitals() could not produce them. It reads
    # the ODS trust sites register now and covers all 33 boroughs, so the blob
    # is written like every other one.
    hosp = _read_parquet_opt(DATA_DIR / "healthcare" / "hospitals.parquet")
    if hosp is not None and len(hosp):
        write_map_blob("HOSP", [hosp_blob_row(r) for _, r in hosp.iterrows()],
                       "Hospital sites, from the ODS NHS trust sites register, "
                       "checked against data/curated/hospital_sites.csv.")
        ok(f"map blob: {len(hosp):,} hospitals -> data/map/{MAP_BLOBS['HOSP']}")


def build_dental_json() -> list:
    """dental_practices.json, in the shape loadDentalLayer() already reads."""
    df = _read_parquet_opt(DATA_DIR / "healthcare" / "dental_practices.parquet")
    if df is None:
        return []
    keep = [c for c in ["name", "addr", "postcode", "tel", "kind", "lat", "lng",
                        "nhs_contracted", "lad_code", "imd_decile"] if c in df.columns]
    out = df[keep].to_dict(orient="records")
    for r in out:
        for k in ("addr", "tel", "kind"):
            if k in r and not r[k]:
                del r[k]
        # loadDentalLayer treats nhs_contracted as a tri-state: true, false, or
        # null for unknown. Keep it a real bool so the filter stays meaningful.
        if "nhs_contracted" in r and r["nhs_contracted"] is not None:
            r["nhs_contracted"] = bool(r["nhs_contracted"])
    return out


def lsoa_census_counts(counts: "pd.DataFrame | None", codes) -> dict:
    """The Census numerators and denominators for each LSOA, for Custom area.

    A custom area is built from LSOAs in the browser. Averaging their
    percentages by residents is wrong for a household measure (tenure, cars),
    so the page sums these counts instead, as a ward's figures are made.
    {"keys": [census_*_pct, ...], "rows": {LSOA21CD: [num..., den...]}}, the
    numerators in key order and then the denominators in the same order."""
    if counts is None or counts.empty or "LSOA21CD" not in counts.columns:
        return {}
    keys = sorted(c[:-5] for c in counts.columns
                  if c.endswith("__num") and f"{c[:-5]}__den" in counts.columns)
    want = set(codes)
    sub = counts[counts["LSOA21CD"].astype(str).isin(want)]
    rows: dict = {}
    for rec in sub.to_dict("records"):
        def iv(x):
            return None if x is None or pd.isna(x) else int(round(float(x)))
        rows[str(rec["LSOA21CD"])] = ([iv(rec[f"{k}__num"]) for k in keys] +
                                      [iv(rec[f"{k}__den"]) for k in keys])
    return {"keys": keys, "rows": rows} if rows else {}


def export_all() -> None:
    rule("Export Leaflet JSON outputs")
    ward_data  = build_ward_data()
    lsoa_data  = build_lsoa_data()
    msoa_data  = build_msoa_data()
    boro_data  = build_borough_data()
    pharm_data = build_pharmacies_json()
    vcse_data  = build_vcse_json()
    dental_data = build_dental_json()

    write_json_atomic(OUT_ROOT / "ward_data.json",  ward_data)
    write_json_atomic(OUT_ROOT / "lsoa_data.json",  lsoa_data)
    write_json_atomic(OUT_ROOT / "msoa_data.json",  msoa_data)
    write_json_atomic(OUT_ROOT / "borough_data.json", boro_data)
    write_json_atomic(OUT_ROOT / "pharmacies.json", pharm_data)
    write_json_atomic(OUT_ROOT / "vcse_data.json",  vcse_data)
    if dental_data:
        write_json_atomic(OUT_ROOT / "dental_practices.json", dental_data)
    # Census counts per LSOA, fetched by Custom area only when it is used.
    cen_counts = lsoa_census_counts(
        _read_parquet_opt(DATA_DIR / "demographics" / "census2021_counts.parquet"), lsoa_data)
    if cen_counts:
        write_json_atomic(OUT_ROOT / "lsoa_census_counts.json", cen_counts)
    # Measured from the four payloads above, so it can never describe a
    # different build than the one being shipped.
    # Descriptors for anything in data/custom, so index.html can add the
    # options, the colour config and the descriptions itself. Written even when
    # empty: a stale custom.js naming an indicator whose CSV has been deleted
    # would leave a permanently blank overlay in the picker.
    _custom_reg = []
    _reg_path = CUSTOM_DIR / "custom_registry.json"
    if _reg_path.exists():
        try:
            _custom_reg = json.loads(_reg_path.read_text(encoding="utf-8"))
        except ValueError as e:
            warn(f"custom: registry unreadable ({e}); the map will not show it")
    write_map_blob("PH_CUSTOM", _custom_reg,
                   "Indicators from data/custom. Generated - do not edit.")

    write_map_blob(
        "PH_IND_STATS",
        build_indicator_stats(ward_data, lsoa_data, msoa_data, boro_data),
        "Per-indicator facts measured from the payloads: which levels carry "
        "it, how many distinct values it has at each, a 5th-95th "
        "percentile range, and the period it covers where the source "
        "states one. Generated — do not edit.",
    )

    ok(f"ward_data.json:  {len(ward_data.get('wards', {})):,} wards")
    ok(f"lsoa_data.json:  {len(lsoa_data):,} LSOAs")
    ok(f"msoa_data.json:  {len(msoa_data):,} MSOAs")
    ok(f"borough_data.json: {len(boro_data):,} {LA_WORD}s")
    ok(f"pharmacies.json: {len(pharm_data):,} rows")
    ok(f"vcse_data.json:  {len(vcse_data):,} charities")
    if dental_data:
        ok(f"dental_practices.json: {len(dental_data):,} practices")

    export_map_blobs()

# ============================================================================
# MAIN
# ============================================================================
# ============================================================================
# SOURCE: Access to green and blue space  (Defra, with ONS, OS, Natural England)
# ============================================================================
# The parquet this writes was previously produced by a one-off script kept in
# archive/, which scoped itself to the LSOA codes in data/boundaries/lsoa.geojson.
# At the time that file held the eight North West London boroughs, so the
# parquet held 1,313 rows, and it kept holding 1,313 after the map went
# London-wide. The seven green and blue overlays therefore worked on a quarter
# of the map and were empty everywhere else, which looks like missing data
# rather than a footprint that was never widened.
#
# As a source it re-derives from the published table each run and takes its
# scope from the same LSOA lookup as everything else, so it cannot fall behind
# the footprint again.
#
# Geography: the table is one row per 2021 Output Area, roughly four to six per
# LSOA. Aggregating means summing the UPRN numerator and the UPRN denominator
# across the OAs in each LSOA and then dividing. Averaging the OA percentages
# would weight a 40-address OA the same as a 400-address one.
GREENBLUE_PAGE = ("https://www.gov.uk/government/statistics/"
                  "access-to-green-and-blue-space-in-england")
GREENBLUE_ODS = ("https://assets.publishing.service.gov.uk/media/"
                 "69a184aef534e7e99adaeab4/"
                 "Access_to_green_and_blue_space_England_data_table.ods")

# The three sheets are named "1", "2" and "3": green-and-blue combined, blue,
# and green. The prefix each becomes in the output columns.
_GB_SHEETS = {"1": "gb", "2": "blue", "3": "green"}

_GB_TABLE_OPEN = re.compile(rb'<table:table[^>]*table:name="([^"]+)"[^>]*>')
_GB_TABLE_CLOSE = re.compile(rb"</table:table>")
_GB_ROW_OPEN = re.compile(rb"<table:table-row[^>]*>")
_GB_ROW_END = re.compile(rb"</table:table-row>")
_GB_CELL = re.compile(
    rb"<table:table-cell([^>]*)>((?:(?!</table:table-cell>).)*)</table:table-cell>"
    rb"|<table:table-cell([^>]*)/>", re.DOTALL)
_GB_REPEAT = re.compile(rb'table:number-columns-repeated="(\d+)"')
_GB_VALUE = re.compile(rb'office:value="([^"]*)"')
_GB_TEXT_P = re.compile(rb"<text:p[^>]*>((?:(?!</text:p>).)*)</text:p>", re.DOTALL)
_GB_TAG = re.compile(rb"<[^>]+>")
_GB_LSOA = re.compile(rb">(E01\d{6})<")


def _gb_parse_row(row_xml: bytes) -> list:
    cells = []
    for m in _GB_CELL.finditer(row_xml):
        attrs = m.group(1) or m.group(3) or b""
        inner = m.group(2) or b""
        vm = _GB_VALUE.search(attrs)
        if vm:
            val = vm.group(1).decode("utf-8", errors="replace")
        else:
            bits = [_GB_TAG.sub(b"", p).decode("utf-8", errors="replace").strip()
                    for p in _GB_TEXT_P.findall(inner)]
            val = " ".join(b for b in bits if b)
        rm = _GB_REPEAT.search(attrs)
        reps = int(rm.group(1)) if rm else 1
        # A run of hundreds of empty repeated cells is padding at the end of a
        # row, not data; keeping one placeholder is enough to hold the column
        # positions of anything that follows.
        if reps > 50 and val == "":
            cells.append("")
        else:
            cells.extend([val] * min(reps, 50))
    while cells and cells[-1] == "":
        cells.pop()
    return cells


def run_greenblue() -> pd.DataFrame:
    rule("Access to green and blue space (Defra)")
    cache_dir = CACHE_DIR / "greenblue"
    cache_dir.mkdir(parents=True, exist_ok=True)
    ods = cache_dir / "Access_to_green_and_blue_space_England_data_table.ods"

    if not ods.exists():
        info(f"  downloading {GREENBLUE_ODS.rsplit('/', 1)[-1]} (about 45 MB)")
        # Generous timeout: it is a 45 MB file and the whole source depends on
        # getting all of it, so a slow connection should wait rather than
        # produce a truncated archive that fails later as a zip error.
        r = _http_bytes(GREENBLUE_ODS, source="greenblue", timeout=900)
        write_bytes_atomic(ods, r.content)
    else:
        ok(f"greenblue: using cached ODS ({ods.stat().st_size / 1e6:.0f} MB)")

    in_scope = set(get_lsoa_ward_lookup())
    if not in_scope:
        raise RuntimeError("greenblue: no in-scope LSOAs; run boundaries first")

    # Streamed, not parsed into a DOM. Unzipped, content.xml is about 1.4 GB of
    # XML; odfpy loads the whole thing and runs out of memory on it.
    sums = {s: {} for s in _GB_SHEETS}
    seen = {s: 0 for s in _GB_SHEETS}
    CHUNK = 16 * 1024 * 1024
    with zipfile.ZipFile(ods) as zf, zf.open("content.xml") as fp:
        carry, cur, header_seen, meta = b"", None, False, (0, [])
        while True:
            data = fp.read(CHUNK)
            if not data and not carry:
                break
            chunk = carry + data if data else carry
            pos, N = 0, len(chunk)
            while pos < N:
                if cur is None:
                    m = _GB_TABLE_OPEN.search(chunk, pos)
                    if m is None:
                        break
                    name = m.group(1).decode("utf-8", errors="replace")
                    pos = m.end()
                    if name in _GB_SHEETS:
                        cur, header_seen, meta = name, False, (0, [])
                    continue
                r = _GB_ROW_OPEN.search(chunk, pos)
                close = _GB_TABLE_CLOSE.search(chunk, pos, r.start() if r else N)
                if close:
                    pos = close.end()
                    cur = None
                    continue
                if r is None:
                    break
                rc = _GB_ROW_END.search(chunk, r.end())
                if rc is None:
                    break
                row_xml = chunk[r.end():rc.start()]
                pos = rc.end()
                if not header_seen:
                    cells = _gb_parse_row(row_xml)
                    if cells and cells[0] == "OA21CD":
                        header_seen = True
                        meta = (cells.index("LSOA21CD"),
                                [(h, i) for i, h in enumerate(cells)
                                 if h == "total_uprn" or h.startswith("uprn_in_")])
                    continue
                m = _GB_LSOA.search(row_xml)
                if m is None:
                    continue
                lsoa = m.group(1).decode("ascii")
                if lsoa not in in_scope:
                    continue
                cells = _gb_parse_row(row_xml)
                if not cells:
                    continue
                seen[cur] += 1
                acc = sums[cur].setdefault(lsoa, {c: 0 for c, _ in meta[1]})
                for name, idx in meta[1]:
                    if idx < len(cells) and cells[idx]:
                        try:
                            acc[name] += int(float(cells[idx]))
                        except ValueError:
                            pass
            carry = chunk[pos:]
            if not data:
                break

    records: dict = {}
    for sheet, pref in _GB_SHEETS.items():
        for lsoa, acc in sums[sheet].items():
            rec = records.setdefault(lsoa, {"LSOA21CD": lsoa})
            total = acc.get("total_uprn", 0)
            rec[f"{pref}_total_uprn"] = total
            for k, v in acc.items():
                if k == "total_uprn":
                    continue
                tag = k.replace("uprn_in_", "")
                rec[f"{pref}_{tag}_uprn"] = v
                rec[f"{pref}_{tag}_pct"] = round(100.0 * v / total, 2) if total else None

    df = pd.DataFrame.from_records(list(records.values()))
    if len(df) < len(in_scope) * 0.95:
        raise RuntimeError(
            f"greenblue: only {len(df):,} of {len(in_scope):,} in-scope LSOAs matched "
            f"the published table. Refusing to ship a partial layer as a whole one, "
            f"which is the fault this source was written to fix."
        )
    out_dir = DATA_DIR / "environment"
    out_dir.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_dir / "greenblue_lsoa.parquet", index=False)
    ok(f"greenblue: {len(df):,} LSOAs, {len(df.columns)} columns -> "
       f"data/environment/greenblue_lsoa.parquet")
    return pd.DataFrame([{"lsoas": len(df), "columns": len(df.columns)}])


# ============================================================================
# SOURCE: Background air quality  (Defra UK-AIR, PCM 1 km modelled grid)
# ============================================================================
# Defra's Pollution Climate Mapping model publishes modelled annual mean
# background concentrations on a 1 km National Grid, one CSV per pollutant per
# year, indexed at:
#   https://uk-air.defra.gov.uk/data/pcm-data
#
# Air quality is the environmental exposure with the clearest link to outcomes
# this map carries: respiratory admissions and cardiovascular disease from
# Local Health, and low birth weight where OHID's figures for the region pass
# the per-series checks (FINGERTIPS_MSOA_EXTRA). Before this source nothing
# here measured air quality.
#
# Three properties of the published files are handled rather than assumed,
# because each one fails silently if guessed wrong:
#
#   - The pollutant token runs straight into the year with no separator, and
#     some files carry a version suffix after it (mappm252024g.csv, but
#     mapno22024.csv). A greedy regex reads "mapno22024" as pollutant "no",
#     year 2200. The token is therefore matched against a known vocabulary,
#     longest first, so pm25 is never read as a variant of pm10 and no2 is
#     never read as no.
#   - The index's links are relative and written "../datastore/pcm/...".
#     They resolve against /data/pcm-data as a page, not a directory: joining
#     them onto a trailing slash puts them under /data/datastore/ and every
#     single one 404s.
#   - Each CSV carries five metadata lines (pollutant, year, statistic, unit,
#     blank) ahead of the real header row.
#
# x and y are the CENTRE of each 1 km square rather than a corner — every x
# satisfies x mod 1000 == 500. Cells are rebuilt as squares spanning ±500 m and
# each LSOA takes the area-weighted mean of the cells it overlaps. Reading a
# single cell at the LSOA centroid would be cheaper, and defensible for dense
# inner-London LSOAs, but outer-borough ones run to several km² and would take
# one cell's value for the whole area.
PCM_INDEX_URL = "https://uk-air.defra.gov.uk/data/pcm-data"
# Pollutant token -> output column. These three are the ones with published
# health-based guideline values; nox, so2 and benzene are on the index too and
# can be added here without other changes.
PCM_POLLUTANTS = {
    "no2":  "no2_ugm3",
    "pm25": "pm25_ugm3",
    "pm10": "pm10_ugm3",
}
# Every token the index publishes, so a filename is never mis-split by matching
# a short pollutant that happens to prefix a longer one.
PCM_ALL_TOKENS = ["coamean", "com8hr", "dgt1", "pm25", "pm10", "nox",
                  "no2", "so2", "o3", "bz"]
# The year the map's labels currently say. Discovery still takes whatever is
# newest, because that is the right default, but the vintage is written on the
# methodology page and in each indicator's metadata as fixed text. When Defra
# publish the next year those labels become wrong silently, so the mismatch is
# announced rather than left for someone to notice.
PCM_LABELLED_YEAR = 2024
# The UK-wide grid (about 255,000 cells) is trimmed to the scope's LSOAs, in
# British National Grid with a margin, before matching. It used to be a fixed
# box around Greater London, which left every other region with no cells.
# A cell is 1 km, so a 2 km margin keeps every cell that can touch an LSOA.
PCM_BBOX_MARGIN_M = 2000


def _pcm_discover_urls() -> dict[str, tuple[int, str]]:
    """Latest published file per pollutant: {token: (year, absolute url)}.

    Read from the index rather than pinned. PTAL was skipped silently for
    months because its URL carried a resource id that stopped resolving, and
    a pinned PCM filename would rot the same way each time Defra republishes.
    """
    from urllib.parse import urljoin
    try:
        r = browser_session(referer="https://uk-air.defra.gov.uk/").get(
            PCM_INDEX_URL, timeout=90)
        r.raise_for_status()
        html = r.text
        info(f"air_quality: index HTTP {r.status_code}, {len(html):,} chars, "
             f"{len(re.findall(r'[.]csv', html, re.I))} .csv mentions")
    except Exception as e:
        warn(f"air_quality: could not reach the UK-AIR index ({e})")
        return {}

    found: dict[str, dict[int, str]] = {}
    for href in re.findall(r'href="([^"]*/pcm/[^"]*\.csv)"', html, re.I):
        # urljoin against the page URL, NOT the page URL plus a slash: these
        # links are "../datastore/pcm/x.csv" and the extra slash sends them to
        # /data/datastore/, which does not exist.
        url = urljoin(PCM_INDEX_URL, href)
        stem = url.rsplit("/", 1)[-1]
        m = re.match(r"map(.+?)\.csv$", stem, re.I)
        if not m:
            continue
        rest = m.group(1).lower()
        token = next((t for t in PCM_ALL_TOKENS if rest.startswith(t)), None)
        if token not in PCM_POLLUTANTS:
            continue
        ym = re.match(r"(\d{4})", rest[len(token):])
        if not ym:
            continue
        found.setdefault(token, {})[int(ym.group(1))] = url

    latest = {t: (max(yrs), yrs[max(yrs)]) for t, yrs in found.items()}
    missing = sorted(set(PCM_POLLUTANTS) - set(latest))
    if missing:
        warn(f"air_quality: the index listed no files for {', '.join(missing)}")
        # The index has come back empty while the files themselves were still
        # served (9 Oct 2026, every regional build at once). Ask for the files
        # by their usual names before giving up: map<token><year>.csv, with a
        # "g" after the year for the gravimetric PM files.
        sess = browser_session(referer=PCM_INDEX_URL)
        for token in missing:
            for year in (PCM_LABELLED_YEAR + 1, PCM_LABELLED_YEAR):
                hit = None
                for suffix in ("", "g"):
                    url = urljoin(PCM_INDEX_URL, f"../datastore/pcm/map{token}{year}{suffix}.csv")
                    # GET, not HEAD: some servers refuse HEAD outright. Only
                    # the headers are read; the body is left for the download.
                    try:
                        with sess.get(url, timeout=60, stream=True) as r:
                            ctype = r.headers.get("content-type", "")
                            info(f"air_quality: tried {url.rsplit('/', 1)[-1]}: "
                                 f"HTTP {r.status_code} {ctype}")
                            if r.ok and "html" not in ctype.lower():
                                hit = url
                                break
                    except Exception as e:
                        info(f"air_quality: tried {url.rsplit('/', 1)[-1]}: {e}")
                if hit:
                    info(f"air_quality: {token} found by name: {hit.rsplit('/', 1)[-1]}")
                    latest[token] = (year, hit)
                    break
    return latest


def _pcm_read_grid(token: str, year: int, url: str,
                   cache_dir: "Path", bbox: tuple) -> "pd.DataFrame | None":
    """One pollutant's cells inside bbox (BNG) as columns x, y, value."""
    src = cache_dir / f"pcm_{token}_{year}.csv"
    if not src.exists():
        info(f"air_quality: downloading {url.rsplit('/', 1)[-1]}")
        try:
            r = browser_session(referer=PCM_INDEX_URL).get(url, timeout=300)
            r.raise_for_status()
            write_bytes_atomic(src, r.content)
        except Exception as e:
            warn(f"air_quality: {token} download failed: {e}. MANUAL STEP: "
                 f"save {url} as {src}, then re-run "
                 f"`python fetch_all_data.py --only air_quality`.")
            return None
    try:
        # low_memory=False because the value column holds a literal "MISSING"
        # for cells the model does not cover. Chunked inference types that
        # column differently depending on where the sentinel falls, which is a
        # warning here but a silently different dtype in other pandas versions.
        df = pd.read_csv(src, skiprows=5, low_memory=False)
    except Exception as e:
        warn(f"air_quality: {token} CSV unreadable ({e})")
        return None
    if df.empty or not {"x", "y"} <= set(df.columns):
        warn(f"air_quality: {token} has no x/y columns; got {list(df.columns)}")
        return None

    # The value column is named for the pollutant and year (no22024). Take the
    # last column rather than rebuilding that name, so a change in Defra's
    # spelling does not empty the layer without saying so.
    val_col = df.columns[-1]
    x0, y0, x1, y1 = bbox
    df = df[df.x.between(x0, x1) & df.y.between(y0, y1)].copy()
    # PCM writes "MISSING" into cells the model does not cover (open sea, and
    # a few Scottish islands). Numeric coercion turns those into NaN.
    df["value"] = pd.to_numeric(df[val_col], errors="coerce")
    df = df.dropna(subset=["value"])
    df = df[df["value"] > 0]
    if df.empty:
        warn(f"air_quality: {token} had no usable cells over {SCOPE_NAME}")
        return None
    return df[["x", "y", "value"]]


def run_air_quality() -> "pd.DataFrame | None":
    rule("Air quality (Defra UK-AIR, PCM 1 km grid)")
    from shapely.geometry import box, shape
    from shapely.ops import transform as shp_transform
    from shapely.strtree import STRtree
    from pyproj import Transformer

    cache_dir = CACHE_DIR / "air_quality"
    cache_dir.mkdir(parents=True, exist_ok=True)

    # LSOA polygons are WGS84; the grid is BNG. Project the polygons rather
    # than the grid, because a projected 1 km square stops being a square and
    # its area weights would drift.
    lsoa_path = DATA_DIR / "boundaries" / "lsoa.geojson"
    if not lsoa_path.exists():
        warn(f"air_quality: {lsoa_path} missing — run `--only boundaries` first")
        return None
    with open(lsoa_path, encoding="utf-8") as f:
        feats = json.load(f)["features"]
    to_bng = Transformer.from_crs(4326, 27700, always_xy=True).transform
    polys, codes = [], []
    for feat in feats:
        code = str(feat["properties"].get("LSOA21CD") or "")
        if not code.startswith("E01"):
            continue
        geom = shp_transform(to_bng, shape(feat["geometry"]))
        # One North West LSOA came through self-intersecting and GEOS refused
        # to intersect it with the grid ("side location conflict"), which
        # failed the whole source. buffer(0) repairs it without moving edges.
        if not geom.is_valid:
            geom = geom.buffer(0)
        if geom.is_empty:
            continue
        polys.append(geom)
        codes.append(code)
    info(f"air_quality: {len(polys):,} LSOA polygons projected to BNG")
    if not polys:
        warn("air_quality: no LSOA polygons in scope")
        return None
    bx = [p.bounds for p in polys]
    m = PCM_BBOX_MARGIN_M
    bbox = (min(b[0] for b in bx) - m, min(b[1] for b in bx) - m,
            max(b[2] for b in bx) + m, max(b[3] for b in bx) + m)

    latest = _pcm_discover_urls()
    if not latest:
        warn("air_quality: nothing to fetch")
        return None

    grids: dict[str, "pd.DataFrame"] = {}
    years: dict[str, int] = {}
    for token in PCM_POLLUTANTS:
        if token not in latest:
            continue
        year, url = latest[token]
        g = _pcm_read_grid(token, year, url, cache_dir, bbox)
        if g is not None:
            grids[token] = g
            years[token] = year
            info(f"air_quality: {token} {year} — {len(g):,} cells in scope, "
                 f"{g['value'].min():.1f}–{g['value'].max():.1f} µg/m³")
    if not grids:
        warn("air_quality: no pollutant grid could be read")
        return None


    records: dict[str, dict] = {c: {"LSOA21CD": c} for c in codes}
    for token, grid in grids.items():
        col = PCM_POLLUTANTS[token]
        cells = [box(x - 500, y - 500, x + 500, y + 500)
                 for x, y in zip(grid.x.to_numpy(), grid.y.to_numpy())]
        values = grid["value"].to_numpy()
        tree = STRtree(cells)
        hit = 0
        for code, poly in zip(codes, polys):
            num = den = 0.0
            for idx in tree.query(poly):
                inter = poly.intersection(cells[idx]).area
                if inter > 0:
                    num += float(values[idx]) * inter
                    den += inter
            if den > 0:
                records[code][col] = round(num / den, 2)
                hit += 1
        ok(f"air_quality: {col} on {hit:,}/{len(codes):,} LSOAs")

    out = pd.DataFrame.from_records(list(records.values()))
    value_cols = [c for c in PCM_POLLUTANTS.values() if c in out.columns]
    if not value_cols:
        warn("air_quality: no LSOA matched any grid cell")
        return None
    # Every pollutant, or none. A missing column is not a smaller file: it is a
    # full 4,994-row frame that passes write_parquet_guarded (which only
    # refuses EMPTY frames), so one timed-out 8 MB download would retire a
    # published overlay from the map and still report "ok" in the manifest.
    missing = [c for c in PCM_POLLUTANTS.values() if c not in out.columns]
    if missing:
        warn(f"air_quality: {', '.join(missing)} could not be built this run, "
             f"so nothing is written and the previous file is kept. A partial "
             f"set would silently drop those overlays. Re-run `python "
             f"fetch_all_data.py --only air_quality`.")
        return None
    out = out.dropna(subset=value_cols, how="all")
    # An LSOA that matched no cell means the bbox or the projection is wrong,
    # not that London has a hole in it. Refuse to ship a partial layer as a
    # whole one — the same rule run_greenblue enforces.
    if len(out) < len(codes) * 0.99:
        raise RuntimeError(
            f"air_quality: only {len(out):,} of {len(codes):,} LSOAs took a "
            f"value from the 1 km grid. That is a join fault, not missing data."
        )
    out_path = DATA_DIR / "environment" / "air_quality_lsoa.parquet"
    out = write_parquet_guarded(out_path, out, source="air_quality")
    yr_note = ", ".join(f"{t} {y}" for t, y in sorted(years.items()))
    drifted = sorted({y for y in years.values() if y != PCM_LABELLED_YEAR})
    if drifted:
        warn(f"air_quality: Defra now publish {yr_note}, but the map labels "
             f"this layer {PCM_LABELLED_YEAR}. The figures are correct and "
             f"current; the year shown beside them is not. Update "
             f"PCM_LABELLED_YEAR, the three `yr:` fields in index.html's "
             f"OV_META, and the air quality `period:` in methodology.html.")
    ok(f"air_quality: {len(out):,} LSOAs ({yr_note}) -> "
       f"{out_path.relative_to(REPO_ROOT)}")
    return out


# ============================================================================
# SOURCE: Public transport access  (TfL Unified API, StopPoint)
# ============================================================================
# PTAL already scores how well-connected a place is overall, but it is a single
# banded index and it is republished rarely. Distance to the nearest station is
# a different and more legible thing: it answers "how far is the walk", it
# separates outer-borough LSOAs that PTAL flattens into one low band, and it
# comes from a register that is current rather than a periodic model run.
#
#   https://api.tfl.gov.uk/StopPoint/Mode/{modes}?page=N
#
# The API returns 1,000 records a page and reports the true total, so paging
# runs until the total is reached rather than until a short page appears — a
# short page mid-run would otherwise truncate the network silently.
#
# What comes back is not a list of stations. It is a list of stop POINTS, which
# includes platforms, entrances and access areas as separate records that all
# share a station's name and sit within metres of each other. Counting those as
# stations would report eleven "stations" at King's Cross. Only the station-
# level stopTypes are kept.
#
# Keeping only those types is NOT enough to get one point per station, and
# deduplicating on stationNaptan does not help: on this endpoint that field is
# null for the hub records and equal to the record's own id for the rest, so it
# collapses nothing at all — 955 records in, 955 out. Two things still
# double-count, and both were measured against the cached register:
#
#   - TransportInterchange is the HUB* grouping record that sits ON TOP of the
#     stations it groups, not a station in its own right. 96 of them are in
#     scope and 95 sit within 250 m of a station that is already kept
#     (HUBBAL is 20 m from both Balham Rail and Balham Underground). It is
#     excluded below.
#   - The same physical site is listed once per operator. Wimbledon appears as
#     910GWDON and 910GWIMBLDN, 1 m apart; Weybridge twice at 0 m.
#
# So the kept points are collapsed spatially: anything within TFL_SITE_RADIUS_M
# of an already-accepted station is the same site. Left uncollapsed, "stations
# within 1 km" overstated central London by roughly a third.
#
# Stations outside London are deliberately NOT dropped. An LSOA in Havering may
# have its nearest station over the Essex border, and clipping to the boundary
# would push its distance to the next London station and overstate it. The
# grid is trimmed to a London bbox with a margin instead.
TFL_API = "https://api.tfl.gov.uk"
# Unkeyed access is rate limited to about 50 requests a minute, which the 43
# pages here fit inside with the pause below. A free app key raises the limit;
# set TFL_APP_KEY to use one.
TFL_APP_KEY = os.environ.get("TFL_APP_KEY", "")
TFL_RAIL_MODES = "tube,dlr,overground,elizabeth-line,national-rail,tram"
# Station-level records. NaptanMetroEntrance / *AccessArea / *Platform are
# parts of a station, not stations; TransportInterchange is a grouping laid
# over stations that are already in this set, so it is not one either.
TFL_STATION_TYPES = {"NaptanMetroStation", "NaptanRailStation"}
# Two station records closer than this are the same site listed twice, once per
# operator. Generous enough for the observed duplicates (0-62 m) and well short
# of the closest genuinely distinct pair in London.
TFL_SITE_RADIUS_M = 150
# Physical bus stops. The Pair and Cluster types are groupings of the stops
# below them and would double-count.
TFL_BUS_TYPES = {"NaptanPublicBusCoachTram", "NaptanBusCoachStation"}
# Greater London plus roughly 8 km, in WGS84 degrees.
TFL_BBOX = (-0.62, 51.19, 0.42, 51.78)


def _tfl_fetch_mode(modes: str, cache_dir: "Path",
                    stem: str) -> tuple[list[dict], bool]:
    """Every stop point for a mode set, paged, cached a page at a time.

    Returns (records, complete). `complete` is False when the API stopped
    short of the total it advertised, which is the whole point of returning
    it: a mid-paging HTTP error used to `return out` with whatever had
    arrived, skipping the completeness check below entirely, and the caller
    then measured every distance against a truncated network and wrote it out
    as if it were the whole one. A short network does not look wrong — it
    looks like a city with fewer stations.
    """
    import time
    out: list[dict] = []
    page, total = 1, None
    while True:
        cache = cache_dir / f"{stem}_p{page}.json"
        if cache.exists():
            try:
                payload = json.loads(cache.read_text(encoding="utf-8"))
            except ValueError:
                cache.unlink()
                continue
        else:
            url = f"{TFL_API}/StopPoint/Mode/{modes}?page={page}"
            if TFL_APP_KEY:
                url += f"&app_key={TFL_APP_KEY}"
            try:
                r = browser_session(referer="https://tfl.gov.uk/").get(
                    url, timeout=180)
                r.raise_for_status()
                payload = r.json()
            except Exception as e:
                warn(f"tfl: {stem} page {page} failed: {e}")
                return out, False
            write_atomic(cache, json.dumps(payload))
            if not TFL_APP_KEY:
                time.sleep(1.3)   # stay under the unkeyed rate limit
        sps = payload.get("stopPoints") or []
        total = payload.get("total") if total is None else total
        out.extend(sps)
        if not sps or total is None or len(out) >= total:
            break
        page += 1
    complete = total is not None and len(out) >= total
    if not complete:
        warn(f"tfl: {stem} returned {len(out):,} of "
             f"{total if total is not None else '?'} advertised records")
    return out, complete


def _tfl_points(records: list[dict], keep_types: set) -> list[tuple[float, float]]:
    """(lon, lat) for the wanted stop types, one per distinct record id.

    Keyed on the record's own naptanId. For bus stops that is exactly right:
    the two stops facing each other across a road are separate stops and must
    stay separate. For stations it removes only exact repeats; the same site
    listed once per operator is collapsed later, spatially, by _collapse_sites.
    """
    x0, y0, x1, y1 = TFL_BBOX
    seen: dict[str, tuple[float, float]] = {}
    for s in records:
        if s.get("stopType") not in keep_types:
            continue
        lat, lon = s.get("lat"), s.get("lon")
        # Presence, not truth. London straddles the Greenwich meridian and this
        # bbox spans it, so a longitude of exactly 0.0 is a real place: the
        # falsy test this replaces discarded 'Larkshall Crescent' (51.61507, 0.0).
        if lat is None or lon is None:
            continue
        if not (x0 <= lon <= x1 and y0 <= lat <= y1):
            continue
        key = s.get("naptanId") or s.get("id")
        seen.setdefault(str(key), (float(lon), float(lat)))
    return list(seen.values())


def _collapse_sites(points_xy: list[tuple[float, float]],
                    radius_m: float) -> list[tuple[float, float]]:
    """One point per physical site, from points already in metres.

    A candidate within radius_m of an ALREADY-ACCEPTED anchor is the same site
    and is dropped. Testing against anchors rather than against any member is
    deliberate: single-linkage would let a row of closely spaced stations chain
    into one blob down a line, which is exactly the failure this is meant to
    avoid. Sorted first so the result does not depend on fetch order.
    """
    anchors: list[tuple[float, float]] = []
    r2 = radius_m * radius_m
    for x, y in sorted(points_xy):
        if any((x - ax) ** 2 + (y - ay) ** 2 <= r2 for ax, ay in anchors):
            continue
        anchors.append((x, y))
    return anchors


def run_tfl_transport() -> "pd.DataFrame | None":
    rule("Public transport access (TfL Unified API)")
    from shapely.geometry import Point, shape
    from shapely.ops import transform as shp_transform
    from shapely.strtree import STRtree
    from pyproj import Transformer

    cache_dir = CACHE_DIR / "tfl"
    cache_dir.mkdir(parents=True, exist_ok=True)

    rail_raw, rail_ok = _tfl_fetch_mode(TFL_RAIL_MODES, cache_dir, "rail")
    bus_raw, bus_ok = _tfl_fetch_mode("bus", cache_dir, "bus")
    # Refuse to publish measurements taken against a network that is known to
    # be short. write_parquet_guarded only protects against an EMPTY frame, and
    # a truncated register still yields a full 4,994 rows — every one of them
    # wrong, and reported "ok" in the manifest.
    if not rail_ok or not bus_ok:
        warn("tfl: the register came back incomplete, so nothing is written. "
             "Distances measured against a partial network are wrong "
             "everywhere, not missing in places, and the previous file is the "
             "safer thing to keep. Re-run `python fetch_all_data.py --only "
             "tfl` once the API is healthy.")
        return None
    rail_pts = _tfl_points(rail_raw, TFL_STATION_TYPES)
    bus = _tfl_points(bus_raw, TFL_BUS_TYPES)
    if not rail_pts:
        warn("tfl: no stations resolved; nothing to measure against")
        return None

    lsoa_path = DATA_DIR / "boundaries" / "lsoa.geojson"
    if not lsoa_path.exists():
        warn(f"tfl: {lsoa_path} missing — run `--only boundaries` first")
        return None
    with open(lsoa_path, encoding="utf-8") as f:
        feats = json.load(f)["features"]

    # Distances have to be in metres, so everything moves to British National
    # Grid. Degrees would make a degree of longitude count the same as a degree
    # of latitude, which at London's latitude is an error of about 38%.
    to_bng = Transformer.from_crs(4326, 27700, always_xy=True).transform

    # Collapse the same station listed once per operator, in metres. Done here
    # rather than in _tfl_points because the radius only means anything after
    # projection.
    rail_xy = _collapse_sites([to_bng(lon, lat) for lon, lat in rail_pts],
                              TFL_SITE_RADIUS_M)
    bus_xy = [to_bng(lon, lat) for lon, lat in bus]
    info(f"tfl: {len(rail_raw):,} rail stop points -> {len(rail_pts):,} station "
         f"records -> {len(rail_xy):,} distinct sites")
    info(f"tfl: {len(bus_raw):,} bus stop points -> {len(bus_xy):,} stops")

    codes, origins = [], []
    for feat in feats:
        code = str(feat["properties"].get("LSOA21CD") or "")
        if not code.startswith("E01"):
            continue
        geom = shp_transform(to_bng, shape(feat["geometry"]))
        if geom.is_empty:
            continue
        # The centroid of a crescent-shaped or river-split LSOA can land
        # outside it, sometimes in the Thames. representative_point is
        # guaranteed to be inside the polygon.
        pt = geom.centroid
        if not geom.contains(pt):
            pt = geom.representative_point()
        codes.append(code)
        origins.append(pt)
    info(f"tfl: measuring from {len(codes):,} LSOA centroids")

    def measure(points_xy, radius_m):
        """(distance to nearest, count within radius) per LSOA, in BNG."""
        geoms = [Point(x, y) for x, y in points_xy]
        tree = STRtree(geoms)
        dists, counts = [], []
        for pt in origins:
            nearest = tree.nearest(pt)
            dists.append(round(pt.distance(geoms[int(nearest)]), 1))
            # predicate="dwithin" is an exact radius test. A plain
            # query(pt.buffer(r)) filters on bounding boxes, so it would count
            # everything in the buffer's SQUARE envelope — about 27% more area
            # than the circle, and a count that overstates most in the dense
            # centre where it matters most.
            counts.append(int(len(
                tree.query(pt, predicate="dwithin", distance=radius_m))))
        return dists, counts

    rail_d, rail_n = measure(rail_xy, 1000)
    out = pd.DataFrame({
        "LSOA21CD": codes,
        "rail_station_dist_m": rail_d,
        "rail_stations_1km": rail_n,
    })
    if not bus_xy:
        # Shipping three of four columns would pass every guard downstream:
        # the frame is non-empty, so write_parquet_guarded writes it, and the
        # builders' `col in df.columns` checks read the gap as "this source has
        # nothing to say" and quietly drop the overlay from the map.
        warn("tfl: no bus stops resolved, so nothing is written rather than a "
             "rail-only file that would silently retire two published overlays")
        return None
    bus_d, bus_n = measure(bus_xy, 800)
    out["bus_stop_dist_m"] = bus_d
    out["bus_stops_800m"] = bus_n

    out_path = DATA_DIR / "environment" / "tfl_transport_lsoa.parquet"
    out = write_parquet_guarded(out_path, out, source="tfl")
    ok(f"tfl: {len(out):,} LSOAs, median walk to a station "
       f"{out['rail_station_dist_m'].median():,.0f} m -> "
       f"{out_path.relative_to(REPO_ROOT)}")
    return out


# ============================================================================
# SOURCE: public transport access outside London (DfT NaPTAN)
# ============================================================================
# The TfL register above covers London only. NaPTAN, the Department for
# Transport's national register of every public transport access point, gives
# the same four measures for any region, so a region build writes the same
# file with the same columns and nothing downstream changes. London keeps TfL,
# which knows the Tube and Overground as TfL runs them.
#
# Stations are NaPTAN's station access areas, RLY for rail and MET for metro
# and tram, one per station; the same site under two codes is collapsed
# spatially as for TfL. Bus stops are on-street stops (BCT) and bus station
# bays (BCS, BCQ), each side of a road its own stop, as TfL registers them.
NAPTAN_URL = "https://naptan.api.dft.gov.uk/v1/access-nodes?dataFormat=csv"
NAPTAN_STATION_TYPES = {"RLY", "MET"}
NAPTAN_BUS_TYPES = {"BCT", "BCS", "BCQ"}
# Stops this far beyond the scope's edge still count: the nearest station to a
# border village can be in the next region.
NAPTAN_MARGIN_M = 5000


def run_naptan_transport() -> "pd.DataFrame | None":
    rule("Public transport access (DfT NaPTAN)")
    if IS_LONDON:
        info("naptan: London uses the TfL register (source tfl); skipped")
        return None
    from shapely.geometry import Point, shape
    from shapely.ops import transform as shp_transform
    from shapely.strtree import STRtree
    from pyproj import Transformer

    cache_dir = CACHE_DIR / "naptan"
    cache_dir.mkdir(parents=True, exist_ok=True)
    csv_path = cache_dir / "access_nodes.csv"
    fresh = csv_path.exists() and (time.time() - csv_path.stat().st_mtime) < 20 * 86400
    if not fresh:
        info("naptan: downloading the national register")
        r = requests.get(NAPTAN_URL, timeout=300)
        r.raise_for_status()
        if len(r.content) < 1_000_000:
            raise RuntimeError(f"naptan: register is only {len(r.content):,} bytes")
        csv_path.write_bytes(r.content)
    df = pd.read_csv(csv_path, usecols=lambda c: c in {"ATCOCode", "StopType", "Status",
                                                      "Longitude", "Latitude"},
                     dtype={"ATCOCode": str, "StopType": str, "Status": str}, low_memory=False)
    if "Status" in df.columns:
        df = df[~df["Status"].fillna("").str.lower().isin({"inactive", "del", "deleted"})]
    df = df.dropna(subset=["Longitude", "Latitude"])

    lsoa_path = DATA_DIR / "boundaries" / "lsoa.geojson"
    if not lsoa_path.exists():
        warn(f"naptan: {lsoa_path} missing; run `--only boundaries` first")
        return None
    with open(lsoa_path, encoding="utf-8") as f:
        feats = json.load(f)["features"]
    to_bng = Transformer.from_crs(4326, 27700, always_xy=True).transform

    codes, origins = [], []
    for feat in feats:
        code = str(feat["properties"].get("LSOA21CD") or feat["properties"].get("code") or "")
        if not code.startswith("E01"):
            continue
        geom = shp_transform(to_bng, shape(feat["geometry"]))
        if not geom.is_valid:
            geom = geom.buffer(0)
        if geom.is_empty:
            continue
        pt = geom.centroid
        if not geom.contains(pt):
            pt = geom.representative_point()
        codes.append(code)
        origins.append(pt)
    if not origins:
        warn("naptan: no LSOAs in scope")
        return None
    xs = [p.x for p in origins]
    ys = [p.y for p in origins]
    x0, x1 = min(xs) - NAPTAN_MARGIN_M, max(xs) + NAPTAN_MARGIN_M
    y0, y1 = min(ys) - NAPTAN_MARGIN_M, max(ys) + NAPTAN_MARGIN_M

    def points(types):
        sub = df[df["StopType"].isin(types)]
        out = []
        for lon, lat in zip(sub["Longitude"].astype(float), sub["Latitude"].astype(float)):
            x, y = to_bng(lon, lat)
            if x0 <= x <= x1 and y0 <= y <= y1:
                out.append((x, y))
        return out

    rail_xy = _collapse_sites(points(NAPTAN_STATION_TYPES), TFL_SITE_RADIUS_M)
    bus_xy = points(NAPTAN_BUS_TYPES)
    info(f"naptan: {len(rail_xy):,} station sites and {len(bus_xy):,} bus stops "
         f"within {NAPTAN_MARGIN_M // 1000} km of the scope")
    if not rail_xy or not bus_xy:
        warn("naptan: no stations or no bus stops near the scope; nothing written")
        return None

    def measure(points_xy, radius_m):
        geoms = [Point(x, y) for x, y in points_xy]
        tree = STRtree(geoms)
        dists, counts = [], []
        for pt in origins:
            nearest = tree.nearest(pt)
            dists.append(round(pt.distance(geoms[int(nearest)]), 1))
            counts.append(int(len(tree.query(pt, predicate="dwithin", distance=radius_m))))
        return dists, counts

    rail_d, rail_n = measure(rail_xy, 1000)
    bus_d, bus_n = measure(bus_xy, 800)
    out = pd.DataFrame({"LSOA21CD": codes,
                        "rail_station_dist_m": rail_d, "rail_stations_1km": rail_n,
                        "bus_stop_dist_m": bus_d, "bus_stops_800m": bus_n})
    out_path = DATA_DIR / "environment" / "tfl_transport_lsoa.parquet"
    out = write_parquet_guarded(out_path, out, source="naptan")
    ok(f"naptan: {len(out):,} LSOAs, median distance to a station "
       f"{out['rail_station_dist_m'].median():,.0f} m -> {out_path.relative_to(REPO_ROOT)}")
    return out


# ============================================================================
# SOURCE: your own figures, from data/custom/
# ============================================================================
# Everything else in this file fetches from a national publisher. This one
# reads CSVs somebody has put in data/custom/ and treats them the same way.
#
# It exists because the five-step route for adding a source assumes you are
# adding a national feed that refreshes monthly. Most of the time that is not
# what somebody has. They have a spreadsheet of their own numbers for wards or
# neighbourhoods and want it on the map, and asking them to write a fetcher,
# edit two builders and hand-write four registry entries for that is asking
# them to learn the whole pipeline to draw one column.
#
# A CSV and a JSON beside it is the whole interface. data/custom/README.md is
# written for the person doing it. Everything else here reads the same as any
# other source downstream: the parquet lands beside the rest, the ward roll-up
# uses the same population weighting, and the UI registry is generated rather
# than typed.
CUSTOM_DIR = DATA_DIR / "custom"


def run_custom() -> "pd.DataFrame | None":
    rule("Your own figures (data/custom)")
    if not CUSTOM_DIR.exists():
        info("custom: no data/custom directory, nothing to read")
        return None

    specs = sorted(CUSTOM_DIR.glob("*.json"))
    if not specs:
        info("custom: no CSV described in data/custom, nothing to read")
        return None

    frames: dict[str, list] = {"LSOA21CD": [], "WD25CD": []}
    registry: list[dict] = []
    for spec_path in specs:
        try:
            spec = json.loads(spec_path.read_text(encoding="utf-8"))
        except ValueError as e:
            warn(f"custom: {spec_path.name} is not valid JSON ({e}); skipped")
            continue
        csv_path = spec_path.with_suffix(".csv")
        if not csv_path.exists():
            warn(f"custom: {spec_path.name} has no {csv_path.name} beside it; skipped")
            continue
        try:
            df = pd.read_csv(csv_path, dtype=str)
        except Exception as e:
            warn(f"custom: {csv_path.name} unreadable ({e}); skipped")
            continue
        if df.empty:
            warn(f"custom: {csv_path.name} is empty; skipped")
            continue

        # The area column is whichever of the two code columns is present,
        # rather than a setting, because there are only two answers and the
        # file already knows which one it used.
        code_col = next((c for c in ("LSOA21CD", "WD25CD") if c in df.columns), None)
        if not code_col:
            warn(f"custom: {csv_path.name} has neither an LSOA21CD nor a "
                 f"WD25CD column; skipped. Its columns are {list(df.columns)[:6]}")
            continue

        inds = spec.get("indicators") or []
        if not inds:
            warn(f"custom: {spec_path.name} lists no indicators; skipped")
            continue

        keep, bad = [], []
        for ind in inds:
            col = ind.get("column")
            if col not in df.columns:
                bad.append(f"{col!r} is not a column in {csv_path.name}")
                continue
            if not isinstance(ind.get("higher_is_worse"), bool):
                # Refused rather than defaulted. A guessed direction paints the
                # best areas red and reads as a finding rather than a guess.
                bad.append(f"{col!r} has no higher_is_worse, so which end is "
                           f"bad is unknown and it cannot be coloured")
                continue
            if not ind.get("label"):
                bad.append(f"{col!r} has no label")
                continue
            keep.append(ind)
        for b in bad:
            warn(f"custom: {b}")
        if not keep:
            continue

        sub = pd.DataFrame({code_col: df[code_col].astype(str).str.strip()})
        for ind in keep:
            sub[ind["column"]] = pd.to_numeric(df[ind["column"]], errors="coerce")
        sub = sub.dropna(subset=[code_col])
        frames[code_col].append(sub)

        for ind in keep:
            registry.append({
                "k": ind["column"],
                "l": ind["label"],
                "grp": ind.get("group") or "Your data",
                "wh": bool(ind["higher_is_worse"]),
                "u": ind.get("unit") or "",
                "src": spec.get("source") or spec_path.stem,
                "desc": ind.get("description") or "",
                "lv": "lsoa" if code_col == "LSOA21CD" else "ward",
            })
        ok(f"custom: {csv_path.name} -> {len(keep)} indicator(s) over "
           f"{len(sub):,} {code_col} rows")

    if not registry:
        warn("custom: nothing usable was found in data/custom")
        return None

    CUSTOM_DIR.mkdir(parents=True, exist_ok=True)
    written = 0
    for code_col, parts in frames.items():
        if not parts:
            continue
        merged = parts[0]
        for extra in parts[1:]:
            merged = merged.merge(extra, on=code_col, how="outer")
        level = "lsoa" if code_col == "LSOA21CD" else "ward"
        out_path = CUSTOM_DIR / f"custom_{level}.parquet"
        write_parquet_atomic(out_path, merged)
        written += len(merged)
        ok(f"custom: {len(merged):,} rows -> {out_path.relative_to(REPO_ROOT)}")

    write_json_atomic(CUSTOM_DIR / "custom_registry.json", registry, pretty=True)
    ok(f"custom: {len(registry)} indicator(s) registered")
    return pd.DataFrame([{"indicators": len(registry), "rows": written}])



# ============================================================================
# PRIMARY CARE NETWORKS
#
# Which practices make up each PCN comes from OHID Fingertips (area type 204,
# PCNs, parent of area type 7, practices), which mirrors NHS England's
# membership list and is reachable from the build servers.
#
# Where each PCN's patients live comes from NHS Digital's "Patients
# Registered at a GP Practice: LSOA" file, which is the only published source
# for it. NHS Digital refuses requests from cloud servers, so the build does
# not download it: it reads a copy someone has downloaded in a browser and
# attached to the "pcn-source" release (see the workflows). With it, each PCN
# gets its real catchment: for every LSOA, how many of the people who live
# there are registered with the PCN's practices, and what share of all the
# registered residents that is. Without it the PCN layer still lists the
# networks and their practices, and says catchments are not loaded.
# ============================================================================
FT_PCN_AREA_TYPE = 204
FT_PRACTICE_AREA_TYPE = 7
GP_REG_DIR = Path(os.environ.get("PH_GP_REG_DIR", str(REPO_ROOT / ".cache" / "gp_reg")))


def _gp_reg_lsoa_frame():
    """The newest NHS Digital LSOA registration file in GP_REG_DIR, as
    (DataFrame[PRACTICE_CODE, LSOA_CODE, PATIENTS], label), or (None, '')."""
    if not GP_REG_DIR.exists():
        return None, ""
    files = sorted([p for p in GP_REG_DIR.iterdir()
                    if p.suffix.lower() in (".csv", ".zip")],
                   key=lambda p: p.stat().st_mtime)
    if not files:
        return None, ""
    src = files[-1]
    try:
        if src.suffix.lower() == ".zip":
            z = zipfile.ZipFile(src)
            names = [n for n in z.namelist() if n.lower().endswith(".csv")]
            # The all-persons file if the zip has one, else every file (the
            # male and female files), summed below.
            pick = [n for n in names if "all" in n.lower()] or names
            df = pd.concat([pd.read_csv(z.open(n), dtype=str, low_memory=False) for n in pick],
                           ignore_index=True)
        else:
            df = pd.read_csv(src, dtype=str, low_memory=False)
    except Exception as e:
        warn(f"pcn: could not read {src.name}: {e}")
        return None, ""
    cols = {c.upper().strip(): c for c in df.columns}
    pc = cols.get("PRACTICE_CODE")
    lc = next((cols[c] for c in ("LSOA_CODE", "LSOA21CD", "LSOA_CODE_2021", "LSOA11CD") if c in cols), None)
    nc = cols.get("NUMBER_OF_PATIENTS")
    if not (pc and lc and nc):
        warn(f"pcn: {src.name} has none of the expected columns ({', '.join(df.columns[:8])})")
        return None, ""
    sx = cols.get("SEX")
    if sx and (df[sx].astype(str).str.upper() == "ALL").any():
        df = df[df[sx].astype(str).str.upper() == "ALL"]
    out = pd.DataFrame({
        "PRACTICE_CODE": df[pc].astype(str).str.strip(),
        "LSOA_CODE": df[lc].astype(str).str.strip(),
        "PATIENTS": pd.to_numeric(df[nc], errors="coerce").fillna(0),
    }).groupby(["PRACTICE_CODE", "LSOA_CODE"], as_index=False)["PATIENTS"].sum()
    label = ""
    for c in ("EXTRACT_DATE", "PUBLICATION"):
        if c in cols:
            v = df[cols[c]].dropna().astype(str)
            if len(v):
                label = v.iloc[0]
                break
    return out, label or src.stem


def run_pcn() -> pd.DataFrame:
    rule("Primary care networks (OHID Fingertips; NHS Digital registrations)")
    gp_path = DATA_DIR / "healthcare" / "gp_practices.parquet"
    if not gp_path.exists():
        warn("pcn: no GP practices for this scope yet; run the gp source first")
        return pd.DataFrame()
    gp = pd.read_parquet(gp_path)
    in_scope_practices = set(gp["code"].astype(str))

    cache_dir = CACHE_DIR / "pcn"
    cache_dir.mkdir(parents=True, exist_ok=True)
    def ft_json(url, name):
        cache = cache_dir / name
        if cache.exists() and time.time() - cache.stat().st_mtime < 7 * 86400:
            return json.loads(cache.read_text(encoding="utf-8"))
        r = _ft_get(url, timeout=120)
        cache.write_text(r.text, encoding="utf-8")
        return r.json()
    members = ft_json("https://fingertips.phe.org.uk/api/parent_to_child_areas"
                      f"?child_area_type_id={FT_PRACTICE_AREA_TYPE}&parent_area_type_id={FT_PCN_AREA_TYPE}",
                      "pcn_members.json")
    names = {a["Code"]: a["Name"] for a in ft_json(
        f"https://fingertips.phe.org.uk/api/areas/by_area_type?area_type_id={FT_PCN_AREA_TYPE}",
        "pcn_names.json")}

    pcns = {}
    for code, practices in members.items():
        mine = [p for p in practices if p in in_scope_practices]
        if mine:
            pcns[code] = {"code": code, "name": names.get(code, code),
                          "practices": sorted(mine),
                          "all_practices": len(practices)}
    if not pcns:
        warn("pcn: no PCN has a practice in this scope")
        return pd.DataFrame()

    reg, reg_label = _gp_reg_lsoa_frame()
    lsoa_scope = set(get_lsoa_to_msoa().keys())
    footprints = None
    if reg is not None:
        practice_pcn = {p: c for c, ps in members.items() for p in ps}
        reg["PCN"] = reg["PRACTICE_CODE"].map(practice_pcn)
        # Everyone registered anywhere who lives in an in-scope LSOA: the
        # denominator for the share, so a PCN's share is of the area's whole
        # registered population, whichever practice they use.
        local = reg[reg["LSOA_CODE"].isin(lsoa_scope)]
        lsoa_tot = local.groupby("LSOA_CODE")["PATIENTS"].sum()
        by = local.dropna(subset=["PCN"]).groupby(["PCN", "LSOA_CODE"])["PATIENTS"].sum()
        pcn_tot = reg.dropna(subset=["PCN"]).groupby("PCN")["PATIENTS"].sum()
        main = {}
        for (pcn, lsoa), n in by.items():
            if pcn not in pcns or n < 5:
                continue
            share = n / lsoa_tot.get(lsoa, n) if lsoa_tot.get(lsoa, 0) else 0
            pcns[pcn].setdefault("lsoas", {})[lsoa] = [int(n), round(100 * share, 1)]
            if share > main.get(lsoa, ("", 0))[1]:
                main[lsoa] = (pcn, share)
        for c in pcns:
            pcns[c]["patients"] = int(pcn_tot.get(c, 0))
            ls = pcns[c].get("lsoas", {})
            pcns[c]["patients_in_scope"] = int(sum(v[0] for v in ls.values()))
        matched = len(set(reg["LSOA_CODE"]) & lsoa_scope)
        info(f"pcn: registrations {reg_label}: {len(local):,} practice-LSOA pairs "
             f"across {matched:,} of {len(lsoa_scope):,} in-scope LSOAs")
        if matched < len(lsoa_scope) * 0.8:
            warn("pcn: under 80% of in-scope LSOAs found in the registration file; "
                 "it may use 2011 LSOA codes")
        # Footprints: each LSOA joins the PCN most of its registered residents
        # use, and the LSOAs of one PCN are merged into an outline.
        try:
            from shapely.geometry import shape, mapping
            from shapely.ops import unary_union
            gj = json.loads((DATA_DIR / "boundaries" / "lsoa.geojson").read_text(encoding="utf-8"))
            groups = {}
            for f in gj["features"]:
                code = f["properties"].get("LSOA21CD")
                if code in main:
                    g = shape(f["geometry"])
                    if not g.is_valid:
                        g = g.buffer(0)
                    groups.setdefault(main[code][0], []).append(g)
            feats = []
            for c, geoms in groups.items():
                u = unary_union(geoms).simplify(0.0003, preserve_topology=True)
                feats.append({"type": "Feature",
                              "properties": {"code": c, "name": pcns[c]["name"], "lsoas": len(geoms)},
                              "geometry": mapping(u)})
            footprints = {"type": "FeatureCollection", "features": feats}
        except Exception as e:
            warn(f"pcn: footprints not drawn: {e}")
    else:
        info("pcn: no NHS Digital registration file in "
             f"{GP_REG_DIR.relative_to(REPO_ROOT) if GP_REG_DIR.is_relative_to(REPO_ROOT) else GP_REG_DIR}; "
             "networks and practices only, no catchments")

    MAP_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"registrations": reg_label, "has_catchments": reg is not None,
               "pcns": sorted(pcns.values(), key=lambda x: x["name"])}
    write_atomic(MAP_DIR / "pcn.json", json.dumps(_scrub_nan(payload), separators=(",", ":"),
                                                  ensure_ascii=False))
    fp_path = MAP_DIR / "pcn_footprints.json"
    if footprints:
        write_atomic(fp_path, json.dumps(_round_coords(footprints), separators=(",", ":")))
    elif fp_path.exists():
        fp_path.unlink()
    ok(f"pcn: {len(pcns):,} networks, "
       f"{sum(len(p['practices']) for p in pcns.values()):,} practices"
       + (f", catchments from {reg_label}" if reg is not None else ", no catchments")
       + f" -> {(MAP_DIR / 'pcn.json').relative_to(REPO_ROOT)}")
    return pd.DataFrame([{"code": c, "practices": len(p["practices"])} for c, p in pcns.items()])


# ============================================================================
# SMALL-AREA ADDITIONS: services, population estimates, youth claimants,
# road casualties and care home beds
# ============================================================================
# Five sources, each with its own manifest entry, so one that fails or has
# nothing to fetch never marks an older source partial or failed:
#
#   service_access   distance to the nearest GP practice, pharmacy and NHS
#                    dentist, measured here from the registers above
#   sape             ONS mid-year population estimates for LSOAs
#   claimant_youth   Nomis claimant count aged 18 to 24, by LSOA
#   road_casualties  DfT STATS19 casualties killed or seriously injured, by
#                    the LSOA the casualty lives in
#   care_homes       CQC care homes and their registered beds
#
# Ward and LA figures are made in build_ward_data and build_borough_data by
# small_area_figures: counts summed over the LSOAs that best fit each ward
# (and over all of an LA's LSOAs) before dividing, and the distances as
# averages weighted by residents. The crime figures for LSOAs need no new
# source: crime_by_lsoa reads the police.uk file the crime source writes.

# ---------------------------------------------------------------------------
# Distance to services, from the GP, pharmacy and dental registers
# ---------------------------------------------------------------------------
# The registers are cut to the scope's councils before they are written, and
# for an LSOA near the edge of the scope the nearest practice is often just
# over it. So each register fetch also keeps every site it would keep in
# England (save_national_sites), and the distances are measured against those.
# A run without them (--only service_access on a fresh checkout) falls back to
# the scope's own files and marks the source partial, because edge LSOAs then
# read too far.
SERVICE_KINDS = {
    # register: (the scope's parquet in data/healthcare, the indicator key)
    "gp":       ("gp_practices.parquet",     "gp_dist_km"),
    "pharmacy": ("pharmacies.parquet",       "pharmacy_dist_km"),
    "dentist":  ("dental_practices.parquet", "dentist_dist_km"),
}
SERVICE_KEYS = tuple(v[1] for v in SERVICE_KINDS.values())
SERVICE_ACCESS_PARQUET = "service_access_lsoa.parquet"
# A national file older than this is from some earlier run, not this one.
SERVICE_NATIONAL_MAX_AGE_DAYS = 45
# ONS's LSOA 2021 population-weighted centroids. Tried by name first, then
# found in the service list, as the output area lookups are.
LSOA_PWC_SERVICES = ["LSOA_PopCentroids_EW_2021_V4", "LSOA_PopCentroids_EW_2021_V3",
                     "LSOA_PopCentroids_EW_2021_V2", "LSOA_PopCentroids_EW_2021"]


def _service_sites_dir() -> Path:
    return CACHE_DIR / "service_access"


def save_national_sites(kind: str, rows) -> None:
    """Every site of one register the map keeps, in or out of scope, as
    (code, lat, lng), for run_service_access. Never fails the register."""
    try:
        df = pd.DataFrame(list(rows), columns=["code", "lat", "lng"])
        df = df.dropna(subset=["lat", "lng"]).drop_duplicates("code")
        if df.empty:
            return
        d = _service_sites_dir()
        d.mkdir(parents=True, exist_ok=True)
        write_atomic(d / f"{kind}_sites.csv", df.to_csv(index=False))
        info(f"service_access: {len(df):,} {kind} sites in England kept for the distances")
    except Exception as e:
        warn(f"service_access: could not keep the national {kind} sites ({e})")


def _service_sites(kind: str) -> tuple[list, str]:
    """[(lat, lng), ...] for one register, and where they came from:
    "england" (the national file from this run's register fetch) or "scope"
    (the scope's own parquet, which lacks the sites over its edge)."""
    f = _service_sites_dir() / f"{kind}_sites.csv"
    if f.exists() and _days_since(datetime.fromtimestamp(f.stat().st_mtime, timezone.utc).isoformat()) \
            <= SERVICE_NATIONAL_MAX_AGE_DAYS:
        try:
            df = pd.read_csv(f)
            pts = [(float(a), float(b)) for a, b in zip(df["lat"], df["lng"])
                   if pd.notna(a) and pd.notna(b)]
            if pts:
                return pts, "england"
        except Exception as e:
            warn(f"service_access: {f.name} unreadable ({e})")
    pq_name = SERVICE_KINDS[kind][0]
    df = _read_parquet_opt(DATA_DIR / "healthcare" / pq_name)
    if df is None or df.empty or not {"lat", "lng"}.issubset(df.columns):
        return [], "none"
    if kind == "dentist" and "kind" in df.columns:
        # High street practices only: not orthodontics, hospital, community
        # or urgent services, which take referrals rather than patients.
        df = df[df["kind"].fillna("") == ""]
    pts = [(float(a), float(b)) for a, b in zip(df["lat"], df["lng"]) if pd.notna(a) and pd.notna(b)]
    return pts, "scope"


def _lsoa_pwc_service() -> str | None:
    """The ONS LSOA 2021 population-weighted centroid service, by a known
    name or from the service list, or None."""
    for name in LSOA_PWC_SERVICES:
        try:
            r = requests.get(f"{ONS_SERVICES}/{name}/FeatureServer/0?f=json", timeout=60)
            if r.ok and "fields" in r.json():
                return name
        except Exception:
            continue
    try:
        r = requests.get(f"{ONS_SERVICES}?f=json", timeout=120)
        names = [x["name"] for x in r.json().get("services", [])]
    except Exception as e:
        warn(f"service_access: could not list ONS services ({type(e).__name__}: {e})")
        return None
    return pick_lsoa_pwc_service(names)


def pick_lsoa_pwc_service(names) -> str | None:
    """The newest LSOA 2021 population-weighted centroid layer in a list of
    ONS service names (PopCentroids or PWC, 2021, not 2011 or output areas)."""
    cands = [n for n in names
             if re.search(r"(?i)lsoa", n) and re.search(r"(?i)pop_?centroid|_pwc", n)
             and re.search(r"2021", n) and not re.search(r"(?i)2011", n)]
    return sorted(cands)[-1] if cands else None


def get_lsoa_pwc(codes) -> dict:
    """{LSOA21CD: (easting, northing)}, the population-weighted centroid in
    British National Grid metres, for the codes asked for; {} when ONS does
    not answer, in which case the middle of each LSOA's shape is used."""
    cache = CACHE_DIR / "ons_lookup" / f"LSOA21_PWC_{SCOPE_KEY}.json"
    want = sorted(set(codes))
    if cache.exists():
        try:
            blob = json.loads(cache.read_text(encoding="utf-8"))
            if blob and all(c in blob for c in want):
                return {c: tuple(blob[c]) for c in want}
        except (OSError, ValueError):
            pass
    layer = _lsoa_pwc_service()
    if not layer:
        warn("service_access: no ONS population-weighted centroid service found")
        return {}
    url = f"{ONS_SERVICES}/{layer}/FeatureServer/0/query"
    try:
        meta = requests.get(f"{ONS_SERVICES}/{layer}/FeatureServer/0?f=json", timeout=60).json()
        fields = [f.get("name", "") for f in meta.get("fields", [])]
        code_f = next((f for f in fields if f.upper() == "LSOA21CD"), None) \
            or next((f for f in fields if "LSOA21CD" in f.upper()), None)
        if not code_f:
            warn(f"service_access: {layer} has no LSOA21CD field ({', '.join(fields[:8])})")
            return {}
        out: dict = {}
        for i in range(0, len(want), 200):
            batch = want[i:i + 200]
            payload = _http_json("POST", url, source="ONS LSOA centroids", form_body={
                "where": f"{code_f} IN (" + ",".join(f"'{c}'" for c in batch) + ")",
                "outFields": code_f, "returnGeometry": "true", "outSR": 27700,
                "f": "json", "resultRecordCount": 2000,
            }, timeout=180)
            for feat in (payload or {}).get("features", []):
                a = feat.get("attributes") or {}
                g = feat.get("geometry") or {}
                if a.get(code_f) and g.get("x") is not None and g.get("y") is not None:
                    out[a[code_f]] = (float(g["x"]), float(g["y"]))
    except Exception as e:
        warn(f"service_access: population-weighted centroids from {layer} failed "
             f"({type(e).__name__}: {e})")
        return {}
    if len(out) < len(want) * 0.99:
        warn(f"service_access: {layer} gave centroids for only {len(out):,} of "
             f"{len(want):,} LSOAs")
        if not out:
            return {}
    write_json_atomic(cache, {c: list(v) for c, v in out.items()})
    ok(f"service_access: {len(out):,} population-weighted LSOA centroids ({layer})")
    return out


def lsoa_shape_centres() -> dict:
    """{LSOA21CD: (easting, northing)}: the centroid of each LSOA's shape, or
    a point inside it when the centroid falls outside (a crescent or a
    river-split LSOA), from data/boundaries/lsoa.geojson."""
    from shapely.geometry import shape
    from shapely.ops import transform as shp_transform
    from pyproj import Transformer
    path = DATA_DIR / "boundaries" / "lsoa.geojson"
    if not path.exists():
        return {}
    to_bng = Transformer.from_crs(4326, 27700, always_xy=True).transform
    out: dict = {}
    for feat in json.loads(path.read_text(encoding="utf-8")).get("features", []):
        p = feat.get("properties") or {}
        code = str(p.get("LSOA21CD") or p.get("code") or "")
        if not code.startswith("E01") or not feat.get("geometry"):
            continue
        geom = shp_transform(to_bng, shape(feat["geometry"]))
        if not geom.is_valid:
            geom = geom.buffer(0)
        if geom.is_empty:
            continue
        pt = geom.centroid
        if not geom.contains(pt):
            pt = geom.representative_point()
        out[code] = (pt.x, pt.y)
    return out


def service_distances(origins: dict, sites: dict) -> dict:
    """{LSOA21CD: {key: km}}: the straight-line distance from each origin
    (easting, northing) to the nearest site of each kind, in kilometres to
    three places. sites is {key: [(easting, northing), ...]}; a kind with no
    sites gives no figure."""
    from shapely.geometry import Point
    from shapely.strtree import STRtree
    out: dict = {c: {} for c in origins}
    for key, pts in sites.items():
        if not pts:
            continue
        geoms = [Point(x, y) for x, y in pts]
        tree = STRtree(geoms)
        for code, (x, y) in origins.items():
            o = Point(x, y)
            near = tree.nearest(o)
            out[code][key] = round(o.distance(geoms[int(near)]) / 1000.0, 3)
    return out


def run_service_access() -> "pd.DataFrame | None":
    rule("Distance to the nearest GP practice, pharmacy and NHS dentist")
    from pyproj import Transformer
    codes = sorted(get_lsoa_ward_lookup())
    if not codes:
        raise RuntimeError("service_access: no LSOAs in scope")
    pwc = get_lsoa_pwc(codes)
    shapes = lsoa_shape_centres() if len(pwc) < len(codes) else {}
    origins, origin_kind = {}, {}
    for c in codes:
        if c in pwc:
            origins[c], origin_kind[c] = pwc[c], "pwc"
        elif c in shapes:
            origins[c], origin_kind[c] = shapes[c], "centroid"
    if not origins:
        raise RuntimeError("service_access: no LSOA centroids, from ONS or from "
                           "data/boundaries/lsoa.geojson; run the boundaries source first")
    n_pwc = sum(1 for v in origin_kind.values() if v == "pwc")
    if n_pwc < len(origins):
        warn(f"service_access: {len(origins) - n_pwc:,} of {len(origins):,} LSOAs measured from "
             "the middle of their shape, not their population-weighted centre")
    to_bng = Transformer.from_crs(4326, 27700, always_xy=True).transform
    sites: dict = {}
    from_scope, missing = [], []
    for kind, (_pq, key) in SERVICE_KINDS.items():
        pts, where = _service_sites(kind)
        if not pts:
            missing.append(kind)
            continue
        if where == "scope":
            from_scope.append(kind)
        sites[key] = [to_bng(lng, lat) for lat, lng in pts]
        info(f"service_access: {len(pts):,} {kind} sites ({where})")
    if not sites:
        raise RuntimeError("service_access: no GP, pharmacy or dental sites to measure to; "
                           "run the gp, pharmacies and dentists sources first")
    dist = service_distances(origins, sites)
    out = pd.DataFrame([{"LSOA21CD": c, **dist[c], "origin": origin_kind[c]} for c in sorted(origins)])
    out_path = DATA_DIR / "environment" / SERVICE_ACCESS_PARQUET
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out = write_parquet_guarded(out_path, out, source="service_access")
    if missing:
        _SOURCE_ERROR["service_access"] = (
            f"no {', '.join(missing)} sites were found, so "
            + ", ".join(SERVICE_KINDS[k][1] for k in missing) + " has no figures")
    elif from_scope:
        _SOURCE_PARTIAL["service_access"] = (
            f"the {', '.join(from_scope)} distances were measured to the sites inside the scope "
            "only (no national file from a register fetch in this run), so LSOAs near the "
            "edge may read too far")
    meds = ", ".join(f"{k} {out[k].median():.2f} km" for k in SERVICE_KEYS if k in out.columns)
    ok(f"service_access: {len(out):,} LSOAs ({n_pwc:,} from population-weighted centroids), "
       f"median {meds} -> {out_path.relative_to(REPO_ROOT)}")
    return out


# ---------------------------------------------------------------------------
# Crime by LSOA (from the police.uk file the crime source writes)
# ---------------------------------------------------------------------------
# Every crime already carries the LSOA its point falls in. The ward figures
# stay exactly as they are: they count the crimes inside each ward's own
# boundary, which is closer to the ward than adding up the LSOAs that best
# fit it would be (in London that differs by more than a tenth in 318 of 704
# wards). LSOAs and wards both nest in LAs, so both add up to the same LA
# total. The LSOA keys are the ward keys, so the map offers one indicator at
# two levels rather than two indicators.
CRIME_LSOA_GROUPS = {
    "crime_violence_12mo": ("violent-crime",),
    "crime_burglary_12mo": ("burglary",),
    "crime_robbery_12mo":  ("robbery",),
    "crime_theft_12mo":    ("other-theft", "shoplifting", "bicycle-theft", "theft-from-the-person"),
    "crime_asb_12mo":      ("anti-social-behaviour",),
}


def crime_by_lsoa(crime: "pd.DataFrame | None", pop_by_lsoa: dict) -> dict:
    """{LSOA21CD: {crime_total, crime_rate_per_1000, crime_*_12mo}} for every
    LSOA with at least one police.uk record. The total leaves anti-social
    behaviour out, as the ward total does, and the rate is per 1,000 usual
    residents at the 2021 Census, to one place, as the map works out the
    ward rate."""
    if crime is None or crime.empty or "LSOA21CD" not in crime.columns:
        return {}
    c = crime[crime["LSOA21CD"].fillna("").astype(str).str.startswith("E01")]
    if c.empty:
        return {}
    lsoas = c["LSOA21CD"].astype(str).unique()
    cat = c["category"].astype(str) if "category" in c.columns else pd.Series("", index=c.index)
    tot = c[~cat.isin(CRIME_TOTAL_EXCLUDES)].groupby("LSOA21CD").size().reindex(lsoas, fill_value=0)
    out: dict = {lc: {"crime_total": int(n)} for lc, n in tot.items()}
    if "category" in c.columns:
        for key, cats in CRIME_LSOA_GROUPS.items():
            n = c[cat.isin(cats)].groupby("LSOA21CD").size().reindex(lsoas, fill_value=0)
            for lc, v in n.items():
                out[lc][key] = int(v)
    for lc, rec in out.items():
        pop = float(pop_by_lsoa.get(lc, 0) or 0)
        if pop > 0:
            rec["crime_rate_per_1000"] = round(rec["crime_total"] / pop * 1000, 1)
    return out


# ---------------------------------------------------------------------------
# ONS mid-year population estimates for LSOAs (SAPE)
# ---------------------------------------------------------------------------
# The single year of age file, by sex, for LSOAs on their 2021 boundaries. Its
# address changes every release, so it is found on ONS's dataset pages (the
# page itself and its /data JSON), newest year first. SAPE_URL overrides, and
# a file put in .cache/sape/ by hand is used as it is.
#
# Layout, as archive/patch_population_mid2024.py read the November 2025
# edition (sapelsoasyoa20222024.xlsx): one sheet per year, "Mid-2024 LSOA
# 2021", a header row with "LSOA 2021 Code" and "Total", then F0 to F90 and M0
# to M90, where 90 means 90 and over. sape_parse_table checks that the single
# years add up to the total before anything is kept.
#
# These are added beside the Census figures, not in place of them: the
# claimant and crime rates keep their Census 2021 denominators, because
# moving them would change almost every published rate at once.
SAPE_PAGES = [
    "https://www.ons.gov.uk/peoplepopulationandcommunity/populationandmigration/"
    "populationestimates/datasets/lowersuperoutputareamidyearpopulationestimates",
    "https://www.ons.gov.uk/peoplepopulationandcommunity/populationandmigration/"
    "populationestimates/datasets/lowersuperoutputareamidyearpopulationestimatesnationalstatistics",
]
SAPE_URL_OVERRIDE = os.environ.get("SAPE_URL", "")
SAPE_PARQUET = "sape_lsoa.parquet"
# Count columns written to the parquet: (first age, last age or None for "and over").
SAPE_BANDS = {
    "sape_age0_15":   (0, 15),
    "sape_age16_64":  (16, 64),
    "sape_age65plus": (65, None),
    "sape_age75plus": (75, None),
    "sape_age18_24":  (18, 24),
    # State Pension age in 2026, the base of Attendance Allowance and Pension
    # Credit (DWP BENEFITS, FROM STAT-XPLORE).
    "sape_age66plus": (66, None),
}
# The shares the map shows, each a band over the population.
SAPE_SHARES = {
    "sape_age0_15_pct":   "sape_age0_15",
    "sape_age16_64_pct":  "sape_age16_64",
    "sape_age65plus_pct": "sape_age65plus",
    "sape_age75plus_pct": "sape_age75plus",
}
SAPE_KEYS = ("sape_population",) + tuple(SAPE_SHARES)
# A row whose single years miss its total by more than this is a misread.
SAPE_TOTAL_TOLERANCE = 1


def _norm_head(s) -> str:
    """A header, lower case, with punctuation as spaces."""
    return re.sub(r"[^a-z0-9+]+", " ", str(s if s is not None else "").replace("﻿", "").lower()).strip()


def _year_of(name: str) -> int:
    ys = [int(y) for y in re.findall(r"(?:19|20)\d{2}", str(name))]
    return max(ys) if ys else 0


def sape_pick(urls) -> str | None:
    """The newest LSOA single-year-of-age estimates file among links, by
    the latest year in its name; None if there is none."""
    def name(u):
        return urllib.parse.unquote(str(u)).rsplit("/", 1)[-1].lower()
    cands = [u for u in dict.fromkeys(urls)
             if re.search(r"\.(xlsx|zip)$", name(u))
             and "lsoa" in name(u) and re.search(r"syoa|singleyear", name(u))]
    if not cands:
        return None
    return max(cands, key=lambda u: (_year_of(name(u)), name(u)))


def _ons_page_files(page: str, sess) -> list:
    """Links to the files on an ONS dataset page: from its HTML and from the
    page's /data JSON, whose editions list their downloads."""
    base = "https://www.ons.gov.uk"
    urls: list = []
    try:
        r = sess.get(page, timeout=60)
        if r.ok:
            for href in re.findall(r'href="([^"]+?\.(?:xlsx|zip))"', r.text):
                urls.append(href if href.startswith("http") else base + href)
    except Exception as e:
        warn(f"sape: could not read {page} ({type(e).__name__}: {e})")
    try:
        j = sess.get(page.rstrip("/") + "/data", timeout=60).json()
        for ed in (j.get("datasets") or []):
            uri = (ed or {}).get("uri")
            if not uri:
                continue
            jj = sess.get(base + uri + "/data", timeout=60).json()
            for d in (jj.get("downloads") or []):
                f = (d or {}).get("file")
                if f:
                    urls.append(f"{base}/file?uri={uri}/{f}")
    except Exception as e:
        warn(f"sape: could not read {page}/data ({type(e).__name__}: {e})")
    return urls


def sape_age_columns(header: list) -> tuple[dict, str]:
    """{age: [column positions]} for the single years in a header row, and
    how they were found ("sex" for F0..F90 and M0..M90, "persons" for 0..90).
    Every age from 0 to the top one must be there, for each sex."""
    by_sex: dict = {}
    plain: dict = {}
    for i, h in enumerate(header):
        t = str(h if h is not None else "").strip()
        m = re.fullmatch(r"(?i)([FM])\s*(\d{1,2})\s*\+?", t)
        if m:
            by_sex.setdefault(m.group(1).upper(), {})[int(m.group(2))] = i
            continue
        m = re.fullmatch(r"(?i)(?:age(?:d)?\s*)?(\d{1,2})\s*\+?(?:\s*(?:and over|plus))?", t)
        if m:
            plain[int(m.group(1))] = i
    def full(d):
        return bool(d) and max(d) >= 85 and all(a in d for a in range(0, max(d) + 1))
    if set(by_sex) == {"F", "M"} and full(by_sex["F"]) and full(by_sex["M"]) \
            and max(by_sex["F"]) == max(by_sex["M"]):
        return {a: [by_sex["F"][a], by_sex["M"][a]] for a in by_sex["F"]}, "sex"
    if full(plain):
        return {a: [i] for a, i in plain.items()}, "persons"
    return {}, ""


def sape_parse_table(rows, scope=None) -> tuple["pd.DataFrame", str]:
    """The estimates from one sheet's rows (lists of cells, header anywhere
    above the data): LSOA21CD, sape_population and the SAPE_BANDS counts.
    Returns (frame, problem); problem is "" when the sheet was read, and the
    frame is empty when it was not."""
    it = iter(rows)
    header = None
    for row in it:
        cells = list(row or [])
        heads = [_norm_head(c) for c in cells]
        code_i = next((i for i, h in enumerate(heads)
                       if "lsoa" in h and "code" in h and "2011" not in h), None)
        tot_i = next((i for i, h in enumerate(heads) if h in ("total", "all ages", "persons all ages")), None)
        if code_i is not None and tot_i is not None:
            header = cells
            break
    if header is None:
        return pd.DataFrame(), "no header row with an LSOA 2021 code column and a Total column"
    ages, how = sape_age_columns(header)
    if not ages:
        return pd.DataFrame(), "no complete set of single years of age (F0 to F90 and M0 to M90)"
    top = max(ages)
    recs: list = []
    bad = 0
    for row in it:
        cells = list(row or [])
        if len(cells) <= code_i:
            continue
        code = str(cells[code_i] or "").strip()
        if not re.fullmatch(r"E01\d{6}", code):
            continue
        if scope is not None and code not in scope:
            continue
        def num(i):
            try:
                v = cells[i]
                return float(v) if v not in (None, "") else 0.0
            except (IndexError, TypeError, ValueError):
                return 0.0
        by_age = {a: sum(num(i) for i in idx) for a, idx in ages.items()}
        total = num(tot_i)
        if abs(sum(by_age.values()) - total) > SAPE_TOTAL_TOLERANCE:
            bad += 1
        rec = {"LSOA21CD": code, "sape_population": int(round(total))}
        for key, (lo, hi) in SAPE_BANDS.items():
            hi_ = top if hi is None else hi
            rec[key] = int(round(sum(v for a, v in by_age.items() if lo <= a <= hi_)))
        recs.append(rec)
    if not recs:
        return pd.DataFrame(), "no English LSOA rows under the header"
    if bad > max(1, len(recs) * 0.01):
        return pd.DataFrame(), (f"the single years of age do not add up to the total in {bad:,} of "
                                f"{len(recs):,} LSOAs, so the columns were misread")
    return pd.DataFrame(recs).drop_duplicates("LSOA21CD"), ""


def sape_read_file(path: Path, scope=None) -> tuple["pd.DataFrame", int, str]:
    """(frame, year, problem) from a downloaded estimates file: an .xlsx (the
    newest "Mid-YYYY LSOA 2021" sheet), a .csv, or a .zip holding either."""
    path = Path(path)
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as z:
            inner = [n for n in z.namelist() if n.lower().endswith((".xlsx", ".csv"))]
            if not inner:
                return pd.DataFrame(), 0, f"{path.name} holds no .xlsx or .csv"
            best = max(inner, key=lambda n: (_year_of(n), "syoa" in n.lower(), n))
            tmp = path.with_name(Path(best).name)
            tmp.write_bytes(z.read(best))
        return sape_read_file(tmp, scope)
    if path.suffix.lower() == ".csv":
        df = pd.read_csv(path, header=None, dtype=str, keep_default_na=False)
        out, problem = sape_parse_table(df.itertuples(index=False, name=None), scope)
        return out, _year_of(path.name), problem
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        names = list(wb.sheetnames)
        dated = [n for n in names if re.search(r"(?i)mid\W*(?:19|20)\d{2}", n)
                 and re.search(r"(?i)lsoa", n) and not re.search(r"(?i)2011", n)]
        order = sorted(dated, key=_year_of, reverse=True) + [n for n in names if n not in dated]
        last = "no sheet with LSOA estimates"
        for n in order:
            out, problem = sape_parse_table(wb[n].iter_rows(values_only=True), scope)
            if not problem:
                return out, _year_of(n) or _year_of(path.name), ""
            last = f"sheet {n!r}: {problem}"
        return pd.DataFrame(), 0, last
    finally:
        wb.close()


def run_sape() -> "pd.DataFrame":
    rule("ONS mid-year population estimates for LSOAs (SAPE)")
    cache_dir = CACHE_DIR / "sape"
    cache_dir.mkdir(parents=True, exist_ok=True)
    out_path = DATA_DIR / "demographics" / SAPE_PARQUET
    prev = _read_parquet_opt(out_path) if out_path.exists() else None
    scope = set(get_lsoa_ward_lookup())

    def fail(msg):
        msg += (". Download the LSOA single year of age file from "
                f"{SAPE_PAGES[0]} into {cache_dir} (or set SAPE_URL) and rerun "
                "`python fetch_all_data.py --only sape`.")
        if prev is not None and len(prev):
            warn(f"sape: {msg}")
            _SOURCE_ERROR["sape"] = msg + " The figures stay as last written."
            return prev
        raise RuntimeError(msg)

    url = SAPE_URL_OVERRIDE
    if not url:
        sess = browser_session(referer="https://www.ons.gov.uk/")
        found: list = []
        for page in SAPE_PAGES:
            found += _ons_page_files(page, sess)
        url = sape_pick(found) or ""
        if url:
            info(f"sape: newest file on the ONS pages is {urllib.parse.unquote(url).rsplit('/', 1)[-1]}")
    local = sorted([p for p in cache_dir.iterdir() if p.suffix.lower() in (".xlsx", ".zip", ".csv")],
                   key=lambda p: (_year_of(p.name), p.stat().st_mtime))
    src = None
    if url:
        name = re.sub(r"[^A-Za-z0-9._-]", "_", urllib.parse.unquote(url).rsplit("/", 1)[-1])
        dest = cache_dir / name
        if not dest.exists() or dest.stat().st_size < 100_000:
            try:
                r = browser_session(referer="https://www.ons.gov.uk/").get(url, timeout=600)
                r.raise_for_status()
                if len(r.content) < 100_000:
                    raise RuntimeError(f"only {len(r.content):,} bytes")
                write_bytes_atomic(dest, r.content)
            except Exception as e:
                warn(f"sape: could not download {url} ({type(e).__name__}: {e})")
        if dest.exists():
            src = dest
    if src is None and local:
        src = local[-1]
        info(f"sape: using {src.name} from {cache_dir}")
    if src is None:
        return fail("no LSOA population estimates file was found on the ONS pages")
    try:
        out, year, problem = sape_read_file(src, scope or None)
    except Exception as e:
        return fail(f"{src.name} could not be read ({type(e).__name__}: {e})")
    if problem or out.empty:
        return fail(f"{src.name}: {problem or 'no rows'}")
    out["sape_year"] = int(year) if year else pd.NA
    if scope and len(out) < len(scope) * 0.99:
        _SOURCE_ERROR["sape"] = (f"{src.name} has estimates for {len(out):,} of the "
                                 f"{len(scope):,} LSOAs in scope")
    out = write_parquet_guarded(out_path, out.sort_values("LSOA21CD").reset_index(drop=True),
                                source="sape")
    ok(f"sape: {len(out):,} LSOAs, mid-{year}, {int(out['sape_population'].sum()):,} people "
       f"-> {out_path.relative_to(REPO_ROOT)}")
    return out


# ---------------------------------------------------------------------------
# Claimant count aged 18 to 24, by LSOA (Nomis NM_162_1)
# ---------------------------------------------------------------------------
# The request run_claimant_count makes, for one age band. The band's code is
# looked up by its name in the dataset's age codelist rather than typed in,
# and the response is checked by AGE_NAME. Whether Nomis publishes the band
# for LSOAs was not confirmed (the fact check could not reach Nomis): if it
# does not, the source is "skipped" and says so, and nothing is written.
#
# Nomis rounds every LSOA count to the nearest 5, so these counts are only
# summed into wards and LAs and never shown for an LSOA. The base is people
# aged 18 to 24 in the ONS mid-year estimates (sape), because the Census
# tables the map reads have no 18 to 24 band.
NOMIS_API = "https://www.nomisweb.co.uk/api/v01/dataset"
CLAIMANT_DATASET = "NM_162_1"
CLAIMANT_YOUTH_PARQUET = "claimant_18_24.parquet"
CLAIMANT_YOUTH_MAX_AGE_DAYS = 20
# "Aged 18-24", "Aged 18 to 24" or with an en dash (written as an escape here,
# so the source holds no dash; pandas' pyarrow regex engine does not read
# \u escapes itself).
AGE_18_24 = r"\b18\s*(?:-|to|" + "\u2013" + r")\s*24\b"


def nomis_codelist(obj) -> dict:
    """{code: description} from a Nomis SDMX-JSON codelist response."""
    out: dict = {}

    def walk(o):
        if isinstance(o, dict):
            d = o.get("description")
            if "value" in o and isinstance(d, dict) and isinstance(d.get("value"), str):
                out[str(o["value"])] = d["value"]
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(obj)
    return out


def claimant_youth_code(codes: dict) -> str | None:
    """The code of the 18 to 24 band in an age codelist, by its name."""
    hits = [c for c, d in codes.items()
            if re.search(AGE_18_24, str(d), re.I)]
    return sorted(hits, key=lambda c: (len(codes[c]), c))[0] if hits else None


def claimant_youth_parse(df: "pd.DataFrame") -> tuple["pd.DataFrame", str]:
    """(LSOA21CD, claimant_count_18_24, claimant_18_24_month) for the latest
    month in a Nomis CSV, and a problem ("" when it was read)."""
    need = {"GEOGRAPHY_CODE", "DATE", "OBS_VALUE"}
    if not need.issubset(df.columns):
        return pd.DataFrame(), f"unexpected columns {list(df.columns)[:8]}"
    if "AGE_NAME" in df.columns:
        names = df["AGE_NAME"].astype(str)
        df = df[names.str.contains(AGE_18_24, case=False, regex=True)]
        if df.empty:
            return pd.DataFrame(), "no rows for ages 18 to 24 (AGE_NAME: " + \
                ", ".join(sorted(set(names))[:5]) + ")"
    df = df.assign(OBS_VALUE=pd.to_numeric(df["OBS_VALUE"], errors="coerce"))
    df = df[df["GEOGRAPHY_CODE"].astype(str).str.match(r"^E01\d{6}$")]
    if df.empty or df["OBS_VALUE"].notna().sum() == 0:
        return pd.DataFrame(), "no figures for LSOAs"
    latest = sorted(df["DATE"].dropna().astype(str).unique())[-1]
    m = df[df["DATE"].astype(str) == latest].groupby("GEOGRAPHY_CODE", as_index=False)["OBS_VALUE"].first()
    out = m.rename(columns={"GEOGRAPHY_CODE": "LSOA21CD", "OBS_VALUE": "claimant_count_18_24"})
    out["claimant_18_24_month"] = latest
    return out.dropna(subset=["claimant_count_18_24"]).reset_index(drop=True), ""


def run_claimant_youth() -> "pd.DataFrame | None":
    rule("NOMIS claimant count aged 18 to 24 (NM_162, LSOA)")
    cache_dir = CACHE_DIR / "claimant"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache = cache_dir / f"claimant_18_24_{SCOPE_KEY}_latest.csv"
    codes = sorted(get_lsoa_ward_lookup())
    if not codes:
        raise RuntimeError("claimant_youth: no LSOAs in scope")
    stale = (not cache.exists() or cache.stat().st_size < 1024 or
             _days_since(datetime.fromtimestamp(cache.stat().st_mtime, timezone.utc).isoformat())
             > CLAIMANT_YOUTH_MAX_AGE_DAYS)
    if stale:
        meta = _http_json("GET", f"{NOMIS_API}/{CLAIMANT_DATASET}/age.def.sdmx.json",
                          source="claimant_youth", timeout=60)
        ages = nomis_codelist(meta or {})
        code = claimant_youth_code(ages)
        if not code:
            msg = (f"Nomis lists no 18 to 24 band in the {CLAIMANT_DATASET} age codelist "
                   f"({'; '.join(list(ages.values())[:8]) or 'empty'})")
            warn(f"claimant_youth: {msg}")
            if not ages:
                raise RuntimeError(f"claimant_youth: {msg}")
            _SOURCE_NOTE["claimant_youth"] = msg
            return None
        info(f"claimant_youth: age code {code} ({ages[code]})")
        base = (f"{NOMIS_API}/{CLAIMANT_DATASET}.data.csv?date=latest&gender=0&age={code}"
                "&measures=20100")
        parts: list = []
        header = None
        for i in range(0, len(codes), 500):
            r = requests.get(base + "&geography=" + ",".join(codes[i:i + 500]), timeout=180)
            if r.status_code in (400, 404) and i == 0:
                # Nomis refuses a cell it does not publish, such as an age
                # band for a geography it is not released for.
                _SOURCE_NOTE["claimant_youth"] = (
                    f"Nomis refused ages 18 to 24 by LSOA (HTTP {r.status_code}: "
                    f"{r.text[:160].strip()}), so it does not publish the band for LSOAs")
                warn(f"claimant_youth: {_SOURCE_NOTE['claimant_youth']}")
                return None
            r.raise_for_status()
            body = r.content
            nl = body.find(b"\n")
            if nl < 0:
                continue
            if header is None:
                header = body[:nl + 1]
                parts.append(body)
            else:
                parts.append(body[nl + 1:])
        if not parts:
            _SOURCE_NOTE["claimant_youth"] = ("Nomis returned nothing for ages 18 to 24 by LSOA, "
                                              "so it does not publish the band for LSOAs")
            warn(f"claimant_youth: {_SOURCE_NOTE['claimant_youth']}")
            return None
        write_bytes_atomic(cache, b"".join(parts))
    try:
        df = pd.read_csv(cache, dtype=str, low_memory=False)
    except pd.errors.EmptyDataError:
        df = pd.DataFrame()
    out, problem = claimant_youth_parse(df)
    if problem:
        _SOURCE_NOTE["claimant_youth"] = (f"Nomis gave no claimant count aged 18 to 24 for LSOAs "
                                          f"({problem}), so there is nothing to show")
        warn(f"claimant_youth: {_SOURCE_NOTE['claimant_youth']}")
        cache.unlink(missing_ok=True)
        return None
    out_path = DATA_DIR / "economy" / CLAIMANT_YOUTH_PARQUET
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out = write_parquet_guarded(out_path, out, source="claimant_youth")
    ok(f"claimant_youth: {len(out):,} LSOAs, month {out['claimant_18_24_month'].iloc[0]}, "
       f"{int(out['claimant_count_18_24'].sum()):,} claimants aged 18 to 24 "
       f"-> {out_path.relative_to(REPO_ROOT)}")
    return out


# ---------------------------------------------------------------------------
# Road casualties killed or seriously injured (DfT STATS19)
# ---------------------------------------------------------------------------
# DfT's casualty file for the last five years. Each casualty carries the LSOA
# they live in (lsoa_of_casualty, blank for people living outside England and
# Wales), so the figure is a rate among residents, wherever the collision
# happened. Counting collisions where they happen and dividing by residents
# would make every ward with a main road look dangerous to live in.
#
# Seriously injured is DfT's adjusted figure (casualty_adjusted_severity_
# serious, a probability for each casualty), because forces using the CRASH
# system, the Met among them, record more injuries as serious than the rest.
# A file without the adjusted column is refused rather than mixed.
#
# Which LSOA vintage lsoa_of_casualty uses was not confirmed. A code in the
# 2021 set is used as it is; a 2011 code that no longer exists is shared
# among the 2021 LSOAs drawn from it, by their Census 2021 residents, so no
# casualty is lost or counted twice.
#
# The October 2026 refresh found no five-year link on the data.gov.uk page and
# a 404 at the old five-year address, so the files are looked for on three
# pages (data.gov.uk, its catalogue API and DfT's own gov.uk page), and when
# there is no five-year file the latest five single-year casualty files are
# read together instead.
STATS19_PAGES = [
    "https://www.data.gov.uk/dataset/cb7ae6f0-4be6-4935-9277-47e5ce24a11f/road-safety-data",
    "https://ckan.publishing.service.gov.uk/api/action/package_show?id=cb7ae6f0-4be6-4935-9277-47e5ce24a11f",
    "https://www.gov.uk/government/statistical-data-sets/road-safety-open-data",
]
STATS19_BASE = "https://data.dft.gov.uk/road-accident-safety-data/"
STATS19_DEFAULT = STATS19_BASE + "dft-road-casualty-statistics-casualty-last-5-years.csv"
STATS19_YEAR_FILE = STATS19_BASE + "dft-road-casualty-statistics-casualty-{year}.csv"
STATS19_URL_OVERRIDE = os.environ.get("STATS19_CASUALTY_URL", "")
ROAD_CASUALTIES_PARQUET = "road_casualties_lsoa.parquet"
STATS19_MAX_AGE_DAYS = 30
STATS19_YEARS = 5


def _stats19_casualty_links(urls) -> list:
    def name(u):
        return str(u).rsplit("/", 1)[-1].lower()
    return [u for u in dict.fromkeys(urls) if "casualt" in name(u) and name(u).endswith(".csv")
            and "collision" not in name(u) and "vehicle" not in name(u) and "guide" not in name(u)]


def stats19_pick(urls) -> str | None:
    """The casualty file for the last five years among links, else None."""
    five = [u for u in _stats19_casualty_links(urls)
            if re.search(r"last.?5.?years|last.?five.?years", str(u).rsplit("/", 1)[-1].lower())]
    return five[0] if five else None


def stats19_pick_years(urls, n: int = None) -> list:
    """The single-year casualty files among links, newest n of them (newest
    first), for when there is no five-year file. A "1979 to latest" file is
    not a single year and is not taken."""
    n = STATS19_YEARS if n is None else n
    by_year = {}
    for u in _stats19_casualty_links(urls):
        m = re.search(r"casualty-(\d{4})\.csv$", str(u).rsplit("/", 1)[-1].lower())
        if m:
            by_year.setdefault(int(m.group(1)), u)
    return [by_year[y] for y in sorted(by_year, reverse=True)[:n]]


def stats19_links_from(text: str) -> list:
    """Every CSV link in a page or a catalogue API answer."""
    return re.findall(r'(https?://[^"\'\s<>]+?\.csv)', text or "")


def stats19_columns(cols) -> tuple[dict, list]:
    """{role: column} for the casualty file's columns, and what is missing."""
    norm = {c: _norm_head(c).replace(" ", "_") for c in cols}
    def find(*pats, exclude=()):
        for c, n in norm.items():
            if all(re.search(p, n) for p in pats) and not any(re.search(x, n) for x in exclude):
                return c
        return None
    got = {
        "year": find(r"^(collision|accident)_year$") or find(r"year$", exclude=(r"age",)),
        "severity": find(r"^casualty_severity$"),
        "serious_adj": find(r"adjusted_severity_serious"),
        "lsoa": find(r"lsoa_of_casualty"),
    }
    missing = [k for k, v in got.items() if not v]
    return got, missing


def stats19_ksi_by_lsoa(df: "pd.DataFrame", cols: dict) -> tuple[dict, list]:
    """({lsoa code: casualties killed or seriously injured}, [years]) over the
    latest STATS19_YEARS years in the file, by home LSOA. A fatal casualty
    counts 1, any other its adjusted chance of having been seriously hurt."""
    years = pd.to_numeric(df[cols["year"]], errors="coerce")
    have = sorted(int(y) for y in years.dropna().unique())
    keep_years = have[-STATS19_YEARS:]
    d = df[years.isin(keep_years)]
    sev = d[cols["severity"]].astype(str).str.strip().str.lower()
    fatal = sev.isin(["1", "1.0", "fatal"])
    serious = sev.isin(["2", "2.0", "serious"])
    adj = pd.to_numeric(d[cols["serious_adj"]], errors="coerce").clip(0, 1)
    # A blank adjusted figure falls back to the casualty's own severity, so a
    # serious injury without one still counts rather than vanishing.
    adj = adj.where(adj.notna(), serious.astype(float))
    ksi = fatal.astype(float) + (~fatal).astype(float) * adj
    code = d[cols["lsoa"]].astype(str).str.strip()
    s = ksi.groupby(code).sum()
    return {c: float(v) for c, v in s.items() if re.fullmatch(r"E01\d{6}", c)}, keep_years


def counts_to_lsoa21(counts: dict, scope: set, rows: list, pop21: dict) -> tuple[dict, int]:
    """{LSOA21CD: count} for the scope from counts keyed by LSOA codes of
    either vintage. A code in the scope is kept as it is. A 2011 code that
    no longer exists is shared among the scope's 2021 LSOAs drawn from it in
    proportion to their Census 2021 residents (equally without them), so the
    total is kept. Returns the counts and how many 2011 codes were shared."""
    out: dict = {}
    for c, v in counts.items():
        if c in scope:
            out[c] = out.get(c, 0.0) + v
    kids: dict = {}
    for p11, c21, _chg in rows:
        if c21 in scope and p11 not in scope:
            kids.setdefault(p11, [])
            if c21 not in kids[p11]:
                kids[p11].append(c21)
    shared = 0
    for p11, ks in kids.items():
        v = counts.get(p11)
        if not v:
            continue
        shared += 1
        w = [float(pop21.get(k, 0) or 0) for k in ks]
        tot = sum(w)
        for k, wk in zip(ks, w):
            out[k] = out.get(k, 0.0) + v * (wk / tot if tot > 0 else 1 / len(ks))
    return out, shared


def run_road_casualties() -> "pd.DataFrame":
    rule("Road casualties killed or seriously injured (DfT STATS19)")
    cache_dir = CACHE_DIR / "stats19"
    cache_dir.mkdir(parents=True, exist_ok=True)
    scope = set(get_lsoa_ward_lookup())
    urls = [STATS19_URL_OVERRIDE] if STATS19_URL_OVERRIDE else []
    if not urls:
        found: list = []
        sess = browser_session(referer="https://www.data.gov.uk/")
        for page in STATS19_PAGES:
            try:
                r = sess.get(page, timeout=60)
                if r.ok:
                    links = stats19_links_from(r.text)
                    info(f"road_casualties: {len(_stats19_casualty_links(links))} casualty files listed on {page}")
                    found += links
            except Exception as e:
                warn(f"road_casualties: could not read {page} ({type(e).__name__}: {e})")
        five = stats19_pick(found)
        urls = [five] if five else stats19_pick_years(found)
        if not urls:
            # Nothing listed anywhere: the five-year file at its old address,
            # then the single years at DfT's usual names, newest first.
            this = datetime.now(timezone.utc).year
            urls = [STATS19_DEFAULT] + [STATS19_YEAR_FILE.format(year=y) for y in range(this - 1, this - 8, -1)]
            warn("road_casualties: no casualty file was listed on the pages; trying DfT's usual names")
    # A five-year file is read on its own; single-year files, up to five of
    # them, together.
    dests: list = []
    for url in urls:
        if len(dests) >= STATS19_YEARS:
            break
        dest = cache_dir / url.rsplit("/", 1)[-1]
        fresh = dest.exists() and dest.stat().st_size > 100_000 and _days_since(
            datetime.fromtimestamp(dest.stat().st_mtime, timezone.utc).isoformat()) <= STATS19_MAX_AGE_DAYS
        if not fresh:
            info(f"road_casualties: downloading {url}")
            try:
                r = requests.get(url, timeout=600)
                r.raise_for_status()
                if len(r.content) < 100_000:
                    raise RuntimeError(f"only {len(r.content):,} bytes")
                write_bytes_atomic(dest, r.content)
            except Exception as e:
                if not dest.exists():
                    warn(f"road_casualties: could not download {url} ({type(e).__name__}: {e})")
                    continue
                warn(f"road_casualties: download failed ({e}); using the cached {dest.name}")
        if stats19_pick([url]):
            dests = [dest]
            break
        dests.append(dest)
    if not dests:
        raise RuntimeError("road_casualties: could not download any DfT casualty file (tried "
                           + ", ".join(u.rsplit("/", 1)[-1] for u in urls[:8]) + ")")
    frames = []
    cols = None
    for dest in dests:
        head = pd.read_csv(dest, nrows=0, encoding="utf-8-sig")
        c, missing = stats19_columns(head.columns)
        if missing:
            raise RuntimeError(f"road_casualties: {dest.name} has no column for "
                               f"{', '.join(missing)} (columns: {', '.join(map(str, head.columns[:25]))})")
        f = pd.read_csv(dest, usecols=list(c.values()), dtype=str, low_memory=False, encoding="utf-8-sig")
        if cols is None:
            cols = c
        frames.append(f.rename(columns={v: cols[k] for k, v in c.items()}))
    if len(dests) > 1:
        info(f"road_casualties: no five-year file, so {len(dests)} single-year files: "
             + ", ".join(d.name for d in dests))
    df = pd.concat(frames, ignore_index=True)
    dest = dests[0]
    by_code, years = stats19_ksi_by_lsoa(df, cols)
    if len(years) < STATS19_YEARS:
        warn(f"road_casualties: the file covers only {len(years)} years ({years})")
    cen = _read_parquet_opt(DATA_DIR / "demographics" / "census2021.parquet")
    pop = {} if cen is None or "census_population" not in cen.columns else dict(
        zip(cen["LSOA21CD"].astype(str), pd.to_numeric(cen["census_population"], errors="coerce").fillna(0)))
    rows: list = []
    if any(c not in scope and c.startswith("E01") for c in by_code):
        try:
            rows = get_lsoa11_lsoa21_rows()
        except Exception as e:
            warn(f"road_casualties: the LSOA 2011 to 2021 lookup failed ({e}); "
                 "casualties with a retired 2011 code are left out")
    counts, shared = counts_to_lsoa21(by_code, scope, rows, pop)
    if shared:
        info(f"road_casualties: {shared:,} LSOA 2011 codes shared among their 2021 LSOAs")
    span = f"{years[0]} to {years[-1]}" if years else ""
    out = pd.DataFrame({"LSOA21CD": sorted(scope)})
    out["road_ksi_5yr"] = out["LSOA21CD"].map(lambda c: round(counts.get(c, 0.0), 3))
    out["road_ksi_years"] = span
    out_path = DATA_DIR / "environment" / ROAD_CASUALTIES_PARQUET
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not counts:
        raise RuntimeError(f"road_casualties: no casualty in {dest.name} lives in an LSOA in scope")
    out = write_parquet_guarded(out_path, out, source="road_casualties")
    ok(f"road_casualties: {out['road_ksi_5yr'].sum():,.0f} killed or seriously injured residents "
       f"of {len(out):,} LSOAs, {span} -> {out_path.relative_to(REPO_ROOT)}")
    return out


# ---------------------------------------------------------------------------
# Care homes and their beds (CQC)
# ---------------------------------------------------------------------------
# The pipeline did not read CQC before. CQC's "Using CQC data" page links a
# monthly "Care directory with filters" file, which carries the number of
# beds each care home is registered for; its address changes every month, so
# it is found on the page. CQC_CARE_DIRECTORY_URL overrides, and a file put in
# .cache/cqc/ by hand (or from a cqc-source release, as the workflows do) is
# used when the page cannot be read.
#
# The column names come from memory of CQC's files ("Location ID", "Care
# home?", "Care homes beds", "Dormant (Y/N)", "Location Postal Code"), not
# from a file seen this year, so they are matched by their words, and a file
# without a location, postcode or beds column fails the source rather than
# reporting no beds.
#
# Beds are registered places, not residents, and include homes for younger
# adults. Per 100 people aged 75 and over they say how much care home
# capacity an area has, not how many of its own residents need it, and one
# large home decides an LSOA's figure, so it is shown for wards and LAs only.
CQC_PAGE = "https://www.cqc.org.uk/about-us/transparency/using-cqc-data"
CQC_URL_OVERRIDE = os.environ.get("CQC_CARE_DIRECTORY_URL", "")
CARE_HOMES_PARQUET = "care_homes.parquet"
CQC_MAX_AGE_DAYS = 32


def cqc_pick(urls) -> str | None:
    """The care directory with filters among links (else the active
    locations file), newest by the date in its name; None if neither."""
    def name(u):
        return urllib.parse.unquote(str(u)).rsplit("/", 1)[-1].lower()
    ok_ext = [u for u in dict.fromkeys(urls) if re.search(r"\.(ods|xlsx|csv|zip)$", name(u))]
    def stamp(u):
        m = re.findall(r"(20\d{2})[-_ ]?(\d{2})[-_ ]?(\d{2})?", name(u))
        return max(m) if m else ("", "", "")
    for test in (lambda n: "directory" in n and "filter" in n,
                 lambda n: "active" in n and "location" in n):
        hits = [u for u in ok_ext if test(name(u).replace("_", " ").replace("-", " "))]
        if hits:
            return max(hits, key=stamp)
    return None


# A cell, empty and self-closing (<table:table-cell/>, or several of them as
# table:number-columns-repeated) or with content. The self-closing form is
# tried first: _GB_CELL, written for the green space table, which has no
# blank cells, reads an empty cell and the one after it as one cell, which
# would shift every later column of a CQC row one place left.
_ODS_CELL = re.compile(
    rb"<table:(?:covered-)?table-cell([^>]*?)/>"
    rb"|<table:(?:covered-)?table-cell([^>]*)>((?:(?!</table:(?:covered-)?table-cell>).)*)"
    rb"</table:(?:covered-)?table-cell>", re.DOTALL)


def ods_parse_row(row_xml: bytes) -> list:
    """The cells of one .ods row, an empty cell kept as "" in its place."""
    cells = []
    for m in _ODS_CELL.finditer(row_xml):
        empty = m.group(2) is None
        attrs = (m.group(1) if empty else m.group(2)) or b""
        inner = b"" if empty else (m.group(3) or b"")
        vm = _GB_VALUE.search(attrs)
        if vm:
            val = vm.group(1).decode("utf-8", errors="replace")
        else:
            bits = [_GB_TAG.sub(b"", x).decode("utf-8", errors="replace").strip()
                    for x in _GB_TEXT_P.findall(inner)]
            val = " ".join(b for b in bits if b)
        rm = _GB_REPEAT.search(attrs)
        reps = int(rm.group(1)) if rm else 1
        cells.extend([val] * (1 if reps > 200 and val == "" else min(reps, 200)))
    while cells and cells[-1] == "":
        cells.pop()
    return [html_unescape(c) for c in cells]


def ods_rows(path: Path):
    """Each row of every sheet in an .ods file as (sheet, [cells]), streamed
    from content.xml."""
    CHUNK = 16 * 1024 * 1024
    with zipfile.ZipFile(path) as zf, zf.open("content.xml") as fp:
        carry, cur = b"", None
        while True:
            data = fp.read(CHUNK)
            if not data and not carry:
                break
            chunk = carry + data if data else carry
            pos, N = 0, len(chunk)
            while pos < N:
                if cur is None:
                    m = _GB_TABLE_OPEN.search(chunk, pos)
                    if m is None:
                        break
                    cur = m.group(1).decode("utf-8", errors="replace")
                    pos = m.end()
                    continue
                r = _GB_ROW_OPEN.search(chunk, pos)
                close = _GB_TABLE_CLOSE.search(chunk, pos, r.start() if r else N)
                if close:
                    pos, cur = close.end(), None
                    continue
                if r is None:
                    break
                rc = _GB_ROW_END.search(chunk, r.end())
                if rc is None:
                    break
                yield cur, ods_parse_row(chunk[r.end():rc.start()])
                pos = rc.end()
            carry = chunk[pos:]
            if not data:
                break


def cqc_read_table(path: Path) -> "pd.DataFrame":
    """The locations table of a CQC file (.ods, .xlsx, .csv or a .zip of
    one), from the first row that names a location ID down."""
    path = Path(path)
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as z:
            inner = [n for n in z.namelist() if n.lower().endswith((".ods", ".xlsx", ".csv"))]
            if not inner:
                raise RuntimeError(f"{path.name} holds no .ods, .xlsx or .csv")
            tmp = path.with_name(Path(inner[0]).name)
            tmp.write_bytes(z.read(inner[0]))
        return cqc_read_table(tmp)
    def tagged_rows():
        """(sheet, cells) for every row, sheet by sheet, read lazily."""
        if path.suffix.lower() == ".csv":
            text = path.read_bytes().decode("utf-8-sig", errors="replace")
            for row in csv.reader(io.StringIO(text)):
                yield "csv", row
        elif path.suffix.lower() == ".xlsx":
            import openpyxl
            wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
            try:
                for n in wb.sheetnames:
                    for row in wb[n].iter_rows(values_only=True):
                        yield n, list(row)
            finally:
                wb.close()
        else:
            yield from ods_rows(path)

    seen: dict = {}
    sheet, cols, body = None, None, []
    for name, row in tagged_rows():
        if cols is not None:
            if name != sheet:
                break                      # the table ends with its sheet
            body.append(row)
            continue
        seen[name] = seen.get(name, 0) + 1
        if seen[name] > 50:
            continue
        heads = [_norm_head(c) for c in row]
        if any("location id" in h and "provider" not in h for h in heads):
            sheet, cols = name, [str(c if c is not None else "").strip() for c in row]
    if cols is None:
        raise RuntimeError(f"{path.name}: no sheet has a 'Location ID' header row")
    width = len(cols)
    named: dict = {}
    for i, c in enumerate(cols):          # blank or repeated headings made unique
        c = c or f"column {i + 1}"
        named[c] = named.get(c, 0) + 1
        cols[i] = c if named[c] == 1 else f"{c} ({named[c]})"
    body = [list(r) + [None] * (width - len(r)) if len(r) < width else list(r)[:width] for r in body]
    return pd.DataFrame(body, columns=cols)


def cqc_care_homes(df: "pd.DataFrame") -> "pd.DataFrame":
    """One row per active care home with beds: location_id, name, postcode,
    beds, nursing (True, False or None), lat, lng. Raises naming the columns
    when a location, postcode or beds column cannot be found."""
    heads = {c: _norm_head(c) for c in df.columns}

    def col(test, prefer=None):
        hits = [c for c, h in heads.items() if test(h)]
        if prefer:
            best = [c for c in hits if prefer(heads[c])]
            if best:
                return best[0]
        return hits[0] if hits else None
    c_id = col(lambda h: "location id" in h and "provider" not in h and "ods" not in h)
    c_name = col(lambda h: h in ("location name", "name") or (h.endswith(" name") and "location" in h),
                 prefer=lambda h: h == "location name")
    c_pc = col(lambda h: "postcode" in h or "postal code" in h, prefer=lambda h: "location" in h)
    c_beds = col(lambda h: "beds" in h and "provider" not in h)
    c_flag = col(lambda h: h in ("care home", "care home y n") or h.startswith("care home ") and h.endswith(" y n"))
    c_with = col(lambda h: "care home service with nursing" in h)
    c_without = col(lambda h: "care home service without nursing" in h)
    c_dormant = col(lambda h: "dormant" in h)
    c_lat = col(lambda h: "latitude" in h)
    c_lng = col(lambda h: "longitude" in h)
    missing = [n for n, c in (("location ID", c_id), ("postcode", c_pc), ("care home beds", c_beds)) if not c]
    if missing:
        raise RuntimeError("CQC file has no column for " + ", ".join(missing) +
                           " (columns: " + ", ".join(map(str, list(df.columns)[:30])) + ")")

    def yes(s):
        return s.astype(str).str.strip().str.upper().isin(["Y", "YES", "TRUE", "1"])
    beds = pd.to_numeric(df[c_beds], errors="coerce")
    keep = beds.fillna(0) > 0
    if c_flag:
        keep &= yes(df[c_flag])
    elif c_with or c_without:
        keep &= (yes(df[c_with]) if c_with else False) | (yes(df[c_without]) if c_without else False)
    if c_dormant:
        keep &= ~yes(df[c_dormant])
    d = df[keep]
    out = pd.DataFrame({
        "location_id": d[c_id].astype(str).str.strip(),
        "name": d[c_name].astype(str).str.strip() if c_name else "",
        "postcode": d[c_pc].astype(str).str.strip(),
        "beds": beds[keep].astype(int),
    })
    out["nursing"] = (yes(d[c_with]).values if c_with else None)
    out["lat"] = pd.to_numeric(d[c_lat], errors="coerce").values if c_lat else float("nan")
    out["lng"] = pd.to_numeric(d[c_lng], errors="coerce").values if c_lng else float("nan")
    out = out[out["location_id"] != ""].drop_duplicates("location_id")
    return out.reset_index(drop=True)


def run_care_homes() -> "pd.DataFrame":
    rule("Care homes and their beds (CQC care directory)")
    cache_dir = CACHE_DIR / "cqc"
    cache_dir.mkdir(parents=True, exist_ok=True)
    url = CQC_URL_OVERRIDE
    if not url:
        try:
            r = browser_session(referer="https://www.cqc.org.uk/").get(CQC_PAGE, timeout=60)
            r.raise_for_status()
            links = [h if h.startswith("http") else "https://www.cqc.org.uk" + h
                     for h in re.findall(r'href="([^"]+)"', r.text)]
            url = cqc_pick(links) or ""
            if not url:
                warn(f"care_homes: {CQC_PAGE} lists no care directory file")
        except Exception as e:
            warn(f"care_homes: could not read {CQC_PAGE} ({type(e).__name__}: {e})")
    src = None
    if url:
        dest = cache_dir / re.sub(r"[^A-Za-z0-9._-]", "_", urllib.parse.unquote(url).rsplit("/", 1)[-1].split("?")[0])
        if not dest.exists() or dest.stat().st_size < 50_000:
            try:
                rr = browser_session(referer=CQC_PAGE).get(url, timeout=600)
                rr.raise_for_status()
                if len(rr.content) < 50_000:
                    raise RuntimeError(f"only {len(rr.content):,} bytes")
                write_bytes_atomic(dest, rr.content)
            except Exception as e:
                warn(f"care_homes: could not download {url} ({type(e).__name__}: {e})")
        if dest.exists():
            src = dest
    if src is None:
        local = [p for p in cache_dir.iterdir() if p.suffix.lower() in (".ods", ".xlsx", ".csv", ".zip")]
        local = [p for p in local if _days_since(datetime.fromtimestamp(
            p.stat().st_mtime, timezone.utc).isoformat()) <= CQC_MAX_AGE_DAYS * 3]
        if local:
            src = max(local, key=lambda p: p.stat().st_mtime)
            info(f"care_homes: using {src.name} from {cache_dir}")
    if src is None:
        raise RuntimeError(
            f"care_homes: no CQC care directory file. Download 'Care directory with filters' "
            f"from {CQC_PAGE} into {cache_dir} (or attach it to a cqc-source release, or set "
            f"CQC_CARE_DIRECTORY_URL) and rerun `python fetch_all_data.py --only care_homes`.")
    homes = cqc_care_homes(cqc_read_table(src))
    if homes.empty:
        raise RuntimeError(f"care_homes: {src.name} lists no active care home with beds")
    info(f"care_homes: {len(homes):,} active care homes in {src.name}, {int(homes['beds'].sum()):,} beds")
    owners = {normalise_postcode(p): n for p, n in zip(homes["postcode"], homes["name"]) if normalise_postcode(p)}
    lookup = lookup_postcodes(owners.keys(), source="care_homes", owners=owners)
    scope = get_lsoa_ward_lookup()
    rows = []
    for rec in homes.to_dict("records"):
        hit = lookup.get(normalise_postcode(rec["postcode"]))
        if hit:
            lat, lng, lsoa, lad, wd = hit
        elif pd.notna(rec.get("lat")) and pd.notna(rec.get("lng")):
            codes = _codes_from_point(float(rec["lat"]), float(rec["lng"]))
            if not codes:
                continue
            lat, lng = float(rec["lat"]), float(rec["lng"])
            lsoa, lad, wd = codes
        else:
            continue
        if lsoa not in scope:
            continue
        rows.append({**{k: rec[k] for k in ("location_id", "name", "postcode", "beds", "nursing")},
                     "lat": lat, "lng": lng, "LSOA21CD": lsoa,
                     "WD25CD": scope[lsoa][0], "LAD25CD": scope[lsoa][1],
                     "as_at": datetime.fromtimestamp(src.stat().st_mtime, timezone.utc).strftime("%Y-%m-%d")})
    out = pd.DataFrame(rows)
    out_path = DATA_DIR / "healthcare" / CARE_HOMES_PARQUET
    out = write_parquet_guarded(out_path, out, source="care_homes")
    if len(out):
        ok(f"care_homes: {len(out):,} care homes in scope, {int(out['beds'].sum()):,} beds "
           f"-> {out_path.relative_to(REPO_ROOT)}")
    return out


# ---------------------------------------------------------------------------
# Ward and LA figures for the measures above
# ---------------------------------------------------------------------------
SMALL_AREA_RATE_KEYS = ("claimant_18_24_pct", "road_ksi_per_1000", "care_home_beds_per_100_75plus")


def _lsoa_frame(path: Path, cols) -> dict:
    """{LSOA21CD: {col: value}} from a parquet, for the columns it has."""
    df = _read_parquet_opt(path) if path.exists() else None
    if df is None or df.empty or "LSOA21CD" not in df.columns:
        return {}
    have = [c for c in cols if c in df.columns]
    return {str(r["LSOA21CD"]): {c: r[c] for c in have} for r in df[["LSOA21CD"] + have].to_dict("records")}


def _summed_rate(num: dict, den: dict, to_area: dict, scale: float, ndigits: int,
                 every_lsoa: bool) -> dict:
    """{area: scale * sum(num) / sum(den)} over the LSOAs of each area. With
    every_lsoa, an LSOA missing from num counts as 0 (a count of events); without
    it, only LSOAs with both a numerator and a base are used."""
    n_sum: dict = {}
    d_sum: dict = {}
    for lc, area in to_area.items():
        if not area:
            continue
        d = den.get(lc)
        if d is None or pd.isna(d) or float(d) <= 0:
            continue
        n = num.get(lc)
        if n is None or pd.isna(n):
            if not every_lsoa:
                continue
            n = 0.0
        n_sum[area] = n_sum.get(area, 0.0) + float(n)
        d_sum[area] = d_sum.get(area, 0.0) + float(d)
    return {a: round(scale * n_sum[a] / d, ndigits) for a, d in d_sum.items() if d > 0}


def small_area_figures(to_area: dict, pop_by_lsoa: dict, *, what: str) -> tuple[dict, dict]:
    """({area: {key: value}}, notes) for the small-area measures, for wards
    (to_area the LSOA to ward best fit) or LAs (every LSOA to its LA).

    Distances: averages of the LSOAs weighted by Census 2021 residents.
    Population estimates: the LSOAs' counts added up, and each share their
    band over their population. Youth claimants, road casualties and care home
    beds: the LSOAs' counts added up over their bases added up (people aged
    18 to 24 and aged 75 and over from the estimates, residents from the
    Census). notes holds each one's period and how distances were measured."""
    demo = DATA_DIR / "demographics"
    out: dict = {}
    notes: dict = {}

    def put(key, vals):
        for a, v in vals.items():
            out.setdefault(a, {})[key] = v

    sa = _lsoa_frame(DATA_DIR / "environment" / SERVICE_ACCESS_PARQUET, SERVICE_KEYS + ("origin",))
    if sa:
        for key in SERVICE_KEYS:
            per = {lc: r[key] for lc, r in sa.items() if key in r and pd.notna(r[key])}
            if per and pop_by_lsoa:
                put(key, {a: round(v, 3) for a, v in lsoa_means_to_areas(
                    per, to_area, pop_by_lsoa, what=f"{what} {key}").items()})
        kinds = {r.get("origin") for r in sa.values()}
        notes["service_access_origin"] = ("pwc" if kinds == {"pwc"} else
                                          "centroid" if kinds == {"centroid"} else "mixed")

    sape = _lsoa_frame(demo / SAPE_PARQUET, ("sape_population", "sape_year") + tuple(SAPE_BANDS))
    if sape:
        pops = {lc: r["sape_population"] for lc, r in sape.items()}
        put("sape_population", {a: int(v) for a, v in lsoa_sums_to_areas(pops, to_area).items()})
        for key, band in SAPE_SHARES.items():
            put(key, _summed_rate({lc: r.get(band) for lc, r in sape.items()}, pops, to_area,
                                  100.0, 1, every_lsoa=False))
        yrs = {int(r["sape_year"]) for r in sape.values() if pd.notna(r.get("sape_year"))}
        if yrs:
            notes["sape_year"] = max(yrs)

    cy = _lsoa_frame(DATA_DIR / "economy" / CLAIMANT_YOUTH_PARQUET,
                     ("claimant_count_18_24", "claimant_18_24_month"))
    if cy:
        if sape:
            put("claimant_18_24_pct", _summed_rate(
                {lc: r["claimant_count_18_24"] for lc, r in cy.items()},
                {lc: r.get("sape_age18_24") for lc, r in sape.items()},
                to_area, 100.0, 1, every_lsoa=False))
            notes["claimant_18_24_month"] = str(next(iter(cy.values())).get("claimant_18_24_month") or "")
        else:
            warn(f"{what}: no population estimates (sape), so the claimant rate aged 18 to 24 "
                 "has no base and is left out")

    rc = _lsoa_frame(DATA_DIR / "environment" / ROAD_CASUALTIES_PARQUET, ("road_ksi_5yr", "road_ksi_years"))
    if rc and pop_by_lsoa:
        put("road_ksi_per_1000", _summed_rate(
            {lc: r["road_ksi_5yr"] for lc, r in rc.items()},
            {lc: pop_by_lsoa.get(lc) for lc in rc}, to_area, 1000.0, 2, every_lsoa=True))
        notes["road_ksi_years"] = str(next(iter(rc.values())).get("road_ksi_years") or "")

    ch_path = DATA_DIR / "healthcare" / CARE_HOMES_PARQUET
    ch = _read_parquet_opt(ch_path) if ch_path.exists() else None
    if ch is not None and not ch.empty and {"LSOA21CD", "beds"}.issubset(ch.columns):
        if sape:
            beds = ch.groupby("LSOA21CD")["beds"].sum().to_dict()
            put("care_home_beds_per_100_75plus", _summed_rate(
                beds, {lc: r.get("sape_age75plus") for lc, r in sape.items()},
                to_area, 100.0, 1, every_lsoa=True))
            if "as_at" in ch.columns and ch["as_at"].notna().any():
                notes["care_homes_as_at"] = str(ch["as_at"].dropna().max())
        else:
            warn(f"{what}: no population estimates (sape), so care home beds per 100 people "
                 "aged 75 and over has no base and is left out")
    # The DWP benefits from Stat-Xplore, made the same way: counts added up
    # over bases added up (dwp_area_figures).
    dv, dn = dwp_area_figures(to_area, what=what, level="ward" if what == "ward" else "la")
    for a, vals in dv.items():
        out.setdefault(a, {}).update(vals)
    notes.update(dn)
    return out, notes


def small_area_lsoa_values(pop_by_lsoa: dict) -> dict:
    """{LSOA21CD: {key: value}} for the measures shown for LSOAs: the
    distances, the population estimates and the police.uk crime figures.
    Youth claimants, road casualties and care home beds are too few per LSOA
    to show there."""
    out: dict = {}
    sa = _lsoa_frame(DATA_DIR / "environment" / SERVICE_ACCESS_PARQUET, SERVICE_KEYS)
    for lc, r in sa.items():
        for k in SERVICE_KEYS:
            if k in r and pd.notna(r[k]):
                out.setdefault(lc, {})[k] = round(float(r[k]), 3)
    sape = _lsoa_frame(DATA_DIR / "demographics" / SAPE_PARQUET, ("sape_population",) + tuple(SAPE_BANDS))
    for lc, r in sape.items():
        p = r.get("sape_population")
        if p is None or pd.isna(p):
            continue
        rec = out.setdefault(lc, {})
        rec["sape_population"] = int(p)
        for key, band in SAPE_SHARES.items():
            v = r.get(band)
            if p > 0 and v is not None and pd.notna(v):
                rec[key] = round(100.0 * float(v) / float(p), 1)
    crime = _read_parquet_opt(DATA_DIR / "crime" / "police_uk_crime.parquet")
    for lc, rec in crime_by_lsoa(crime, pop_by_lsoa).items():
        out.setdefault(lc, {}).update(rec)
    # Universal Credit, whose LSOA counts are large enough to show.
    for lc, rec in dwp_lsoa_values().items():
        out.setdefault(lc, {}).update(rec)
    return out


def small_area_periods() -> dict:
    """{key: period} for the small-area measures, for indicators.js."""
    out: dict = {}
    demo = DATA_DIR / "demographics"
    sape = _read_parquet_opt(demo / SAPE_PARQUET) if (demo / SAPE_PARQUET).exists() else None
    if sape is not None and "sape_year" in sape.columns and sape["sape_year"].notna().any():
        y = f"mid-{int(pd.to_numeric(sape['sape_year'], errors='coerce').max())}"
        for k in SAPE_KEYS:
            out[k] = y
    cy_p = DATA_DIR / "economy" / CLAIMANT_YOUTH_PARQUET
    cy = _read_parquet_opt(cy_p) if cy_p.exists() else None
    if cy is not None and "claimant_18_24_month" in cy.columns and cy["claimant_18_24_month"].notna().any():
        out["claimant_18_24_pct"] = _month_label(str(cy["claimant_18_24_month"].dropna().iloc[0]))
    rc_p = DATA_DIR / "environment" / ROAD_CASUALTIES_PARQUET
    rc = _read_parquet_opt(rc_p) if rc_p.exists() else None
    if rc is not None and "road_ksi_years" in rc.columns and rc["road_ksi_years"].notna().any():
        out["road_ksi_per_1000"] = str(rc["road_ksi_years"].dropna().iloc[0])
    ch_p = DATA_DIR / "healthcare" / CARE_HOMES_PARQUET
    ch = _read_parquet_opt(ch_p) if ch_p.exists() else None
    if ch is not None and "as_at" in ch.columns and ch["as_at"].notna().any():
        try:
            out["care_home_beds_per_100_75plus"] = "as at " + datetime.strptime(
                str(ch["as_at"].dropna().max()), "%Y-%m-%d").strftime("%b %Y")
        except ValueError:
            pass
    out.update(dwp_periods())
    return out


# ============================================================================
# DWP BENEFITS, FROM STAT-XPLORE
# ============================================================================
# DWP publishes its benefit caseloads on Stat-Xplore, not on Nomis (see
# "SOURCE 3b: DWP benefits - RETIRED" above for why the old fetch went). This
# is a small client for its open data API,
#
#     https://stat-xplore.dwp.gov.uk/webapi/rest/v1/
#       GET  schema          the top folder of the catalogue
#       GET  schema/{id}     one folder, database, field or value set, with
#                            its children
#       POST table           a table: a database, a measure, and for each
#                            field a recode naming the values wanted
#
# with the key in an APIKey header on every request. The key belongs to a
# person: the owner registers on Stat-Xplore and adds it as the repository
# secret STAT_XPLORE_API_KEY, which the refresh and region workflows pass to
# the pipeline step (HANDOVER.md, "DWP benefits"). Without it every source
# below is "skipped" with a note saying so, never "failed", so no region pack
# is held up, and the map keeps what it had.
#
# Nothing here is addressed by an id typed in by hand. The database, measure,
# field and value ids are found by walking the schema and matching labels:
# the database whose label names Universal Credit people, its residence-based
# LSOA level, its newest month. Every id chosen is logged, so the first run
# with a key shows exactly what was picked. The JSON layout read here (nodes
# with id, label, type, location and children; a table answer with "fields",
# each holding "items", and "cubes", holding each measure's "values" nested
# one list per field) is the API's published documentation as remembered
# when this was written, not checked against a live answer from this
# sandbox, so each step checks what it gets and says what it found when that
# is not what was expected.
#
# Disclosure control: DWP adjusts or rounds small counts (Universal Credit by
# adding a little random error to every cell, the older benefits by
# rounding), and a cell can come back empty or marked as too small to show.
# Such a cell is no figure, never 0: an LSOA without one is left out of its
# ward and LA, count and base alike, and an area whose LSOAs with counts hold
# under WARD_MIN_COVER of its base gets no figure. Counts are otherwise used
# as published, so a ward or LA figure, made by adding up its LSOAs, will not
# match a total DWP publishes for it exactly. The map says the counts are
# rounded.
#
# Geography: Universal Credit has been built from the output areas of 2021
# since its October 2025 release, so its LSOAs are 2021 LSOAs. For the other
# benefits the vintage was not confirmed, so it is read from the value set:
# a set holding any LSOA code first issued in 2021 is on 2021 boundaries, one
# without is on 2011 boundaries and is moved onto 2021 LSOAs through the ONS
# lookup (get_lsoa11_lsoa21_rows), a split LSOA's count shared among its parts
# by their residents.
#
# Levels: LSOA, ward and LA for Universal Credit, whose small-area geography
# is confirmed and whose counts per LSOA are in the tens to hundreds. Wards
# and LAs only for PIP, DLA, Attendance Allowance, Carer's Allowance and
# Pension Credit, from summed LSOA counts over summed bases, because their
# LSOA level and vintage were not confirmed and their counts per LSOA are
# small. Children in relative low income families are published for wards,
# and used only where DWP's wards are the map's 2025 wards.
#
# The bases: residents of each age from ONS's mid-year estimates (sape), and
# households from the 2021 Census. A count from 2026 over a base from 2021 to
# 2024 runs a little high where the population has grown since.
STAT_XPLORE_API = "https://stat-xplore.dwp.gov.uk/webapi/rest/v1/"
STAT_XPLORE_KEY_ENV = "STAT_XPLORE_API_KEY"
# Stat-Xplore refuses a table with too many cells, so the geography values go
# in batches that keep each request well under its cap, whatever it is now.
SX_MAX_CELLS = 5000
SX_GEO_BATCH = 1000
# A gentle pace: one request at a time with a pause between them, and after a
# 429 or a server error a longer wait (Retry-After when the API sends one).
SX_PAUSE_S = 0.5
SX_ATTEMPTS = 4
SX_RETRY_S = 10.0
SX_TIMEOUT = 180
SX_SCHEMA_DEPTH = 6
SX_MAX_CANDIDATES = 4
# The LSOA level must hold at least this share of the LSOAs the scope needs,
# or it is the wrong level or the wrong vintage.
SX_MIN_FOUND = 0.9
# The last LSOA code of 2011 in England. Every English code above it was
# first issued for the LSOAs of 2021 (1,945 of the 33,755), so a value set
# holding any of them is on 2021 boundaries.
LSOA11_LAST_E01 = 33768
# Assessments for limited capability for work are backdated, so the newest
# months undercount these households, by up to two years' worth of
# decisions (the fact check). The figure is taken from the newest month at
# least this many months older than the latest, and the card says so.
SX_LCW_LAG_MONTHS = 12
# A category that means "not this one".
SX_NOT = r"(?i)^\s*(?:no|not|without|none)\b|unknown|missing|not known"
_sx_sleep = time.sleep          # the tests replace it
_SX_SCHEMA: dict = {}           # schema answers, shared by every source in a run
_SX_CATALOGUE: dict = {}        # the databases found by walking the folders

SX_PARQUETS = {
    "dwp_uc_people":            "dwp_uc_people_lsoa.parquet",
    "dwp_uc_households":        "dwp_uc_households_lsoa.parquet",
    "dwp_pip":                  "dwp_pip_lsoa.parquet",
    "dwp_dla_children":         "dwp_dla_children_lsoa.parquet",
    "dwp_attendance_allowance": "dwp_attendance_allowance_lsoa.parquet",
    "dwp_carers_allowance":     "dwp_carers_allowance_lsoa.parquet",
    "dwp_pension_credit":       "dwp_pension_credit_lsoa.parquet",
    "dwp_cilif":                "dwp_cilif_ward.parquet",
}
# How each database is recognised: every pattern in want must match its label
# with the folders above it, and none in avoid its own label. Among several
# that fit, the one with the newest period wins, then the one prefer names
# first. Carer's Allowance must be the in-payment count: the entitled count
# includes pensioners whose State Pension overlaps it, and would map where
# pensioners live.
SX_DATABASES = {
    "dwp_uc_people": dict(
        what="people on Universal Credit",
        want=(r"universal credit|\bUC\b", r"\bpeople\b"),
        avoid=(r"\bstarts?\b", r"\bhouseholds?\b", r"claimant count", r"\bflows?\b")),
    "dwp_uc_households": dict(
        what="households on Universal Credit",
        want=(r"universal credit|\bUC\b", r"\bhouseholds?\b"),
        avoid=(r"\bstarts?\b", r"\bpeople\b", r"\bflows?\b")),
    "dwp_pip": dict(
        what="Personal Independence Payment cases with entitlement",
        want=(r"\bPIP\b|personal independence payment", r"entitle"),
        avoid=(r"registration", r"clearance", r"reconsideration", r"appeal", r"review", r"\bstarts?\b")),
    "dwp_dla_children": dict(
        what="Disability Living Allowance cases",
        want=(r"\bDLA\b|disability living allowance", r"entitle|in payment"),
        avoid=(r"\bstarts?\b", r"clearance"), prefer=(r"entitle",)),
    "dwp_attendance_allowance": dict(
        what="Attendance Allowance cases",
        want=(r"\bAA\b|attendance allowance", r"entitle|in payment"),
        avoid=(r"\bstarts?\b", r"clearance"), prefer=(r"entitle",)),
    "dwp_carers_allowance": dict(
        what="Carer's Allowance cases in payment",
        want=(r"\bCA\b|carer.?s allowance", r"in payment"),
        avoid=(r"\bstarts?\b", r"entitle")),
    "dwp_pension_credit": dict(
        what="Pension Credit",
        want=(r"pension credit|\bPC\b",),
        avoid=(r"\bstarts?\b", r"application", r"\bflows?\b", r"clearance")),
    "dwp_cilif": dict(
        what="children in relative low income families",
        want=(r"low income", r"relative"), avoid=(r"absolute",), level="ward"),
}
# PIP by the main disabling condition DWP records. Whether Stat-Xplore crosses
# it with small areas was not confirmed, so a missing field or a refused
# table leaves these three out with a note, and PIP itself stands.
SX_PIP_CONDITION_FIELD = r"(?i)disabling condition|disability (?:category|condition)|main condition"
SX_PIP_CONDITIONS = {"psych": r"(?i)psychiatric", "msk": r"(?i)musculoskeletal", "neuro": r"(?i)neurolog"}


class StatXploreError(RuntimeError):
    """Stat-Xplore refused a request or answered with something unreadable."""


def statxplore_key() -> str:
    return (os.environ.get(STAT_XPLORE_KEY_ENV) or "").strip()


SX_NO_KEY_NOTE = ("no Stat-Xplore API key, so no DWP figures were fetched and the map keeps what it "
                  "had. Register free at stat-xplore.dwp.gov.uk and add the key as the repository "
                  f"secret {STAT_XPLORE_KEY_ENV} (HANDOVER.md, DWP benefits)")


def _sx_type(node) -> str:
    return str((node or {}).get("type") or "").strip().upper()


def _sx_label(node) -> str:
    return str((node or {}).get("label") or "").strip()


def statxplore_error_text(obj) -> str:
    """The message of a Stat-Xplore error body, or "" when obj is not one.
    A schema node or a table answer is never an error, whatever else it
    carries."""
    if not isinstance(obj, dict) or any(k in obj for k in ("cubes", "children", "fields")):
        return ""
    parts = []
    for k in ("error", "errors", "message", "messages", "detail", "description"):
        v = obj.get(k)
        if v:
            parts.append(v if isinstance(v, str) else json.dumps(v)[:300])
    if parts:
        return "; ".join(parts)
    st = obj.get("status") or obj.get("statusCode")
    if st is not None and str(st).strip().lower() not in ("200", "ok", "success"):
        return f"status {st}"
    return ""


class StatXplore:
    """The open data API, one request at a time. Schema answers are kept for
    the run in _SX_SCHEMA, so the catalogue is walked once however many
    sources ask."""

    def __init__(self, key: str, *, source: str, base: str = STAT_XPLORE_API):
        self.key = key
        self.source = source
        self.base = base.rstrip("/") + "/"
        self.requests = 0

    def _call(self, method: str, path: str, body=None):
        url = self.base + path
        headers = {"APIKey": self.key, "Accept": "application/json"}
        last = "no attempt made"
        for i in range(SX_ATTEMPTS):
            if self.requests:
                _sx_sleep(SX_PAUSE_S)
            self.requests += 1
            wait = SX_RETRY_S * (3 ** i)
            try:
                if method == "POST":
                    r = requests.post(url, headers=headers, json=body, timeout=SX_TIMEOUT)
                else:
                    r = requests.get(url, headers=headers, timeout=SX_TIMEOUT)
            except requests.RequestException as e:
                last = f"{type(e).__name__}: {e}"
                r = None
            if r is not None:
                code = int(r.status_code)
                text = str(getattr(r, "text", "") or "")[:300]
                if code in (401, 403):
                    raise StatXploreError(
                        f"{self.source}: Stat-Xplore refused the API key (HTTP {code}) for {path}. "
                        f"Check the {STAT_XPLORE_KEY_ENV} secret: {text}")
                if code == 429 or code >= 500:
                    last = f"HTTP {code}"
                    ra = (getattr(r, "headers", None) or {}).get("Retry-After")
                    if ra and str(ra).strip().isdigit():
                        wait = float(ra)
                else:
                    try:
                        j = r.json()
                    except ValueError:
                        raise StatXploreError(f"{self.source}: Stat-Xplore answered {path} with HTTP "
                                              f"{code} and a body that is not JSON: {text!r}") from None
                    msg = statxplore_error_text(j)
                    if code >= 400 or msg:
                        raise StatXploreError(f"{self.source}: Stat-Xplore answered {path} with an error "
                                              f"(HTTP {code}): {msg or json.dumps(j)[:300]}")
                    return j
            if i < SX_ATTEMPTS - 1:
                _sx_sleep(min(wait, 300.0))
        raise StatXploreError(f"{self.source}: Stat-Xplore did not answer {path} after "
                              f"{SX_ATTEMPTS} attempts ({last})")

    def node(self, ident=None) -> dict:
        """A schema node with its children: the catalogue's top folder
        without ident."""
        key = (self.base, ident or "")
        if key not in _SX_SCHEMA:
            path = "schema" if not ident else "schema/" + urllib.parse.quote(str(ident), safe=":")
            j = self._call("GET", path)
            if not isinstance(j, dict):
                raise StatXploreError(f"{self.source}: the schema answer for {ident or 'the top folder'} "
                                      f"is a {type(j).__name__}, not an object")
            _SX_SCHEMA[key] = j
        return _SX_SCHEMA[key]

    def children(self, ident=None) -> list:
        kids = self.node(ident).get("children")
        return [c for c in kids if isinstance(c, dict) and c.get("id")] if isinstance(kids, list) else []

    def table(self, query: dict) -> dict:
        return self._call("POST", "table", query)


def statxplore_databases(sx: StatXplore) -> list:
    """[{id, label, path}] for every database in the catalogue, found by
    walking its folders (path is the folders above it)."""
    out: list = []
    seen: set = set()
    todo = [(None, "", 0)]
    while todo:
        ident, path, depth = todo.pop(0)
        for c in sx.children(ident):
            cid = c["id"]
            if cid in seen:
                continue
            seen.add(cid)
            t = _sx_type(c)
            if t == "DATABASE":
                out.append({"id": cid, "label": _sx_label(c), "path": path})
            elif t == "FOLDER" and depth < SX_SCHEMA_DEPTH:
                todo.append((cid, (path + " > " if path else "") + _sx_label(c), depth + 1))
    return out


def _sx_catalogue(sx: StatXplore) -> list:
    if sx.base not in _SX_CATALOGUE:
        dbs = statxplore_databases(sx)
        info(f"{sx.source}: Stat-Xplore lists {len(dbs)} databases")
        _SX_CATALOGUE[sx.base] = dbs
    return _SX_CATALOGUE[sx.base]


def statxplore_match(dbs: list, want, avoid=()) -> list:
    """The databases whose label, with the folders above it, matches every
    pattern in want, and whose own label matches none in avoid."""
    out = []
    for d in dbs:
        full = ((d.get("path") or "") + " > " + d["label"]).strip(" >")
        if all(re.search(w, full, re.I) for w in want) and \
                not any(re.search(a, d["label"], re.I) for a in avoid):
            out.append(d)
    return out


def statxplore_parts(sx: StatXplore, db_id: str) -> tuple[list, list]:
    """(measures, fields) of a database: its COUNT and MEASURE nodes and its
    FIELD nodes, looking inside any folders it groups them in."""
    measures: list = []
    fields: list = []
    seen: set = set()
    todo = [(db_id, 0)]
    while todo:
        ident, depth = todo.pop(0)
        for c in sx.children(ident):
            if c["id"] in seen:
                continue
            seen.add(c["id"])
            t = _sx_type(c)
            if t in ("COUNT", "MEASURE"):
                measures.append(c)
            elif t == "FIELD":
                fields.append(c)
            elif t in ("FOLDER", "GROUP") and depth < 3:
                todo.append((c["id"], depth + 1))
    return measures, fields


def statxplore_measure(measures: list, pattern=None):
    """The count to tabulate: a COUNT measure (the one whose label matches
    pattern, if any does), else a MEASURE whose label says it counts
    something. Never an amount or an average. None when there is none."""
    counts = [m for m in measures if _sx_type(m) == "COUNT"] or \
        [m for m in measures if re.search(r"(?i)\b(people|households|cases|claimants|claims|children|number|count)\b",
                                          _sx_label(m)) and not re.search(r"(?i)amount|average|mean|£", _sx_label(m))]
    if pattern:
        hit = [m for m in counts if re.search(pattern, _sx_label(m), re.I)]
        if hit:
            return hit[0]
    return counts[0] if counts else None


def statxplore_valuesets(sx: StatXplore, field: dict) -> list:
    """A field's value sets, or the field itself when its values hang straight
    under it."""
    kids = sx.children(field["id"])
    vs = [c for c in kids if _sx_type(c) == "VALUESET"]
    if vs:
        return vs
    return [field] if any(_sx_type(c) == "VALUE" for c in kids) else []


def statxplore_values(sx: StatXplore, valueset: dict) -> list:
    return [c for c in sx.children(valueset["id"]) if _sx_type(c) == "VALUE"]


def statxplore_field_values(sx: StatXplore, field: dict) -> list:
    """The values of the first of a field's value sets that has any."""
    for vs in statxplore_valuesets(sx, field):
        vals = statxplore_values(sx, vs)
        if vals:
            return vals
    return []


SX_DATE_LABEL = (r"(?i)^\s*(?:month|quarter|year|date|period|time|financial year|"
                 r"reference (?:month|date|period|year))\b")


def statxplore_date_field(fields: list):
    """The field that holds the dates, by its label ("Month", "Quarter",
    "Year"), else by DATE in its id. None when there is none."""
    hits = [f for f in fields if re.search(SX_DATE_LABEL, _sx_label(f))]
    if not hits:
        hits = [f for f in fields if re.search(r"(?<![A-Z])DATE", str(f.get("id") or "").upper())]
    return hits[0] if hits else None


_SX_MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}


def statxplore_period(label, ident="") -> tuple | None:
    """(year, month, kind) of a date value from its label (or the end of its
    id), for sorting and for saying which period a figure is. kind is
    "month", "fy" (a financial year, dated to its March) or "year". None for
    a value that is not a date."""
    t = str(label or "")
    m = re.search(r"(?i)\b(?:fye|financial year end(?:ing)?|year end(?:ing)?)\s*(?:march\s*)?((?:19|20)\d{2})\b", t)
    if m:
        return (int(m.group(1)), 3, "fy")
    m = re.search(r"\b((?:19|20)\d{2})\s*/\s*(\d{2}|\d{4})\b", t)
    if m:
        y1, y2 = int(m.group(1)), int(m.group(2))
        y2 = (y1 // 100) * 100 + y2 if y2 < 100 else y2
        if y2 == y1 + 1:
            return (y2, 3, "fy")
    m = re.search(r"(?i)\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?[\s'/-]*((?:19|20)\d{2}|\d{2})\b", t)
    if m:
        y = int(m.group(2))
        return (y + 2000 if y < 100 else y, _SX_MONTHS[m.group(1).lower()], "month")
    m = re.search(r"(?i)\b((?:19|20)\d{2})\s*-?\s*q([1-4])\b", t) or re.search(r"(?i)\bq([1-4])\s*((?:19|20)\d{2})\b", t)
    if m:
        a, b = m.group(1), m.group(2)
        y, q = (int(a), int(b)) if len(a) == 4 else (int(b), int(a))
        return (y, 3 * q, "month")
    m = re.search(r"\b((?:19|20)\d{2})[-/]?(0[1-9]|1[0-2])\b", t)
    if m:
        return (int(m.group(1)), int(m.group(2)), "month")
    m = re.fullmatch(r"\s*((?:19|20)\d{2})\s*(?:\((?:p|r)\))?\s*", t, re.I)
    if m:
        return (int(m.group(1)), 12, "year")
    tail = str(ident or "").rsplit(":", 1)[-1]
    m = re.fullmatch(r"((?:19|20)\d{2})(0[1-9]|1[0-2])", tail)
    if m:
        return (int(m.group(1)), int(m.group(2)), "month")
    return None


def statxplore_period_label(p) -> str:
    """'May 2026', '2024/25' or '2025'."""
    y, m, kind = p
    if kind == "fy":
        return f"{y - 1}/{str(y)[2:]}"
    if kind == "year":
        return str(y)
    return datetime(y, m, 1).strftime("%B %Y")


def statxplore_pick_period(values: list, lag_months: int = 0):
    """(value, period) of the newest dated value, or with lag_months of the
    newest at least that many months older than it (the newest when there is
    none so old). (None, None) when no value is a date."""
    dated = []
    for v in values:
        p = statxplore_period(_sx_label(v), v.get("id"))
        if p:
            dated.append((p, v))
    if not dated:
        return None, None
    dated.sort(key=lambda x: (x[0][0], x[0][1]))
    newest = dated[-1]
    if lag_months:
        top = newest[0][0] * 12 + newest[0][1]
        older = [x for x in dated if x[0][0] * 12 + x[0][1] <= top - lag_months]
        if older:
            return older[-1][1], older[-1][0]
    return newest[1], newest[0]


# The levels, by the label of the value set (or of a field holding just one
# level), and what they must not be mistaken for.
SX_LEVEL_LABEL = {
    "lsoa": (r"(?i)lower\s*layer\s*super\s*output|\blsoas?\b", r"(?i)middle|\bmsoa"),
    "ward": (r"(?i)\bwards?\b", r"(?i)parliament|constituenc"),
}
SX_GEO_FIELD = (r"(?i)geograph|national|region|\bLAs?\b|local authorit|output area|\bOAs?\b|"
                r"super output|\blsoas?\b|\bwards?\b")
# Where the office handling the claim is, rather than where the claimant lives.
SX_GEO_AVOID = r"(?i)jobcentre|\bjcp\b|workplace|office"
SX_CODE = {"lsoa": re.compile(r"(?<![A-Z0-9])([EW]01\d{6})(?![0-9])"),
           "ward": re.compile(r"(?<![A-Z0-9])([EW]05\d{6})(?![0-9])")}


def statxplore_geo(sx: StatXplore, fields: list, level: str):
    """(field, value set) holding the level in the geography of where people
    live, or None. A residence-based field and a 2021 label are preferred."""
    want, avoid = SX_LEVEL_LABEL[level]
    found = []
    for f in fields:
        fl = _sx_label(f)
        if re.search(SX_GEO_AVOID, fl) or not re.search(SX_GEO_FIELD, fl):
            continue
        for vs in statxplore_valuesets(sx, f):
            vl = fl if vs is f else _sx_label(vs)
            if re.search(want, vl) and not re.search(avoid, vl):
                found.append((f, vs))
    if not found:
        return None
    found.sort(key=lambda x: (0 if re.search(r"(?i)residen", _sx_label(x[0])) else 1,
                              0 if "2021" in _sx_label(x[1]) + _sx_label(x[0]) else 1))
    return found[0]


def statxplore_code(value: dict, level: str) -> str | None:
    """The LSOA (E01, W01) or ward (E05, W05) code of a value, from the end of
    its id, else its id, else its label."""
    ident = str(value.get("id") or "")
    for t in (ident.rsplit(":", 1)[-1], ident, _sx_label(value)):
        m = SX_CODE[level].search(t)
        if m:
            return m.group(1)
    return None


def statxplore_lsoa_vintage(label: str, codes) -> str:
    """"2021" when the codes include any English LSOA code first issued in
    2021, else "2011". The label is only a fallback for a set with no English
    codes: a 2021 set always holds some of the new ones."""
    nums = [int(c[3:]) for c in codes if str(c).startswith("E01") and str(c)[3:].isdigit()]
    if any(n > LSOA11_LAST_E01 for n in nums):
        return "2021"
    if not nums and re.search(r"2021", label or ""):
        return "2021"
    return "2011"


def statxplore_dataset(sx: StatXplore, *, what: str, want, avoid=(), prefer=(),
                       level: str = "lsoa", measure=None) -> dict:
    """The database for one benefit, found by label, with its count measure,
    its date field and newest period, and its geography at level. Raises
    StatXploreError, saying what was found, when no database fits."""
    src = sx.source
    dbs = _sx_catalogue(sx)
    cands = statxplore_match(dbs, want, avoid)
    level_word = "LSOA" if level == "lsoa" else level
    if not cands:
        near = [d["label"] for d in dbs if re.search(want[0], (d.get("path") or "") + " " + d["label"], re.I)][:8]
        raise StatXploreError(f"{src}: no Stat-Xplore database is labelled as {what}"
                              + (f"; the nearest are: {'; '.join(near)}" if near else f" among the {len(dbs)} listed"))
    usable: list = []
    why: list = []
    for d in cands[:SX_MAX_CANDIDATES]:
        measures, fields = statxplore_parts(sx, d["id"])
        m = statxplore_measure(measures, measure)
        dfield = statxplore_date_field(fields)
        dvals = statxplore_field_values(sx, dfield) if dfield else []
        dv, per = statxplore_pick_period(dvals)
        geo = statxplore_geo(sx, fields, level)
        miss = [n for n, x in (("count measure", m), ("date field", dfield), ("dated value", dv),
                                (f"{level_word} level", geo)) if not x]
        if miss:
            why.append(f"{d['label']!r} has no {', no '.join(miss)}")
            continue
        usable.append({
            "db": d["id"], "db_label": d["label"], "db_path": d.get("path") or "",
            "measure": m["id"], "measure_label": _sx_label(m), "fields": fields,
            "date_field": dfield["id"], "date_label": _sx_label(dfield), "date_values": dvals,
            "date_value": dv["id"], "period": per, "period_label": statxplore_period_label(per),
            "geo_field": geo[0]["id"], "geo_valueset": geo[1],
            "geo_label": _sx_label(geo[0]) if geo[1] is geo[0] else f"{_sx_label(geo[0])}, {_sx_label(geo[1])}",
            "level": level,
        })
    if not usable:
        raise StatXploreError(f"{src}: no Stat-Xplore database for {what} can be used: " + "; ".join(why))

    def rank(u):
        pr = next((i for i, p in enumerate(prefer) if re.search(p, u["db_label"], re.I)), len(prefer))
        return (-(u["period"][0] * 12 + u["period"][1]), pr, len(u["db_label"]))
    usable.sort(key=rank)
    best = usable[0]
    if len(usable) > 1:
        info(f"{src}: {len(usable)} databases fit; taking the newest: "
             + "; ".join(f"{u['db_label']!r} to {u['period_label']}" for u in usable))
    info(f"{src}: database {best['db']} ({best['db_label']!r}, in {best['db_path'] or 'the top folder'})")
    info(f"{src}: measure {best['measure']} ({best['measure_label']!r}); date field {best['date_field']} "
         f"({best['date_label']!r}), latest {best['date_value']} ({best['period_label']})")
    info(f"{src}: {level_word} level {best['geo_valueset']['id']} ({best['geo_label']!r}) in field {best['geo_field']}")
    for d in why:
        info(f"{src}: passed over {d}")
    return best


def statxplore_groups(sx: StatXplore, ds: dict, field_pat: str, named: dict, *,
                      drop=None, avoid_field=None, rest: bool = True):
    """(field id, groups of value ids, {name: group index}, {name: labels},
    field label) for a field found by its label: one group for each name in
    named (name: pattern) that some value matches and drop does not, then,
    with rest, one group of every other value, so the groups add up to the
    whole. None when there is no such field or no name matches."""
    for f in ds["fields"]:
        fl = _sx_label(f)
        if not re.search(field_pat, fl) or (avoid_field and re.search(avoid_field, fl)):
            continue
        for vs in statxplore_valuesets(sx, f):
            vals = statxplore_values(sx, vs)
            taken: set = set()
            groups: list = []
            index: dict = {}
            labels: dict = {}
            for name, pat in named.items():
                hit = [v for v in vals if v["id"] not in taken and re.search(pat, _sx_label(v))
                       and not (drop and re.search(drop, _sx_label(v)))]
                if hit:
                    index[name] = len(groups)
                    groups.append([v["id"] for v in hit])
                    labels[name] = [_sx_label(v) for v in hit]
                    taken.update(v["id"] for v in hit)
            if index:
                others = [v["id"] for v in vals if v["id"] not in taken]
                if rest and others:
                    groups.append(others)
                return f["id"], groups, index, labels, fl
    return None


def statxplore_age_band(label) -> tuple | None:
    """(first age, last age) of an age band's label: "0-4", "5 to 10",
    "Under 16", "16+", "65 and over", "7". None for an unknown age or a label
    that is not an age."""
    t = str(label or "").lower().replace("\u2013", "-")      # an en dash, written as an escape
    if re.search(r"unknown|missing|not known|unavailable", t):
        return None
    m = re.search(r"under\s*(\d{1,3})", t)
    if m:
        return (0, int(m.group(1)) - 1)
    m = re.search(r"(\d{1,3})\s*(?:-|to)\s*(\d{1,3})", t)
    if m:
        return (int(m.group(1)), int(m.group(2)))
    m = re.search(r"(\d{1,3})\s*(?:\+|and over|or over|and above|plus|or more)", t)
    if m:
        return (int(m.group(1)), 200)
    m = re.fullmatch(r"\s*(?:aged?\s*)?(\d{1,3})(?:\s*years?(?:\s*old)?)?\s*", t)
    if m:
        return (int(m.group(1)), int(m.group(1)))
    return None


def statxplore_ages_upto(values: list, last_age: int) -> tuple[list, str]:
    """(value ids of the bands that together make ages 0 to last_age,
    problem). A band running across last_age, bands that overlap or leave an
    age out, give no ids and say why."""
    bands = [(b, v) for v in values for b in [statxplore_age_band(_sx_label(v))] if b]
    keep = [(b, v) for b, v in bands if b[1] <= last_age]
    across = [v for b, v in bands if b[0] <= last_age < b[1]]
    if across:
        return [], f"the band {_sx_label(across[0])!r} runs across age {last_age}"
    ages: list = []
    for (lo, hi), _v in keep:
        ages.extend(range(lo, hi + 1))
    if not keep or sorted(set(ages)) != list(range(0, last_age + 1)):
        return [], (f"the bands do not make ages 0 to {last_age} "
                    f"({', '.join(_sx_label(v) for v in values[:12])})")
    if len(ages) != len(set(ages)):
        return [], f"the bands up to {last_age} overlap ({', '.join(_sx_label(v) for _b, v in keep)})"
    return [v["id"] for _b, v in keep], ""


def statxplore_ages(sx: StatXplore, ds: dict, last_age: int) -> tuple:
    """(field id, [[value ids]], labels) restricting a table to ages 0 to
    last_age through the database's age field. Raises when no age field
    makes those ages, because without it the count is everyone's."""
    why = []
    fields = [f for f in ds["fields"] if re.search(r"(?i)^\s*age\b", _sx_label(f))
              and not re.search(r"(?i)pension|date|start", _sx_label(f))]
    for f in fields:
        for vs in statxplore_valuesets(sx, f):
            vals = statxplore_values(sx, vs)
            ids, problem = statxplore_ages_upto(vals, last_age)
            if ids:
                labels = [_sx_label(v) for v in vals if v["id"] in ids]
                info(f"{sx.source}: ages 0 to {last_age} from {_sx_label(f)!r} ({f['id']}): {', '.join(labels)}")
                return f["id"], [ids], labels
            why.append(f"{_sx_label(f)!r}: {problem}")
    raise StatXploreError(
        f"{sx.source}: no age field in {ds['db_label']!r} makes ages 0 to {last_age}"
        + (": " + "; ".join(why) if why else
           f" (its fields: {', '.join(_sx_label(f) for f in ds['fields'][:15])})"))


def statxplore_filter(sx: StatXplore, ds: dict, field_pat: str, value_pat: str):
    """(field id, [[value id]], label) keeping one value of a field found by
    label, or None when there is no such field or value."""
    g = statxplore_groups(sx, ds, field_pat, {"keep": value_pat}, rest=False)
    if not g:
        return None
    fid, groups, _index, labels, flabel = g
    return fid, groups, f"{flabel}: {', '.join(labels['keep'])}"


def statxplore_query(db: str, measure: str, recodes: list) -> dict:
    """The body of a table request: one dimension per recode, each recode a
    field and its groups of value ids, with no totals."""
    return {"database": db, "measures": [measure],
            "recodes": {f: {"map": g, "total": False} for f, g in recodes},
            "dimensions": [[f] for f, _g in recodes]}


def statxplore_cell(v) -> float | None:
    """A cell as a count, or None when DWP withheld it: a cell that is empty,
    not a number, or marked (".." or "*", say). Never 0 for a withheld cell."""
    if isinstance(v, dict):
        return statxplore_cell(v.get("value"))
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return None if v != v else max(0.0, float(v))
    try:
        f = float(str(v).strip().replace(",", ""))
    except ValueError:
        return None
    return None if f != f else max(0.0, f)


def statxplore_cube(resp, measure: str, dims: list) -> tuple[list, dict]:
    """(items of each dimension, {index tuple: cell}) from a table answer,
    the dimensions in the order of dims (field ids). Raises on an error body
    or an answer whose shape does not match what was asked."""
    if not isinstance(resp, dict):
        raise StatXploreError(f"the table answer is a {type(resp).__name__}, not an object")
    msg = statxplore_error_text(resp)
    if msg:
        raise StatXploreError(f"the table answer is an error: {msg}")
    cubes = resp.get("cubes")
    if not isinstance(cubes, dict) or not cubes:
        raise StatXploreError("the table answer has no cubes (it has " + ", ".join(map(str, list(resp)[:8])) + ")")
    cube = cubes.get(measure)
    if cube is None and len(cubes) == 1:
        cube = next(iter(cubes.values()))
    if not isinstance(cube, dict) or "values" not in cube:
        raise StatXploreError(f"the table answer has no values for {measure}")
    fields = resp.get("fields")
    if not isinstance(fields, list) or len(fields) != len(dims):
        raise StatXploreError(f"the table answer has {len(fields) if isinstance(fields, list) else 'no'} "
                              f"fields, not {len(dims)}")
    ids = [str((f or {}).get("uri") or (f or {}).get("id") or "") for f in fields]
    order = list(range(len(dims)))
    if all(ids) and sorted(ids) == sorted(dims) and len(set(ids)) == len(ids):
        order = [ids.index(d) for d in dims]
    items_in = [list((f or {}).get("items") or []) for f in fields]
    cells_in: dict = {}

    def walk(arr, idx):
        if len(idx) == len(fields):
            cells_in[tuple(idx)] = arr
            return
        n = len(items_in[len(idx)])
        if not isinstance(arr, list) or len(arr) != n:
            raise StatXploreError(f"the cube does not match its fields ({n} items at depth {len(idx)}, "
                                  f"{len(arr) if isinstance(arr, list) else 'no list of'} values)")
        for i, a in enumerate(arr):
            walk(a, idx + [i])
    walk(cube["values"], [])
    items = [items_in[order[d]] for d in range(len(dims))]
    cells = {tuple(idx[order[d]] for d in range(len(dims))): v for idx, v in cells_in.items()}
    return items, cells


def statxplore_counts(sx: StatXplore, ds: dict, geo_ids: dict, *, split=None, filters=(),
                      date_value=None) -> dict:
    """{geography code: [cell per group of split]} for the values in geo_ids
    ({code: value id}) at the dataset's date (or date_value), restricted by
    filters ((field id, [[value ids]]) each) and crossed with split (field
    id, groups), in batches that keep each request under SX_MAX_CELLS
    cells. A cell DWP withheld is None."""
    groups = split[1] if split else [None]
    per_geo = len(groups)
    for _f, g in filters:
        per_geo *= max(1, len(g))
    size = max(1, min(SX_GEO_BATCH, SX_MAX_CELLS // max(1, per_geo)))
    by_value = {v: c for c, v in geo_ids.items()}
    codes = sorted(geo_ids)
    out: dict = {}
    n_req = 0
    for i in range(0, len(codes), size):
        batch = codes[i:i + size]
        recodes = [(ds["geo_field"], [[geo_ids[c]] for c in batch]),
                   (ds["date_field"], [[date_value or ds["date_value"]]])]
        recodes += [(f, g) for f, g in filters]
        if split:
            recodes.append((split[0], split[1]))
        dims = [f for f, _g in recodes]
        resp = sx.table(statxplore_query(ds["db"], ds["measure"], recodes))
        n_req += 1
        try:
            items, cells = statxplore_cube(resp, ds["measure"], dims)
        except StatXploreError as e:
            raise StatXploreError(f"{sx.source}: {e}") from None
        geo = []
        for it in items[0]:
            uris = [str(u) for u in ((it or {}).get("uris") or [])]
            c = next((by_value[u] for u in uris if u in by_value), None)
            if c is None:
                c = statxplore_code({"id": uris[0] if uris else "",
                                     "label": " ".join(map(str, (it or {}).get("labels") or []))}, ds["level"])
            geo.append(c)
        gidx = [0]
        if split:
            gidx = []
            for j, it in enumerate(items[-1]):
                uris = {str(u) for u in ((it or {}).get("uris") or [])}
                gidx.append(next((n for n, g in enumerate(split[1]) if uris & set(g)), None if uris else j))
        for idx, v in cells.items():
            c = geo[idx[0]]
            if c is None or c not in geo_ids:
                continue
            k = gidx[idx[-1]] if split else 0
            if k is None:
                continue
            out.setdefault(c, [None] * len(groups))[k] = statxplore_cell(v)
    info(f"{sx.source}: {len(codes):,} {ds['level'].upper()} values in {n_req} table request(s) "
         f"of at most {size:,} ({per_geo} cell(s) each)")
    return out


def counts11_to_lsoa21(counts11: dict, rows: list, pop21: dict) -> dict:
    """{LSOA21CD: count} from counts keyed by 2011 LSOA codes, through the
    ONS lookup rows [(LSOA11CD, LSOA21CD, CHGIND)]. A 2011 LSOA's count goes
    to the 2021 LSOAs drawn from it, shared by their Census 2021 residents
    (equally without them) when it was split, so no one is lost or counted
    twice; a 2021 LSOA merged from several takes the sum. A 2011 LSOA with no
    figure (withheld, or not in the table) leaves every 2021 LSOA drawn from
    it without one (None), rather than with part of a sum."""
    kids: dict = {}
    for p11, c21, _chg in rows:
        kids.setdefault(p11, [])
        if c21 not in kids[p11]:
            kids[p11].append(c21)
    out: dict = {}
    blank: set = set()
    for p11, ks in kids.items():
        v = counts11.get(p11)
        if v is None or pd.isna(v):
            blank.update(ks)
            continue
        w = [float(pop21.get(k, 0) or 0) for k in ks]
        tot = sum(w)
        for k, wk in zip(ks, w):
            out[k] = out.get(k, 0.0) + float(v) * (wk / tot if tot > 0 else 1.0 / len(ks))
    for k in blank:
        out[k] = None
    return out


def _sx_pop21() -> dict:
    cen = _read_parquet_opt(DATA_DIR / "demographics" / "census2021.parquet")
    if cen is None or "census_population" not in cen.columns:
        return {}
    return dict(zip(cen["LSOA21CD"].astype(str), pd.to_numeric(cen["census_population"], errors="coerce").fillna(0)))


def statxplore_lsoa_ids(sx: StatXplore, ds: dict) -> tuple[dict, str, list]:
    """({LSOA code: value id} to ask for, vintage, ONS lookup rows for a
    2011 set). The codes are the scope's 2021 LSOAs, or for a 2011 set the
    2011 LSOAs they were drawn from. Kept on ds for the dataset's other
    tables."""
    if "_ids" in ds:
        return ds["_ids"]
    vals = statxplore_values(sx, ds["geo_valueset"])
    codes: dict = {}
    for v in vals:
        c = statxplore_code(v, "lsoa")
        if c and c not in codes:
            codes[c] = v["id"]
    if not codes:
        raise StatXploreError(f"{sx.source}: the LSOA level ({ds['geo_label']!r}) holds no LSOA codes; "
                              f"its first values are {'; '.join(_sx_label(v) for v in vals[:3]) or 'none'}")
    vintage = statxplore_lsoa_vintage(ds["geo_label"], codes)
    scope = set(get_lsoa_ward_lookup())
    rows: list = []
    if vintage == "2021":
        want = scope
    else:
        rows = get_lsoa11_lsoa21_rows()
        want = {r[0] for r in rows}
    found = {c: codes[c] for c in sorted(want) if c in codes}
    share = len(found) / len(want) if want else 0.0
    info(f"{sx.source}: the LSOA values are {vintage} LSOAs ({len(codes):,} in all); "
         f"{len(found):,} of the {len(want):,} this scope needs are there"
         + (" (moved onto 2021 LSOAs through the ONS lookup)" if vintage == "2011" else ""))
    if share < SX_MIN_FOUND:
        raise StatXploreError(f"{sx.source}: the LSOA level ({ds['geo_label']!r}, {vintage} codes) holds only "
                              f"{len(found):,} of the {len(want):,} LSOAs this scope needs, so it is the "
                              "wrong level or the wrong vintage")
    if share < 0.99:
        warn(f"{sx.source}: {len(want) - len(found):,} LSOAs this scope needs are not in Stat-Xplore's list, "
             "so they have no figure")
    ds["_ids"] = (found, vintage, rows)
    return ds["_ids"]


def statxplore_lsoa_counts(sx: StatXplore, ds: dict, **kw) -> tuple[dict, dict]:
    """({LSOA21CD: [cell per group]}, facts) for the scope's LSOAs, moved
    onto 2021 LSOAs when the dataset is on 2011 ones. facts: vintage, and
    how many cells are withheld or blank."""
    found, vintage, rows = statxplore_lsoa_ids(sx, ds)
    raw = statxplore_counts(sx, ds, found, **kw)
    n = len(kw["split"][1]) if kw.get("split") else 1
    if vintage == "2021":
        out = {c: list(raw.get(c) or [None] * n) for c in found}
    else:
        pop = _sx_pop21()
        out = {}
        for j in range(n):
            col = counts11_to_lsoa21({c: (raw.get(c) or [None] * n)[j] for c in found}, rows, pop)
            for c, v in col.items():
                out.setdefault(c, [None] * n)[j] = v
    withheld = sum(1 for r in out.values() for v in r if v is None)
    if withheld:
        info(f"{sx.source}: {withheld:,} of {len(out) * n:,} cells are withheld or blank, kept as no figure (not 0)")
    return out, {"vintage": vintage, "withheld": withheld}


def _sx_sum(cells) -> float | None:
    """The groups of a split added up: the whole. None if any is withheld."""
    if not cells or any(v is None for v in cells):
        return None
    return float(sum(cells))


def _sx_frame(rows: dict, cols: dict, key: str = "LSOA21CD") -> "pd.DataFrame":
    """One row per area from {code: [cells]}, a column per {name: position};
    a withheld cell is NaN."""
    codes = sorted(rows)
    df = pd.DataFrame({key: codes})
    for name, j in cols.items():
        df[name] = pd.array([rows[c][j] if rows[c][j] is not None else float("nan") for c in codes], dtype="float64")
    return df


def _sx_col(rows: dict, codes, j: int) -> "pd.array":
    """Position j of each code's cells, NaN where withheld or missing."""
    def get(c):
        r = rows.get(c) or []
        return r[j] if len(r) > j and r[j] is not None else float("nan")
    return pd.array([get(c) for c in codes], dtype="float64")


def _sx_need(source: str, df: "pd.DataFrame", col: str) -> None:
    if df.empty or col not in df.columns or df[col].notna().sum() == 0:
        raise StatXploreError(f"{source}: Stat-Xplore gave no {col} figure for any area in scope")


def _sx_run(source: str, title: str, build) -> "pd.DataFrame | None":
    """Run one Stat-Xplore source: skipped with a note when there is no key,
    otherwise build(sx) -> (frame, summary), or None when it set its own
    note, and the frame written to data/economy/."""
    rule(title)
    key = statxplore_key()
    if not key:
        _SOURCE_NOTE[source] = SX_NO_KEY_NOTE
        info(f"{source}: skipped, {SX_NO_KEY_NOTE}")
        return None
    sx = StatXplore(key, source=source)
    res = build(sx)
    if res is None:
        return None
    df, summary = res
    path = DATA_DIR / "economy" / SX_PARQUETS[source]
    path.parent.mkdir(parents=True, exist_ok=True)
    out = write_parquet_guarded(path, df, source=source)
    ok(f"{source}: {summary} ({sx.requests} requests) -> {path.relative_to(REPO_ROOT)}")
    return out


def _sx_simple(sx: StatXplore, col: str, what: str) -> tuple:
    """One count per LSOA at the newest period: Attendance Allowance,
    Carer's Allowance, Pension Credit."""
    ds = statxplore_dataset(sx, **SX_DATABASES[sx.source])
    counts, facts = statxplore_lsoa_counts(sx, ds)
    df = _sx_frame(counts, {col: 0})
    df[f"{col}_period"] = ds["period_label"]
    df["lsoa_vintage"] = facts["vintage"]
    _sx_need(sx.source, df, col)
    return df, f"{len(df):,} LSOAs, {ds['period_label']}, {df[col].sum():,.0f} {what}"


def run_dwp_uc_people() -> "pd.DataFrame | None":
    def build(sx):
        ds = statxplore_dataset(sx, **SX_DATABASES["dwp_uc_people"])
        counts, facts = statxplore_lsoa_counts(sx, ds)
        df = _sx_frame(counts, {"uc_people": 0})
        df["uc_people_period"] = ds["period_label"]
        df["lsoa_vintage"] = facts["vintage"]
        _sx_need(sx.source, df, "uc_people")
        return df, f"{len(df):,} LSOAs, {ds['period_label']}, {df['uc_people'].sum():,.0f} people on Universal Credit"
    return _sx_run("dwp_uc_people", "DWP Stat-Xplore: people on Universal Credit (LSOA)", build)


def run_dwp_uc_households() -> "pd.DataFrame | None":
    """Households on Universal Credit, those with a child element, and those
    with limited capability for work (LCW or LCWRA), from one database. The
    two splits were confirmed in the fact check, so a missing one marks the
    source failed, with the rest written."""
    def build(sx):
        src = sx.source
        ds = statxplore_dataset(sx, **SX_DATABASES[src])
        filters = []
        pay = statxplore_filter(sx, ds, r"(?i)\bpayment\b", r"(?i)^\s*in payment\s*$")
        if pay:
            filters.append(pay[:2])
            info(f"{src}: counting households in payment ({pay[2]})")
        else:
            info(f"{src}: no field with an \"In payment\" value, so every household on Universal Credit is counted")
        problems = []
        child = statxplore_groups(sx, ds, r"(?i)^\s*child entitlement", {"children": r"(?i)\bchild"}, drop=SX_NOT)
        if child:
            fid, groups, index, labels, flabel = child
            info(f"{src}: children from {flabel!r} ({fid}): {', '.join(labels['children'])}")
            counts, facts = statxplore_lsoa_counts(sx, ds, split=(fid, groups), filters=filters)
            rows = {c: [r[index["children"]], _sx_sum(r)] for c, r in counts.items()}
        else:
            problems.append("Stat-Xplore has no Child Entitlement field in this database any more, so UC "
                            "households with children are left out")
            counts, facts = statxplore_lsoa_counts(sx, ds, filters=filters)
            rows = {c: [None, r[0]] for c, r in counts.items()}
        df = _sx_frame(rows, {"uc_hh_children": 0, "uc_hh": 1})
        df["uc_hh_period"] = ds["period_label"]
        lcw = statxplore_groups(sx, ds, r"(?i)limited capability for work", {"lcw": r"(?i)\blcw|limited capability"},
                                drop=SX_NOT)
        if lcw:
            fid, groups, index, labels, flabel = lcw
            dv, per = statxplore_pick_period(ds["date_values"], SX_LCW_LAG_MONTHS)
            info(f"{src}: limited capability for work from {flabel!r} ({fid}): {', '.join(labels['lcw'])}, "
                 f"at {statxplore_period_label(per)} ({dv['id']}), {SX_LCW_LAG_MONTHS} months before the latest, "
                 "because assessments are backdated")
            lc, _f = statxplore_lsoa_counts(sx, ds, split=(fid, groups), filters=filters, date_value=dv["id"])
            df["uc_hh_lcw"] = _sx_col(lc, df["LSOA21CD"], index["lcw"])
            df["uc_hh_lcw_period"] = statxplore_period_label(per)
        else:
            problems.append("Stat-Xplore has no Limited Capability for Work Entitlement field in this database "
                            "any more, so those households are left out")
        df["uc_hh_in_payment"] = bool(pay)
        df["lsoa_vintage"] = facts["vintage"]
        _sx_need(src, df, "uc_hh")
        if problems:
            _SOURCE_ERROR[src] = "; ".join(problems)
            warn(f"{src}: {_SOURCE_ERROR[src]}")
        return df, (f"{len(df):,} LSOAs, {ds['period_label']}, {df['uc_hh'].sum():,.0f} households on Universal Credit"
                    + (f", {df['uc_hh_children'].sum():,.0f} with children" if child else "")
                    + (f", {df['uc_hh_lcw'].sum():,.0f} with limited capability for work at "
                       f"{df['uc_hh_lcw_period'].iloc[0]}" if lcw else ""))
    return _sx_run("dwp_uc_households", "DWP Stat-Xplore: households on Universal Credit (LSOA)", build)


def run_dwp_pip() -> "pd.DataFrame | None":
    """PIP cases with entitlement, and by main disabling condition where
    Stat-Xplore crosses it with small areas (not confirmed, so its absence is
    a note, not a failure)."""
    def build(sx):
        src = sx.source
        ds = statxplore_dataset(sx, **SX_DATABASES[src])
        counts, facts = statxplore_lsoa_counts(sx, ds)
        df = _sx_frame(counts, {"pip": 0})
        df["pip_period"] = ds["period_label"]
        df["lsoa_vintage"] = facts["vintage"]
        _sx_need(src, df, "pip")
        cond = statxplore_groups(sx, ds, SX_PIP_CONDITION_FIELD, SX_PIP_CONDITIONS,
                                 avoid_field=r"(?i)sub.?(?:category|group)|specific")
        got = []
        if not cond:
            _SOURCE_NOTE[src] = ("Stat-Xplore offers no main disabling condition field here, so PIP by "
                                 "condition is left out")
        else:
            fid, groups, index, labels, flabel = cond
            info(f"{src}: conditions from {flabel!r} ({fid}): "
                 + "; ".join(f"{n}: {', '.join(v)}" for n, v in labels.items()))
            try:
                cc, _f = statxplore_lsoa_counts(sx, ds, split=(fid, groups))
            except StatXploreError as e:
                if "refused the API key" in str(e):
                    raise
                cc = None
                _SOURCE_NOTE[src] = (f"the table of PIP by main disabling condition for LSOAs could not be "
                                     f"read, so PIP by condition is left out ({e})")
                warn(f"{src}: {_SOURCE_NOTE[src]}")
            if cc is not None:
                for name in SX_PIP_CONDITIONS:
                    if name in index:
                        df[f"pip_{name}"] = _sx_col(cc, df["LSOA21CD"], index[name])
                        got.append(name)
                gone = [n for n in SX_PIP_CONDITIONS if n not in index]
                if gone:
                    _SOURCE_NOTE[src] = (f"no category matched {', '.join(gone)} in {flabel!r}, so "
                                         "those are left out")
        return df, (f"{len(df):,} LSOAs, {ds['period_label']}, {df['pip'].sum():,.0f} people entitled to PIP"
                    + (f", by condition: {', '.join(got)}" if got else ""))
    return _sx_run("dwp_pip", "DWP Stat-Xplore: Personal Independence Payment (LSOA, for wards and LAs)", build)


def run_dwp_dla_children() -> "pd.DataFrame | None":
    """Children aged 0 to 15 with Disability Living Allowance, through the
    database's age field (a table without it would count adults too)."""
    def build(sx):
        ds = statxplore_dataset(sx, **SX_DATABASES[sx.source])
        age = statxplore_ages(sx, ds, 15)
        counts, facts = statxplore_lsoa_counts(sx, ds, filters=[age[:2]])
        df = _sx_frame(counts, {"dla_child": 0})
        df["dla_child_period"] = ds["period_label"]
        df["lsoa_vintage"] = facts["vintage"]
        _sx_need(sx.source, df, "dla_child")
        return df, (f"{len(df):,} LSOAs, {ds['period_label']}, {df['dla_child'].sum():,.0f} children aged 0 to 15 "
                    "receiving DLA")
    return _sx_run("dwp_dla_children", "DWP Stat-Xplore: Disability Living Allowance, children (LSOA, for wards and LAs)",
                   build)


def run_dwp_attendance_allowance() -> "pd.DataFrame | None":
    return _sx_run("dwp_attendance_allowance", "DWP Stat-Xplore: Attendance Allowance (LSOA, for wards and LAs)",
                   lambda sx: _sx_simple(sx, "aa", "Attendance Allowance claimants"))


def run_dwp_carers_allowance() -> "pd.DataFrame | None":
    return _sx_run("dwp_carers_allowance", "DWP Stat-Xplore: Carer's Allowance in payment (LSOA, for wards and LAs)",
                   lambda sx: _sx_simple(sx, "ca", "Carer's Allowance claimants in payment"))


def run_dwp_pension_credit() -> "pd.DataFrame | None":
    return _sx_run("dwp_pension_credit", "DWP Stat-Xplore: Pension Credit (LSOA, for wards and LAs)",
                   lambda sx: _sx_simple(sx, "pc", "Pension Credit claims"))


def run_dwp_cilif() -> "pd.DataFrame | None":
    """Children aged 0 to 15 in relative low income families, by ward. Used
    only for LAs all of whose 2025 wards are in DWP's ward list by code: a
    ward of another vintage is a different patch, and is not forced onto the
    map's wards (HANDOVER.md says what would be needed)."""
    def build(sx):
        src = sx.source
        ds = statxplore_dataset(sx, **SX_DATABASES[src])
        age = statxplore_ages(sx, ds, 15)
        vals = statxplore_values(sx, ds["geo_valueset"])
        codes: dict = {}
        for v in vals:
            c = statxplore_code(v, "ward")
            if c and c not in codes:
                codes[c] = v["id"]
        if not codes:
            raise StatXploreError(f"{src}: the ward level ({ds['geo_label']!r}) holds no ward codes")
        by_la: dict = {}
        for wd, lad in _ward_to_lad().items():
            if lad in SCOPE_LADS:
                by_la.setdefault(lad, []).append(wd)
        complete = {la: sorted(wds) for la, wds in by_la.items() if all(w in codes for w in wds)}
        n_scope = sum(len(w) for w in by_la.values())
        n_found = sum(1 for wds in by_la.values() for w in wds if w in codes)
        years = sorted(set(re.findall(r"(?:19|20)\d{2}", ds["geo_label"])))
        vint = f" (labelled {', '.join(years)})" if years else ""
        info(f"{src}: DWP's wards{vint} hold {n_found:,} of this scope's {n_scope:,} 2025 wards by code; "
             f"{len(complete)} of {len(by_la)} LAs have every ward")
        if not complete:
            _SOURCE_NOTE[src] = (f"DWP's ward geography{vint} holds {n_found:,} of this scope's {n_scope:,} 2025 "
                                 "wards, and no LA has all of them, so it is an older ward vintage and is not "
                                 "forced onto the map's wards (HANDOVER.md, DWP benefits)")
            warn(f"{src}: {_SOURCE_NOTE[src]}")
            return None
        if len(complete) < len(by_la):
            left = sorted(set(by_la) - set(complete))
            _SOURCE_NOTE[src] = (f"{len(left)} LA(s) left out because DWP's wards{vint} are not their 2025 wards: "
                                 + ", ".join(LAD_NAMES.get(la, la) for la in left))
            warn(f"{src}: {_SOURCE_NOTE[src]}")
        ids = {w: codes[w] for wds in complete.values() for w in wds}
        raw = statxplore_counts(sx, ds, ids, filters=[age[:2]])
        recs = [{"WD25CD": w, "LAD25CD": la,
                 "cilif_rel_0_15": (raw.get(w) or [None])[0]} for la, wds in complete.items() for w in wds]
        df = pd.DataFrame(recs).sort_values("WD25CD").reset_index(drop=True)
        df["cilif_rel_0_15"] = pd.to_numeric(df["cilif_rel_0_15"], errors="coerce").astype("float64")
        df["cilif_period"] = ds["period_label"]
        _sx_need(src, df, "cilif_rel_0_15")
        return df, (f"{len(df):,} wards in {len(complete)} LAs, {ds['period_label']}, "
                    f"{df['cilif_rel_0_15'].sum():,.0f} children aged 0 to 15 in relative low income families")
    return _sx_run("dwp_cilif", "DWP Stat-Xplore: children in relative low income families (ward)", build)


# ---------------------------------------------------------------------------
# Ward, LA and LSOA figures for the DWP benefits
# ---------------------------------------------------------------------------
# key: (source, count column, base, also for LSOAs, period column). The bases
# are named in DWP_BASES; "uc_hh" is the source's own column of all UC
# households, the base of the share of them with children.
DWP_SHARES = {
    "dwp_uc_people_pct":          ("dwp_uc_people", "uc_people", "age16_64", True, "uc_people_period"),
    "dwp_uc_hh_pct":              ("dwp_uc_households", "uc_hh", "households", True, "uc_hh_period"),
    "dwp_uc_hh_lcw_pct":          ("dwp_uc_households", "uc_hh_lcw", "households", True, "uc_hh_lcw_period"),
    "dwp_uc_hh_children_pct":     ("dwp_uc_households", "uc_hh_children", "uc_hh", True, "uc_hh_period"),
    "dwp_pip_pct":                ("dwp_pip", "pip", "age16plus", False, "pip_period"),
    "dwp_pip_psych_pct":          ("dwp_pip", "pip_psych", "age16plus", False, "pip_period"),
    "dwp_pip_msk_pct":            ("dwp_pip", "pip_msk", "age16plus", False, "pip_period"),
    "dwp_pip_neuro_pct":          ("dwp_pip", "pip_neuro", "age16plus", False, "pip_period"),
    "dwp_dla_child_pct":          ("dwp_dla_children", "dla_child", "age0_15", False, "dla_child_period"),
    "dwp_aa_pct":                 ("dwp_attendance_allowance", "aa", "age66plus", False, "aa_period"),
    "dwp_carers_pct":             ("dwp_carers_allowance", "ca", "age16plus", False, "ca_period"),
    "dwp_pension_credit_per_100": ("dwp_pension_credit", "pc", "age66plus", False, "pc_period"),
}
DWP_CILIF_KEY = "dwp_cilif_pct"
DWP_BASES = {
    "households": "households at the 2021 Census",
    "age0_15": "children aged 0 to 15 (ONS mid-year estimates)",
    "age16_64": "residents aged 16 to 64 (ONS mid-year estimates)",
    "age16plus": "residents aged 16 and over (ONS mid-year estimates)",
    "age66plus": "residents aged 66 and over (ONS mid-year estimates)",
    "uc_hh": "households on Universal Credit",
}
DWP_SOURCE_NAMES = {
    "dwp_uc_people": "DWP Stat-Xplore, people on Universal Credit",
    "dwp_uc_households": "DWP Stat-Xplore, households on Universal Credit",
    "dwp_pip": "DWP Stat-Xplore, Personal Independence Payment",
    "dwp_dla_children": "DWP Stat-Xplore, Disability Living Allowance",
    "dwp_attendance_allowance": "DWP Stat-Xplore, Attendance Allowance",
    "dwp_carers_allowance": "DWP Stat-Xplore, Carer's Allowance",
    "dwp_pension_credit": "DWP Stat-Xplore, Pension Credit",
    "dwp_cilif": "DWP Stat-Xplore, children in low income families",
}


def _dwp_frames() -> dict:
    """{source: {LSOA21CD: {column: value}}} for the DWP parquets on disk."""
    cols: dict = {}
    for _k, (src, col, _base, _l, pcol) in DWP_SHARES.items():
        cols.setdefault(src, set()).update((col, pcol, "uc_hh"))
    out = {}
    for src, cs in cols.items():
        fr = _lsoa_frame(DATA_DIR / "economy" / SX_PARQUETS[src], sorted(cs))
        if fr:
            out[src] = fr
    return out


def dwp_bases(need) -> dict:
    """{base: {LSOA21CD: count}} for the bases in need: households from the
    Census counts, ages from the mid-year estimates. A base whose file or
    column is missing is left out, and its figures with it."""
    out: dict = {}
    if "households" in need:
        hh = census_households_by_lsoa(_read_parquet_opt(DATA_DIR / "demographics" / "census2021_counts.parquet"))
        if hh:
            out["households"] = hh
    if need & {"age0_15", "age16_64", "age16plus", "age66plus"}:
        sape = _lsoa_frame(DATA_DIR / "demographics" / SAPE_PARQUET,
                           ("sape_age0_15", "sape_age16_64", "sape_age65plus", "sape_age66plus"))

        def col(c):
            return {lc: float(r[c]) for lc, r in sape.items() if c in r and r[c] is not None and pd.notna(r[c])}
        a, b = col("sape_age16_64"), col("sape_age65plus")
        for name, vals in (("age0_15", col("sape_age0_15")), ("age16_64", a), ("age66plus", col("sape_age66plus")),
                           ("age16plus", {lc: a[lc] + b[lc] for lc in a if lc in b})):
            if vals:
                out[name] = vals
    return out


def dwp_summed_rate(num: dict, den: dict, to_area: dict, *, scale: float = 100.0, ndigits: int = 1,
                    min_cover: float = WARD_MIN_COVER) -> dict:
    """{area: scale * summed count / summed base} over the LSOAs of each area.
    An LSOA whose count is withheld (None or NaN) or missing is left out of the
    count and the base alike, and an area whose LSOAs with a count hold less
    than min_cover of its base gets no figure, rather than the figure of a
    corner of it."""
    n_sum: dict = {}
    d_sum: dict = {}
    d_all: dict = {}
    for lc, area in to_area.items():
        if not area:
            continue
        d = den.get(lc)
        if d is None or pd.isna(d) or float(d) <= 0:
            continue
        d_all[area] = d_all.get(area, 0.0) + float(d)
        n = num.get(lc)
        if n is None or pd.isna(n):
            continue
        n_sum[area] = n_sum.get(area, 0.0) + float(n)
        d_sum[area] = d_sum.get(area, 0.0) + float(d)
    return {a: round(scale * n_sum[a] / d, ndigits) for a, d in d_sum.items()
            if d > 0 and d >= min_cover * d_all[a]}


def dwp_cilif_figures(to_area: dict, *, level: str, age0_15: dict) -> tuple[dict, str]:
    """({area: % of children aged 0 to 15 in relative low income families},
    period). A ward's count is DWP's own, over the children of the LSOAs
    that best fit it; an LA's is its wards' counts added up (only where none
    is withheld) over the children of all its LSOAs."""
    path = DATA_DIR / "economy" / SX_PARQUETS["dwp_cilif"]
    cf = _read_parquet_opt(path) if path.exists() else None
    if cf is None or cf.empty or not {"WD25CD", "LAD25CD", "cilif_rel_0_15"}.issubset(cf.columns):
        return {}, ""
    per = str(cf["cilif_period"].dropna().iloc[0]) if "cilif_period" in cf.columns and cf["cilif_period"].notna().any() else ""
    if not age0_15:
        warn("dwp_cilif: no children aged 0 to 15 from the mid-year estimates (sape), so the "
             "low income figure has no base and is left out")
        return {}, per
    den: dict = {}
    for lc, area in to_area.items():
        d = age0_15.get(lc)
        if area and d is not None and pd.notna(d):
            den[area] = den.get(area, 0.0) + float(d)
    num: dict = {}
    v = pd.to_numeric(cf["cilif_rel_0_15"], errors="coerce")
    if level == "ward":
        num = {str(w): float(x) for w, x in zip(cf["WD25CD"], v) if pd.notna(x)}
    else:
        for la, g in v.groupby(cf["LAD25CD"].astype(str)):
            if g.notna().all():
                num[la] = float(g.sum())
    return {a: round(100.0 * n / den[a], 1) for a, n in num.items() if den.get(a, 0) > 0}, per


def dwp_area_figures(to_area: dict, *, what: str, level: str) -> tuple[dict, dict]:
    """({area: {key: value}}, notes) for the DWP benefits, for wards (to_area
    the LSOA to ward best fit, level "ward") or LAs (every LSOA to its LA):
    the LSOAs' counts added up over their bases added up (dwp_summed_rate).
    notes["dwp"] holds each source's period."""
    out: dict = {}
    notes: dict = {}
    frames = _dwp_frames()
    cil = DATA_DIR / "economy" / SX_PARQUETS["dwp_cilif"]
    if not frames and not cil.exists():
        return out, notes
    need = {base for src, _c, base, _l, _p in DWP_SHARES.values() if src in frames}
    if cil.exists():
        need.add("age0_15")
    bases = dwp_bases(need)
    for key, (src, col, base, _lsoa, pcol) in DWP_SHARES.items():
        fr = frames.get(src)
        if not fr:
            continue
        num = {lc: r.get(col) for lc, r in fr.items() if col in r}
        if not any(x is not None and pd.notna(x) for x in num.values()):
            continue
        den = {lc: r.get("uc_hh") for lc, r in fr.items()} if base == "uc_hh" else bases.get(base) or {}
        if not den:
            warn(f"{what}: {key} has no base ({DWP_BASES[base]}), so it is left out rather than "
                 "divided by something else")
            continue
        for a, val in dwp_summed_rate(num, den, to_area).items():
            out.setdefault(a, {})[key] = val
        per = next((str(r[pcol]) for r in fr.values() if r.get(pcol)), "")
        notes.setdefault("dwp", {})[src] = per
    cvals, cper = dwp_cilif_figures(to_area, level=level, age0_15=bases.get("age0_15") or {})
    for a, val in cvals.items():
        out.setdefault(a, {})[DWP_CILIF_KEY] = val
    if cvals:
        notes.setdefault("dwp", {})["dwp_cilif"] = cper
    return out, notes


def dwp_lsoa_values() -> dict:
    """{LSOA21CD: {key: value}} for the DWP shares shown for LSOAs (Universal
    Credit). An LSOA whose count is withheld has no figure."""
    out: dict = {}
    frames = _dwp_frames()
    if not frames:
        return out
    need = {base for src, _c, base, lsoa, _p in DWP_SHARES.values() if src in frames and lsoa}
    bases = dwp_bases(need)
    for key, (src, col, base, lsoa, _p) in DWP_SHARES.items():
        fr = frames.get(src)
        if not lsoa or not fr:
            continue
        den = {lc: r.get("uc_hh") for lc, r in fr.items()} if base == "uc_hh" else bases.get(base) or {}
        for lc, r in fr.items():
            n, d = r.get(col), den.get(lc)
            if n is None or d is None or pd.isna(n) or pd.isna(d) or float(d) <= 0:
                continue
            out.setdefault(lc, {})[key] = round(100.0 * float(n) / float(d), 1)
    return out


def dwp_periods() -> dict:
    """{key: period} for the DWP figures, for indicators.js."""
    out: dict = {}
    for key, (src, col, _base, _l, pcol) in DWP_SHARES.items():
        path = DATA_DIR / "economy" / SX_PARQUETS[src]
        df = _read_parquet_opt(path) if path.exists() else None
        if df is not None and col in df.columns and pcol in df.columns and df[col].notna().any() \
                and df[pcol].notna().any():
            out[key] = str(df[pcol].dropna().iloc[0])
    path = DATA_DIR / "economy" / SX_PARQUETS["dwp_cilif"]
    cf = _read_parquet_opt(path) if path.exists() else None
    if cf is not None and "cilif_period" in cf.columns and cf["cilif_period"].notna().any():
        out[DWP_CILIF_KEY] = str(cf["cilif_period"].dropna().iloc[0])
    return out


SOURCES = {
    "custom":      run_custom,
    "boundaries":  run_boundaries,
    "greenblue":   run_greenblue,
    "air_quality": run_air_quality,
    "tfl":         run_tfl_transport,
    "naptan":      run_naptan_transport,
    "gp":          run_gp_practices,
    "pcn":         run_pcn,
    "pharmacies":  run_pharmacies,
    "imd":         run_imd2025,
    # After imd: run_iod_indicators checks each indicator against its
    # domain score in imd2025.parquet.
    "iod_extra":   run_iod_extra,
    "iod_indicators": run_iod_indicators,
    "census":      run_census2021,
    "census_oa":   run_census_oa,
    "claimant":    run_claimant_count,
    "qof":         run_qof,
    "fingertips":  run_fingertips,
    "fingertips_msoa": run_fingertips_msoa,
    "fingertips_msoa_la": run_fingertips_msoa_la,
    "fingertips_profiles": run_fingertips_profiles,
    # New series in sources of their own, so one that fails cannot mark an
    # established source partial or failed (which stops region packs).
    "fingertips_msoa_extra": run_fingertips_msoa_extra,
    "saie":        run_saie,
    "hle_msoa":    run_hle_msoa,
    "fuel_poverty": run_fuel_poverty,
    "ptal":        run_ptal,
    "crime":       run_police_crime,
    "hospitals":   run_hospitals,
    "charities":   run_charities,
    "dentists":    run_dentists,
    # The small-area additions, each in its own manifest entry. After the GP,
    # pharmacy and dental registers, which leave the national site files
    # service_access measures to.
    "sape":        run_sape,
    "claimant_youth": run_claimant_youth,
    "road_casualties": run_road_casualties,
    "care_homes":  run_care_homes,
    "service_access": run_service_access,
    # DWP benefits from Stat-Xplore, one entry each, so one that fails never
    # hides the others. After sape, whose estimates are their bases. Each is
    # "skipped", with a note, until STAT_XPLORE_API_KEY is set.
    "dwp_uc_people": run_dwp_uc_people,
    "dwp_uc_households": run_dwp_uc_households,
    "dwp_pip":     run_dwp_pip,
    "dwp_dla_children": run_dwp_dla_children,
    "dwp_attendance_allowance": run_dwp_attendance_allowance,
    "dwp_carers_allowance": run_dwp_carers_allowance,
    "dwp_pension_credit": run_dwp_pension_credit,
    "dwp_cilif":   run_dwp_cilif,
}

def write_manifest(source_records: dict) -> None:
    """
    Merge this run's per-source records into data/meta/manifest.json.

    Merged rather than replaced because a partial run (--only gp) must not
    erase what the other sources reported last time. Sources that did not run
    keep their previous entry untouched.
    """
    existing: dict = {}
    if MANIFEST_PATH.exists():
        try:
            existing = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            warn(f"manifest: existing file unreadable ({e}); starting a fresh one")
            existing = {}
    sources = existing.get("sources")
    if not isinstance(sources, dict):
        sources = {}
    # Drop entries for keys that are not current sources. The file predates this
    # writer and carries names from an older scheme (imd2025 rather than imd)
    # with no status field, which would otherwise be reported forever.
    stale = [k for k in sources if k not in SOURCES]
    for k in stale:
        sources.pop(k)
    if stale:
        info(f"manifest: dropped {len(stale)} entry(ies) for unknown sources: "
             + ", ".join(sorted(stale)))
    sources.update(source_records)

    write_json_atomic(MANIFEST_PATH, {
        "last_run": datetime.now(timezone.utc).isoformat(),
        "sources": sources,
    }, pretty=True)

    failed = sorted(k for k, v in source_records.items() if v.get("status") == "failed")
    if failed:
        warn(f"manifest: {len(failed)} source(s) failed this run: {', '.join(failed)}")
    ok(f"manifest -> {MANIFEST_PATH.relative_to(REPO_ROOT)}")

def main() -> int:
    p = argparse.ArgumentParser(
        description="Fetch + aggregate NW London population health data.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Sources: " + ", ".join(SOURCES) + "\n"
            "Each source caches its raw files in .cache/<source>/ — safe to rerun."
        ),
    )
    p.add_argument("--only", nargs="+", choices=list(SOURCES),
                   help="Only run the named sources (default: all).")
    p.add_argument("--skip", nargs="+", choices=list(SOURCES), default=[],
                   help="Skip the named sources.")
    p.add_argument("--scope", default=SCOPE_ID,
                   help="Which scopes/<name>.json to build (default london). Read at "
                        "import time; listed here so --help shows it.")
    p.add_argument("--export-only", action="store_true",
                   help="Skip all fetches; just rebuild ward/lsoa/pharmacy JSON "
                        "from the existing parquets and rewrite data/map/.")
    args = p.parse_args()

    if not args.export_only:
        to_run = list(SOURCES) if not args.only else args.only
        to_run = [s for s in to_run if s not in args.skip]
        info(f"Running sources: {', '.join(to_run)}")
        start = time.time()

        global _MANIFEST_SOURCE
        records: dict = {}
        for s in to_run:
            _MANIFEST_SOURCE = s
            _MANIFEST_WRITES.pop(s, None)
            _SOURCE_PARTIAL.pop(s, None)
            _SOURCE_ERROR.pop(s, None)
            _SOURCE_NOTE.pop(s, None)
            _SOURCE_SKIPPED.pop(s, None)
            t0 = time.time()
            rec: dict = {"status": "ok", "error": "", "notes": "",
                         "fetched_at": datetime.now(timezone.utc).isoformat(),
                         "output_path": "", "rows_written": 0, "schema": {}}
            try:
                df = SOURCES[s]()
                if df is None:
                    # An optional source that had nothing to do, e.g. hospitals
                    # with no Hospital.csv. Not a failure, but not a success
                    # either, so it is distinguishable in the manifest.
                    rec["status"] = "skipped"
                    rec["notes"] = (_SOURCE_SKIPPED.get(s) or _SOURCE_NOTE.get(s)
                                    or "source returned no frame")
                elif hasattr(df, "columns"):
                    rec["rows_written"] = int(len(df))
                    rec["schema"] = {str(c): str(t) for c, t in df.dtypes.items()}
                    if len(df) == 0:
                        # Several sources report total upstream failure by
                        # warning and returning an empty frame rather than
                        # raising, so an exception is not the only failure
                        # signal. Without this a wholly dead source reports ok
                        # and the refresh workflow stays silent about it.
                        rec["status"] = "failed"
                        rec["error"] = ("source produced no rows; see the run "
                                        "log for the upstream error")
                    elif s in _SOURCE_ERROR:
                        rec["status"] = "failed"
                        rec["error"] = _SOURCE_ERROR[s]
                    elif s in _SOURCE_PARTIAL:
                        # Usable, but some of it is last run's. Not ok, so the
                        # refresh workflow raises it; not failed, because the
                        # map is right, just partly older than the date says.
                        rec["status"] = "partial"
                        rec["notes"] = _SOURCE_PARTIAL[s]
                    elif s in _SOURCE_NOTE:
                        rec["notes"] = _SOURCE_NOTE[s]
            except Exception as e:
                rec["status"] = "failed"
                rec["error"] = f"{type(e).__name__}: {e}"
                err(f"{s} failed: {type(e).__name__}: {e}")
                # Keep going - we'd rather have partial outputs than zero.
            finally:
                rec["duration_s"] = round(time.time() - t0, 1)
                written = _MANIFEST_WRITES.get(s) or []
                if written:
                    rec["output_path"] = written[0][0]
                    # A guarded write reports what actually landed on disk,
                    # which is what the previous file held if an empty frame
                    # was refused. Prefer that over the in-memory row count.
                    rec["rows_written"] = written[0][1]
                    if len(written) > 1:
                        rec["notes"] = (rec["notes"] + " " if rec["notes"] else "") + \
                            f"also wrote {', '.join(w[0] for w in written[1:])}"
                records[s] = rec
        _MANIFEST_SOURCE = None
        info(f"Fetch phase done in {time.time() - start:.1f}s")
        write_manifest(records)

    export_all()
    print()
    ok("All done. Refresh index.html in your browser.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
