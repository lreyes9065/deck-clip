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
mkdir -p "$plugin_dir/dist" "$release_dir"

cp "$project_dir/package.json" "$plugin_dir/package.json"
cp "$project_dir/plugin.json" "$plugin_dir/plugin.json"
cp "$project_dir/main.py" "$plugin_dir/main.py"
cp "$project_dir/README.md" "$plugin_dir/README.md"
cp "$project_dir/LICENSE" "$plugin_dir/LICENSE"
cp "$project_dir/dist/index.js" "$plugin_dir/dist/index.js"

archive="$release_dir/$plugin_name-$version.zip"
rm -f -- "$archive"
(
  cd "$staging_dir"
  zip -q -r "$archive" "$plugin_name"
)

python3 "$project_dir/scripts/verify-decky-zip.py" "$archive"
echo "Created $archive"
