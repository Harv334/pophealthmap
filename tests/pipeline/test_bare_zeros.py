"""A Local Health 0 that OHID gives no England comparison is no figure.

Long-term unemployment (93098) counts Jobseeker's Allowance claims only, and
309 of London's MSOAs read exactly 0 with no comparison, so they are stored as
missing rather than drawn as none. Offline: a small frame, then the shipped
msoa_data.json.
"""
import json
import sys
import pathlib as _pl

import pandas as pd

REPO = _pl.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
import fetch_all_data as F

failures = 0


def check(ok, label, detail=""):
    global failures
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(': ' + detail) if detail and not ok else ''}")
    if not ok:
        failures += 1


df = pd.DataFrame({
    "MSOA21CD": ["M1", "M2", "M3", "M4"],
    "indicator_id": [93098, 93098, 93259, 93098],
    "value": [0.0, 0.0, 0.0, 0.5],
    "vs_england": ["", "S", "B", ""],
})
out = F.ft_msoa_drop_bare_zeros(df)
print("=== ft_msoa_drop_bare_zeros ===")
check(list(out["MSOA21CD"]) == ["M2", "M3", "M4"], "only a 0 with no comparison goes", str(list(out["MSOA21CD"])))
check(F.ft_msoa_drop_bare_zeros(None) is None, "no frame passes through")

print("\n=== the shipped MSOA file ===")
msoa = json.loads((REPO / "msoa_data.json").read_text(encoding="utf-8"))
bare = [c for c, r in msoa.items() for k, v in r.items()
        if k.startswith("ft_") and not k.endswith(("_vs", "_est")) and v == 0 and not r.get(k + "_vs")]
check(not bare, "no Local Health 0 without a comparison in msoa_data.json", ", ".join(bare[:5]))

print("\nALL PASS" if not failures else f"\n{failures} FAILED")
sys.exit(1 if failures else 0)
