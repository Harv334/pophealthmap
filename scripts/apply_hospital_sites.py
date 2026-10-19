"""Apply data/curated/hospital_sites.csv to the hospital files already on disk.

run_hospitals() applies the checked list on every data refresh. This does the
same to the files that are shipped now, without a download: the London
parquet and data/map/hospitals.js, and the hospitals.js of any region build
present under data/regions/<id>/map/. Safe to run twice, since a record is
matched on its postcode when its name has already been changed.

    python3 scripts/apply_hospital_sites.py
"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import pandas as pd  # noqa: E402
import fetch_all_data as F  # noqa: E402

HEAD = ("// Hospital sites, from the ODS NHS trust sites register, checked against "
        "data/curated/hospital_sites.csv.\n"
        "// Loaded by index.html as a classic script before the main block,\n"
        "// so this global is defined by the time any code reads it.\n")


def read_js(path: Path) -> list:
    s = path.read_text(encoding="utf-8")
    return json.loads(s[s.index("["):s.rindex("]") + 1])


def write_js(path: Path, rows: list) -> None:
    body = json.dumps(rows, separators=(",", ":"), ensure_ascii=False)
    path.write_text(HEAD + f"var HOSP = {body};\n", encoding="utf-8")


def main() -> None:
    sites = F.load_hospital_sites()
    pq = REPO / "data" / "healthcare" / "hospitals.parquet"
    if pq.exists():
        df = pd.read_parquet(pq)
        if "ae" not in df.columns:
            df["ae"] = ""
        rows = F.apply_hospital_sites(df.to_dict(orient="records"), sites)
        out = pd.DataFrame(rows, columns=df.columns).sort_values("name")
        out.to_parquet(pq, index=False)
        print(f"{pq.relative_to(REPO)}: {len(df)} -> {len(out)}")

    targets = [REPO / "data" / "map" / "hospitals.js"]
    targets += sorted((REPO / "data" / "regions").glob("*/map/hospitals.js"))
    for js in targets:
        if not js.exists():
            continue
        recs = read_js(js)
        rows = [{"name": d["n"], "postcode": d["a"], "lat": d["lat"], "lng": d["lng"],
                 "type": d.get("t", ""), "ae": d.get("ae", "")} for d in recs]
        # ae comes back from an earlier run as true/false; the list re-sets it.
        rows = F.apply_hospital_sites(rows, sites)
        rows.sort(key=lambda r: r["name"].lower())
        write_js(js, [F.hosp_blob_row(r) for r in rows])
        print(f"{js.relative_to(REPO)}: {len(recs)} -> {len(rows)}")


if __name__ == "__main__":
    main()
