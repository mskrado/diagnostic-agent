# End-to-end runbook scenario runner — wrapper for scripts/runbook-e2e.py
# See docs/TESTING.md.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Py = Get-Command python -ErrorAction SilentlyContinue
if (-not $Py) { $Py = Get-Command python3 -ErrorAction SilentlyContinue }
if (-not $Py) { throw "python not found on PATH" }
& $Py.Source (Join-Path $Root "scripts/runbook-e2e.py") @args
exit $LASTEXITCODE
