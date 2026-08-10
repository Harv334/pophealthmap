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
only files you can drop in .cache/ are these, and both are optional:

  .cache/imd2025/File_7_IoD2025_All_Ranks_...csv  [only to rebuild]
      Index of Multiple Deprivation 2025 - File 7 (all domains). IMD is static
      between releases, so data/demographics/imd2025.parquet is committed and
      used as-is. Drop the raw CSV here only when a new IoD is published and
      the parquet needs regenerating. See IMD_SOURCE_URL below.

  .cache/hospitals/Hospital.csv                   [optional]
      NHS.uk dataset. https://www.nhs.uk/about-us/nhs-website-datasets/
      If missing, hospitals simply won't render on the map.

No cache needed - the script hits these APIs directly (cached between runs):
  - OHID Fingertips      (health outcomes per LAD)
  - data.police.uk       (crime per borough polygon per month)
  - Nomis Census 2021    (topic-summary tables, ~150 MB first run, cached)
  - postcodes.io         (postcode -> LSOA / ward / borough + coordinates)
  - ONS Open Geography   (LSOA 2021 -> ward 2025 best-fit lookup)
  - NHS ODS Data Search  (epraccur GP + edispensary pharmacy registers,
                          both ETag-revalidated so an unchanged file is a 304)

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
import zipfile
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
# SCOPE: the 33 London local authorities (32 boroughs + the City of London).
# Codes and names are the ONS LAD25 set, taken from LAD_MAY_2025_UK_BGC_V2.
# This single list is what defines the map's footprint. Everything else, the
# boundaries, the LSOA set, the ward set, the facility filters, derives from it.
# ============================================================================
SCOPE_NAME = "London"
BOROUGHS = [
    ("City of London",         "E09000001"), ("Barking and Dagenham",   "E09000002"),
    ("Barnet",                 "E09000003"), ("Bexley",                 "E09000004"),
    ("Brent",                  "E09000005"), ("Bromley",                "E09000006"),
    ("Camden",                 "E09000007"), ("Croydon",                "E09000008"),
    ("Ealing",                 "E09000009"), ("Enfield",                "E09000010"),
    ("Greenwich",              "E09000011"), ("Hackney",                "E09000012"),
    ("Hammersmith and Fulham", "E09000013"), ("Haringey",               "E09000014"),
    ("Harrow",                 "E09000015"), ("Havering",               "E09000016"),
    ("Hillingdon",             "E09000017"), ("Hounslow",               "E09000018"),
    ("Islington",              "E09000019"), ("Kensington and Chelsea", "E09000020"),
    ("Kingston upon Thames",   "E09000021"), ("Lambeth",                "E09000022"),
    ("Lewisham",               "E09000023"), ("Merton",                 "E09000024"),
    ("Newham",                 "E09000025"), ("Redbridge",              "E09000026"),
    ("Richmond upon Thames",   "E09000027"), ("Southwark",              "E09000028"),
    ("Sutton",                 "E09000029"), ("Tower Hamlets",          "E09000030"),
    ("Waltham Forest",         "E09000031"), ("Wandsworth",             "E09000032"),
    ("Westminster",            "E09000033"),
]
SCOPE_LADS = {b[1] for b in BOROUGHS}
LAD_NAMES = {b[1]: b[0] for b in BOROUGHS}

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
        "Accept-Encoding": "gzip, deflate, br",
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
                          marker: tuple[int, str, int] | None = None) -> int:
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
                     marker: tuple[int, str, int] | None = None) -> Path:
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

    info(f"ONS lookup: fetching {ONS_LOOKUP_LAYER} for {len(SCOPE_LADS)} boroughs")
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
    write_json_atomic(ONS_LOOKUP_CACHE, {k: list(v) for k, v in lookup.items()})
    ok(f"ONS lookup: {len(lookup):,} LSOAs across {len(SCOPE_LADS)} boroughs")
    return lookup

def get_lsoa_to_ward() -> dict:
    """{LSOA21CD: WD25CD}: the mapping used by the ward-level aggregations."""
    return {lc: wd for lc, (wd, _lad) in get_lsoa_ward_lookup().items()}

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

    owners = {normalise_postcode(r["Postcode"]): (r["Name"] or "").title()
              for _, r in df.iterrows() if normalise_postcode(r["Postcode"])}
    lookup = lookup_postcodes(owners.keys(), source="gp_practices", owners=owners)

    rows = []
    for _, r in df.iterrows():
        pc = normalise_postcode(r["Postcode"])
        hit = lookup.get(pc)
        if not hit:
            continue
        lat, lng, lsoa, lad, wd = hit
        if lad not in SCOPE_LADS:
            continue
        rows.append({
            "code": r["OrganisationCode"],
            "name": (r["Name"] or "").title(),
            "addr": ", ".join(filter(None, [
                (r["AddressLine1"] or "").title(),
                (r["AddressLine2"] or "").title(),
                (r["AddressLine3"] or "").title(),
            ])),
            "postcode": r["Postcode"],
            "tel": r["ContactTelephoneNumber"],
            "lat": lat, "lng": lng,
            "LSOA21CD": lsoa, "WD25CD": wd, "LAD25CD": lad,
            "lad": LAD_NAMES.get(lad, ""),
        })
    out = pd.DataFrame(rows)
    out_path = DATA_DIR / "healthcare" / "gp_practices.parquet"
    out = write_parquet_guarded(out_path, out, source="gp_practices")
    ok(f"gp_practices: {len(out):,} rows -> {out_path.relative_to(REPO_ROOT)}")
    return out


# ============================================================================
# SOURCE 2: Pharmacies  (NHS ODS edispensary)
# ============================================================================
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

    owners = {normalise_postcode(r.get("Postcode", "")): (r.get("Name") or "").title()
              for _, r in df.iterrows() if normalise_postcode(r.get("Postcode", ""))}
    lookup = lookup_postcodes(owners.keys(), source="pharmacies", owners=owners)

    rows = []
    for _, r in df.iterrows():
        pc = normalise_postcode(r.get("Postcode", ""))
        hit = lookup.get(pc)
        if not hit:
            continue
        lat, lng, lsoa, lad, wd = hit
        if lad not in SCOPE_LADS:
            continue
        rows.append({
            "code": r.get("OrganisationCode", ""),
            "name": (r.get("Name") or "").title(),
            "addr": ", ".join(filter(None, [
                (r.get("AddressLine1") or "").title(),
                (r.get("AddressLine2") or "").title(),
                (r.get("AddressLine4") or "").title(),
            ])),
            "postcode": r.get("Postcode", ""),
            "tel": r.get("ContactTelephoneNumber", ""),
            "lat": lat, "lng": lng,
            "LSOA21CD": lsoa, "WD25CD": wd, "LAD25CD": lad,
        })
    out = pd.DataFrame(rows)
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
    load_boundary_index.cache_clear()
    return pd.DataFrame([{"kind": k, "features": v} for k, v in counts.items()])


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

    owners = {normalise_postcode(r["Postcode"]): (r["Name"] or "").title()
              for _, r in df.iterrows() if normalise_postcode(r["Postcode"])}
    lookup = lookup_postcodes(owners.keys(), source="dentists", owners=owners)

    by_pc: dict[str, dict] = {}
    for _, r in df.iterrows():
        pc = normalise_postcode(r["Postcode"])
        hit = lookup.get(pc)
        if not hit:
            continue
        lat, lng, lsoa, lad, wd = hit
        if lad not in SCOPE_LADS:
            continue
        by_pc[pc] = {
            "name": (r["Name"] or "").title(),
            "postcode": r["Postcode"],
            "lat": lat, "lng": lng,
            "nhs_contracted": True,          # presence in ODS means an NHS contract
            "lad_code": lad,
            "LSOA21CD": lsoa, "WD25CD": wd,
            "source": "ods",
        }
    n_ods = len(by_pc)

    # ODS only. The curated overlay used to add private practices ODS does not
    # list, and a nhs_contracted flag to tell the two apart, which is what the
    # map's NHS/private filter ran on. Both are gone: this layer is now exactly
    # the NHS dental register, which is a claim the source can support on its
    # own and which nobody has to maintain by hand.
    #
    # It costs the 215 private practices the overlay contributed.
    # data/curated/dental_practices_curated.json is still in the repo, so
    # restoring the merge is a matter of putting this block back.
    rows = list(by_pc.values())

    out = pd.DataFrame(rows)
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
#             the per-domain ranks and deciles are dropped as unused.

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

    def find_col(*kws):
        for c in df.columns:
            lc = c.lower()
            if all(k in lc for k in kws):
                return c
        return None

    code_col   = find_col("lsoa", "code")
    score_col  = find_col("multiple deprivation", "score")  or find_col("imd", "score")
    decile_col = find_col("multiple deprivation", "decile") or find_col("imd", "decile")
    rank_col   = find_col("multiple deprivation", "rank")   or find_col("imd", "rank")
    income_col = find_col("income",    "score")
    emp_col    = find_col("employment","score")
    edu_col    = find_col("education", "score")
    health_col = find_col("health",    "score")
    crime_col  = find_col("crime",     "score")
    barriers_col = find_col("barriers","score")
    env_col    = find_col("environment","score")

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
#   census_under16_pct / over65_pct TS009 (age, 18 categories)
#   census_non_white_pct           TS021  (ethnic group)
#   census_born_outside_uk_pct     TS004  (country of birth)
#   census_good_health_pct / bad   TS037  (general health)
#   census_disability_any / lot    TS038  (disability, Equality Act)
#   census_provides_unpaid_care_pct TS039
#   census_housing_deprived_pct    TS044  (household deprivation, any dim.)
#   census_no_car_pct              TS045
#   census_owned_pct / social_rented / private_rented  TS054  (tenure)
#   census_higher_managerial_pct / routine_semi_routine_pct  TS062  (NS-SEC)
#   census_unemployed_pct          TS066
#   census_no_qual_pct / level4_qual_pct  TS067

CENSUS_TABLES = [
    "TS001", "TS004", "TS007A", "TS021", "TS037", "TS038", "TS039",
    "TS044", "TS045", "TS054", "TS062", "TS066", "TS067",
    # Phase-A expansion. Only tables that ship LSOA CSV + whose contents
    # match our intended indicators remain.
    #   TS025 = Household language (English/Welsh) — household-level.
    #   TS061 = Method of travel to work — matches intent.
    # Dropped (document here):
    #   TS022 (Religion)                — no LSOA CSV
    #   TS024 (Language)                — no LSOA CSV
    #   TS041 — "Number of households" count only, not composition
    #   TS059 — Hours worked, not accommodation type
    #   TS068 — Schoolchild/student indicator, not year of arrival
    "TS025", "TS061",
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


def _fetch_census_table(tab_id: str) -> pd.DataFrame | None:
    """Cache-first download + return the LSOA CSV as a DataFrame. None on failure."""
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
            lsoa_name = next(
                (n for n in names if "lsoa" in n.lower() and n.endswith(".csv")),
                None,
            )
            if not lsoa_name:
                warn(f"  {tab_id}: no LSOA CSV inside zip ({names[:3]}...)")
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
    out = []
    for c in raw:
        is_descendant = False
        for other in raw:
            if other == c:
                continue
            if c.startswith(other + ": "):
                is_descendant = True
                break
        if not is_descendant:
            out.append(c)
    return out


def _cen_pct(df: pd.DataFrame, num_cols: list[str], den_col: str) -> "pd.Series":
    """Compute (sum(numerator) / denominator) * 100, as a float Series."""
    num = df[num_cols].apply(pd.to_numeric, errors="coerce").sum(axis=1)
    den = pd.to_numeric(df[den_col], errors="coerce").replace(0, pd.NA)
    return (num / den) * 100


def run_census2021() -> pd.DataFrame:
    rule("Census 2021 (Nomis bulk, LSOA)")
    tables: dict[str, pd.DataFrame] = {}
    for tab in CENSUS_TABLES:
        df = _fetch_census_table(tab)
        if df is not None:
            tables[tab] = df
            info(f"  {tab}: {len(df):,} rows x {len(df.columns)} cols")

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
    def attach(key: str, tab: str, num_kw_list: list[tuple], den_kw=("total",),
               den_exclude=(), num_exclude=(), silent: bool = False):
        nonlocal out
        t = tables.get(tab)
        if t is None:
            return
        cc = _cen_code_col(t)
        if not cc:
            return
        num_cols: list[str] = []
        for kws in num_kw_list:
            for c in _cen_findall(t, *kws, exclude=num_exclude):
                if c not in num_cols:
                    num_cols.append(c)
        den_col = _cen_find(t, *den_kw, exclude=den_exclude)
        if not num_cols or not den_col:
            if not silent:
                warn(f"  {tab}/{key}: num={len(num_cols)} den={den_col} - column match failed")
            return
        vals = _cen_pct(t, num_cols, den_col)
        df = pd.DataFrame({
            "LSOA21CD": t[cc].astype(str).str.strip(),
            key: vals.values,
        })
        out = out.merge(df, on="LSOA21CD", how="left")

    # TS007A age bands (LSOA-level) ---------------------------------------
    # Column names look like "Age (5 category broad age bands): Aged 0 to 15
    # years" etc. We try TS007A first; fall back to TS009 if the
    # downstream format ever reintroduces LSOA rows.
    _age_tab = "TS007A" if "TS007A" in tables else "TS009"
    attach("census_under16_pct", _age_tab, [
        ("aged 0 to 15",),
        ("aged 4 years and under",), ("aged 5 to 9",),
        ("aged 10 to 14",), ("aged 15 years",),
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
    # Derived working-age 16-64 band (computed once under16 + over65 are present).
    if "census_under16_pct" in out.columns and "census_over65_pct" in out.columns:
        wa = 100 - pd.to_numeric(out["census_under16_pct"], errors="coerce") \
                 - pd.to_numeric(out["census_over65_pct"], errors="coerce")
        out["census_working_age_pct"] = wa

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
                vals = (1 - pd.to_numeric(t[uk_col], errors="coerce")
                        / pd.to_numeric(t[tot_col], errors="coerce").replace(0, pd.NA)) * 100
                out = out.merge(pd.DataFrame({
                    "LSOA21CD": t[cc].astype(str).str.strip(),
                    "census_born_outside_uk_pct": vals.values,
                }), on="LSOA21CD", how="left")

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
            den = pd.to_numeric(t[tot_col], errors="coerce").replace(0, pd.NA)
            merge_df = pd.DataFrame({
                "LSOA21CD": t[cc].astype(str).str.strip(),
            })
            for key, col in parents.items():
                if not col:
                    warn(f"  TS021/{key}: parent column not found")
                    continue
                merge_df[key] = (pd.to_numeric(t[col], errors="coerce") / den) * 100
            # Derive non-white as 100 - white (kept for back-compat with the UI).
            if "census_white_pct" in merge_df.columns:
                merge_df["census_non_white_pct"] = 100 - merge_df["census_white_pct"]
            out = out.merge(merge_df, on="LSOA21CD", how="left")

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
            vals = (1 - pd.to_numeric(t[none_col], errors="coerce")
                    / pd.to_numeric(t[tot_col], errors="coerce").replace(0, pd.NA)) * 100
            out = out.merge(pd.DataFrame({
                "LSOA21CD": t[cc].astype(str).str.strip(),
                "census_provides_unpaid_care_pct": vals.values,
            }), on="LSOA21CD", how="left")

    # TS044 household deprivation (any of 4 dimensions) ------------------
    t = tables.get("TS044")
    if t is not None:
        cc = _cen_code_col(t)
        none_col = _cen_find(t, "not deprived in any dimension") or _cen_find(t, "not deprived")
        tot_col = _cen_find(t, "total")
        if cc and none_col and tot_col:
            vals = (1 - pd.to_numeric(t[none_col], errors="coerce")
                    / pd.to_numeric(t[tot_col], errors="coerce").replace(0, pd.NA)) * 100
            out = out.merge(pd.DataFrame({
                "LSOA21CD": t[cc].astype(str).str.strip(),
                "census_housing_deprived_pct": vals.values,
            }), on="LSOA21CD", how="left")

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
    # L1-L3 = higher managerial/professional; L7+L8 = semi-routine + routine
    attach("census_higher_managerial_pct", "TS062",
           [("l1, l2 and l3",), ("higher managerial",)])
    attach("census_routine_semi_routine_pct", "TS062",
           [("l7 ",), ("l8 ",), ("routine occupations",), ("semi-routine",)])

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

    # TS067 qualifications ----------------------------------------------
    attach("census_no_qual_pct", "TS067",
           [("no qualifications",)])
    attach("census_level4_qual_pct", "TS067",
           [("level 4 qualifications",)])

    # Note on dropped Phase-A tables (documented for future revisit):
    #   TS022 (Religion)    — Nomis bulk zip has no LSOA CSV sheet.
    #   TS024 (Language)    — Nomis bulk zip has no LSOA CSV sheet.
    #   TS041               — LSOA CSV only carries "Number of households"
    #                         (total count), not one-person/lone-parent
    #                         breakdown. Sub-category needs a different TS.
    #   TS059               — LSOA CSV is hours worked, not accommodation
    #                         type; accommodation table isn't shipped at LSOA.
    #   TS068               — LSOA CSV is schoolchild/student indicator,
    #                         not year-of-arrival. Migration-arrival tables
    #                         aren't shipped at LSOA in the bulk zips.
    # If Nomis re-ships these at LSOA in future, restore the attach() calls.

    # TS025 household language (English/Welsh) --------------------------
    # Despite the TS025 code (which in the Nomis catalogue is labelled
    # "Proficiency in English"), the bulk LSOA zip actually ships
    # household-language rows:
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

    out_path = DATA_DIR / "demographics" / "census2021.parquet"
    out = write_parquet_guarded(out_path, out, source="census")
    ok(f"census2021: {len(out):,} LSOAs x {len(out.columns)-1} indicators -> {out_path.relative_to(REPO_ROOT)}")
    return out


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
# SOURCE 3b: DWP benefits (PIP / UC / ESA / Carer's Allowance / Pension Credit)
# ----------------------------------------------------------------------------
# NOMIS mirrors the headline DWP Stat-Xplore datasets at LSOA 2021 (TYPE298).
# Pull the latest period per dataset, filter to NW London, merge into one
# parquet with count + derived per-working-age rate columns.
# ============================================================================
DWP_DATASETS = [
    # (nomis_id,     short_name,          human label)
    ("NM_208_1",     "pip_cases",         "PIP: cases in payment"),
    ("NM_210_1",     "uc_households",     "Universal Credit households on UC"),
    ("NM_209_1",     "esa_claimants",     "ESA claimants"),
    ("NM_189_1",     "carers_allowance",  "Carer's Allowance recipients"),
    ("NM_193_1",     "pension_credit",    "Pension Credit claimants (65+)"),
]

def _nomis_discover_dims(dataset_id: str, cache_dir: "Path") -> list[str]:
    """Return the list of dimension names for a NOMIS dataset, excluding
    the mandatory 'geography' / 'date' / 'measures' axes. Result is cached
    under cache_dir/<id>.dims.txt so we only hit the metadata endpoint once.
    """
    cache = cache_dir / f"{dataset_id}.dims.txt"
    if cache.exists() and cache.stat().st_size > 0:
        return [l for l in cache.read_text().splitlines() if l.strip()]
    url = f"https://www.nomisweb.co.uk/api/v01/dataset/{dataset_id}.def.sdmx.json"
    try:
        r = requests.get(url, timeout=60)
        r.raise_for_status()
        j = r.json()
    except Exception as e:
        warn(f"  {dataset_id}: dim discovery failed ({e})")
        return []
    # The SDMX JSON nests: Structure -> KeyFamilies -> KeyFamily -> Components
    #   -> Dimension -> @conceptRef (one per dim)
    dims: list[str] = []
    try:
        kfs = j["structure"]["keyfamilies"]["keyfamily"]
        if isinstance(kfs, list):
            kfs = kfs[0]
        comp = kfs["components"]["dimension"]
        if isinstance(comp, dict):
            comp = [comp]
        for d in comp:
            nm = (d.get("conceptref") or d.get("@conceptRef") or
                  d.get("conceptRef")  or "").lower()
            if nm and nm not in ("geography", "date", "measures"):
                dims.append(nm)
    except Exception as e:
        warn(f"  {dataset_id}: couldn't parse dim list ({e})")
        return []
    cache.write_text("\n".join(dims) + "\n")
    info(f"  {dataset_id}: dims = {dims}")
    return dims


def run_dwp_benefits() -> pd.DataFrame:
    rule("NOMIS DWP benefits (PIP / UC / ESA / Carer's / Pension Credit, LSOA)")
    cache_dir = CACHE_DIR / "dwp"
    cache_dir.mkdir(parents=True, exist_ok=True)

    merged: pd.DataFrame | None = None
    periods: dict[str, str] = {}

    for ds_id, short, _human in DWP_DATASETS:
        cache = cache_dir / f"{ds_id}.csv"
        # Discover this dataset's dimensions so we can pin each to "0" (Total).
        dims = _nomis_discover_dims(ds_id, cache_dir)
        dim_part = "".join(f"&{d}=0" for d in dims)

        if not cache.exists() or cache.stat().st_size < 4096:
            # Hit a LAD-level fallback if LSOA path fails or returns empty.
            tried = []
            for geo in ("TYPE298", "TYPE432"):  # LSOA2021, then LAD2022
                url = (
                    f"https://www.nomisweb.co.uk/api/v01/dataset/{ds_id}.data.csv"
                    f"?geography={geo}&date=latest{dim_part}&measures=20100"
                )
                tried.append(url)
                try:
                    r = requests.get(url, timeout=180)
                    r.raise_for_status()
                    body = r.content
                    info(f"  {short} ({geo}): {len(body)/1e6:.2f} MB")
                    if len(body) > 4096:
                        cache.write_bytes(body)
                        break
                except Exception as e:
                    warn(f"  {short} ({geo}): fetch failed ({e})")
                    continue
                time.sleep(0.4)
            if not cache.exists() or cache.stat().st_size < 4096:
                warn(f"  {short}: both LSOA and LAD came back empty")
                for u in tried:
                    warn(f"    tried: {u}")
                continue
        try:
            df = pd.read_csv(cache, dtype=str, low_memory=False)
        except pd.errors.EmptyDataError:
            warn(f"  {short}: cached CSV empty - skipping")
            continue

        need = {"GEOGRAPHY_CODE", "DATE", "OBS_VALUE"}
        if not need.issubset(df.columns):
            warn(f"  {short}: unexpected columns {list(df.columns)[:6]} - skipping")
            continue

        df["OBS_VALUE"] = pd.to_numeric(df["OBS_VALUE"], errors="coerce")
        dates = sorted(df["DATE"].dropna().unique())
        if not dates:
            warn(f"  {short}: no dates in response - skipping")
            continue
        latest = dates[-1]
        periods[short] = latest

        # If we fell back to LAD, rows will use LAD25CD codes. Infer which axis.
        sample_geo = str(df["GEOGRAPHY_CODE"].iloc[0]) if len(df) else ""
        is_lsoa = sample_geo.startswith("E0") and len(sample_geo) == 9
        geo_col = "LSOA21CD" if is_lsoa else "LAD25CD"

        cut = (df[df["DATE"] == latest]
                 .groupby("GEOGRAPHY_CODE", as_index=False)["OBS_VALUE"].sum()
                 .rename(columns={"GEOGRAPHY_CODE": geo_col, "OBS_VALUE": short}))

        # As with the claimant count, the geography was set by the request,
        # so no post-filter is needed.

        info(f"  {short}: {len(cut):,} rows at {geo_col} level (period={latest})")

        if merged is None:
            merged = cut
        elif geo_col in merged.columns:
            merged = merged.merge(cut, on=geo_col, how="outer")
        else:
            # First merge was LSOA, this one is LAD (or vice versa) - skip.
            warn(f"  {short}: geography mismatch with earlier dataset - skipping merge")
            continue

    if merged is None or merged.empty:
        warn("dwp: no datasets returned any data - skipping write")
        return pd.DataFrame()

    # Derive per-working-age-pop rates if we landed at LSOA and have census.
    if "LSOA21CD" in merged.columns:
        cen_path = DATA_DIR / "demographics" / "census2021.parquet"
        if cen_path.exists():
            try:
                cen = pd.read_parquet(cen_path)
                wapop_col = None
                if "census_working_age_pop" in cen.columns:
                    wapop_col = "census_working_age_pop"
                elif all(c in cen.columns for c in
                         ("census_population", "census_age_under18_pct", "census_age_65plus_pct")):
                    cen = cen.copy()
                    cen["_wapop"] = (cen["census_population"] *
                                     (100.0
                                      - cen["census_age_under18_pct"].fillna(0)
                                      - cen["census_age_65plus_pct"].fillna(0)) / 100.0)
                    wapop_col = "_wapop"
                if wapop_col:
                    merged = merged.merge(
                        cen[["LSOA21CD", wapop_col]].rename(columns={wapop_col: "_wapop"}),
                        on="LSOA21CD", how="left")
                    for short in ("pip_cases", "uc_households", "esa_claimants",
                                  "carers_allowance"):
                        if short in merged.columns:
                            merged[f"{short}_rate_pct"] = (
                                merged[short] / merged["_wapop"].replace(0, pd.NA) * 100
                            ).round(2)
                    merged = merged.drop(columns=["_wapop"])
            except Exception as e:
                warn(f"  rate calc skipped ({e})")

    if periods:
        merged["dwp_period"] = max(periods.values())

    out_path = DATA_DIR / "economy" / "dwp_benefits.parquet"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    merged = write_parquet_guarded(out_path, merged, source="dwp")
    ok(f"dwp benefits: {len(merged):,} rows across {len(periods)} datasets "
       f"-> {out_path.relative_to(REPO_ROOT)}")
    return merged




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
# ---------------------------------------------------------------------------
QOF_INDICATORS = [
    # (fingertips_id, short_name,              human label)
    (   241,           "qof_hypertension_pct",  "Hypertension: QOF prevalence"),
    (   848,           "qof_depression_pct",    "Depression: QOF prevalence (18+)"),
    ( 90813,           "qof_smi_pct",           "Severe mental illness: QOF prevalence"),
    (   253,           "qof_diabetes_pct",      "Diabetes: QOF prevalence (17+)"),
    (   273,           "qof_copd_pct",          "COPD: QOF prevalence"),
    (   258,           "qof_asthma_pct",        "Asthma: QOF prevalence"),
    (   263,           "qof_chd_pct",           "CHD: QOF prevalence"),
    (   268,           "qof_ckd_pct",           "CKD: QOF prevalence (18+)"),
    (   282,           "qof_dementia_pct",      "Dementia: QOF prevalence (aged 65+)"),
    (   349,           "qof_af_pct",            "Atrial fibrillation: QOF prevalence"),
    (   219,           "qof_smoking_pct",       "Smoking: QOF prevalence (15+)"),
    (   324,           "qof_obesity_pct",       "Obesity: QOF prevalence (18+)"),
    (   265,           "qof_stroke_tia_pct",    "Stroke/TIA: QOF prevalence"),
    (   295,           "qof_heart_failure_pct", "Heart failure: QOF prevalence"),
    (   262,           "qof_cancer_pct",        "Cancer: QOF prevalence"),
    (   266,           "qof_ld_pct",            "Learning disability: QOF prevalence"),
]

def run_qof() -> pd.DataFrame:
    rule("OHID Fingertips QOF (GP-practice prevalence)")
    cache_dir = CACHE_DIR / "qof_fingertips"
    cache_dir.mkdir(parents=True, exist_ok=True)
    AREA_TYPE_PRACTICE = 7  # GP practice

    rows: list[dict] = []
    for ind_id, short, _desc in QOF_INDICATORS:
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

def run_fingertips() -> pd.DataFrame:
    rule("OHID Fingertips (public health outcomes)")
    cache_dir = CACHE_DIR / "fingertips"
    cache_dir.mkdir(parents=True, exist_ok=True)
    AREA_TYPE_LA = 502  # Upper-tier LAs (post Apr 2023)

    rows: list = []
    for ind_id, short, desc in FINGERTIPS_INDICATORS:
        cache = cache_dir / f"ind_{ind_id}.csv"
        if not cache.exists():
            url = (
                f"https://fingertips.phe.org.uk/api/all_data/csv/by_indicator_id"
                f"?indicator_ids={ind_id}"
                f"&child_area_type_id={AREA_TYPE_LA}"
                f"&parent_area_type_id=15"
            )
            try:
                r = requests.get(url, timeout=30)
                r.raise_for_status()
                # A non-existent indicator id answers 200 with a header row and
                # nothing under it. Caching that makes the gap permanent: the
                # file exists, so no rerun ever asks again, and the indicator
                # is simply absent from the map with nothing to say why. Five
                # of them sat like that until someone counted the outputs.
                if len(r.content.splitlines()) < 2:
                    warn(f"fingertips {ind_id} ({short}): no rows returned; "
                         f"not caching. Check the id exists and is published "
                         f"for area type {AREA_TYPE_LA}.")
                    continue
                cache.write_bytes(r.content)
                time.sleep(1.0)  # be polite
            except Exception as e:
                warn(f"fingertips {ind_id}: {e}")
                continue
        try:
            df = pd.read_csv(cache, dtype=str, low_memory=False)
        except pd.errors.EmptyDataError:
            continue
        if df.empty or "Area Code" not in df.columns:
            continue

        # Check the id is the indicator we think it is. An id that points at
        # the wrong indicator returns a full, valid, plausible set of numbers
        # under our own label, which is the one failure mode nothing else here
        # can catch: hypertension prevalence published as smoking prevalence
        # was 10.8%, and looked entirely reasonable, for months.
        if "Indicator Name" in df.columns and not df["Indicator Name"].empty:
            actual = str(df["Indicator Name"].iloc[0])
            stem = re.sub(r"[^a-z]", "", desc.lower())[:14]
            if stem and stem not in re.sub(r"[^a-z]", "", actual.lower()):
                warn(f"fingertips {ind_id}: asked for {desc!r} but the data is "
                     f"{actual!r}. Verify the id before trusting this column.")
        df = df[df["Area Code"].isin(SCOPE_LADS)]
        if df.empty:
            continue
        # Fingertips returns Male, Female and Persons rows for the same
        # indicator. Without choosing, the tail(1) below takes whichever sorts
        # last, which silently produced Female figures for the two _male
        # indicators. Pick explicitly: the indicator name says what it wants,
        # and everything else wants Persons.
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
                warn(f"fingertips {ind_id} ({short}): no '{want}' rows; skipping")
                continue
            df = sub
        df = df.sort_values("Time period Sortable").groupby("Area Code", as_index=False).tail(1)
        for _, row in df.iterrows():
            rows.append({
                "LAD25CD": row["Area Code"],
                "lad_name": row.get("Area Name", ""),
                "indicator_id": ind_id,
                "indicator_short": short,
                "indicator_name":  desc,
                "value":    _tofloat(row.get("Value")),
                "lower_ci": _tofloat(row.get("Lower CI 95.0 limit")),
                "upper_ci": _tofloat(row.get("Upper CI 95.0 limit")),
                "period":   row.get("Time period", ""),
                "sex":      row.get("Sex", ""),
                "age":      row.get("Age", ""),
            })

    out = pd.DataFrame(rows)
    out_path = DATA_DIR / "outcomes" / "fingertips.parquet"
    out = write_parquet_guarded(out_path, out, source="fingertips")
    ok(f"fingertips: {len(out):,} rows -> {out_path.relative_to(REPO_ROOT)}")
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
FUEL_POVERTY_DEFAULT_URL = os.environ.get(
    "FUEL_POVERTY_URL",
    # 2023 data (pub. Feb 2025). If 404, grab the latest XLSX from
    # https://www.gov.uk/government/collections/fuel-poverty-sub-regional-statistics
    # and save as .cache/fuel_poverty/fuel_poverty_lsoa.xlsx.
    "https://assets.publishing.service.gov.uk/media/"
    "67a5a52fd0346e3cb63419c7/"
    "sub-regional-fuel-poverty-2025-tables.xlsx",
)


def run_fuel_poverty() -> pd.DataFrame | None:
    rule("Fuel poverty (DESNZ sub-regional, LSOA)")
    cache_dir = CACHE_DIR / "fuel_poverty"
    cache_dir.mkdir(parents=True, exist_ok=True)

    # Prefer any XLSX the user has dropped in; else try the default URL.
    candidates = sorted(cache_dir.glob("*.xlsx"),
                        key=lambda p: p.stat().st_size, reverse=True)
    if not candidates:
        url = FUEL_POVERTY_DEFAULT_URL
        src = cache_dir / "fuel_poverty_lsoa.xlsx"
        info(f"No local cache — downloading {url}")
        try:
            r = browser_session(referer="https://www.gov.uk/").get(url, timeout=120)
            r.raise_for_status()
            src.write_bytes(r.content)
            candidates = [src]
        except Exception as e:
            warn(f"fuel_poverty download failed: {e}. "
                 f"Drop the DESNZ sub-regional XLSX (LSOA tab) in {cache_dir}/ "
                 "and re-run `python fetch_all_data.py --only fuel_poverty`.")
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

    out = pd.DataFrame({
        "LSOA21CD": df[code_col].astype(str).str.strip(),
        "fuel_poverty_pct": pd.to_numeric(df[pct_col], errors="coerce"),
    }).dropna(subset=["LSOA21CD"])
    # Many DESNZ releases use LSOA 2011 codes (E01xxxxxx) which still align
    # with most 2021 boundaries — keep as-is; mismatches will just be skipped
    # in build_lsoa_data.
    out_path = DATA_DIR / "demographics" / "fuel_poverty.parquet"
    out = write_parquet_guarded(out_path, out, source="fuel_poverty")
    ok(f"fuel_poverty: {len(out):,} LSOAs -> "
       f"{out_path.relative_to(REPO_ROOT)}")
    return out


# ============================================================================
# SOURCE 4c: GLA LSOA Atlas — PTAI score (LSOA-level)
# ============================================================================
# PTAL (Public Transport Accessibility Level) is TfL's 0-6b banded score of
# how well-connected a location is by public transport. The underlying
# continuous score (PTAI) is published at LSOA level in the GLA's
# "LSOA Atlas". Source:
#   https://data.london.gov.uk/dataset/lsoa-atlas
#
# The LSOA Atlas CSV contains a column "Average PTAI score" per LSOA. Bigger
# is better (6b ~= 25+, 0 ~= <0.01).
PTAL_DEFAULT_URL = os.environ.get(
    "PTAL_URL",
    # LSOA Atlas CSV on London Datastore. If this 404s, grab the CSV from
    # https://data.london.gov.uk/dataset/lsoa-atlas and drop in
    # .cache/ptal/lsoa_atlas.csv.
    "https://data.london.gov.uk/download/lsoa-atlas/"
    "00f1a8c6-9a8e-4d90-a48e-7b2d2b4ab15b/lsoa-data.csv",
)


def run_ptal() -> pd.DataFrame | None:
    rule("PTAL (GLA LSOA Atlas, average PTAI)")
    cache_dir = CACHE_DIR / "ptal"
    cache_dir.mkdir(parents=True, exist_ok=True)

    candidates = sorted(cache_dir.glob("*.csv"),
                        key=lambda p: p.stat().st_size, reverse=True)
    if not candidates:
        url = PTAL_DEFAULT_URL
        src = cache_dir / "lsoa_atlas.csv"
        info(f"No local cache — downloading {url}")
        try:
            r = browser_session(referer="https://data.london.gov.uk/").get(
                url, timeout=120)
            r.raise_for_status()
            src.write_bytes(r.content)
            candidates = [src]
        except Exception as e:
            warn(f"PTAL download failed: {e}. "
                 f"Download the LSOA Atlas CSV from "
                 "https://data.london.gov.uk/dataset/lsoa-atlas and save as "
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
    ptai_col = (find_col("average", "ptai")
                or find_col("ptai", "score")
                or find_col("ptai"))
    if not code_col or not ptai_col:
        warn(f"ptal: columns not found (code={code_col!r}, "
             f"ptai={ptai_col!r}). Columns seen: {list(df.columns)[:10]}")
        return None

    out = pd.DataFrame({
        "LSOA21CD": df[code_col].astype(str).str.strip(),
        "ptai_score": pd.to_numeric(df[ptai_col], errors="coerce"),
    }).dropna(subset=["LSOA21CD", "ptai_score"])
    # Filter to well-formed E01 LSOA codes.
    out = out[out["LSOA21CD"].str.startswith("E01")]
    out_path = DATA_DIR / "demographics" / "ptal.parquet"
    out = write_parquet_guarded(out_path, out, source="ptal")
    ok(f"ptal: {len(out):,} LSOAs -> {out_path.relative_to(REPO_ROOT)}")
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


def run_police_crime(months_back: int = 12) -> pd.DataFrame:
    rule(f"Police.uk crime (last {months_back} months)")
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
# SOURCE 6: Hospitals  (NHS.uk dataset - optional)
# ============================================================================
def run_hospitals() -> pd.DataFrame | None:
    rule("Hospitals (NHS.uk, optional)")
    cache_dir = CACHE_DIR / "hospitals"
    cache_dir.mkdir(parents=True, exist_ok=True)
    csvs = list(cache_dir.glob("*.csv"))
    if not csvs:
        warn("No Hospital.csv in .cache/hospitals/ — skipping. "
             "Download from https://www.nhs.uk/about-us/nhs-website-datasets/ "
             "if you want hospital markers on the map.")
        return None

    src = csvs[0]
    df = pd.read_csv(src, dtype=str, keep_default_na=False, low_memory=False)

    # NHS.uk schema varies - find columns by keyword
    def col(*kws):
        for c in df.columns:
            lc = c.lower()
            if all(k in lc for k in kws):
                return c
        return None
    name_c = col("organisationname") or col("name")
    addr_c = col("address1") or col("address")
    pc_c   = col("postcode")
    lat_c  = col("lat")
    lng_c  = col("long") or col("lng")
    type_c = col("organisationtype") or col("sector") or col("type")

    owners = {}
    if pc_c:
        for _, r in df.iterrows():
            pc = normalise_postcode(r.get(pc_c, ""))
            if pc:
                owners[pc] = str(r.get(name_c, "") if name_c else "")
    lookup = lookup_postcodes(owners.keys(), source="hospitals", owners=owners)

    rows = []
    for _, r in df.iterrows():
        pc = normalise_postcode(r.get(pc_c, "") if pc_c else "")
        # Prefer explicit lat/lng if present; else postcode lookup
        lat = _tofloat(r.get(lat_c, "")) if lat_c else None
        lng = _tofloat(r.get(lng_c, "")) if lng_c else None
        lsoa = lad = wd = ""
        if (lat is None or lng is None) and pc:
            hit = lookup.get(pc)
            if hit:
                lat, lng, lsoa, lad, wd = hit
        if pc:
            hit2 = lookup.get(pc)
            if hit2:
                _, _, lsoa, lad, wd = hit2
        if lat is None or lng is None:
            continue
        if lad and lad not in SCOPE_LADS:
            continue
        rows.append({
            "name": r.get(name_c, "") if name_c else "",
            "addr": r.get(addr_c, "") if addr_c else "",
            "postcode": pc,
            "lat": lat, "lng": lng,
            "type": r.get(type_c, "") if type_c else "",
            "LSOA21CD": lsoa, "WD25CD": wd, "LAD25CD": lad,
        })
    out = pd.DataFrame(rows)
    out_path = DATA_DIR / "healthcare" / "hospitals.parquet"
    out = write_parquet_guarded(out_path, out, source="hospitals")
    ok(f"hospitals: {len(out):,} rows -> {out_path.relative_to(REPO_ROOT)}")
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

    info(f"CCEW: {len(covers_map):,} charities cover at least one borough in scope "
         f"(explicit: {sum(1 for v in scope_map.values() if v == 'explicit'):,}, "
         f"london_wide only: {sum(1 for v in scope_map.values() if v == 'london_wide'):,})")

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
    if gps is not None and "WD25CD" in gps.columns:
        for wd, n in gps.groupby("WD25CD").size().items():
            if wd:
                _get(wd)["indicators"]["gp_practice_count"] = int(n)
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
        for wd, n in pharm.groupby("WD25CD").size().items():
            if wd:
                _get(wd)["indicators"]["pharmacy_count"] = int(n)
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

    crime = _read_parquet_opt(DATA_DIR / "crime" / "police_uk_crime.parquet")
    if crime is not None and "WD25CD" in crime.columns:
        for wd, n in crime.groupby("WD25CD").size().items():
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

    # --- Census 2021: per-LSOA -> per-ward via population-weighted mean ----
    cen = _read_parquet_opt(DATA_DIR / "demographics" / "census2021.parquet")
    if cen is not None and not cen.empty:
        sources["census"] = "ONS Census 2021 (Nomis)"
        # LSOA21CD -> WD25CD from the ONS best-fit lookup.
        lsoa_to_ward = get_lsoa_to_ward()

        pop_by_lsoa = dict(zip(
            cen["LSOA21CD"].astype(str),
            pd.to_numeric(cen.get("census_population", pd.Series(dtype="float")),
                          errors="coerce").fillna(0),
        ))
        pct_cols = [c for c in cen.columns if c.endswith("_pct")]
        from collections import defaultdict as _dd
        # Weighted numerator/denominator: uses LSOA population when present,
        # otherwise a weight of 1.0 per LSOA (unweighted mean). This keeps the
        # fetcher producing ward-level %s even when TS001 pop is missing.
        ward_num = _dd(lambda: _dd(float))
        ward_den = _dd(lambda: _dd(float))
        ward_pop_sum = _dd(float)

        for _, row in cen.iterrows():
            lc = str(row["LSOA21CD"])
            wd = lsoa_to_ward.get(lc)
            if not wd:
                continue
            pop = pop_by_lsoa.get(lc, 0) or 0
            if pop > 0:
                ward_pop_sum[wd] += float(pop)
            weight = float(pop) if pop > 0 else 1.0
            for pc in pct_cols:
                v = row[pc]
                if pd.notna(v):
                    ward_num[wd][pc] += float(v) * weight
                    ward_den[wd][pc] += weight

        for wd, w in wards.items():
            if ward_pop_sum.get(wd, 0) > 0:
                w["indicators"]["census_population"] = int(round(ward_pop_sum[wd]))
            for pc in pct_cols:
                den = ward_den[wd].get(pc, 0)
                if den > 0:
                    w["indicators"][pc] = round(ward_num[wd][pc] / den, 2)

    # --- Fuel poverty + PTAL: per-LSOA -> per-ward (population weighted) ----
    # Reuses lsoa_to_ward + pop_by_lsoa built in the census block above. If
    # census was absent both dicts may be missing, so guard for that.
    if "lsoa_to_ward" not in locals():
        lsoa_to_ward = get_lsoa_to_ward()
    if "pop_by_lsoa" not in locals():
        pop_by_lsoa = {}

    def _agg_to_wards(df, value_col, ward_key):
        if df is None or df.empty or value_col not in df.columns:
            return
        num, den = {}, {}
        for _, row in df.iterrows():
            lc = str(row["LSOA21CD"])
            wd = lsoa_to_ward.get(lc)
            v = row.get(value_col)
            if not wd or pd.isna(v):
                continue
            pop = pop_by_lsoa.get(lc, 0) or 0
            weight = float(pop) if pop > 0 else 1.0
            num[wd] = num.get(wd, 0.0) + float(v) * weight
            den[wd] = den.get(wd, 0.0) + weight
        for wd, w in wards.items():
            if den.get(wd, 0) > 0:
                w["indicators"][ward_key] = round(num[wd] / den[wd], 2)

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
                _agg_to_wards(imd_ward, col, col)
        # An average of deciles is not itself a decile, so it is named for what
        # it is. The per-ward Core20 counts below remain the categorical view.
        if "imd_decile" in imd_ward.columns:
            _agg_to_wards(imd_ward, "imd_decile", "imd_decile_mean")

    _agg_to_wards(fp, "fuel_poverty_pct", "fuel_poverty_pct")
    if fp is not None:
        sources["fuel_poverty"] = "DESNZ sub-regional fuel poverty (LILEE)"

    pt = _read_parquet_opt(DATA_DIR / "demographics" / "ptal.parquet")
    _agg_to_wards(pt, "ptai_score", "ptai_score")
    if pt is not None:
        sources["ptal"] = "GLA LSOA Atlas (average PTAI score)"

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

    # --- DWP benefits: counts SUM, rates pop-weighted MEAN ------------------
    dwp = _read_parquet_opt(DATA_DIR / "economy" / "dwp_benefits.parquet")
    if dwp is not None and not dwp.empty:
        try:
            dwp_period = str(dwp["dwp_period"].dropna().iloc[0])
        except Exception:
            dwp_period = ""
        sources["dwp"] = f"NOMIS DWP benefits (PIP/UC/ESA/CA/PC, {dwp_period})"
        count_cols = [c for c in ("pip_cases", "uc_households", "esa_claimants",
                                   "carers_allowance", "pension_credit")
                      if c in dwp.columns]
        for raw_col in count_cols:
            agg = {}
            for _, row in dwp.iterrows():
                lc = str(row["LSOA21CD"])
                wd = lsoa_to_ward.get(lc)
                v = row.get(raw_col)
                if not wd or pd.isna(v):
                    continue
                agg[wd] = agg.get(wd, 0) + int(v)
            for wd, w in wards.items():
                if wd in agg:
                    w["indicators"][raw_col] = int(agg[wd])
        rate_cols = [c for c in dwp.columns if c.endswith("_rate_pct")]
        for raw_col in rate_cols:
            _agg_to_wards(dwp, raw_col, raw_col)

    # --- Core20: ward is Core20 if any of its LSOAs is in IMD decile 1-2 -----
    # NHS Core20PLUS5 framework definition.
    imd = _read_parquet_opt(DATA_DIR / "demographics" / "imd2025.parquet")
    if imd is not None and "imd_decile" in imd.columns:
        # Reuse the LSOA->ward map built for census; rebuild if census was absent.
        if "lsoa_to_ward" not in locals():
            lsoa_to_ward = get_lsoa_to_ward()
        core20_wards: set = set()
        n_core20_lsoas: dict = {}
        n_ward_lsoas: dict = {}
        for _, r in imd.iterrows():
            d = r.get("imd_decile")
            lc = str(r.get("LSOA21CD") or "")
            wd = lsoa_to_ward.get(lc)
            if not wd:
                continue
            n_ward_lsoas[wd] = n_ward_lsoas.get(wd, 0) + 1
            if pd.notna(d) and int(d) in (1, 2):
                core20_wards.add(wd)
                n_core20_lsoas[wd] = n_core20_lsoas.get(wd, 0) + 1
        for wd, w in wards.items():
            w["is_core20"] = wd in core20_wards
            if wd in n_ward_lsoas:
                w["indicators"]["core20_lsoa_count"] = n_core20_lsoas.get(wd, 0)
                w["indicators"]["total_lsoa_count"] = n_ward_lsoas[wd]
        sources["core20"] = "IMD2025 deciles 1-2 per LSOA"

    return {
        "wards": wards,
        "metadata": {
            "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "sources": sources,
            "claimant_period": "",
            # Provenance for the LSOA -> ward attribution behind every
            # LSOA-derived ward indicator above.
            "lsoa_to_ward_lookup":
                "ONS LSOA (2021) -> Electoral Ward (2025) -> LAD (2025) best-fit",
            "lsoa_to_ward_lookup_year": "2025",
        },
    }

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

    # PTAL (GLA LSOA Atlas, average PTAI score)
    pt = _read_parquet_opt(DATA_DIR / "demographics" / "ptal.parquet")
    if pt is not None and not pt.empty:
        for _, row in pt.iterrows():
            code = str(row["LSOA21CD"])
            v = row.get("ptai_score")
            if code in out and pd.notna(v):
                out[code]["ptai_score"] = round(float(v), 2)

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

    # DWP benefits (PIP / UC / ESA / CA / PC)
    dwp = _read_parquet_opt(DATA_DIR / "economy" / "dwp_benefits.parquet")
    if dwp is not None and not dwp.empty:
        carry_int   = [c for c in ("pip_cases", "uc_households", "esa_claimants",
                                    "carers_allowance", "pension_credit")
                       if c in dwp.columns]
        carry_float = [c for c in dwp.columns if c.endswith("_rate_pct")]
        for _, row in dwp.iterrows():
            code = str(row["LSOA21CD"])
            if code not in out:
                continue
            for c in carry_int:
                v = row.get(c)
                if pd.notna(v):
                    out[code][c] = int(v)
            for c in carry_float:
                v = row.get(c)
                if pd.notna(v):
                    out[code][c] = round(float(v), 2)

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
    #   pc  (postcode)    - every .pc read belongs to GP, dental or pharmacy data
    #   ward (WD25CD)     - every .ward read is a CSS class, an LSOA_IMD
    #                       property, or a GP record
    # Together they were 37.9% of a 9.7 MB payload that every visitor downloads.
    # Adding a field back means adding it here and reading it in index.html.
    keep = ["num","name","addr","lat","lng","LAD25CD",
            "hq_in_nwl","covers","scope",
            "income","income_band","website",
            "what_tags","what_desc","how_tags","how_desc","who_tags","who_desc"]
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
    return out.to_dict(orient="records")


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

def export_map_blobs() -> None:
    """Regenerate the map data files the pipeline owns."""
    index_path = REPO_ROOT / "index.html"
    if not index_path.exists():
        warn(f"index.html not found at {index_path}; skipping map blob export")
        return

    gps = _read_parquet_opt(DATA_DIR / "healthcare" / "gp_practices.parquet")
    if gps is not None:
        cols = [c for c in ["name", "addr", "lat", "lng", "postcode", "code",
                            "ward", "lad", "tel", "patients"] if c in gps.columns]
        gp_records = (gps[cols]
                      .rename(columns={"name": "n", "addr": "a", "postcode": "pc"})
                      .to_dict(orient="records"))

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
            info(f"  {shared} ward name(s) shared by more than one borough; "
                 f"disambiguated with the borough name")
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
        ok(f"map blob: {len(bor_gj['features']):,} boroughs -> data/map/{MAP_BLOBS['BOROUGH_GJ']}")

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

    # HOSP is deliberately not written here. data/map/hosp.js is hand-curated:
    # 20 hospitals with their parent NHS trust, which run_hospitals() does not
    # produce. The old splice regex for it never matched (the block carries //
    # comment lines), so this was already the de facto behaviour; making it
    # explicit stops a future change from silently destroying that file.


def build_dental_json() -> list:
    """dental_practices.json, in the shape loadDentalLayer() already reads."""
    df = _read_parquet_opt(DATA_DIR / "healthcare" / "dental_practices.parquet")
    if df is None:
        return []
    keep = [c for c in ["name", "postcode", "lat", "lng", "nhs_contracted",
                        "lad_code", "imd_decile"] if c in df.columns]
    out = df[keep].to_dict(orient="records")
    for r in out:
        # loadDentalLayer treats nhs_contracted as a tri-state: true, false, or
        # null for unknown. Keep it a real bool so the filter stays meaningful.
        if "nhs_contracted" in r and r["nhs_contracted"] is not None:
            r["nhs_contracted"] = bool(r["nhs_contracted"])
    return out


def export_all() -> None:
    rule("Export Leaflet JSON outputs")
    ward_data  = build_ward_data()
    lsoa_data  = build_lsoa_data()
    pharm_data = build_pharmacies_json()
    vcse_data  = build_vcse_json()
    dental_data = build_dental_json()

    write_json_atomic(REPO_ROOT / "ward_data.json",  ward_data)
    write_json_atomic(REPO_ROOT / "lsoa_data.json",  lsoa_data)
    write_json_atomic(REPO_ROOT / "pharmacies.json", pharm_data)
    write_json_atomic(REPO_ROOT / "vcse_data.json",  vcse_data)
    if dental_data:
        write_json_atomic(REPO_ROOT / "dental_practices.json", dental_data)
    ok(f"ward_data.json:  {len(ward_data.get('wards', {})):,} wards")
    ok(f"lsoa_data.json:  {len(lsoa_data):,} LSOAs")
    ok(f"pharmacies.json: {len(pharm_data):,} rows")
    ok(f"vcse_data.json:  {len(vcse_data):,} charities")
    if dental_data:
        ok(f"dental_practices.json: {len(dental_data):,} practices")

    export_map_blobs()

# ============================================================================
# MAIN
# ============================================================================
SOURCES = {
    "boundaries":  run_boundaries,
    "gp":          run_gp_practices,
    "pharmacies":  run_pharmacies,
    "imd":         run_imd2025,
    "census":      run_census2021,
    "claimant":    run_claimant_count,
    "dwp":         run_dwp_benefits,
    "qof":         run_qof,
    "fingertips":  run_fingertips,
    "fuel_poverty": run_fuel_poverty,
    "ptal":        run_ptal,
    "crime":       run_police_crime,
    "hospitals":   run_hospitals,
    "charities":   run_charities,
    "dentists":    run_dentists,
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
                    rec["notes"] = "source returned no frame"
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
