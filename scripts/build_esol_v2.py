#!/usr/bin/env python3
"""Build NWL ESOL planning point-layer JSONs (v2 - with outward fallback)."""
from __future__ import annotations
import csv
import json
import zipfile
import io
from pathlib import Path
from pyproj import Transformer

REPO = Path('/sessions/brave-funny-clarke/mnt/Downloads/nw-london-health-pipeline')
UPLOADS = Path('/sessions/brave-funny-clarke/mnt/uploads')
ONSPD_ZIP = REPO / '.cache/onspd/ONSPD_FEB_2026_UK.zip'

NWL_BOROUGHS = {
    'Brent':                  'E09000005',
    'Ealing':                 'E09000009',
    'Hammersmith and Fulham': 'E09000013',
    'Harrow':                 'E09000015',
    'Hillingdon':             'E09000017',
    'Hounslow':               'E09000018',
    'Kensington and Chelsea': 'E09000020',
    'Westminster':            'E09000033',
}
NWL_BOROUGH_NAMES = set(NWL_BOROUGHS.keys())

osgb_to_wgs = Transformer.from_crs('EPSG:27700', 'EPSG:4326', always_xy=True)


def norm_postcode(pc):
    if not pc:
        return ''
    pc = ''.join(pc.split()).upper()
    if len(pc) >= 5:
        return pc[:-3] + ' ' + pc[-3:]
    return pc


def build_pc_index(needed):
    if not needed:
        return {}
    needed_norm = {norm_postcode(p) for p in needed if p}
    areas = set()
    for pc in needed_norm:
        head = ''
        for ch in pc:
            if ch.isalpha():
                head += ch
            else:
                break
        if head:
            areas.add(head)
            if len(head) > 1:
                areas.add(head[0])
    for ext in ('WC', 'EC', 'NW', 'SW'):
        areas.add(ext)

    out = {}
    sector_sums = {}
    outward_sums = {}
    sector_keys = {pc[:5] for pc in needed_norm if len(pc) >= 5}
    outward_keys = {pc.split(' ')[0] for pc in needed_norm if ' ' in pc}

    with zipfile.ZipFile(ONSPD_ZIP) as zf:
        relevant = [m for m in zf.namelist()
                    if m.startswith('Data/multi_csv/ONSPD_') and m.endswith('.csv')
                    and Path(m).stem.split('_')[-1] in areas]
        print('  postcode shards:', sorted(Path(m).stem.split('_')[-1] for m in relevant))
        for member in relevant:
            with zf.open(member) as raw:
                rdr = csv.DictReader(io.TextIOWrapper(raw, encoding='utf-8-sig', newline=''))
                for row in rdr:
                    pcds = (row.get('pcds') or '').strip().upper()
                    lat_s = (row.get('lat') or '').strip()
                    lng_s = (row.get('long') or '').strip()
                    if not pcds or not lat_s or lat_s == '99.999999':
                        continue
                    try:
                        lat, lng = float(lat_s), float(lng_s)
                    except ValueError:
                        continue
                    if pcds in needed_norm:
                        out[pcds] = (lat, lng)
                    if len(pcds) >= 5 and pcds[:5] in sector_keys:
                        s = sector_sums.get(pcds[:5], (0.0, 0.0, 0))
                        sector_sums[pcds[:5]] = (s[0] + lat, s[1] + lng, s[2] + 1)
                    if ' ' in pcds:
                        outw = pcds.split(' ')[0]
                        if outw in outward_keys:
                            o = outward_sums.get(outw, (0.0, 0.0, 0))
                            outward_sums[outw] = (o[0] + lat, o[1] + lng, o[2] + 1)
    sf = of = 0
    for pc in needed_norm:
        if pc in out:
            continue
        if len(pc) >= 5:
            s = sector_sums.get(pc[:5])
            if s and s[2] > 0:
                out[pc] = (s[0] / s[2], s[1] / s[2])
                sf += 1
                continue
        if ' ' in pc:
            o = outward_sums.get(pc.split(' ')[0])
            if o and o[2] > 0:
                out[pc] = (o[0] / o[2], o[1] / o[2])
                of += 1
    if sf:
        print('  sector-centroid fallback:', sf)
    if of:
        print('  outward-code fallback:', of)
    return out


def safe_int(v):
    if v in (None, ''):
        return None
    try:
        return int(float(str(v).replace(',', '')))
    except (ValueError, TypeError):
        return None


def safe_float(v):
    if v in (None, ''):
        return None
    try:
        return float(str(v).replace(',', ''))
    except (ValueError, TypeError):
        return None


def build_schools():
    out = []
    with (UPLOADS / 'London Schools.csv').open(encoding='utf-8-sig') as f:
        for r in csv.DictReader(f):
            lad = (r.get('lad11nm') or '').strip()
            if lad not in NWL_BOROUGH_NAMES:
                continue
            if (r.get('establishmentstatus__name_') or '').strip() != 'Open':
                continue
            e = safe_float(r.get('easting'))
            n = safe_float(r.get('northing'))
            if e is None or n is None:
                continue
            lng, lat = osgb_to_wgs.transform(e, n)
            out.append({
                'name':     (r.get('establishmentname') or '').strip(),
                'urn':      (r.get('urn') or '').strip(),
                'phase':    (r.get('phaseofeducation__name_') or '').strip(),
                'type':     (r.get('typeofestablishment__name_') or '').strip(),
                'gender':   (r.get('gender__name_') or '').strip(),
                'low_age':  safe_int(r.get('statutorylowage')),
                'high_age': safe_int(r.get('statutoryhighage')),
                'capacity': safe_int(r.get('schoolcapacity')),
                'pupils':   safe_int(r.get('numberofpupils')),
                'fsm_pct':  safe_float(r.get('percentagefsm')),
                'street':   (r.get('street') or '').strip(),
                'town':     (r.get('town') or '').strip(),
                'postcode': (r.get('postcode') or '').strip().upper(),
                'website':  (r.get('schoolwebsite') or '').strip(),
                'borough':  lad,
                'lad_code': NWL_BOROUGHS[lad],
                'ward_code':(r.get('wardcd') or '').strip(),
                'lat':      round(lat, 6),
                'lng':      round(lng, 6),
            })
    by_phase = {}
    for r in out:
        by_phase[r['phase']] = by_phase.get(r['phase'], 0) + 1
    print('Schools:', len(out), 'records, phases:', by_phase)
    (REPO / 'schools.json').write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding='utf-8')


def build_centres():
    out = []
    with (UPLOADS / 'Community Centres.csv').open(encoding='utf-8-sig') as f:
        for r in csv.DictReader(f):
            borough = (r.get('Borough') or '').strip()
            if borough not in NWL_BOROUGH_NAMES:
                continue
            lat = safe_float(r.get('latitude'))
            lng = safe_float(r.get('longitude'))
            if lat is None or lng is None:
                continue
            out.append({
                'name':      (r.get('name') or '').strip(),
                'address':   ', '.join(s for s in [
                                (r.get('Address') or '').strip(),
                                (r.get('address2') or '').strip(),
                             ] if s and s.upper() != 'NA'),
                'postcode':  (r.get('Postcode') or '').strip().upper(),
                'website':   (r.get('website') or '').strip(),
                'borough':   borough,
                'lad_code':  (r.get('borough_code') or NWL_BOROUGHS[borough]).strip(),
                'ward_name': (r.get('ward_2022_name') or '').strip(),
                'ward_code': (r.get('ward_2022_code') or '').strip(),
                'lat':       round(lat, 6),
                'lng':       round(lng, 6),
            })
    print('Community Centres:', len(out), 'records')
    (REPO / 'community_centres.json').write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding='utf-8')


def build_libs():
    out = []
    with (UPLOADS / 'Libraries.csv').open(encoding='utf-8-sig') as f:
        for r in csv.DictReader(f):
            borough = (r.get('Borough') or '').strip()
            if borough not in NWL_BOROUGH_NAMES:
                continue
            if (r.get('open_status') or '').strip() != '1':
                continue
            lat = safe_float(r.get('latitude'))
            lng = safe_float(r.get('longitude'))
            if lat is None or lng is None:
                continue
            out.append({
                'name':      (r.get('name') or '').strip(),
                'address':   ', '.join(s for s in [
                                (r.get('Address') or '').strip(),
                                (r.get('address2') or '').strip(),
                             ] if s and s.upper() != 'NA'),
                'postcode':  (r.get('Postcode') or '').strip().upper(),
                'website':   (r.get('website') or '').strip(),
                'borough':   borough,
                'lad_code':  (r.get('borough_code') or NWL_BOROUGHS[borough]).strip(),
                'ward_name': (r.get('ward_2022_name') or '').strip(),
                'ward_code': (r.get('ward_2022_code') or '').strip(),
                'status':    (r.get('status') or '').strip(),
                'lat':       round(lat, 6),
                'lng':       round(lng, 6),
            })
    print('Libraries:', len(out), 'records')
    (REPO / 'libraries.json').write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding='utf-8')


def build_esol():
    rows = []
    needed = set()
    with (UPLOADS / 'Formal ESOL providers.csv').open(encoding='utf-8-sig') as f:
        for r in csv.DictReader(f):
            borough = (r.get('Borough') or '').strip()
            if borough not in NWL_BOROUGH_NAMES:
                continue
            pc = norm_postcode(r.get('Postcode') or r.get('pc') or '')
            if pc:
                needed.add(pc)
            rows.append({**r, '_borough': borough, '_pc': pc})
    print('ESOL pre-geocode:', len(rows), 'rows,', len(needed), 'unique postcodes')
    pc_index = build_pc_index(needed)
    print('  hit rate:', len(pc_index), '/', len(needed))
    out = []
    dropped = 0
    for r in rows:
        c = pc_index.get(r['_pc'])
        if c is None:
            dropped += 1
            continue
        lat, lng = c
        out.append({
            'name':     (r.get('Name') or '').strip(),
            'type':     (r.get('Type of Institution') or '').strip(),
            'website':  (r.get('Website') or '').strip(),
            'address':  (r.get('Address') or '').strip(),
            'postcode': r['_pc'],
            'borough':  r['_borough'],
            'lad_code': NWL_BOROUGHS[r['_borough']],
            'lat':      round(lat, 6),
            'lng':      round(lng, 6),
        })
    print('ESOL providers:', len(out), 'kept,', dropped, 'dropped')
    (REPO / 'esol_providers.json').write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding='utf-8')


if __name__ == '__main__':
    build_schools()
    build_centres()
    build_libs()
    build_esol()
