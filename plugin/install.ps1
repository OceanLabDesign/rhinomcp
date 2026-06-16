# Build the rhinomcp plugin and install it into the Windows user-level Rhino
# plug-ins directory. Windows counterpart of install.sh.
#
# Rhino for Windows loads user plug-ins from
#   %APPDATA%\McNeel\Rhinoceros\<ver>\Plug-ins\
# First-time install: drag the built .rhp onto a running Rhino window so Rhino
# registers the path. Afterwards every rebuild just refreshes the files in place
# and Rhino picks up the new build on next launch.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File plugin\install.ps1
#   $env:CONFIG="Debug";              powershell -ExecutionPolicy Bypass -File plugin\install.ps1
#   $env:RHINO_VERSION="8.0";         powershell -ExecutionPolicy Bypass -File plugin\install.ps1
#   $env:RHINO_PLUGIN_DIR="C:\path";  powershell -ExecutionPolicy Bypass -File plugin\install.ps1

$ErrorActionPreference = "Stop"

$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$Sln         = Join-Path $ScriptDir "rhinomcp.sln"
$Config      = if ($env:CONFIG) { $env:CONFIG } else { "Release" }
$RhinoVersion= if ($env:RHINO_VERSION) { $env:RHINO_VERSION } else { "8.0" }
$PluginDir   = if ($env:RHINO_PLUGIN_DIR) { $env:RHINO_PLUGIN_DIR } `
               else { Join-Path $env:APPDATA "McNeel\Rhinoceros\$RhinoVersion\Plug-ins\rhinomcp" }

if (-not (Test-Path $Sln)) { Write-Error "solution not found at $Sln" }
if (-not (Get-Command dotnet -ErrorAction SilentlyContinue)) {
    Write-Error "dotnet CLI not found. Install the .NET 8 SDK from https://dotnet.microsoft.com/"
}
if (Get-Process -Name "Rhino" -ErrorAction SilentlyContinue) {
    Write-Warning "Rhino appears to be running. Its loaded dlls are locked, so the copy may fail. Quit Rhino if you hit errors below."
}

Write-Host "==> building rhinomcp ($Config)"
dotnet build $Sln --configuration $Config --nologo --verbosity minimal
if ($LASTEXITCODE -ne 0) { Write-Error "dotnet build failed (exit $LASTEXITCODE)" }

$BuildOut = Join-Path $ScriptDir "bin\$Config\net8.0"
$Rhp      = Join-Path $BuildOut "rhinomcp.rhp"
if (-not (Test-Path $Rhp)) { Write-Error "build succeeded but rhinomcp.rhp is not at $BuildOut" }

Write-Host "==> installing to $PluginDir"
New-Item -ItemType Directory -Force -Path $PluginDir | Out-Null
# /MIR mirrors the folder (deletes stale dlls so an old NuGet build doesn't shadow the new one).
robocopy $BuildOut $PluginDir /MIR /NJH /NJS /NDL /NFL | Out-Null
# robocopy exit codes 0-7 are success; 8+ is a real failure.
if ($LASTEXITCODE -ge 8) { Write-Error "robocopy failed with code $LASTEXITCODE" }

Write-Host ""
Write-Host "done. Plugin staged at:"
Write-Host "  $Rhp"
Write-Host ""
Write-Host "First-time install only:"
Write-Host "  1. Launch Rhino 8."
Write-Host "  2. Drag the .rhp file (above) onto an open Rhino viewport."
Write-Host "  3. Accept the load dialog. Rhino remembers the path and picks up every"
Write-Host "     subsequent rebuild on next launch."
Write-Host ""
Write-Host "Once registered, run 'mcpstart' in the Rhino command line to start the TCP"
Write-Host "listener on 127.0.0.1:1999, then point your MCP client at the Python server."
