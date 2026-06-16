#!/usr/bin/env bash
# One-command setup for the OceanLab rhinomcp fork (macOS).
#
# End-to-end orchestrator: prerequisite checks -> build & install the Rhino
# plugin -> create the Python server venv -> register the MCP client -> print
# the final `mcpstart` step. A fresh clone goes from zero to working in one
# command.
#
# It delegates the plugin build to plugin/install.sh (the plugin-only builder)
# and wires the server + client around it, so the two scripts stay separate:
#   setup.sh           -> full setup (this file)
#   plugin/install.sh  -> just build & stage the .rhp
#
# Usage:
#   ./setup.sh                 # full auto: plugin + server + client + instructions
#   SKIP_CLIENT=1 ./setup.sh   # skip the MCP-client registration step
#   CONFIG=Debug ./setup.sh    # forwarded to the plugin build (Debug symbols)
#   RHINO_VERSION=8.0 ./setup.sh   # forwarded to the plugin install path
#
# Prerequisites (checked, not auto-installed):
#   .NET 8 SDK  https://dotnet.microsoft.com/   — builds the .rhp
#   uv          https://docs.astral.sh/uv/      — runs the Python server (brew install uv)
#   Rhino 8                                       — loads the plugin

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVER_DIR="$ROOT_DIR/server"

bold() { printf '\033[1m%s\033[0m\n' "$1"; }
info() { printf '\n==> %s\n' "$1"; }
die()  { printf 'error: %s\n' "$1" >&2; exit 1; }

[[ "$(uname)" == "Darwin" ]] || \
  die "this script targets macOS. On Windows run: powershell -ExecutionPolicy Bypass -File setup.ps1"

# 1. Prerequisite checks (check only — we don't auto-install).
info "checking prerequisites"
command -v dotnet >/dev/null 2>&1 || \
  die "dotnet CLI not found. Install the .NET 8 SDK: https://dotnet.microsoft.com/"
command -v uv >/dev/null 2>&1 || \
  die "uv not found. Install it:  brew install uv   (or see https://docs.astral.sh/uv/)"
HAS_CLAUDE=0
if command -v claude >/dev/null 2>&1; then
  HAS_CLAUDE=1
else
  printf 'note: claude CLI not found — will print the MCP client config instead of auto-registering.\n'
fi

# 2. Build & install the Rhino plugin (delegates to plugin/install.sh, which on
#    macOS builds a Yak package and installs it). Rhino must be CLOSED for this —
#    a running Rhino locks the package; install.sh errors out if it's open.
info "building & installing the Rhino plugin (Rhino must be closed)"
bash "$ROOT_DIR/plugin/install.sh"

# 3. Python server: venv + editable install (subshell keeps our cwd intact).
info "setting up the Python server (uv venv + editable install)"
( cd "$SERVER_DIR" && uv venv && uv pip install -e . )

# 4. Register the MCP client (Claude Code) or print the config to paste.
if [[ "${SKIP_CLIENT:-}" == "1" ]]; then
  info "skipping MCP client registration (SKIP_CLIENT=1)"
elif [[ "$HAS_CLAUDE" == "1" ]]; then
  info "registering MCP server 'rhino' with Claude Code"
  # Remove any stale entry first so re-runs are idempotent.
  claude mcp remove rhino >/dev/null 2>&1 || true
  claude mcp add rhino -- uv run --directory "$SERVER_DIR" rhinomcp
else
  info "MCP client config (paste into Claude Desktop / Cursor / Codex)"
  cat <<EOF
{
  "mcpServers": {
    "rhino": {
      "command": "uv",
      "args": ["run", "--directory", "$SERVER_DIR", "rhinomcp"],
      "env": { "RHINO_MCP_HOST": "127.0.0.1" }
    }
  }
}
EOF
fi

# 5. Final manual step (only Rhino can start its own listener).
echo
bold "Setup done."
cat <<EOF
Last step — start the bridge inside Rhino:
  1. Launch Rhino 8 — it loads the installed Yak package automatically (no drag-drop).
  2. In the Rhino command line, type:  mcpstart
  3. Reconnect your MCP client — you'll see the tools, including diagnose_edge_pair.

Re-run ./setup.sh after code changes: restart Rhino for C# (plugin) changes;
the MCP server picks up new Python tools on its next start.
EOF
