"""Temporary: print the shape of sources this sandbox cannot reach."""
import io, json, re, zipfile
import requests
S = requests.Session(); S.headers["User-Agent"] = "pophealthmap-probe"
def get(u, **k):
    try:
        r = S.get(u, timeout=120, **k); print("GET", r.status_code, u, len(r.content), r.headers.get("content-type")); return r
    except Exception as e:
        print("ERR", u, e); return None
print("=== Fingertips PCN -> practice")
r = get("https://fingertips.phe.org.uk/api/parent_to_child_areas?child_area_type_id=7&parent_area_type_id=204")
if r is not None and r.ok:
    j = r.json(); print(type(j).__name__, len(j)); items = list(j.items())[:2] if isinstance(j, dict) else j[:2]; print(items)
r = get("https://fingertips.phe.org.uk/api/areas/by_area_type?area_type_id=204")
if r is not None and r.ok: j=r.json(); print(len(j), j[:2])
print("=== ODS reports")
for rep in ["epcn", "epcncorepartners", "epcn_core_partners", "epracmem", "epraccur", "egpcur"]:
    r = get(f"https://www.odsdatasearchandexport.nhs.uk/api/getReport?report={rep}")
    if r is not None and r.ok: print(rep, r.text[:400].replace("\n", " || "))
print("=== Fingertips practice registered population quintiles / LSOA?")
r = get("https://fingertips.phe.org.uk/api/indicator_search?search_text=registered%20population%20LSOA")
if r is not None and r.ok: print(r.text[:600])
print("=== NHS England / other mirrors")
for u in ["https://www.england.nhs.uk/statistics/",
          "https://opendata.nhsbsa.net/api/3/action/package_search?q=lsoa%20registered",
          "https://www.opendatanhs.com/",
          "https://digital.nhs.uk/robots.txt"]:
    r = get(u)
    if r is not None and r.ok: print(r.text[:400].replace("\n", " "))
