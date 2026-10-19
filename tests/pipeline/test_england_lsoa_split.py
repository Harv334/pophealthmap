"""build_england.write_lsoa_by_la: England's LSOAs go out one file per LA,
each with that LA's shapes and figures, placed by ward and then by LA name,
and a rebuild leaves no file from an LA that is gone. Offline, no data needed
beyond shapely being importable (build_england imports it).

    python3 tests/pipeline/test_england_lsoa_split.py
"""
import json
import pathlib
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
import build_england as be  # noqa: E402


def feat(code, ward_code, borough):
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [0, 0]},
            "properties": {"code": code, "ward_code": ward_code, "borough": borough, "ward": "W"}}


def main():
    lsoas = [feat("E01000001", "E05000001", "Alpha"),
             feat("E01000002", "E05000002", "Alpha"),
             feat("E01000003", "", "Beta"),          # no ward: placed by LA name
             feat("E01000004", "E05999999", "Nowhere")]  # placed nowhere
    data = {"E01000001": {"x": 1}, "E01000002": {"x": 2}, "E01000003": {"x": 3}}
    ward_lad = {"E05000001": "E09000001", "E05000002": "E09000001"}
    name_lad = {"Alpha": "E09000001", "Beta": "E06000001"}
    with tempfile.TemporaryDirectory() as tmp:
        out = pathlib.Path(tmp) / "lsoa"
        out.mkdir()
        (out / "E07000999.json").write_text("{}")    # from an earlier build
        n, unplaced = be.write_lsoa_by_la(lsoas, data, ward_lad, name_lad, out)
        files = sorted(p.name for p in out.glob("*.json"))
        a = json.loads((out / "E09000001.json").read_text())
        b = json.loads((out / "E06000001.json").read_text())
    checks = [
        (n == 2 and unplaced == 1, f"two LA files and one LSOA placed nowhere ({n}, {unplaced})"),
        (files == ["E06000001.json", "E09000001.json"], f"only this build's files are left ({files})"),
        ([f["properties"]["code"] for f in a["features"]] == ["E01000001", "E01000002"]
         and a["data"] == {"E01000001": {"x": 1}, "E01000002": {"x": 2}}, "an LA's file holds its LSOAs and their figures"),
        (a["type"] == "FeatureCollection", "each file is a FeatureCollection"),
        ([f["properties"]["code"] for f in b["features"]] == ["E01000003"], "an LSOA with no ward is placed by its LA's name"),
    ]
    bad = 0
    for good, name in checks:
        print(("PASS " if good else "FAIL ") + name)
        bad += not good
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
