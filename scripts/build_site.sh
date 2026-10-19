#!/usr/bin/env bash
# Assemble the published site in _site/ from the files the pages actually
# serve, and nothing else. The Pages deploy uploads this folder, and the smoke
# tests run against it, so what is tested is what goes live.
#
# Jekyll used to run over the whole repository, which published every tracked
# file: HANDOVER.md as /HANDOVER, README.md, the pipeline, worker/, archive/
# and the parquet files. Only the list below is copied now, with an empty
# .nojekyll so Pages serves the folder as it is.
#
# When the page starts fetching a new file, add it here. The paths come from
# index.html (dataUrl/regionPath calls, the head preloads and script tags),
# vendor/fonts.css, site.webmanifest and the meta tags of the other pages.
#
# Usage: scripts/build_site.sh [OUT]   (OUT defaults to _site)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${1:-_site}"
case "$OUT" in /*) ;; *) OUT="$ROOT/$OUT" ;; esac
cd "$ROOT"

rm -rf "$OUT"
mkdir -p "$OUT/data"

# Pages, styles, crawler files, the domain and the icons.
FILES=(
  index.html methodology.html privacy.html accessibility.html 404.html
  pages.css robots.txt sitemap.xml site.webmanifest CNAME
  favicon-32.png apple-touch-icon.png icon-192.png icon-512.png
  icon-maskable-512.png og.png
  # London's root data files, fetched through dataUrl().
  ward_data.json borough_data.json lsoa_data.json msoa_data.json
  vcse_data.json pharmacies.json dental_practices.json greenspaces.geojson
  lsoa_census_counts.json
)
for f in "${FILES[@]}"; do
  if [ ! -f "$f" ]; then echo "build_site: missing $f" >&2; exit 1; fi
  cp -p "$f" "$OUT/$f"
done

# Leaflet, the marker clusters and the self-hosted fonts.
cp -rp vendor "$OUT/vendor"

# The map scripts and boundaries (data/map/, including oa/ and the Ask
# assistant), the manifest and layer counts (data/meta/), and the ward
# figures split into the first-paint and on-demand halves.
mkdir -p "$OUT/data/map" "$OUT/data/meta"
cp -rp data/map/. "$OUT/data/map/"
cp -rp data/meta/. "$OUT/data/meta/"
cp -p data/ward_core.json data/ward_rest.json "$OUT/data/"

# The region packs unpacked by the deploy and the England view built from
# them (scripts/build_england.py writes data/regions/england/). Absent on a
# plain checkout, where only London is served.
if [ -d data/regions ]; then
  # -L: a symlinked pack (a local test setup) is copied, not linked, so the
  # steps below that rewrite files in _site never reach the original.
  cp -rpL data/regions "$OUT/data/regions"
fi

: > "$OUT/.nojekyll"

# The developer comments out of the published page (about a third of
# index.html); the repository keeps them. Needs node and the typescript
# package, which the workflows install pinned; without them the page is
# published as it is.
if command -v node >/dev/null 2>&1; then
  node "$ROOT/scripts/strip_comments.cjs" "$OUT/index.html"
else
  echo "build_site: node not found, so index.html keeps its comments" >&2
fi

# The LSOA and MSOA figures in column form, a fifth of the size; the page
# turns them back into records (scripts/compact_tables.py).
python3 "$ROOT/scripts/compact_tables.py" "$OUT"

# The sitemap (one entry per published region) and the Dataset dateModified,
# dated from the manifests rather than typed by hand.
python3 "$ROOT/scripts/stamp_site.py" "$OUT"

# What must be there, and what must not.
fail=0
for f in index.html 404.html CNAME .nojekyll data/map/wards.js data/meta/manifest.json \
         data/ward_core.json ward_data.json vendor/leaflet-1.9.4/leaflet.js vendor/fonts.css; do
  [ -e "$OUT/$f" ] || { echo "build_site: $f is missing from the site" >&2; fail=1; }
done
for f in HANDOVER HANDOVER.md HANDOVER.html README.md DOMAIN.md fetch_all_data.py \
         worker archive scripts tests scopes data/outcomes data/healthcare "PopHealth Map - Handover.docx"; do
  [ ! -e "$OUT/$f" ] || { echo "build_site: $f must not be published" >&2; fail=1; }
done
if find "$OUT" \( -name '*.parquet' -o -name '*.md' -o -name '*.py' -o -name '*.docx' \) | grep -q .; then
  echo "build_site: internal files found in the site:" >&2
  find "$OUT" \( -name '*.parquet' -o -name '*.md' -o -name '*.py' -o -name '*.docx' \) >&2
  fail=1
fi
[ "$fail" = 0 ] || exit 1

echo "build_site: $(find "$OUT" -type f | wc -l) files, $(du -sh "$OUT" | cut -f1) in $OUT"
