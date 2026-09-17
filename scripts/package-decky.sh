#!/bin/sh
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
plugin_name="DeckClip"
version=$(node -p "require('$project_dir/package.json').version")
release_dir="$project_dir/release"
staging_dir=$(mktemp -d "${TMPDIR:-/tmp}/deckclip-package.XXXXXX")

cleanup() {
  rm -rf -- "$staging_dir"
}
trap cleanup EXIT HUP INT TERM

if [ ! -f "$project_dir/dist/index.js" ]; then
  echo "dist/index.js is missing. Run 'pnpm run build' first." >&2
  exit 1
fi

plugin_dir="$staging_dir/$plugin_name"
mkdir -p "$plugin_dir/backend" "$plugin_dir/dist" "$plugin_dir/docs" "$release_dir"

cp "$project_dir/package.json" "$plugin_dir/package.json"
cp "$project_dir/plugin.json" "$plugin_dir/plugin.json"
cp "$project_dir/main.py" "$plugin_dir/main.py"
cp "$project_dir/backend/__init__.py" "$plugin_dir/backend/__init__.py"
cp "$project_dir/backend/exports.py" "$plugin_dir/backend/exports.py"
cp "$project_dir/backend/library.py" "$plugin_dir/backend/library.py"
cp "$project_dir/backend/media.py" "$plugin_dir/backend/media.py"
cp "$project_dir/backend/qr.py" "$plugin_dir/backend/qr.py"
cp "$project_dir/backend/transfer.py" "$plugin_dir/backend/transfer.py"
cp "$project_dir/backend/thumbnails.py" "$plugin_dir/backend/thumbnails.py"
cp "$project_dir/backend/process_env.py" "$plugin_dir/backend/process_env.py"
cp "$project_dir/backend/processes.py" "$plugin_dir/backend/processes.py"
cp "$project_dir/backend/history.py" "$plugin_dir/backend/history.py"
cp "$project_dir/README.md" "$plugin_dir/README.md"
cp "$project_dir/LICENSE" "$plugin_dir/LICENSE"
cp "$project_dir/THIRD_PARTY_NOTICES.md" "$plugin_dir/THIRD_PARTY_NOTICES.md"
cp "$project_dir/dist/index.js" "$plugin_dir/dist/index.js"
cp "$project_dir/docs/iphone-shortcut.md" "$plugin_dir/docs/iphone-shortcut.md"
cp "$project_dir/docs/release-checklist.md" "$plugin_dir/docs/release-checklist.md"
cp "$project_dir/docs/code-walkthrough.md" "$plugin_dir/docs/code-walkthrough.md"
cp "$project_dir/docs/security-review-2026-09-12.md" "$plugin_dir/docs/security-review-2026-09-12.md"

archive="$release_dir/Decky-ClipPort-$version.zip"
rm -f -- "$archive"
(
  cd "$staging_dir"
  zip -q -r "$archive" "$plugin_name"
)

python3 "$project_dir/scripts/verify-decky-zip.py" "$archive"
echo "Created $archive"
