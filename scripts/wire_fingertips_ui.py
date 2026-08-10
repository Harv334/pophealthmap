#!/usr/bin/env python3
"""Patch index.html to wire Fingertips MSOA-derived ward indicators.

Reads scripts/fingertips_metadata.json (produced by fetch_fingertips.py) and
ward_data.json. For every indicator that survived the ward aggregation, this
script idempotently injects:

    1. A new entry in CATS under the "health_fingertips" category (or appends
       to an existing one).
    2. An OV_DOMAIN entry with min/max/wh derived from observed values.
    3. An OV_META entry with name/unit/polarity/source/desc.
    4. An <option> in #ov dropdown's "Health (Fingertips, MSOA→ward)" optgroup.
       Bivariate dropdown #ov2 is left unchanged because Fingertips fields are
       single-axis health indicators.

Idempotent: re-running with a new metadata file adds only new indicators;
existing entries are updated in place but not duplicated.

Usage:
    python scripts/wire_fingertips_ui.py
    python scripts/wire_fingertips_ui.py --dry-run    # print diff, don't write

Run AFTER fetch_fingertips.py has produced fingertips_metadata.json and
patched ward_data.json.
"""
from __future__ import annotations
import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

CATS_KEY = 'health_fingertips'
CATS_LABEL = 'Health & wellbeing (Fingertips, MSOA → ward)'
CATS_ICO = '⚕'
DROPDOWN_OPTGROUP_LABEL = 'Health (Fingertips, MSOA → ward)'


def slug_from_name(name: str) -> str:
    s = re.sub(r'[^a-z0-9]+', '_', (name or '').lower()).strip('_')
    return s[:50]


def load_metadata():
    p = REPO / 'scripts' / 'fingertips_metadata.json'
    if not p.exists():
        print(f"ERROR: {p} not found. Run fetch_fingertips.py first.", file=sys.stderr)
        sys.exit(2)
    return json.loads(p.read_text(encoding='utf-8'))


def observed_range(field_key, ward_data):
    """Compute (min, max) of values across NWL wards for this indicator key."""
    vs = []
    for w in ward_data['wards'].values():
        v = (w.get('indicators') or {}).get(field_key)
        try:
            v = float(v)
            if v == v:  # not NaN
                vs.append(v)
        except (ValueError, TypeError):
            pass
    if not vs:
        return None, None
    lo, hi = min(vs), max(vs)
    # Pad 5% so the colour ramp doesn't max out exactly at the extremes.
    span = hi - lo
    return round(lo - span * 0.05, 4), round(hi + span * 0.05, 4)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true', help='print summary, do not modify index.html')
    args = ap.parse_args()

    meta = load_metadata()
    ward_data = json.loads((REPO / 'ward_data.json').read_text(encoding='utf-8'))

    # Filter to indicators that actually have ward values after aggregation.
    indicators = []
    for m in meta:
        iid = m['indicator_id']
        field = f'ft_{iid}'
        n_with = sum(1 for w in ward_data['wards'].values()
                     if (w.get('indicators') or {}).get(field) is not None)
        if n_with == 0:
            continue
        lo, hi = observed_range(field, ward_data)
        if lo is None:
            continue
        wh = m.get('wh')
        if wh is None:
            wh = False  # default: higher = better (don't penalise)
        indicators.append({
            'field':        field,
            'iid':          iid,
            'name':         (m.get('name') or f'Indicator {iid}').strip(),
            'unit':         (m.get('unit') or '').strip(),
            'polarity':     m.get('polarity') or '',
            'wh':           wh,
            'lo':           lo,
            'hi':           hi,
            'n_wards':      n_with,
        })
    print(f"Wirable indicators: {len(indicators)} (out of {len(meta)} fetched)")
    if args.dry_run:
        for i in indicators[:30]:
            print(f"  ft_{i['iid']:>6}  {i['name'][:60]:60}  {i['unit'][:18]:18}  range=[{i['lo']}, {i['hi']}]  wh={i['wh']}  ({i['n_wards']} wards)")
        if len(indicators) > 30:
            print(f"  …and {len(indicators)-30} more")
        return

    html_path = REPO / 'index.html'
    html = html_path.read_text(encoding='utf-8')

    # ── 1. CATS entry ──────────────────────────────────────────────────────
    cats_re = re.compile(
        r'(\{\s*key:\s*"' + re.escape(CATS_KEY) + r'"[^}]*?fields:\s*\[)(.*?)(\]\s*\})',
        flags=re.DOTALL,
    )
    new_cats_fields = ',\n'.join(
        f'    {{ k: "{i["field"]}", l: {json.dumps(i["name"])}, g: "ward" }}' for i in indicators
    )
    if cats_re.search(html):
        # Replace existing fields list.
        html = cats_re.sub(lambda m: m.group(1) + '\n' + new_cats_fields + '\n  ' + m.group(3), html)
        print("  CATS: replaced existing health_fingertips category")
    else:
        # Insert before the closing `];` of the CATS const.
        cats_close_re = re.compile(r'(\n\];?\s*\n//\s*Set of overlay keys that only)', flags=re.DOTALL)
        new_cat_block = (
            f',\n  {{ key: "{CATS_KEY}", label: {json.dumps(CATS_LABEL)}, ico: "{CATS_ICO}", fields: [\n'
            f'{new_cats_fields}\n  ]}}'
        )
        if not cats_close_re.search(html):
            print("ERROR: could not find CATS close marker. Aborting.", file=sys.stderr)
            sys.exit(1)
        # Use a lambda for the replacement so backslash sequences in indicator
        # names (e.g. unicode escapes like é) aren't interpreted by re.sub.
        html = cats_close_re.sub(lambda m: new_cat_block + m.group(1), html, count=1)
        print(f"  CATS: appended new {CATS_KEY} category with {len(indicators)} fields")

    # Defence: collapse any accidental `}, ,` (caused by re-running the append
    # branch when the existing fingertips block can't be detected) — produces
    # an undefined entry inside CATS that crashes ovLabel on every hover.
    html = re.sub(r'\}\s*,\s*,', '},', html)

    # ── 2. OV_CFG entries (the choropleth domain config — file calls it OV_CFG, not OV_DOMAIN) ──
    dom_re = re.compile(r'(const OV_CFG\s*=\s*\{)([\s\S]*?)(\n\};\s*\n)', flags=re.MULTILINE)
    m_dom = dom_re.search(html)
    if m_dom:
        existing_in_dom = set(re.findall(r'^\s*(ft_\d+)\s*:', m_dom.group(2), flags=re.MULTILINE))
        new_doms = '\n'.join(
            f'  {i["field"]:30}: {{ lo:"Lower", hi:"Higher", wh:{str(i["wh"]).lower()}, '
            f'min:{i["lo"]}, max:{i["hi"]} }},'
            for i in indicators if i['field'] not in existing_in_dom
        )
        if new_doms:
            html = html[:m_dom.end(2)] + '\n' + new_doms + '\n' + html[m_dom.end(2):]
            print(f"  OV_CFG: added {new_doms.count(chr(10))+1} entries")
        else:
            print("  OV_CFG: nothing new to add")
    else:
        print("  OV_CFG: not found (skipped)")

    # ── 3. OV_META entries ─────────────────────────────────────────────────
    ov_meta_re = re.compile(r'(const OV_META\s*=\s*\{)([\s\S]*?)(\n\};\s*\n)', flags=re.MULTILINE)
    m_meta = ov_meta_re.search(html)
    if m_meta:
        existing_meta = set(re.findall(r'^\s*(ft_\d+):', m_meta.group(2), flags=re.MULTILINE))
        new_metas = '\n'.join(
            f'  {i["field"]:30}: {{ src: "OHID Fingertips (Local Health, MSOA → ward via LSOA bridge)", '
            f'yr: "latest", g: "Ward (MSOA-derived)", u: {json.dumps(i["unit"] or "")}, '
            f'desc: {json.dumps((i["name"] + ". Polarity: " + (i["polarity"] or "n/a")).strip())} }},'
            for i in indicators if i['field'] not in existing_meta
        )
        if new_metas:
            html = html[:m_meta.end(2)] + '\n' + new_metas + '\n' + html[m_meta.end(2):]
            print(f"  OV_META: added {new_metas.count(chr(10))+1} entries")
        else:
            print("  OV_META: nothing new to add")

    # ── 4. Dropdown #ov optgroup ───────────────────────────────────────────
    # Find #ov select. Inject a new <optgroup> at the end.
    sel_ov_re = re.compile(r'(<select id="ov"[^>]*>)([\s\S]*?)(</select>)', flags=re.MULTILINE)
    m_ov = sel_ov_re.search(html)
    if m_ov:
        opt_label = DROPDOWN_OPTGROUP_LABEL
        existing_optgroup_re = re.compile(
            r'<optgroup label="' + re.escape(opt_label) + r'">[\s\S]*?</optgroup>',
            flags=re.MULTILINE,
        )
        opts_html = ''.join(
            f'\n                <option value="{i["field"]}">{i["name"]} · Ward</option>'
            for i in sorted(indicators, key=lambda x: x['name'].lower())
        )
        new_optgroup = f'              <optgroup label="{opt_label}">{opts_html}\n              </optgroup>'
        body = m_ov.group(2)
        if existing_optgroup_re.search(body):
            body = existing_optgroup_re.sub(new_optgroup, body)
            print(f"  #ov: replaced existing optgroup with {len(indicators)} options")
        else:
            # Append before </select>
            body = body.rstrip() + '\n' + new_optgroup + '\n            '
            print(f"  #ov: appended new optgroup with {len(indicators)} options")
        html = html[:m_ov.start(2)] + body + html[m_ov.end(2):]
    else:
        print("  #ov select not found")

    html_path.write_text(html, encoding='utf-8')
    print(f"\nWrote {html_path}")


if __name__ == '__main__':
    main()
