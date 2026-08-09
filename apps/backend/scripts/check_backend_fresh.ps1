# Pre-flight: is the backend on :8000 running the CURRENT code?
# Compares the server process start time against the newest backend .py
# modification time. STALE = restart before launching a campaign.
#
# Run from anywhere:
#   powershell -File apps/backend/scripts/check_backend_fresh.ps1

$backendRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

$conn = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($null -eq $conn) {
    Write-Host "NO SERVER: nothing is listening on :8000. Start the backend first." -ForegroundColor Yellow
    exit 1
}

$proc = Get-Process -Id $conn.OwningProcess
$started = $proc.StartTime

$newest = Get-ChildItem -Path (Join-Path $backendRoot "nexus"), (Join-Path $backendRoot "app") `
    -Recurse -Filter *.py | Sort-Object LastWriteTime -Descending | Select-Object -First 1

Write-Host ("server process  : PID {0} ({1})" -f $proc.Id, $proc.ProcessName)
Write-Host ("server started  : {0}" -f $started)
Write-Host ("newest code file: {0}" -f $newest.LastWriteTime)
Write-Host ("                  {0}" -f $newest.FullName.Substring($backendRoot.Length + 1))

if ($newest.LastWriteTime -gt $started) {
    Write-Host ""
    Write-Host "STALE: code changed AFTER the server started. RESTART before running a campaign." -ForegroundColor Red
    exit 1
} else {
    Write-Host ""
    Write-Host "FRESH: the running server includes every code change. Safe to launch." -ForegroundColor Green
    exit 0
}
