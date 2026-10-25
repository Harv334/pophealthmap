"""GP-recorded prevalence estimates for MSOAs (run_qof_msoa): the House of
Commons Library's modelled share of patients on a GP register, for about 20
conditions, from QOF practice prevalence and where each practice's patients
live.

Offline. The fact-check note confirms the Library publishes MSOA (and
constituency and region) estimates for 20 conditions from QOF 2024/25, but
the download's layout was not seen. So two synthetic workbooks are tested, as
the Library's other dashboard downloads are often laid out: a wide sheet (an
"MSOA code" column, names, then one percentage column per condition beside
patient counts) and a long one (a "Condition" column beside "Prevalence
(%)"), each with a constituency sheet that has no MSOA codes. Both are
unverified here. The Library's page is answered by a fake session.

Covers the parsing (conditions by their words, diabetes kept apart from
non-diabetic hyperglycaemia, counts and standardised figures left out,
proportions turned into percentages), the period, the checks (2011 MSOAs, a
condition matched twice), ward and LA figures through the LSOAs weighted by
residents with the 80% cover rule, the MSOA file, finding the download on
the page, the skip with no file, and kept figures.
"""
import importlib.util
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
        dct.pop("qof_msoa", None)
    return tmp


class Resp:
    def __init__(self, status=200, body=b""):
        self.status_code, self.content = status, body
        self.ok = status < 400
        self.text = body.decode("utf-8", "replace")

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError(f"HTTP {self.status_code}")


class Sess:
    def __init__(self, routes):
        self.routes = routes

    def get(self, url, **kw):
        for k, v in self.routes.items():
            if url.startswith(k):
                return v
        raise RuntimeError(f"no internet here ({url})")


# Ten MSOAs, three of them drawn in 2021 (codes above E02006934).
MSOAS = [f"E0200{i:04d}" for i in range(1, 8)] + ["E02006940", "E02006941", "E02006942"]
CONDS = {"Asthma": 6.5, "Atrial fibrillation": 2.1, "Cancer": 3.4, "Coronary heart disease (CHD)": 3.0,
         "Chronic kidney disease (CKD)": 4.2, "COPD": 1.9, "Dementia": 0.8, "Depression": 13.0,
         "Diabetes": 7.5, "Epilepsy": 0.8, "Heart failure": 1.0, "Hypertension": 14.5,
         "Learning disability": 0.5, "Mental health": 1.0, "Non-diabetic hyperglycaemia": 6.0,
         "Obesity": 11.0, "Osteoporosis": 1.0, "Peripheral arterial disease": 0.6,
         "Rheumatoid arthritis": 0.8, "Stroke and TIA": 1.8, "Palliative care": 0.4}


def val(cond, i):
    return round(CONDS[cond] * (0.8 + 0.04 * i), 2)


def wide(scale=1.0):
    d = {"MSOA code": MSOAS, "MSOA name": [f"Place {i:03d}" for i in range(10)],
         "Constituency name": ["Somewhere"] * 10}
    for c in CONDS:
        d[f"{c} (%)"] = [val(c, i) * scale for i in range(10)]
        d[f"{c}: number of patients"] = [int(val(c, i) * 80) for i in range(10)]
    d["Hypertension, age-standardised (%)"] = [9.9] * 10
    return pd.DataFrame(d)


def long_():
    return pd.DataFrame([{"MSOA code": m, "MSOA name": f"Place {i}", "Condition": c,
                          "Prevalence (%)": val(c, i), "Estimated patients": int(val(c, i) * 80)}
                         for c in CONDS for i, m in enumerate(MSOAS)])


CONST = pd.DataFrame({"Constituency code": ["E14001001"], "Constituency name": ["Somewhere"], "Asthma (%)": [6.0]})

print("=== the conditions by their words ===")
for text, want in (("Asthma (%)", "qof_est_asthma_pct"), ("Diabetes", "qof_est_diabetes_pct"),
                   ("Non-diabetic hyperglycaemia", "qof_est_ndh_pct"), ("Stroke and TIA", "qof_est_stroke_pct"),
                   ("Mental health (SMI)", "qof_est_smi_pct"), ("Coronary heart disease (CHD)", "qof_est_chd_pct"),
                   ("Heart failure", "qof_est_hf_pct"), ("Palliative care", None), ("COPD and asthma", None),
                   ("Learning disabilities", "qof_est_ld_pct")):
    check(f"qof_condition({text!r}) is {want}", p.qof_condition(text) == want, str(p.qof_condition(text)))
check("the QOF year is read from text", p.qof_period("QOF 2024/25 estimates") == "2024/25"
      and p.qof_period("health-conditions-2024-25.xlsx") == "2024/25" and p.qof_period("2019 to 2023") == "")

print("=== the wide layout ===")
out, problems, matched = p.qof_msoa_parse([("MSOAs", "Constituency data: health conditions, QOF 2024/25", wide().astype(str)),
                                           ("Constituencies", "", CONST.astype(str))])
o = out.set_index("MSOA21CD")
check("all 20 conditions are found, from their percentage columns", len([k for k in p.QOF_MSOA_KEYS if k in o.columns]) == 20,
      f"{problems} {sorted(matched)}")
check("the figure is the percentage, not the count or the age-standardised figure",
      o.loc["E02000001", "qof_est_hypertension_pct"] == val("Hypertension", 0)
      and matched["qof_est_hypertension_pct"].endswith("Hypertension (%)"), str(matched.get("qof_est_hypertension_pct")))
check("diabetes and non-diabetic hyperglycaemia are kept apart",
      o.loc["E02000003", "qof_est_diabetes_pct"] == val("Diabetes", 2) and o.loc["E02000003", "qof_est_ndh_pct"] == val("Non-diabetic hyperglycaemia", 2))
check("a register that is not one of the twenty (palliative care) is not taken", not any("palliative" in k for k in o.columns))
check("only MSOA rows are read", list(o.index) == sorted(MSOAS))
prop, _p, _m = p.qof_msoa_parse([("MSOAs", "", wide(scale=0.01).astype(str))])
check("proportions are turned into percentages", abs(prop.set_index("MSOA21CD").loc["E02000001", "qof_est_asthma_pct"]
                                                       - val("Asthma", 0)) < 1e-6)

print("=== the long layout ===")
out_l, prob_l, m_l = p.qof_msoa_parse([("Data", "", long_().astype(str)), ("Constituencies", "", CONST.astype(str))])
ol = out_l.set_index("MSOA21CD")
check("a condition column beside a prevalence column reads the same",
      len([k for k in p.QOF_MSOA_KEYS if k in ol.columns]) == 20 and ol.loc["E02006942", "qof_est_copd_pct"] == val("COPD", 9),
      f"{prob_l} {m_l.get('qof_est_copd_pct')}")
check("the patient counts are not taken for the figure", m_l["qof_est_copd_pct"].endswith("Condition / Prevalence (%)"))

print("=== what is refused ===")
old = wide()
old["MSOA code"] = [f"E0200{i:04d}" for i in range(1, 11)]
check("a file on the 2011 MSOAs is refused when there is no lookup to the 2021 ones",
      raises(lambda: p.qof_msoa_parse([("x", "", old.astype(str))]), "2011")
      and raises(lambda: p.qof_msoa_parse([("x", "", old.astype(str))], bridge=lambda: {}), "2011"))
# With the ONS lookup (as the Library's GitHub output was, October 2026): an
# unchanged MSOA keeps its figure, a split one gives it to both parts, and a
# merged one takes the mean of its parents.
look = {"E02000001": ["E02000001"], "E02006950": ["E02000002"], "E02006951": ["E02000002"],
        "E02006952": ["E02000003", "E02000004"]}
br, prob_b, _m = p.qof_msoa_parse([("x", "", old.astype(str))], bridge=lambda: look)
key = next(k for k in p.QOF_MSOA_KEYS if k in br.columns)
o_ix = {c: float(v) for c, v in zip(old["MSOA code"], p.qof_msoa_parse([("y", "", wide().astype(str))])[0]
                                    .set_index("MSOA21CD")[key].reindex(MSOAS))}
b = br.set_index("MSOA21CD")[key]
check("a file on the 2011 MSOAs is bridged to the 2021 ones: kept, split and merged",
      sorted(br["MSOA21CD"]) == sorted(look) and abs(b["E02000001"] - o_ix["E02000001"]) < 1e-3
      and abs(b["E02006950"] - o_ix["E02000002"]) < 1e-3 and abs(b["E02006951"] - o_ix["E02000002"]) < 1e-3
      and abs(b["E02006952"] - (o_ix["E02000003"] + o_ix["E02000004"]) / 2) < 1e-3, str(b.to_dict()))
check("a file with no MSOA codes is refused", raises(lambda: p.qof_msoa_parse([("x", "", CONST.astype(str))]), "MSOA codes"))
twice = wide()
twice["Asthma prevalence"] = twice["Asthma (%)"]
_o, prob_t, _m = p.qof_msoa_parse([("x", "", twice.astype(str))])
check("a condition matched by two columns is reported and not used",
      "qof_est_asthma_pct" not in _o.columns and any("qof_est_asthma_pct" in x for x in prob_t), str(prob_t))

print("=== the source ===")
tmp = fresh()
(tmp / "cache/qof_msoa").mkdir(parents=True)
f = tmp / "cache/qof_msoa/constituency-health-conditions-2024-25.xlsx"
with pd.ExcelWriter(f) as xw:
    pd.DataFrame([["Constituency data: health conditions"], ["QOF 2024/25"]]).to_excel(xw, sheet_name="MSOAs", index=False, header=False)
    wide().to_excel(xw, sheet_name="MSOAs", index=False, startrow=3)
    CONST.to_excel(xw, sheet_name="Constituencies", index=False)
p.browser_session = lambda referer=None: Sess({})
res = p.run_qof_msoa()
check("a workbook in .cache/qof_msoa/ is read, title rows and all, with its period",
      res is not None and len(res) == 10 and set(res["period"]) == {"2024/25"}
      and (tmp / "data/healthcare" / p.QOF_MSOA_PARQUET).exists(), "" if res is None else str(res.columns.tolist()))
f.unlink()
kept = p.run_qof_msoa()
check("with no new file, last run's figures are kept and the note says so",
      kept is not None and len(kept) == 10 and "stay as last written" in p._SOURCE_NOTE.get("qof_msoa", ""))

tmp = fresh()
r = p.run_qof_msoa()
check("with no file at all: skipped, not failed, naming the qof-msoa-source release and the Library's page",
      r is None and "qof-msoa-source" in p._SOURCE_SKIPPED.get("qof_msoa", "")
      and p.QOF_MSOA_PAGES[0] in p._SOURCE_SKIPPED["qof_msoa"] and "qof_msoa" not in p._SOURCE_ERROR)

tmp = fresh()
xl = tmp / "dl.xlsx"
with pd.ExcelWriter(xl) as xw:
    wide().to_excel(xw, sheet_name="MSOAs", index=False)
url = "https://commonslibrary.parliament.uk/wp-content/uploads/2026/03/Health-conditions-MSOA-2024-25.xlsx"
page = (f'<html><a href="/about">About</a><a href="https://example.org/other.xlsx">Population data (Excel)</a>'
        f'<a href="{url}">Download the MSOA data (Excel)</a></html>').encode()
p.browser_session = lambda referer=None: Sess({p.QOF_MSOA_PAGES[0]: Resp(body=page), url: Resp(body=xl.read_bytes())})
check("the download is found on the Library's page by its name", p.qof_msoa_find() == [url], str(p.qof_msoa_find()))
res = p.run_qof_msoa()
check("and is fetched and read", res is not None and len(res) == 10 and set(res["period"]) == {"2024/25"})

print("=== wards, LAs and MSOAs ===")
tmp = fresh()
# Six LSOAs: four in MSOA E02006940 and two in E02006941, which has no
# figure for asthma.
L2M = {"E01000001": "E02006940", "E01000002": "E02006940", "E01000003": "E02006940",
       "E01000004": "E02006940", "E01000005": "E02006941", "E01000006": "E02006941"}
L2W = {"E01000001": "W1", "E01000002": "W1", "E01000003": "W2", "E01000004": "W2", "E01000005": "W2", "E01000006": "W1"}
POP = {"E01000001": 1000, "E01000002": 1000, "E01000003": 1000, "E01000004": 1000, "E01000005": 1000, "E01000006": 4000}
pd.DataFrame({"MSOA21CD": ["E02006940", "E02006941"], "qof_est_depression_pct": [10.0, 20.0],
              "qof_est_asthma_pct": [6.0, None], "period": ["2024/25", "2024/25"]}).to_parquet(
    tmp / "data/healthcare" / p.QOF_MSOA_PARQUET, index=False)
vals, notes, covers = p.places_figures(L2W, POP, what="ward", lsoa_to_msoa=L2M)
check("a ward blends its MSOAs by where its residents live ((10 x 2,000 + 20 x 4,000) / 6,000 = 16.67)",
      vals["W1"]["qof_est_depression_pct"] == 16.67 and vals["W2"]["qof_est_depression_pct"] == 13.33, str(vals))
check("a ward whose residents are mostly in an MSOA with no figure has none (W1: 2,000 of 6,000 covered)",
      "qof_est_asthma_pct" not in vals["W1"] and vals["W2"].get("qof_est_asthma_pct") is None
      and covers["qof_est_asthma_pct"][1]["W2"] == 2 / 3, str((vals, covers.get("qof_est_asthma_pct"))))
check("the notes carry the QOF year", notes.get("qof_msoa_period") == "2024/25")
la, _n, _c = p.places_figures({c: "E09000001" for c in L2M}, POP, what="LA", lsoa_to_msoa=L2M)
check("an LA's is its MSOAs' weighted by residents ((10 x 4,000 + 20 x 5,000) / 9,000 = 15.56)",
      la["E09000001"]["qof_est_depression_pct"] == 15.56, str(la))
check("an MSOA carries its own published figure", p.places_msoa_values({"E02006940"}) ==
      {"E02006940": {"qof_est_depression_pct": 10.0, "qof_est_asthma_pct": 6.0}}, str(p.places_msoa_values({"E02006940"})))
check("the QOF year is the period of every condition the file has",
      p.places_periods().get("qof_est_depression_pct") == "2024/25" and "qof_est_cancer_pct" not in p.places_periods())

print("=== registered as a source ===")
check("qof_msoa is a source of its own", p.SOURCES.get("qof_msoa") is p.run_qof_msoa)

print(f"\n{'All checks passed' if not FAILS else f'{len(FAILS)} failed'}")
sys.exit(1 if FAILS else 0)
