"""The ONS LSOA 2011 to 2021 lookup (get_lsoa11_lsoa21_rows), which PTAL and
any other 2011-coded LSOA source joins through.

Offline: requests.get and requests.post are replaced by a fake ONS service.
The October 2026 refresh got no rows from the unsuffixed service name while
the MSOA lookup, which names its _v2 service, worked; so the lookup now finds
its service by name, and an ArcGIS error body (HTTP 200 with "error") is
reported as itself rather than as "0 of 4,994 LSOAs came back".

Checks: the _v2 service is used when the unsuffixed one has gone; a service
found only through the ONS service list is used; field names are matched
whatever their case; an error body is a clear error and nothing is cached;
no service at all is a clear error.
"""
import importlib.util
import json
import pathlib
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("p", REPO / "fetch_all_data.py")
p = importlib.util.module_from_spec(spec); sys.modules["p"] = p; spec.loader.exec_module(p)

FAILS = []


def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + (f"\n          {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(label)


CODES = [f"E0100{i:04d}" for i in range(250)]
p.get_lsoa_ward_lookup = lambda: {c: "E05000001" for c in CODES}
p.CACHE_DIR = pathlib.Path(tempfile.mkdtemp())
p.SCOPE_KEY = "test"


class Resp:
    def __init__(self, body, status=200):
        self.body, self.status_code = body, status
        self.ok = status < 400

    def json(self):
        return self.body

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError(f"HTTP {self.status_code}")


def service(live: dict, listed=(), error_body=None):
    """Fake ONS: live maps a service name to its field names."""
    def get(url, timeout=None, **k):
        if url.endswith("/services?f=json"):
            return Resp({"services": [{"name": n} for n in list(live) + list(listed)]})
        name = url.split("/services/")[1].split("/")[0]
        if name not in live:
            return Resp({"error": {"code": 400, "message": "Invalid URL"}})
        return Resp({"fields": [{"name": f} for f in live[name]]})

    def post(url, data=None, timeout=None, **k):
        if error_body:
            return Resp({"error": error_body})
        name = url.split("/services/")[1].split("/")[0]
        f11, f21, fchg = live[name]
        inside = data["where"].split("(", 1)[1].rstrip(")")
        batch = [c.strip("'") for c in inside.split(",")]
        assert data["where"].startswith(f21), data["where"]
        return Resp({"features": [{"attributes": {f11: c.replace("E0100", "E0199"), f21: c, fchg: "U"}}
                                  for c in batch]})
    p.requests.get, p.requests.post = get, post


def clear_cache():
    for f in (p.CACHE_DIR / "lookups").glob("*"):
        f.unlink()


print("=== the service is found by name ===")
service({"LSOA11_LSOA21_LAD22_EW_LU_v2": ["LSOA11CD", "LSOA21CD", "CHGIND"]})
rows = p.get_lsoa11_lsoa21_rows()
check("the _v2 service is used when the unsuffixed one has gone", len({r[1] for r in rows}) == len(CODES))
check("and the lookup is cached", (p.CACHE_DIR / "lookups" / "lsoa11_lsoa21_test.json").exists())
clear_cache()
service({"LSOA11_LSOA21_LAD23_EW_LU": ["lsoa11cd", "lsoa21cd", "chgind"]})
rows = p.get_lsoa11_lsoa21_rows()
check("a service found only through the list, with lower-case fields, is used",
      len(rows) == len(CODES) and rows[0][2] == "U", str(rows[:2]))

print("=== failures say what happened ===")
clear_cache()
service({"LSOA11_LSOA21_LAD22_EW_LU_v2": ["LSOA11CD", "LSOA21CD", "CHGIND"]},
        error_body={"code": 400, "message": "Unable to complete operation."})
try:
    p.get_lsoa11_lsoa21_rows()
    check("an ArcGIS error body is an error", False)
except RuntimeError as e:
    check("an ArcGIS error body is reported as itself", "Unable to complete operation" in str(e), str(e))
check("and nothing is cached", not (p.CACHE_DIR / "lookups" / "lsoa11_lsoa21_test.json").exists())
service({})
try:
    p.get_lsoa11_lsoa21_rows()
    check("no service is an error", False)
except RuntimeError as e:
    check("no service at all is a clear error", "no ONS service found" in str(e), str(e))

print()
if FAILS:
    print(f"{len(FAILS)} failed")
    sys.exit(1)
print("All checks passed")
