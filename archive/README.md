# archive/

One-off scripts kept for reference only. Nothing here runs as part of the
pipeline, nothing imports them, and they are not maintained.

Each was written to correct or investigate a specific problem in the data at a
point in time. They are retained because they record how a particular figure in
the committed outputs came to be, which is occasionally useful when a number
looks wrong. Treat them as history, not as tools.

| Script | What it was for |
|--------|-----------------|
| `patch_age.py` | Backfilled census age-band percentages onto ward records |
| `patch_env.py` | Backfilled environment domain fields |
| `patch_greenblue.py` | Attached Defra green and blue space access figures |
| `patch_split_lsoas.py` | Handled LSOAs split between the 2011 and 2021 geographies |
| `patch_fingertips_suppression.py` | Marked Fingertips indicators suppressed for small numerators |
| `patch_imd_denominator_mid2022.py` | Switched IMD weighting to the mid-2022 population denominator |
| `patch_population_mid2024.py` | Switched ward populations to the ONS mid-2024 estimate |
| `diagnose_indicator_ward.py` | Traced a single indicator through to one ward |
| `diagnose_missing_wards.py` | Found wards missing from an output |

If you need the behaviour of one of these permanently, move it into
`fetch_all_data.py` as a proper source or export step rather than reviving the
script.
