#!/bin/bash
# Build the rhinomcp plugin and install it into Rhino for Mac as a Yak package.
#
# Why Yak and not a raw .rhp: Rhino for Mac has no reliable way to sideload a raw
# .rhp. Dragging it onto a viewport is treated as geometry import (Open/Import/Insert),
# double-clicking opens it as a document, and the Plug-in Manager has no "Install"
# button on Mac (Windows-only). The supported route is a Yak package: `yak build`
# then `yak install --source <local dir>`, which lands in
#   ~/Library/Application Support/McNeel/Rhinoceros/packages/<ver>/rhinomcp/
# and Rhino loads it at startup. (Windows can still use plugin\install.ps1.)
#
# Rhino must be CLOSED while installing — a running Rhino locks the package's dlls,
# so yak cannot overwrite them.
#
# Usage:
#   ./install.sh                      # Release build, install as a Yak package
#   CONFIG=Debug ./install.sh         # Debug build (symbols, no optimization)
#   RHINO_APP="/Applications/Rhino 8.app" ./install.sh   # non-default Rhino app

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SLN="$SCRIPT_DIR/rhinomcp.sln"
CONFIG="${CONFIG:-Release}"
RHINO_APP="${RHINO_APP:-/Applications/Rhino 8.app}"
YAK="$RHINO_APP/Contents/Resources/bin/yak"
PKG="rhinomcp"

if [[ "$(uname)" != "Darwin" ]]; then
  echo "error: this script targets macOS. On Windows, use plugin\\install.ps1." >&2
  exit 1
fi
if [[ ! -f "$SLN" ]]; then
  echo "error: solution not found at $SLN" >&2
  exit 1
fi
if ! command -v dotnet >/dev/null 2>&1; then
  echo "error: dotnet CLI not found. Install the .NET 8 SDK from https://dotnet.microsoft.com/" >&2
  exit 1
fi
if [[ ! -x "$YAK" ]]; then
  echo "error: yak not found at: $YAK" >&2
  echo "       Set RHINO_APP to your Rhino app, e.g. RHINO_APP=\"/Applications/Rhino 8.app\"" >&2
  exit 1
fi
# A running Rhino locks the installed package dlls -> yak install would fail mid-copy.
if pgrep -xq "Rhinoceros"; then
  echo "error: Rhino is running. Quit it first (type 'mcpstop' in Rhino, then Cmd+Q) so the" >&2
  echo "       installed package isn't locked, then re-run ./install.sh." >&2
  exit 1
fi

echo "==> building rhinomcp ($CONFIG)"
dotnet build "$SLN" --configuration "$CONFIG" --nologo --verbosity minimal

BUILD_OUT="$SCRIPT_DIR/bin/$CONFIG/net8.0"
RHP="$BUILD_OUT/rhinomcp.rhp"
if [[ ! -f "$RHP" ]]; then
  echo "error: build succeeded but rhinomcp.rhp is not at $BUILD_OUT" >&2
  exit 1
fi

echo "==> packaging Yak (.yak)"
# yak build packages everything in the current directory using manifest.yml.
cp "$SCRIPT_DIR/manifest.yml" "$BUILD_OUT/manifest.yml"
rm -f "$BUILD_OUT"/*.yak
( cd "$BUILD_OUT" && "$YAK" build >/dev/null )
YAK_FILE="$(ls "$BUILD_OUT"/*.yak 2>/dev/null | head -1)"
if [[ -z "$YAK_FILE" ]]; then
  echo "error: yak build produced no .yak" >&2
  exit 1
fi
echo "    $(basename "$YAK_FILE")"

echo "==> installing package (replacing any existing rhinomcp)"
# Remove the previous install so the same-version package is overwritten cleanly.
"$YAK" uninstall "$PKG" >/dev/null 2>&1 || true
( cd "$BUILD_OUT" && "$YAK" install --source="$BUILD_OUT" "$PKG" )

echo
echo "done. Installed package:"
"$YAK" list 2>/dev/null | sed 's/^/  /'
echo
echo "Next:"
echo "  1. Launch Rhino 8 — it loads the package automatically at startup (no drag-drop)."
echo "  2. In the Rhino command line, type:  mcpstart   (TCP listener on 127.0.0.1:1999)"
echo "  3. Point your MCP client at the Python server."
echo
echo "After a code change: quit Rhino, re-run ./install.sh, relaunch Rhino, then mcpstart."
