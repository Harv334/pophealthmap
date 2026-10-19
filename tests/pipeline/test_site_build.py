"""The deploy's own steps, offline, on a made-up site folder.

  scripts/stamp_site.py        the sitemap lists each published region and is
                               dated from the manifests, and the Dataset's
                               dateModified is the manifest's last_run
  scripts/write_pack_files.py  a region manifest lists exactly the pack's files
  scripts/compact_tables.py    the LSOA and MSOA figures in column form come
                               back as the same records
  scripts/build_layer_counts.py --region reads a region's own layout
  scripts/build_fonts.py       one face per family and subset, with a weight
                               range, where Google serves one file for several

    python3 tests/pipeline/test_site_build.py
"""
import json
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from worker_harness import Checks  # noqa: E402
import stamp_site  # noqa: E402
import write_pack_files  # noqa: E402
import compact_tables  # noqa: E402
import build_layer_counts  # noqa: E402
import build_fonts  # noqa: E402

check = Checks()

with tempfile.TemporaryDirectory() as d:
    out = pathlib.Path(d)
    (out / "data/meta").mkdir(parents=True)
    (out / "data/meta/manifest.json").write_text(json.dumps({"last_run": "2026-10-19T19:11:56+00:00"}))
    ne = out / "data/regions/north-east"
    (ne / "map").mkdir(parents=True)
    (ne / "meta").mkdir()
    (ne / "map/wards.js").write_text("var GJ = {};")
    (ne / "meta/manifest.json").write_text(json.dumps({"last_run": "2026-10-09T21:09:32+00:00"}))
    (ne / "ward_data.json").write_text("{}")
    (ne / "notes.parquet").write_text("x")
    eng = out / "data/regions/england/map"
    eng.mkdir(parents=True)
    (eng / "wards.js").write_text("var GJ = {};")
    (out / "sitemap.xml").write_text(
        "<urlset><url><loc>https://pophealth.uk/privacy.html</loc>\n<lastmod>2026-10-13</lastmod></url></urlset>")
    (out / "index.html").write_text('<script type="application/ld+json">{"dateModified": "2026-08-10"}</script>')
    stamp_site.main(["stamp_site.py", str(out)])
    sm = (out / "sitemap.xml").read_text()
    check("the sitemap lists the home page dated from London's manifest",
          "<loc>https://pophealth.uk/</loc>\n    <lastmod>2026-10-19</lastmod>" in sm, sm)
    check("each published region has its own entry, dated from its manifest",
          "<loc>https://pophealth.uk/?r=north-east</loc>\n    <lastmod>2026-10-09</lastmod>" in sm, sm)
    check("the England view, which asks before it loads, is left out", "?r=england" not in sm, sm)
    check("a static page keeps the date given for it", "<lastmod>2026-10-13</lastmod>" in sm, sm)
    check("the Dataset dateModified is the manifest's last_run",
          '"dateModified": "2026-10-19"' in (out / "index.html").read_text())

    write_pack_files.write_files(ne, ["ward_data.json", "map", "meta", "absent.json"])
    files = json.loads((ne / "meta/manifest.json").read_text())["files"]
    check("a region manifest lists exactly the packed files",
          files == ["map/wards.js", "meta/manifest.json", "ward_data.json"], json.dumps(files))
    check("the manifest keeps its last_run", json.loads((ne / "meta/manifest.json").read_text())["last_run"].startswith("2026-10-09"))

    recs = {"E01000001": {"imd_score": 8.742, "health_score": -1.771, "census_population": 1475},
            "E01000002": {"imd_score": 6.1, "name": "Somewhere"}}
    (out / "lsoa_data.json").write_text(json.dumps(recs))
    compact_tables.compact(out / "lsoa_data.json")
    doc = json.loads((out / "lsoa_data.json").read_text())
    check("the LSOA figures are written as columns",
          doc.get("format") == "columns" and doc["codes"] == ["E01000001", "E01000002"]
          and doc["cols"]["census_population"] == [1475, None], json.dumps(doc))
    check("the columns come back as the same records, missing figures left out",
          compact_tables.from_columns(doc) == recs)
    check("a file already in columns is left as it is", compact_tables.compact(out / "lsoa_data.json") is None)

    (ne / "dental_practices.json").write_text(json.dumps([{"lat": 54.9, "lng": -1.6}, {"lat": 0, "lng": 1}]))
    (ne / "map/msoa_boundaries.json").write_text(json.dumps({"features": [{}, {}, {}]}))
    c = build_layer_counts.count_layers(ne, region=True)
    check("layer counts read a region's own folder", c.get("dental") == 1 and c.get("msoa") == 3, json.dumps(c))

faces = [("latin", "Inter", w, "https://x/inter.woff2", "@font-face { font-weight: %s; src: url(https://x/inter.woff2); }" % w)
         for w in ("400", "500", "600", "700")]
faces.append(("latin", "Mono", "400", "https://x/m400.woff2", "@font-face { font-weight: 400; src: url(https://x/m400.woff2); }"))
faces.append(("latin", "Mono", "500", "https://x/m500.woff2", "@font-face { font-weight: 500; src: url(https://x/m500.woff2); }"))
merged = build_fonts.merge_faces(faces)
check("one variable font file becomes one face with a weight range",
      merged[0][:4] == ("latin", "Inter", 400, 700) and len(merged) == 3, json.dumps([m[:4] for m in merged]))
css = build_fonts.face_css("latin", "Inter", 400, 700, "Inter-latin.woff2", faces[0][4])
check("the face reads font-weight: 400 700 and names the local file",
      "font-weight: 400 700;" in css and "url(fonts/Inter-latin.woff2)" in css, css)

check.done()
