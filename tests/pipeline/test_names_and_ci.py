"""Display names, phone numbers, Fingertips placeholders and the DESNZ year.

The helpers behind workplan items 2.12 (names and phone numbers people read)
and 2.15 (a placeholder row stored as missing, the year a fuel poverty release
describes) only run on a data refresh, so a later edit could break them without
anyone noticing until bad names or a 0% reach the map. These check them
offline, with no downloads.
"""
import sys
import pathlib as _pl

REPO = _pl.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
import fetch_all_data as F

failures = 0


def check(ok, label, detail=""):
    global failures
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(': ' + detail) if detail and not ok else ''}")
    if not ok:
        failures += 1


def eq(got, want, label):
    check(got == want, label, f"got {got!r}, want {want!r}")


# Names, as the practice and site lists show them.
eq(F.title_case("ST MARY'S SURGERY", initials=True), "St Mary's Surgery", "an apostrophe keeps its s lower case")
eq(F.title_case("BRENT CENTRAL KWH", initials=True), "Brent Central KWH", "initials stay in capitals")
eq(F.title_case("SOUTHEND-ON-SEA"), "Southend-on-Sea", "small words inside a hyphenated name stay lower case")
eq(F.title_case("MCCARTHY"), "McCarthy", "Mc names take a capital after the prefix")

# Phone numbers, grouped the way UK numbers are read.
eq(F.format_uk_phone("02089852795"), "020 8985 2795", "a London number groups as 020 xxxx xxxx")
eq(F.format_uk_phone("01214960000"), "0121 496 0000", "a Birmingham number groups as 0121 xxx xxxx")

# A Fingertips placeholder row (upper limit below lower) is stored as missing,
# and a normal row keeps its value and limits.
eq(F._ft_value_with_ci({"Value": "0", "Lower CI 95.0 limit": "0", "Upper CI 95.0 limit": "-1"}),
   (None, None, None), "the City of London placeholder row is stored as missing")
v, lo, hi = F._ft_value_with_ci({"Value": "12.5", "Lower CI 95.0 limit": "10.1", "Upper CI 95.0 limit": "15.2"})
check(v == 12.5 and lo == 10.1 and hi == 15.2, "a normal row keeps its value and limits", f"{v}, {lo}, {hi}")

# The year a DESNZ release's figures describe, from its title.
eq(F._fuel_poverty_data_year({"title": "Sub-regional fuel poverty data 2025 (2023 data)"}, [], []), "2023",
   "the fuel poverty year is read from the release title")

print(f"\n{'ALL PASS' if not failures else f'{failures} FAILED'}")
sys.exit(1 if failures else 0)
