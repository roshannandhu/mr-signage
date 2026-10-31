<#
    Stop the services started by start-dev.ps1.

    Postgres is left alone — it runs as a Windows service and other things may use it.
#>

$ErrorActionPreference = 'SilentlyContinue'

function Stop-OnPort([string]$Name, [int]$Port) {
    $conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if (-not $conns) {
        Write-Host ("  {0,-10} not running" -f $Name) -ForegroundColor DarkGray
        return
    }
    foreach ($procId in ($conns.OwningProcess | Select-Object -Unique)) {
        Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
        Write-Host ("  {0,-10} stopped (pid {1})" -f $Name, $procId) -ForegroundColor Yellow
    }
}

Write-Host "`nStopping MR Signage" -ForegroundColor Cyan
Stop-OnPort -Name 'frontend' -Port 3000
Stop-OnPort -Name 'backend'  -Port 8010
Stop-OnPort -Name 'redis'    -Port 6379

# The worker holds no port, so it is found by its command line.
$workers = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
    Where-Object { $_.CommandLine -like '*arq*' }
foreach ($w in $workers) {
    Stop-Process -Id $w.ProcessId -Force -ErrorAction SilentlyContinue
    Write-Host ("  {0,-10} stopped (pid {1})" -f 'worker', $w.ProcessId) -ForegroundColor Yellow
}
if (-not $workers) { Write-Host ("  {0,-10} not running" -f 'worker') -ForegroundColor DarkGray }
Write-Host ""
