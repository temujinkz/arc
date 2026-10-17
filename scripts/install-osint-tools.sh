#!/usr/bin/env sh
# Installs the optional local OSINT tools the research agent can call: Sherlock, Maigret and theHarvester.
# Everything goes under .arc/ (git-ignored) and is linked into .arc/bin, where the backend looks first.
# Versions are pinned. theHarvester is built from its own lockfile at a fixed commit.
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TOOLS="$ROOT/.arc/tools"
BIN="$ROOT/.arc/bin"
HARVESTER_TAG="4.11.1"
HARVESTER_COMMIT="fd832dee7936458a4f6e2029bf37ba6797228676"

mkdir -p "$TOOLS" "$BIN"
export UV_HTTP_TIMEOUT="${UV_HTTP_TIMEOUT:-300}"

install_pip_tool() {
  name="$1"; spec="$2"; command_name="$3"
  uv venv --quiet --python 3.13 "$TOOLS/$name"
  uv pip install --quiet --python "$TOOLS/$name/bin/python" "$spec"
  ln -sf "$TOOLS/$name/bin/$command_name" "$BIN/$command_name"
  echo "installed $spec"
}

install_pip_tool sherlock "sherlock-project==0.16.2" sherlock
install_pip_tool maigret "maigret==0.6.6" maigret

if [ ! -d "$TOOLS/theHarvester/.git" ]; then
  git clone --quiet --depth 1 --branch "$HARVESTER_TAG" https://github.com/laramies/theHarvester "$TOOLS/theHarvester"
fi
actual="$(git -C "$TOOLS/theHarvester" rev-parse HEAD)"
if [ "$actual" != "$HARVESTER_COMMIT" ]; then
  echo "theHarvester $HARVESTER_TAG is at $actual, expected $HARVESTER_COMMIT. Not installing." >&2
  exit 1
fi
(cd "$TOOLS/theHarvester" && uv sync --quiet --locked --python 3.14)
ln -sf "$TOOLS/theHarvester/.venv/bin/theHarvester" "$BIN/theHarvester"
echo "installed theHarvester $HARVESTER_TAG ($actual)"

echo "Done. Check with: curl -s http://127.0.0.1:8010/api/tools"
