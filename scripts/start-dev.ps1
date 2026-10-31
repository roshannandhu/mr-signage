<#
    Start every MR Signage service and keep them running.

    Why this exists: the four services (Redis, media worker, API, dashboard) were being
    started as background jobs inside a terminal session. When that session ended, they
    all died together and the app looked broken — uploads stuck "processing", pages
    erroring, everything blamed on the code. Each service now runs in its own detached
    process that outlives whatever shell started it.

    In production use docker-compose instead; it has the same four services with
    restart: unless-stopped. This is the local equivalent for machines without Docker.

    Usage:   powershell -ExecutionPolicy Bypass -File scripts\start-dev.ps1
    Stop:    powershell -ExecutionPolicy Bypass -File scripts\stop-dev.ps1
#>

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$logs = Join-Path $root 'logs'
if (-not (Test-Path $logs)) { New-Item -ItemType Directory -Path $logs | Out-Null }

function Test-Port([int]$Port) {
    $null -ne (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
}

function Start-Service-Detached {
    param([string]$Name, [string]$Exe, [string[]]$ArgList, [int]$Port, [string]$WorkDir)

    if ($Port -and (Test-Port $Port)) {
        Write-Host ("  {0,-10} already listening on {1}" -f $Name, $Port) -ForegroundColor DarkGray
        return
    }
    $out = Join-Path $logs "$Name.log"
    $err = Join-Path $logs "$Name.err.log"
    Start-Process -FilePath $Exe -ArgumentList $ArgList -WorkingDirectory $WorkDir `
        -RedirectStandardOutput $out -RedirectStandardError $err `
        -WindowStyle Hidden | Out-Null
    Write-Host ("  {0,-10} started -> logs\{0}.log" -f $Name) -ForegroundColor Green
}

Write-Host "`nStarting MR Signage" -ForegroundColor Cyan

# Postgres is installed as a Windows service; only check it.
if (Test-Port 5432) {
    Write-Host "  postgres   listening on 5432" -ForegroundColor DarkGray
} else {
    Write-Host "  postgres   NOT RUNNING - start the PostgreSQL service first" -ForegroundColor Red
}

Start-Service-Detached -Name 'redis' -Port 6379 -WorkDir $root `
    -Exe (Join-Path $root 'Redis\redis-server.exe') -ArgList @('--port', '6379')

# The worker needs Redis reachable at start-up or arq exits immediately.
$deadline = (Get-Date).AddSeconds(15)
while (-not (Test-Port 6379) -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 300 }

# The worker holds no port, so the port guard above cannot see it — and -Port 0 is falsy,
# which meant every run of this script started *another* worker. They accumulated silently,
# each one willing to spawn ffmpeg, which is a good way to bring the machine to its knees.
# It is identified by its command line here, exactly as stop-dev.ps1 already does.
$runningWorker = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
    Where-Object { $_.CommandLine -like '*arq*' } | Select-Object -First 1
if ($runningWorker) {
    Write-Host ("  {0,-10} already running (pid {1})" -f 'worker', $runningWorker.ProcessId) -ForegroundColor DarkGray
} else {
    Start-Service-Detached -Name 'worker' -Port 0 -WorkDir $root `
        -Exe 'python' -ArgList @('-m', 'arq', 'backend.worker.WorkerSettings')
}

Start-Service-Detached -Name 'backend' -Port 8010 -WorkDir $root `
    -Exe 'python' -ArgList @('-m', 'uvicorn', 'backend.main:app', '--host', '0.0.0.0', '--port', '8010')

# Production build, not dev mode. Measured on this machine: dev served the dashboard's
# slowest asset in 596 ms, the production build in 33 ms. Dev mode is the lag.
# Note: never run "npm run build" while a dev server is up — they share .next and the
# build pulls the directory out from under the running server.
$frontend = Join-Path $root 'frontend'
$buildId  = Join-Path $frontend '.next\BUILD_ID'
$envLocal = Join-Path $frontend '.env.local'

# NEXT_PUBLIC_* values are inlined into the bundle at build time, so editing .env.local
# changes nothing until it is rebuilt. Rebuilding only when BUILD_ID was missing meant
# pasting a Google Maps key and rerunning this script looked like the key was ignored.
$needsBuild = -not (Test-Path $buildId)
if (-not $needsBuild -and (Test-Path $envLocal)) {
    $needsBuild = (Get-Item $envLocal).LastWriteTime -gt (Get-Item $buildId).LastWriteTime
    if ($needsBuild) { Write-Host "  frontend   .env.local changed since the last build" -ForegroundColor DarkGray }
}

if ($needsBuild) {
    # A build replaces .next wholesale, which breaks a server already serving from it.
    if (Test-Port 3000) {
        $running = (Get-NetTCPConnection -LocalPort 3000 -State Listen -ErrorAction SilentlyContinue).OwningProcess |
            Select-Object -Unique
        foreach ($procId in $running) { Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue }
        Start-Sleep -Seconds 2
    }
    Write-Host "  frontend   building (about a minute)..." -ForegroundColor DarkGray
    # Waited on as a process, not through a pipeline: "| Out-Null" blocks until every
    # inherited stdout handle closes, and Next.js leaves build workers holding one, so the
    # script hung forever after a build that had already succeeded. Output goes to a log
    # rather than being swallowed - a failed build used to pass silently and then serve
    # the previous bundle, which looks exactly like the new code not working.
    $buildLog = Join-Path $logs 'frontend-build.log'
    $build = Start-Process -FilePath $env:ComSpec -ArgumentList @('/c', 'npm', 'run', 'build') `
        -WorkingDirectory $frontend -RedirectStandardOutput $buildLog `
        -RedirectStandardError (Join-Path $logs 'frontend-build.err.log') `
        -NoNewWindow -Wait -PassThru
    if ($build.ExitCode -ne 0) {
        Write-Host "  frontend   BUILD FAILED - see logs\frontend-build.err.log" -ForegroundColor Red
        Write-Host "             not starting the dashboard on a stale bundle.`n" -ForegroundColor Red
        exit 1
    }
}
Start-Service-Detached -Name 'frontend' -Port 3000 -WorkDir $frontend `
    -Exe $env:ComSpec -ArgList @('/c', 'npm', 'run', 'start', '--', '-p', '3000')

Write-Host "`nWaiting for the API and dashboard..." -ForegroundColor Cyan
$deadline = (Get-Date).AddSeconds(90)
while ((Get-Date) -lt $deadline) {
    if ((Test-Port 8010) -and (Test-Port 3000)) { break }
    Start-Sleep -Seconds 1
}

Write-Host ""
foreach ($svc in @(@('postgres',5432), @('redis',6379), @('backend',8010), @('frontend',3000))) {
    $ok = Test-Port $svc[1]
    $mark = if ($ok) { 'UP  ' } else { 'DOWN' }
    $colour = if ($ok) { 'Green' } else { 'Red' }
    Write-Host ("  {0} {1,-10} :{2}" -f $mark, $svc[0], $svc[1]) -ForegroundColor $colour
}
$worker = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
    Where-Object { $_.CommandLine -like '*arq*' }
if ($worker) {
    Write-Host ("  UP   worker     pid {0}" -f $worker.ProcessId) -ForegroundColor Green
} else {
    Write-Host "  DOWN worker     (check logs\worker.err.log)" -ForegroundColor Red
}

Write-Host "`n  Dashboard  http://localhost:3000" -ForegroundColor Cyan
$lan = (Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } |
    Select-Object -First 1).IPAddress
Write-Host "  API docs   http://127.0.0.1:8010/docs" -ForegroundColor Cyan
if ($lan) { Write-Host "  On phone / TV   http://${lan}:3000`n" -ForegroundColor Cyan }
