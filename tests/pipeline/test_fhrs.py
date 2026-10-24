"""Food hygiene ratings (run_fhrs): the Food Standards Agency's register of
food businesses, counted by LSOA into takeaways and sandwich shops, pubs,
bars and nightclubs, and businesses rated 0 to 2, then added up for wards
and LAs.

Offline. The synthetic files follow the FSA layouts as the fact-check note
and the FSA's API guidance describe them, as best recalled: the API's
/Authorities list (Name, RegionName, FileName, LocalAuthorityId), each
council's open data XML file (FHRSEstablishment, Header/ExtractDate, then
EstablishmentDetail elements with FHRSID, BusinessName, BusinessType,
RatingValue, PostCode, LocalAuthorityName and Geocode/Longitude and
Latitude), and the API's JSON establishments. Unverified here. The API is
answered by a fake session; the LSOAs are squares.

Covers the matching of FSA's council names to the scope's LAs, the business
types and ratings, both file layouts, placing by coordinates and by postcode,
the counting (a business in two files once, one outside the scope not at
all), an LA whose file could not be had (its LSOAs left out and the source
failed, never counted as having none), the files attached by hand when FSA
does not answer, the skip with nothing at all, and ward and LA figures from
summed counts.
"""
import importlib.util
import json
import pathlib
import sys
import tempfile

import pandas as pd

REPO = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("p", REPO / "fetch_all_data.py")
p = importlib.util.module_from_spec(spec); sys.modules["p"] = p; spec.loader.exec_module(p)

FAILS = []


def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + (f"\n          {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(label)


def raises(fn, contains=""):
    try:
        fn()
    except RuntimeError as e:
        return contains.lower() in str(e).lower()
    return False


class Resp:
    def __init__(self, status=200, body=b"", js=None):
        self.status_code, self.content, self._js = status, body, js
        self.ok = status < 400
        self.text = body.decode("utf-8", "replace") if body else ""

    def json(self):
        if self._js is None:
            raise ValueError("not json")
        return self._js

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError(f"HTTP {self.status_code}")


class Sess:
    def __init__(self, routes):
        self.routes, self.asked = routes, []

    def get(self, url, **kw):
        self.asked.append((url, kw.get("headers")))
        for k, v in self.routes.items():
            if url.startswith(k):
                return v
        raise RuntimeError(f"no internet here ({url})")


# Two LAs, each of two square LSOAs; one ward each.
LADS = {"E09000001": "City of London", "E09000002": "Barking and Dagenham"}
SQ = {"E01000001": (-0.10, 51.50), "E01000002": (-0.08, 51.50), "E01000003": (0.10, 51.55), "E01000004": (0.12, 51.55)}
LOOKUP = {"E01000001": ("W1", "E09000001"), "E01000002": ("W1", "E09000001"),
          "E01000003": ("W2", "E09000002"), "E01000004": ("W2", "E09000002")}
POP = {"E01000001": 1000, "E01000002": 1000, "E01000003": 2000, "E01000004": 2000}


def fresh():
    tmp = pathlib.Path(tempfile.mkdtemp())
    p.REPO_ROOT = tmp
    p.CACHE_DIR = tmp / "cache"
    p.DATA_DIR = tmp / "data"
    p.NATIONAL_DATA_DIR = tmp / "data"
    p.CUSTOM_DIR = tmp / "data" / "custom"
    for d in ("boundaries", "environment", "demographics", "outcomes", "healthcare", "economy"):
        (tmp / "data" / d).mkdir(parents=True, exist_ok=True)
    for dct in (p._SOURCE_ERROR, p._SOURCE_PARTIAL, p._SOURCE_NOTE, p._SOURCE_SKIPPED):
        dct.pop("fhrs", None)
    (tmp / "data/boundaries/lsoa.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"LSOA21CD": c},
         "geometry": {"type": "Polygon", "coordinates": [[[x, y], [x + 0.02, y], [x + 0.02, y + 0.02], [x, y + 0.02], [x, y]]]}}
        for c, (x, y) in SQ.items()]}), encoding="utf-8")
    p.get_lsoa_ward_lookup = lambda: dict(LOOKUP)
    p.get_lsoa_to_ward = lambda: {l: w for l, (w, _) in LOOKUP.items()}
    p.get_lsoa_to_msoa = lambda: {l: "E02000001" for l in LOOKUP}
    p._census_ward_inputs = lambda: (None, None, {})
    p.get_oa_to_ward = lambda: {}
    p._ward_to_lad = lambda: {"W1": "E09000001", "W2": "E09000002"}
    p.SCOPE_LADS = set(LADS)
    p.LAD_NAMES = dict(LADS)
    p.SCOPE_REGION = "E12000007"
    return tmp


def xml(la, rows, extract="2026-10-20"):
    def one(r):
        geo = (f"<Geocode><Longitude>{r['lng']}</Longitude><Latitude>{r['lat']}</Latitude></Geocode>"
               if r.get("lng") is not None else "<Geocode />")
        return (f"<EstablishmentDetail><FHRSID>{r['id']}</FHRSID><LocalAuthorityBusinessID>x</LocalAuthorityBusinessID>"
                f"<BusinessName>{r['name']}</BusinessName><BusinessType>{r['type']}</BusinessType>"
                f"<BusinessTypeID>1</BusinessTypeID><PostCode>{r.get('pc', '')}</PostCode>"
                f"<RatingValue>{r['rating']}</RatingValue><RatingKey>fhrs_x_en-GB</RatingKey>"
                f"<LocalAuthorityCode>500</LocalAuthorityCode><LocalAuthorityName>{la}</LocalAuthorityName>"
                f"<Scores><Hygiene>5</Hygiene></Scores><SchemeType>FHRS</SchemeType>"
                f"<NewRatingPending>False</NewRatingPending>{geo}</EstablishmentDetail>")
    return ('<?xml version="1.0" encoding="utf-8"?><FHRSEstablishment><Header>'
            f"<ExtractDate>{extract}</ExtractDate><ItemCount>{len(rows)}</ItemCount><ReturnCode>Success</ReturnCode>"
            "</Header><EstablishmentCollection>" + "".join(one(r) for r in rows)
            + "</EstablishmentCollection></FHRSEstablishment>").encode()


# City of London's file: two takeaways, a pub, a restaurant rated 1 and an
# exempt shop in E01000001, a cafe rated 0 in E01000002, and one business
# just outside every LSOA.
CITY = [
    {"id": 1, "name": "Chicken Spot", "type": "Takeaway/sandwich shop", "rating": "2", "lng": -0.095, "lat": 51.505},
    {"id": 2, "name": "Kebab House", "type": "Takeaway/sandwich shop", "rating": "5", "lng": -0.091, "lat": 51.509},
    {"id": 3, "name": "The Crown", "type": "Pub/bar/nightclub", "rating": "4", "lng": -0.092, "lat": 51.511},
    {"id": 4, "name": "Bistro", "type": "Restaurant/Cafe/Canteen", "rating": "1", "lng": -0.093, "lat": 51.512},
    {"id": 5, "name": "Corner Shop", "type": "Retailers - other", "rating": "Exempt", "lng": -0.094, "lat": 51.513},
    {"id": 6, "name": "Cafe", "type": "Restaurant/Cafe/Canteen", "rating": "0", "lng": -0.075, "lat": 51.505},
    {"id": 7, "name": "Elsewhere", "type": "Takeaway/sandwich shop", "rating": "3", "lng": -1.5, "lat": 53.0},
]
# Barking and Dagenham's: a takeaway with no coordinates (placed by its
# postcode), a nightclub awaiting inspection, and the City's pub again.
BD = [
    {"id": 20, "name": "Pizza", "type": "Takeaway/sandwich shop", "rating": "AwaitingInspection", "lng": None, "pc": "IG11 7AA"},
    {"id": 21, "name": "Club", "type": "Pub/bar/nightclub", "rating": "AwaitingInspection", "lng": 0.105, "lat": 51.555},
    {"id": 3, "name": "The Crown", "type": "Pub/bar/nightclub", "rating": "4", "lng": -0.092, "lat": 51.511},
]
AUTHS = {"authorities": [
    {"LocalAuthorityId": 1, "Name": "City of London Corporation", "RegionName": "London",
     "FileName": "http://ratings.food.gov.uk/OpenDataFiles/FHRS508en-GB.xml"},
    {"LocalAuthorityId": 2, "Name": "Barking and Dagenham", "RegionName": "London",
     "FileName": "http://ratings.food.gov.uk/OpenDataFiles/FHRS509en-GB.xml"},
    {"LocalAuthorityId": 3, "Name": "Leeds", "RegionName": "Yorkshire and Humberside",
     "FileName": "http://ratings.food.gov.uk/OpenDataFiles/FHRS413en-GB.xml"},
]}

print("=== FSA council names and the scope's LAs ===")
m = p.fhrs_match(["City of London Corporation", "Bristol", "Hull City", "Richmond-Upon-Thames", "Richmondshire",
                  "Durham", "Barking and Dagenham", "Newcastle-Upon-Tyne", "Newcastle-under-Lyme"],
                 {"E09000001": "City of London", "E06000023": "Bristol, City of", "E06000010": "Kingston upon Hull, City of",
                  "E09000027": "Richmond upon Thames", "E06000047": "County Durham", "E09000002": "Barking and Dagenham",
                  "E08000021": "Newcastle upon Tyne"})
check("councils are matched by the words that tell them apart",
      m == {"E09000001": ["City of London Corporation"], "E06000023": ["Bristol"], "E06000010": ["Hull City"],
            "E09000027": ["Richmond-Upon-Thames"], "E06000047": ["Durham"], "E09000002": ["Barking and Dagenham"],
            "E08000021": ["Newcastle-Upon-Tyne"]}, str(m))
check("a rating is 0 to 5, anything else none", [p.fhrs_rating(v) for v in ("0", "5", "Exempt", "AwaitingInspection", "Pass", "")]
      == [0, 5, None, None, None, None])
check("business types by their words", [p.fhrs_kind(t) for t in ("Takeaway/sandwich shop", "Pub/bar/nightclub",
                                                                   "Restaurant/Cafe/Canteen", "Retailers - other")]
      == ["takeaway", "pub", "", ""])

print("=== the two file layouts ===")
tmp = fresh()
fx = tmp / "city.xml"
fx.write_bytes(xml("City of London Corporation", CITY))
recs, ex = p.fhrs_read(fx)
check("the open data XML: every business, its type, rating, place and council, and the extract date",
      len(recs) == 7 and ex == "2026-10-20" and recs[0]["type"] == "Takeaway/sandwich shop" and recs[0]["rating"] == "2"
      and recs[0]["lat"] == 51.505 and recs[0]["la"] == "City of London Corporation", str(recs[:1]))
fj = tmp / "api.json"
fj.write_text(json.dumps({"establishments": [{"FHRSID": 9, "BusinessName": "X", "BusinessType": "Pub/bar/nightclub",
                                              "RatingValue": "3", "PostCode": "E1 1AA", "LocalAuthorityName": "Tower Hamlets",
                                              "geocode": {"longitude": "-0.05", "latitude": "51.52"}}],
                          "meta": {"extractDate": "2026-10-21T00:00:00"}}), encoding="utf-8")
rj, exj = p.fhrs_read(fj)
check("the API's JSON reads the same", len(rj) == 1 and rj[0]["lng"] == -0.05 and rj[0]["rating"] == "3" and exj == "2026-10-21")
check("a point is placed in the LSOA holding it, and one outside every LSOA in none",
      p.points_in_lsoas([-0.095, 0.11, -1.5], [51.505, 51.56, 53.0]) == ["E01000001", "E01000003", ""])

print("=== the source ===")
tmp = fresh()
city_url, bd_url = AUTHS["authorities"][0]["FileName"], AUTHS["authorities"][1]["FileName"]
sess = Sess({f"{p.FHRS_API}/Authorities": Resp(js=AUTHS), city_url: Resp(body=xml("City of London Corporation", CITY)),
             bd_url: Resp(body=xml("Barking and Dagenham", BD, extract="2026-10-22"))})
p.requests.Session = lambda: sess
p.lookup_postcodes = lambda pcs, source="", owners=None: {"IG117AA": (51.56, 0.11, "E01000003", "E09000002", "W2")}
out = p.run_fhrs()
o = out.set_index("LSOA21CD")
check("FSA's API is asked with its version header", any(h and h.get("x-api-version") == "2" for _u, h in sess.asked))
check("the scope's councils are read, and not a council outside its region", not any("FHRS413" in u for u, _h in sess.asked))
check("businesses are counted in their LSOAs: two takeaways, a pub, four rated, two of them 0 to 2 in E01000001",
      o.loc["E01000001"].to_dict() | {} == {**o.loc["E01000001"].to_dict(), "fhrs_takeaways": 2, "fhrs_pubs": 1,
                                             "fhrs_rated": 4, "fhrs_rated_0_2": 2, "fhrs_food_businesses": 5},
      str(o.loc["E01000001"].to_dict()))
check("a business with no coordinates is placed by its postcode; awaiting inspection is not a rating",
      o.loc["E01000003", "fhrs_takeaways"] == 1 and o.loc["E01000003", "fhrs_pubs"] == 1 and o.loc["E01000003", "fhrs_rated"] == 0)
check("a business in two files is counted once, and one outside the scope not at all",
      int(o["fhrs_pubs"].sum()) == 2 and int(o["fhrs_takeaways"].sum()) == 3)
check("an LSOA with none is there with none", o.loc["E01000004", "fhrs_food_businesses"] == 0)
check("the date is the newest extract's", set(out["fhrs_as_at"]) == {"2026-10-22"})
check("nothing is reported when every LA is covered", "fhrs" not in p._SOURCE_ERROR)

tmp = fresh()
sess2 = Sess({f"{p.FHRS_API}/Authorities": Resp(js=AUTHS), city_url: Resp(body=xml("City of London Corporation", CITY)),
              bd_url: Resp(status=503), f"{p.FHRS_API}/Establishments": Resp(status=503)})
p.requests.Session = lambda: sess2
out2 = p.run_fhrs()
check("an LA whose file could not be had: its LSOAs are left out, not counted as having none, and the source fails",
      sorted(out2["LSOA21CD"]) == ["E01000001", "E01000002"] and "Barking and Dagenham" in p._SOURCE_ERROR.get("fhrs", ""),
      str(p._SOURCE_ERROR.get("fhrs")))

tmp = fresh()
sess3 = Sess({f"{p.FHRS_API}/Authorities": Resp(js=AUTHS), city_url: Resp(status=404), bd_url: Resp(status=404),
              f"{p.FHRS_API}/Establishments": Resp(js={"establishments": [
                  {"FHRSID": 30, "BusinessName": "Y", "BusinessType": "Takeaway/sandwich shop", "RatingValue": "1",
                   "LocalAuthorityName": "City of London Corporation", "geocode": {"longitude": -0.09, "latitude": 51.51}}],
                  "meta": {"totalPages": 1}})})
p.requests.Session = lambda: sess3
out3 = p.run_fhrs()
check("without an open data file, the API's pages are read instead",
      int(out3.set_index("LSOA21CD").loc["E01000001", "fhrs_takeaways"]) == 1)

tmp = fresh()
p.requests.Session = lambda: Sess({})
r = p.run_fhrs()
check("FSA unreachable and nothing in .cache/fhrs/: skipped, not failed, naming the fhrs-source release",
      r is None and "fhrs-source" in p._SOURCE_SKIPPED.get("fhrs", "") and "fhrs" not in p._SOURCE_ERROR)
(tmp / "cache/fhrs").mkdir(parents=True, exist_ok=True)
(tmp / "cache/fhrs/FHRS508en-GB.xml").write_bytes(xml("City of London Corporation", CITY))
(tmp / "cache/fhrs/FHRS509en-GB.xml").write_bytes(xml("Barking and Dagenham", BD))
out4 = p.run_fhrs()
check("FSA unreachable, files attached by hand: they are read and both LAs covered",
      out4 is not None and len(out4) == 4 and "fhrs" not in p._SOURCE_ERROR)

print("=== wards and LAs ===")
tmp = fresh()
p.FHRS_MIN_RATED = 3
pd.DataFrame({"LSOA21CD": list(SQ), "fhrs_food_businesses": [10, 4, 2, 0], "fhrs_takeaways": [3, 1, 2, 0],
              "fhrs_pubs": [2, 0, 1, 0], "fhrs_rated": [8, 2, 2, 0], "fhrs_rated_0_2": [2, 1, 1, 0],
              "fhrs_as_at": ["2026-10-22"] * 4}).to_parquet(tmp / "data/environment" / p.FHRS_PARQUET, index=False)
L2W = {l: w for l, (w, _) in LOOKUP.items()}
vals, notes, _c = p.places_figures(L2W, POP, what="ward")
check("takeaways per 1,000 residents from summed counts (4 / 2,000 = 2.0; 2 / 4,000 = 0.5)",
      vals["W1"]["fhrs_takeaways_per_1000"] == 2.0 and vals["W2"]["fhrs_takeaways_per_1000"] == 0.5, str(vals))
check("pubs per 1,000 (2 / 2,000 = 1.0)", vals["W1"]["fhrs_pubs_per_1000"] == 1.0)
check("the share rated 0 to 2 is of the rated businesses added up (3 of 10 = 30.0%)", vals["W1"]["fhrs_low_hygiene_pct"] == 30.0)
check("a ward with fewer rated businesses than the minimum has no share (W2: 2)", "fhrs_low_hygiene_pct" not in vals["W2"])
check("the notes carry the date", notes.get("fhrs_as_at") == "2026-10-22")
la, _n, _c = p.places_figures({l: "E09000001" for l in SQ}, POP, what="LA")
check("an LA's from all its LSOAs (6 takeaways / 6,000 = 1.0; 4 of 12 rated = 33.3%)",
      la["E09000001"]["fhrs_takeaways_per_1000"] == 1.0 and la["E09000001"]["fhrs_low_hygiene_pct"] == 33.3, str(la))
check("no LSOA figures: the counts are too small", not any(k.startswith("fhrs_") for v in p.places_lsoa_values().values() for k in v))
check("the extract date is the period", p.places_periods().get("fhrs_takeaways_per_1000") == "as at Oct 2026")

print("=== registered as a source ===")
check("fhrs is a source of its own, after the boundaries it places businesses in",
      list(p.SOURCES).index("boundaries") < list(p.SOURCES).index("fhrs") and p.SOURCES.get("fhrs") is p.run_fhrs)

print(f"\n{'All checks passed' if not FAILS else f'{len(FAILS)} failed'}")
sys.exit(1 if FAILS else 0)
