# One-command setup for the OceanLab rhinomcp fork (Windows).
#
# End-to-end orchestrator: prerequisite checks -> build & install the Rhino
# plugin -> create the Python server venv -> register the MCP client -> print
# the final `mcpstart` step. A fresh clone goes from zero to working in one
# command.
#
# It delegates the plugin build to plugin\install.ps1 (the plugin-only builder)
# and wires the server + client around it, so the two scripts stay separate:
#   setup.ps1            -> full setup (this file)
#   plugin\install.ps1   -> just build & stage the .rhp
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File setup.ps1
#   $env:SKIP_CLIENT="1"; powershell -ExecutionPolicy Bypass -File setup.ps1   # skip client step
#   $env:CONFIG="Debug";  powershell -ExecutionPolicy Bypass -File setup.ps1   # forwarded to plugin build
#   $env:RHINO_VERSION="8.0"; powershell -ExecutionPolicy Bypass -File setup.ps1
#
# Prerequisites (checked, not auto-installed):
#   .NET 8 SDK  https://dotnet.microsoft.com/   — builds the .rhp
#   uv          https://docs.astral.sh/uv/      — runs the Python server
#               (winget install astral-sh.uv  /  irm https://astral.sh/uv/install.ps1 | iex)
#   Rhino 8                                       — loads the plugin

$ErrorActionPreference = "Stop"

$RootDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$ServerDir = Join-Path $RootDir "server"

function Info($m) { Write-Host "`n==> $m" }

# 1. Prerequisite checks (check only — we don't auto-install).
Info "checking prerequisites"
if (-not (Get-Command dotnet -ErrorAction SilentlyContinue)) {
    Write-Error "dotnet CLI not found. Install the .NET 8 SDK: https://dotnet.microsoft.com/"
}
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Error "uv not found. Install it:  winget install astral-sh.uv   (or see https://docs.astral.sh/uv/)"
}
$HasClaude = [bool](Get-Command claude -ErrorAction SilentlyContinue)
if (-not $HasClaude) {
    Write-Host "note: claude CLI not found - will print the MCP client config instead of auto-registering."
}

# 2. Build & install the Rhino plugin (invoked in-process so CONFIG/RHINO_VERSION
#    env vars and terminating errors propagate).
Info "building & installing the Rhino plugin"
& (Join-Path $RootDir "plugin\install.ps1")

# 3. Python server: venv + editable install.
Info "setting up the Python server (uv venv + editable install)"
Push-Location $ServerDir
try {
    uv venv
    if ($LASTEXITCODE -ne 0) { Write-Error "uv venv failed (exit $LASTEXITCODE)" }
    uv pip install -e .
    if ($LASTEXITCODE -ne 0) { Write-Error "uv pip install failed (exit $LASTEXITCODE)" }
} finally {
    Pop-Location
}

# 4. Register the MCP client (Claude Code) or print the config to paste.
if ($env:SKIP_CLIENT -eq "1") {
    Info "skipping MCP client registration (SKIP_CLIENT=1)"
} elseif ($HasClaude) {
    Info "registering MCP server 'rhino' with Claude Code"
    # Remove any stale entry first so re-runs are idempotent.
    claude mcp remove rhino *> $null
    claude mcp add rhino -- uv run --directory $ServerDir rhinomcp
} else {
    Info "MCP client config (paste into Claude Desktop / Cursor / Codex)"
    $dir = $ServerDir -replace '\\', '/'
    Write-Host @"
{
  "mcpServers": {
    "rhino": {
      "command": "uv",
      "args": ["run", "--directory", "$dir", "rhinomcp"],
      "env": { "RHINO_MCP_HOST": "127.0.0.1" }
    }
  }
}
"@
}

# 5. Final manual step (only Rhino can start its own listener).
Write-Host ""
Write-Host "Setup done." -ForegroundColor Green
Write-Host @"
Last step - start the bridge inside Rhino:
  1. Launch Rhino 8. (First install only: drag the rhinomcp.rhp printed above
     onto a viewport and accept the load dialog so Rhino registers it.)
  2. In the Rhino command line, type:  mcpstart
  3. Reconnect your MCP client - you'll see the tools, including diagnose_edge_pair.

Re-run setup.ps1 after code changes: restart Rhino for C# (plugin) changes;
the MCP server picks up new Python tools on its next start.
"@
