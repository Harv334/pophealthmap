"""A Fingertips refresh that comes back thin keeps last run's figures.

The October 2026 refresh lost six Local Health indicators outright, cut five
more from 963 MSOAs to 77 and dropped 115 profile indicators, and every source
still reported ok. carry_forward_thin is the guard: run it against a small
previous file and a short new frame, and check it keeps what it should, drops
nothing it was not asked about, and flags the source.
"""
import sys, tempfile
import pathlib as _pl

REPO = _pl.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
import pandas as pd
import fetch_all_data as F

failures = 0


def check(ok, label, detail=""):
    global failures
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(': ' + detail) if detail and not ok else ''}")
    if not ok:
        failures += 1


def frame(spec):
    """{indicator: (n_areas, period)} -> one row per area."""
    return pd.DataFrame([{"indicator_id": k, "area": f"A{i}", "value": float(i), "period": per}
                         for k, (n, per) in spec.items() for i in range(n)])


with tempfile.TemporaryDirectory() as tmp:
    tmp = _pl.Path(tmp)
    path = tmp / "prev.parquet"
    frame({1: (100, "2024"), 2: (100, "2024"), 3: (100, "2024"), 9: (100, "2019")}).to_parquet(path)
    cache = {k: tmp / f"ind_{k}.csv" for k in (1, 2, 3)}
    for p in cache.values():
        p.write_text("x")

    # 1 refreshed fully, 2 came back with 7 areas, 3 is missing, 9 was retired.
    new = frame({1: (100, "2025"), 2: (7, "2025")})
    F._SOURCE_PARTIAL.clear()
    out = F.carry_forward_thin(path, new, key="indicator_id", source="t",
                               requested={1, 2, 3}, cache_file=lambda k: cache[k])
    n = out["indicator_id"].value_counts()

    print("=== thin and missing indicators keep last run's rows ===")
    check(n.get(1) == 100 and set(out.loc[out.indicator_id == 1, "period"]) == {"2025"},
          "a full refresh is used as is")
    check(n.get(2) == 100 and set(out.loc[out.indicator_id == 2, "period"]) == {"2024"},
          "a thin indicator is replaced whole by last run's rows, with last run's period",
          str(n.get(2)))
    check(n.get(3) == 100, "a missing indicator is carried forward")
    check(9 not in n, "an indicator nobody asked for is not resurrected")
    check("t" in F._SOURCE_PARTIAL, "the source is flagged partial")
    check(not cache[2].exists() and not cache[3].exists() and cache[1].exists(),
          "only the thin downloads are deleted, so the next run asks again")

    print("\n=== a clean refresh is left alone ===")
    F._SOURCE_PARTIAL.clear()
    clean = frame({1: (100, "2025"), 2: (95, "2025"), 3: (100, "2025")})
    out = F.carry_forward_thin(path, clean, key="indicator_id", source="t", requested={1, 2, 3})
    check(out.equals(clean), "nothing changes when every indicator is within 90%")
    check("t" not in F._SOURCE_PARTIAL, "and the source is not flagged")

print("\n=== Fingertips downloads retry, and stop when the service is down ===")
import requests


class Resp:
    def __init__(self, code):
        self.status_code = code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)


calls = []
answers = []
F.time.sleep = lambda s: None
F.requests.get = lambda url, timeout: (calls.append(url), answers.pop(0))[1]


def get(*seq):
    calls.clear()
    answers[:] = list(seq)
    try:
        return F._ft_get("u", 1)
    except Exception as e:
        return e


F._FT_FAILS_IN_A_ROW[0] = 0
check(isinstance(get(Resp(503), Resp(200)), Resp) and len(calls) == 2,
      "a 5xx is retried and the second answer is used")
check(isinstance(get(Resp(404)), requests.HTTPError) and len(calls) == 1,
      "a 404 is not retried")
for _ in range(5):
    get(Resp(503), Resp(503), Resp(503))
r = get(Resp(200))
check(isinstance(r, RuntimeError) and not calls,
      "after five failed downloads in a row it stops asking")

print(f"\n{'ALL PASS' if not failures else f'{failures} FAILED'}")
sys.exit(1 if failures else 0)
