"""Prove each ODS guard fires, and that failure isolation preserves the cache."""
import importlib.util, pathlib, shutil, sys, tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("p", REPO / "fetch_all_data.py")
p = importlib.util.module_from_spec(spec); sys.modules["p"] = p; spec.loader.exec_module(p)

live = (REPO / ".cache/pharmacies/edispensary.csv").read_bytes()
PHARM = dict(source="pharmacies", code_prefix="F",
             role_codes={"RO182", "RO94"}, min_active=8_000)

def expect_raise(label, fn):
    try:
        fn()
    except RuntimeError as e:
        print(f"  PASS  {label}\n          -> {str(e)[:150]}")
    except Exception as e:
        print(f"  ?     {label}: unexpected {type(e).__name__}: {e}")
    else:
        print(f"  FAIL  {label}: no error raised")

print("=== validator guards ===")
expect_raise("happy path is NOT rejected (should print nothing below)",
             lambda: (_ for _ in ()).throw(RuntimeError(
                 "sentinel")) if False else None) if False else None
n = p._validate_ods_extract(live, report="edispensary", **PHARM)
print(f"  PASS  valid extract accepted ({n:,} active rows)")

expect_raise("wrong report (epraccur bytes through pharmacy checks)",
             lambda: p._validate_ods_extract(
                 p._http_bytes(p.ODS_EXPORT_URL.format(report="epraccur"),
                               source="test").content,
                 report="epraccur", **PHARM))

import csv as _csv, io as _io
_rows = [r for r in _csv.reader(_io.StringIO(live.decode("latin-1"), newline=""))][:200]
_buf = _io.StringIO(newline="")
_w = _csv.writer(_buf, quoting=_csv.QUOTE_ALL, lineterminator="\r\n")
for _r in _rows:
    _w.writerow(_r + ["EXTRA"])          # a genuine 28th ODS column
expect_raise("column drift (28 fields)",
             lambda: p._validate_ods_extract(
                 _buf.getvalue().encode("latin-1"), report="edispensary", **PHARM))

expect_raise("HTML error page served as HTTP 200",
             lambda: p._validate_ods_extract(
                 b"<!DOCTYPE html><html><body>503 Service Unavailable</body></html>",
                 report="edispensary", **PHARM))

expect_raise("body with broken quoting",
             lambda: p._validate_ods_extract(
                 b'"FA002","ROWLANDS\n unterminated', report="edispensary", **PHARM))

hdr = b'"OrganisationCode","Name"' + b',"x"' * 25 + b'\n' + live
expect_raise("header row appears",
             lambda: p._validate_ods_extract(hdr, report="edispensary", **PHARM))

trunc = b'\n'.join(live.split(b'\n')[:500])
expect_raise("truncated file (too few active rows)",
             lambda: p._validate_ods_extract(trunc, report="edispensary", **PHARM))

expect_raise("empty body",
             lambda: p._validate_ods_extract(b"", report="edispensary", **PHARM))

print("\n=== failure isolation (network down, cache present) ===")
tmp = pathlib.Path(tempfile.mkdtemp())
cache = tmp / "edispensary.csv"
cache.write_bytes(live)
(tmp / "edispensary.csv.etag").write_text('"stale-etag"')
orig = p.ODS_EXPORT_URL
p.ODS_EXPORT_URL = "https://nonexistent.invalid.example/api/getReport?report={report}"
try:
    got = p.fetch_ods_report("edispensary", cache, **PHARM)
    same = got.read_bytes() == live
    print(f"  PASS  unreachable host fell back to cache, bytes intact={same}")
except Exception as e:
    print(f"  FAIL  raised instead of falling back: {type(e).__name__}: {e}")

print("\n=== hard failure (network down, no cache) ===")
cache2 = tmp / "absent.csv"
try:
    p.fetch_ods_report("edispensary", cache2, **PHARM)
    print("  FAIL  no error raised")
except RuntimeError as e:
    print(f"  PASS  raised loudly -> {str(e)[:170]}")
finally:
    p.ODS_EXPORT_URL = orig
    shutil.rmtree(tmp, ignore_errors=True)
