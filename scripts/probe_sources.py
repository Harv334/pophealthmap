"""Temporary: print the shape of sources this sandbox cannot reach."""
import io, json, re, zipfile
import requests
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
S = requests.Session(); S.headers.update({"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,application/json,*/*", "Accept-Language": "en-GB,en;q=0.9"})
def get(u, **k):
    try:
        r = S.get(u, timeout=120, **k); print("GET", r.status_code, u, len(r.content), r.headers.get("server")); return r
    except Exception as e:
        print("ERR", u, e); return None
print("=== ft metadata")
r = get("https://fingertips.phe.org.uk/api/indicator_metadata/by_indicator_id?indicator_ids=93089,93097,93098,93105,93106,93107,93108,93114,93115,93219,93224,93227,93229,93231,93232,93233,93239,93240,93241,93250,93252,93253,93254,93255,93256,93257,93259,93260,93280,93283,93465,93480")
if r is not None and r.ok:
    out = {}
    for k, v in r.json().items():
        out[k] = {"vt": (v.get("ValueType") or {}).get("Name"), "u": (v.get("Unit") or {}).get("Label"),
                  "yt": (v.get("YearType") or {}).get("Name"), "src": (v.get("Descriptive") or {}).get("DataSource"),
                  "def": (v.get("Descriptive") or {}).get("Definition"), "up": (v.get("DataChange") or {}).get("LastUploadedAt")}
    print("FTMETA " + json.dumps(out))
print("=== ft msoa row counts per indicator")
for ind in "93089,93097,93098,93105,93106,93107,93108,93114,93115,93219,93224,93227,93229,93231,93232,93233,93239,93240,93241,93250,93252,93253,93254,93255,93256,93257,93259,93260,93280,93283,93465,93480".split(","):
    r = get(f"https://fingertips.phe.org.uk/api/all_data/csv/by_indicator_id?indicator_ids={ind}&child_area_type_id=3&parent_area_type_id=15")
    if r is not None and r.ok:
        import pandas as pd
        df = pd.read_csv(io.BytesIO(r.content), dtype=str)
        m = df[df["Area Type"].str.contains("MSOA", na=False)]
        print("ROWS", ind, len(m), m["Area Code"].nunique(), list(m["Time period"].unique())[:3])
print("=== ft area types PCN / practice")
r = get("https://fingertips.phe.org.uk/api/area_types")
if r is not None and r.ok:
    for a in r.json():
        if re.search(r"pcn|primary care|gp|practice|icb|sub", json.dumps(a), re.I): print(a)
print("=== NHS Digital with browser UA")
for u in ["https://digital.nhs.uk/data-and-information/publications/statistical/patients-registered-at-a-gp-practice",
          "https://files.digital.nhs.uk/assets/ods/current/epcn.zip",
          "https://directory.spineservices.nhs.uk/ORD/2-0-0/organisations?PrimaryRoleId=RO272&Limit=5",
          "https://directory.spineservices.nhs.uk/ORD/2-0-0/organisations/A81001",
          "https://www.odsdatasearchandexport.nhs.uk/api/getReport?report=epcn"]:
    r = get(u)
    if r is not None and r.ok: print(r.text[:1200])
