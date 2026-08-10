"""Standalone patcher: aggregate Defra green/blue space ODS → LSOA21,
write parquet + merge headline fields into lsoa_data.json.

Inputs
------
  Downloads/Access_to_green_and_blue_space_England_data_table.ods
  (download from https://www.gov.uk/government/statistics/access-to-green-and-blue-space-in-england-2025)

Outputs
-------
  data/environment/greenblue_lsoa.parquet      (1,313 NWL LSOA rows)
  lsoa_data.json                                (7 new fields per NWL LSOA)

Why a streaming parser: the unzipped content.xml is ~1.37 GB — odfpy
reads the whole thing into DOM and OOMs. Running time ~5s.

Aggregation note (OA21 → LSOA21): sum the UPRN numerator and the UPRN
denominator across the OAs in each LSOA, then divide. A simple mean of
the OA percentages would over-weight small OAs.
"""
import zipfile, re, json, os, shutil, sys, time
import pandas as pd
from pathlib import Path

ROOT = Path(__file__).parent
ODS = ROOT / 'Access_to_green_and_blue_space_England_data_table.ods'
OUT_PARQUET = ROOT / 'data' / 'environment' / 'greenblue_lsoa.parquet'
LSOA_JSON = ROOT / 'lsoa_data.json'
NWL_LSOA_SRC = ROOT / 'data' / 'boundaries' / 'lsoa.geojson'

if not ODS.exists():
    sys.exit(f'missing {ODS} — download from Defra landing page first')
if not NWL_LSOA_SRC.exists():
    sys.exit(f'missing {NWL_LSOA_SRC}')

# -- build NWL LSOA set from the boundaries GeoJSON
gj = json.loads(NWL_LSOA_SRC.read_text())
nwl = set()
for feat in gj['features']:
    p = feat['properties']
    code = p.get('LSOA21CD') or p.get('lsoa21cd') or p.get('LSOA21NM')
    if code and code.startswith('E01'):
        nwl.add(code)
print(f'NWL LSOAs: {len(nwl)}')

# -- streaming ODS parser
TABLE_OPEN = re.compile(rb'<table:table[^>]*table:name="([^"]+)"[^>]*>')
TABLE_CLOSE = re.compile(rb'</table:table>')
ROW_OPEN = re.compile(rb'<table:table-row[^>]*>')
ROW_END = re.compile(rb'</table:table-row>')
CELL_RE = re.compile(
    rb'<table:table-cell([^>]*)>((?:(?!</table:table-cell>).)*)</table:table-cell>|<table:table-cell([^>]*)/>',
    re.DOTALL,
)
REPEAT_RE = re.compile(rb'table:number-columns-repeated="(\d+)"')
VALUE_RE = re.compile(rb'office:value="([^"]*)"')
TEXT_P_RE = re.compile(rb'<text:p[^>]*>((?:(?!</text:p>).)*)</text:p>', re.DOTALL)
TAG_STRIP = re.compile(rb'<[^>]+>')
LSOA_CODE_RE = re.compile(rb'>(E01\d{6})<')


def parse_row(row_xml):
    cells = []
    for m in CELL_RE.finditer(row_xml):
        attrs = m.group(1) or m.group(3) or b''
        inner = m.group(2) or b''
        val = ''
        vm = VALUE_RE.search(attrs)
        if vm:
            val = vm.group(1).decode('utf-8', errors='replace')
        else:
            bits = [TAG_STRIP.sub(b'', p).decode('utf-8', errors='replace').strip()
                    for p in TEXT_P_RE.findall(inner)]
            val = ' '.join(b for b in bits if b)
        rm = REPEAT_RE.search(attrs)
        reps = int(rm.group(1)) if rm else 1
        if reps > 50 and val == '':
            cells.append('')
        else:
            for _ in range(min(reps, 50)):
                cells.append(val)
    while cells and cells[-1] == '':
        cells.pop()
    return cells


sums = {'1': {}, '2': {}, '3': {}}
headers = {}
zf = zipfile.ZipFile(ODS)
fp = zf.open('content.xml')
CHUNK = 16 * 1024 * 1024
t0 = time.time()
rows_seen = {'1': 0, '2': 0, '3': 0}
nwl_matches = {'1': 0, '2': 0, '3': 0}
carry = b''
cur_sheet = None
header_seen = False
header_cols = []
sheet_meta = (0, [])

while True:
    data = fp.read(CHUNK)
    if not data and not carry:
        break
    chunk = carry + data if data else carry
    pos, N = 0, len(chunk)
    while pos < N:
        if cur_sheet is None:
            m = TABLE_OPEN.search(chunk, pos)
            if m is None: break
            name = m.group(1).decode('utf-8', errors='replace')
            pos = m.end()
            if name in sums:
                cur_sheet = name
                header_seen = False
                header_cols = []
                print(f'[{time.time()-t0:5.1f}] entering sheet {name}', flush=True)
            continue
        r = ROW_OPEN.search(chunk, pos)
        if r is None:
            te = TABLE_CLOSE.search(chunk, pos)
            if te:
                pos = te.end()
                print(f'[{time.time()-t0:5.1f}] end sheet {cur_sheet}: nwl={nwl_matches[cur_sheet]} unique={len(sums[cur_sheet])}', flush=True)
                headers[cur_sheet] = header_cols[:]
                cur_sheet = None
                continue
            break
        te = TABLE_CLOSE.search(chunk, pos, r.start())
        if te:
            pos = te.end()
            print(f'[{time.time()-t0:5.1f}] end sheet {cur_sheet}: nwl={nwl_matches[cur_sheet]} unique={len(sums[cur_sheet])}', flush=True)
            headers[cur_sheet] = header_cols[:]
            cur_sheet = None
            continue
        rc = ROW_END.search(chunk, r.end())
        if rc is None: break
        row_xml = chunk[r.end():rc.start()]
        pos = rc.end()
        if header_seen:
            m = LSOA_CODE_RE.search(row_xml)
            if m is None: continue
            lsoa = m.group(1).decode('ascii')
            rows_seen[cur_sheet] += 1
            if lsoa not in nwl: continue
            cells = parse_row(row_xml)
            if not cells: continue
            nwl_matches[cur_sheet] += 1
            acc = sums[cur_sheet].setdefault(lsoa, {c: 0 for c, _ in sheet_meta[1]})
            for name, idx in sheet_meta[1]:
                if idx < len(cells):
                    v = cells[idx]
                    if v:
                        try: acc[name] += int(float(v))
                        except ValueError: pass
            continue
        cells = parse_row(row_xml)
        if not cells: continue
        if cells[0] == 'OA21CD':
            header_cols = cells
            header_seen = True
            idx_lsoa = header_cols.index('LSOA21CD')
            numeric_cols = [(h, i) for i, h in enumerate(header_cols)
                            if h == 'total_uprn' or h.startswith('uprn_in_')]
            print(f'  numeric cols: {numeric_cols}', flush=True)
            sheet_meta = (idx_lsoa, numeric_cols)
        continue
    carry = chunk[pos:]
    if not data: break
fp.close()
print(f'[{time.time()-t0:5.1f}] aggregation done')

# -- build parquet (one row per LSOA, three sheet prefixes combined)
PREFIX = {'1': 'gb', '2': 'blue', '3': 'green'}
records = {}
for s in ['1', '2', '3']:
    pref = PREFIX[s]
    for lsoa, acc in sums[s].items():
        rec = records.setdefault(lsoa, {'LSOA21CD': lsoa})
        total = acc.get('total_uprn', 0)
        rec[f'{pref}_total_uprn'] = total
        for k, v in acc.items():
            if k == 'total_uprn': continue
            tag = k.replace('uprn_in_', '')
            rec[f'{pref}_{tag}_uprn'] = v
            rec[f'{pref}_{tag}_pct'] = round(100.0 * v / total, 2) if total else None

df = pd.DataFrame.from_records(list(records.values()))
OUT_PARQUET.parent.mkdir(parents=True, exist_ok=True)
df.to_parquet(OUT_PARQUET, index=False)
print(f'wrote {OUT_PARQUET} ({len(df)} rows, {len(df.columns)} cols)')

# -- merge headline indicators into lsoa_data.json (atomic write + byte verify)
KEEP = [
    ('gb_total_uprn',          'gb_total_uprn'),
    ('gb_commitment_pct',      'gb_commitment_pct'),
    ('green_commitment_pct',   'green_commitment_pct'),
    ('green_doorstep_pct',     'green_doorstep_pct'),
    ('green_local_pct',        'green_local_pct'),
    ('green_neighbourhood_pct','green_neighbourhood_pct'),
    ('blue_commitment_pct',    'blue_commitment_pct'),
]
idx = df.set_index('LSOA21CD')
data = json.loads(LSOA_JSON.read_text())
for lsoa, row in data.items():
    if lsoa in idx.index:
        r = idx.loc[lsoa]
        for src, dst in KEEP:
            v = r[src]
            if pd.isna(v): row[dst] = None
            elif src.endswith('_uprn'): row[dst] = int(v)
            else: row[dst] = float(round(v, 2))
    else:
        for _, dst in KEEP:
            row.setdefault(dst, None)

out = json.dumps(data, separators=(',', ':'))
tmp = '/tmp/lsoa_data.withgb.json'
with open(tmp, 'w') as f: f.write(out)
exp = os.path.getsize(tmp)
for attempt in range(1, 5):
    shutil.copy2(tmp, LSOA_JSON)
    got = os.path.getsize(LSOA_JSON)
    if got == exp:
        print(f'wrote {LSOA_JSON} ({got:,} bytes)')
        break
    print(f'attempt {attempt}: short write {got}/{exp}')
    time.sleep(0.5)
else:
    sys.exit('FAILED to write lsoa_data.json after 4 attempts')
