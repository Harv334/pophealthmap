#!/usr/bin/env bash
# One-time cleanup after collapsing the pipeline/ tree into fetch_all_data.py.
# Removes the now-redundant pipeline/, scripts/, and stale docs, then commits.
#
# Run this from the repo root:
#   bash cleanup_and_commit.sh
#
# Safe to rerun - every step is idempotent.

set -e
cd "$(dirname "$0")"

# Fix the corrupted git index (harmless no-op if index is healthy)
if ! git status --short >/dev/null 2>&1; then
  echo "Rebuilding git index..."
  rm -f .git/index .git/index.lock 2>/dev/null || true
  git reset
fi

echo "Removing old pipeline tree..."
git rm -rf pipeline/ 2>/dev/null || rm -rf pipeline/

echo "Removing old scripts and egg-info..."
git rm -rf scripts/ 2>/dev/null || rm -rf scripts/
git rm -rf nw_london_health_pipeline.egg-info/ 2>/dev/null || rm -rf nw_london_health_pipeline.egg-info/

echo "Removing stale docs (superseded by README)..."
for f in docs/ADDING_A_FETCHER.md docs/DATA_SOURCES.md docs/FIX_SESSION_SUMMARY.md docs/HANDOVER.md docs/NEXT_STEPS.md docs/POWERBI.md docs/TROUBLESHOOTING.md; do
  git rm -f "$f" 2>/dev/null || rm -f "$f"
done
rmdir docs 2>/dev/null || true

echo "Removing pyproject (no longer a package)..."
git rm -f pyproject.toml 2>/dev/null || rm -f pyproject.toml

echo "Removing disabled workflows and the egg symlink..."
git rm -f .github/workflows/pipeline-*.yml.disabled .github/workflows/pipeline-on-demand.yml 2>/dev/null || true

echo
echo "Staging new files..."
git add fetch_all_data.py README.md
git add -A

echo
echo "Status:"
git status --short

echo
echo "To commit + push:"
echo "  git commit -m 'refactor: collapse pipeline/ into single fetch_all_data.py'"
echo "  git push"
