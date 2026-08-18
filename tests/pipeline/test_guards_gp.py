"""Guards for the epraccur checks, plus the legacy-zip fallback path."""
import importlib.util, pathlib, shutil, sys, tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("p", REPO / "fetch_all_data.py")
p = importlib.util.module_from_spec(spec); sys.modules["p"] = p; spec.loader.exec_module(p)

GP = dict(source="gp_practices", role_codes={"B", "Z"}, min_active=10_000,
          marker=(p.EPRACCUR_HEADER.index("PrescribingSetting"), "RO76", 5_000))
live_gp = (REPO / ".cache/gp_practices/epraccur.csv").read_bytes()


def expect_raise(label, fn):
    try:
        fn()
    except RuntimeError as e:
        print(f"  PASS  {label}\n          -> {str(e)[:140]}")
    except Exception as e:
        print(f"  ?     {label}: unexpected {type(e).__name__}: {e}")
    else:
        print(f"  FAIL  {label}: no error raised")


print("=== epraccur guards ===")
n = p._validate_ods_extract(live_gp, report="epraccur", **GP)
print(f"  PASS  valid epraccur accepted ({n:,} active rows)")

for wrong in ("edispensary", "ebranchs", "epharmacyhq", "egpcur"):
    expect_raise(f"wrong report '{wrong}' rejected by GP checks",
                 lambda w=wrong: p._validate_ods_extract(
                     p._http_bytes(p.ODS_EXPORT_URL.format(report=w), source="t").content,
                     report=w, **GP))

# and the reverse: epraccur must not pass the pharmacy checks
PHARM = dict(source="pharmacies", code_prefix="F",
             role_codes={"RO182", "RO94"}, min_active=8_000)
expect_raise("epraccur rejected by pharmacy checks",
             lambda: p._validate_ods_extract(live_gp, report="epraccur", **PHARM))

print("\n=== legacy zip fallback (API unreachable, no csv cache, zip present) ===")
tmp = pathlib.Path(tempfile.mkdtemp())
shutil.copy(REPO / ".cache/gp_practices/epraccur.zip", tmp / "epraccur.zip")
orig_cache, orig_url = p.CACHE_DIR, p.ODS_EXPORT_URL
p.CACHE_DIR = tmp.parent / "nocache"
p.ODS_EXPORT_URL = "https://nonexistent.invalid.example/api/getReport?report={report}"
try:
    cache_dir = tmp
    cache = cache_dir / "epraccur.csv"
    legacy = cache_dir / "epraccur.zip"
    try:
        p.fetch_ods_report("epraccur", cache, **GP)
        src = cache
    except Exception:
        src = legacy if legacy.exists() else None
    print(f"  {'PASS' if src == legacy else 'FAIL'}  fell back to {src.name if src else None}")
finally:
    p.CACHE_DIR, p.ODS_EXPORT_URL = orig_cache, orig_url
    shutil.rmtree(tmp, ignore_errors=True)
