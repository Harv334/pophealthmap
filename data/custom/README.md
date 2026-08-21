# Your own figures

Drop a CSV in here, describe it in a JSON file beside it, and it appears on the
map. No Python to edit.

## The two files

`example_wellbeing.csv`

    LSOA21CD,wellbeing_score
    E01000001,7.2
    E01000002,6.8

The first column is the area code. Use `LSOA21CD` for neighbourhoods or
`WD25CD` for wards. Every other column is a figure.

`example_wellbeing.json`

    {
      "source": "My team's survey, 2026",
      "indicators": [
        {
          "column": "wellbeing_score",
          "label": "Wellbeing score (1-10)",
          "group": "Health & care",
          "higher_is_worse": false,
          "unit": "score",
          "description": "Average self-reported wellbeing, 2026 survey."
        }
      ]
    }

## Then

    python fetch_all_data.py --only custom
    python fetch_all_data.py --export-only

Commit and push. That publishes it.

## The fields

| Field | Needed | What it is |
|-------|--------|------------|
| `source` | yes | Who produced the figures. Shown on the indicator |
| `column` | yes | The column in the CSV |
| `label` | yes | What the map calls it |
| `higher_is_worse` | yes | Which end is bad. See below |
| `group` | no | Which list it appears under. Defaults to "Your data" |
| `unit` | no | Shown beside the number |
| `description` | no | Shown when the indicator is selected |

`higher_is_worse` has no sensible default, so it has to be stated. It decides
which end of the colour scale is red. Getting it wrong does not look like an
error, it looks like a finding, which is why nothing here guesses it.

## What happens to your numbers

Nothing is smoothed or modelled. LSOA figures are rolled up to wards by
population-weighted mean, the same rule every other source here follows.
Counts are not summed unless you name the column so it ends in `_count`.

Areas you do not supply are left blank rather than filled in.
