# Wrapper invoked by Windows Task Scheduler for the daily 7am run.
# Mirrors what the old macOS launchd plist did: run the venv's Python
# against main.py, appending stdout/stderr to data/cron.log.

$ErrorActionPreference = 'Continue'

$proj = $PSScriptRoot | Split-Path -Parent
$py   = Join-Path $proj ".venv\Scripts\python.exe"
$log  = Join-Path $proj "data\cron.log"

New-Item -ItemType Directory -Force -Path (Join-Path $proj "data") | Out-Null

$start = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Add-Content -Path $log -Value "`n=== Run started $start ==="

Push-Location $proj
& $py "main.py" *>> $log
$exitCode = $LASTEXITCODE
Pop-Location

$end = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Add-Content -Path $log -Value "=== Run finished $end (exit $exitCode) ==="
