"""Temporary: print the shape of sources this sandbox cannot reach."""
import io, json, re, zipfile
import requests
S = requests.Session(); S.headers["User-Agent"] = "pophealthmap-probe"
def get(u, **k):
    try:
        r = S.get(u, timeout=120, **k); print("GET", r.status_code, u, len(r.content)); return r
    except Exception as e:
        print("ERR", u, e); return None
print("=== Fingertips area types with MSOA")
r = get("https://fingertips.phe.org.uk/api/area_types")
if r is not None and r.ok:
    for a in r.json():
        if "msoa" in json.dumps(a).lower() or "middle" in json.dumps(a).lower(): print(a)
for ind in (93233, 93283, 93097):
    print("=== available_data", ind)
    r = get(f"https://fingertips.phe.org.uk/api/available_data?indicator_id={ind}")
    if r is not None and r.ok: print(r.text[:1500])
print("=== csv 93233 type 3")
r = get("https://fingertips.phe.org.uk/api/all_data/csv/by_indicator_id?indicator_ids=93233&child_area_type_id=3&parent_area_type_id=502")
if r is not None and r.ok:
    import pandas as pd
    df = pd.read_csv(io.BytesIO(r.content), dtype=str)
    print(df.columns.tolist()); print(df["Area Type"].value_counts().head(10) if "Area Type" in df else "")
    print(df[df["Area Type"].str.contains("MSOA", na=False)].head(3).to_string())
    m = df[df["Area Type"].str.contains("MSOA", na=False)]
    print("msoa rows", len(m), "periods", m["Time period"].unique()[:10], "codes sample", m["Area Code"].head(5).tolist())
print("=== metadata 93283,93233")
r = get("https://fingertips.phe.org.uk/api/indicator_metadata/by_indicator_id?indicator_ids=93283,93233")
if r is not None and r.ok:
    j = r.json()
    for k, v in j.items():
        print(k, list(v.keys()))
        d = v.get("Descriptive", {})
        print(" Descriptive keys:", list(d.keys()))
        for kk, vv in d.items(): print("   ", kk, ":", str(vv)[:200].replace("\n", " "))
        for kk in v:
            if kk != "Descriptive": print("  ", kk, ":", str(v[kk])[:200])
        break
print("=== ONS services with MSOA11")
r = get("https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services?f=json")
if r is not None and r.ok:
    names = [s["name"] for s in r.json().get("services", [])]
    print(len(names), "services")
    for n in names:
        if re.search(r"MSOA11.*MSOA21|LSOA11.*LSOA21", n, re.I): print("  ", n)
print("=== NHS Digital GP registrations")
r = get("https://digital.nhs.uk/data-and-information/publications/statistical/patients-registered-at-a-gp-practice")
if r is not None and r.ok:
    links = sorted(set(re.findall(r'href="([^"]+)"', r.text)))
    pubs = [l for l in links if "patients-registered-at-a-gp-practice/" in l]
    print(pubs[:8])
    if pubs:
        u = pubs[0] if pubs[0].startswith("http") else "https://digital.nhs.uk" + pubs[0]
        r2 = get(u)
        if r2 is not None and r2.ok:
            for l in sorted(set(re.findall(r'href="([^"]+)"', r2.text))):
                if re.search(r"files\.digital|\.zip|\.csv", l): print("  ", l)
print("=== ODS epcn")
for u in ["https://digital.nhs.uk/services/organisation-data-service/export-data-files/csv-downloads/gp-and-gp-practice-related-data",
          "https://files.digital.nhs.uk/assets/ods/current/epcn.zip"]:
    r = get(u)
    if r is not None and r.ok and u.endswith(".zip"):
        z = zipfile.ZipFile(io.BytesIO(r.content)); print(z.namelist())
        for n in z.namelist():
            if n.lower().endswith((".csv", ".xlsx")): print(n, z.read(n)[:600])
    elif r is not None and r.ok:
        for l in sorted(set(re.findall(r'href="([^"]+)"', r.text))):
            if re.search(r"epcn|pcn", l, re.I): print("  ", l)
