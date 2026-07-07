<#
.SYNOPSIS
    ProdVideo - Status check script
#>

$PROJECT_ROOT = $PSScriptRoot

Write-Host "=== Service Health Check ===" -ForegroundColor Cyan

$services = [ordered]@{
    "crawl-scheduler"      = 8001
    "product-analyzer"     = 8002
    "ai-generation"        = 8003
    "video-composer"       = 8004
    "publish-dispatcher"   = 8005
    "asset-manager"        = 8006
    "web-backend"          = 8007
    "pipeline-orchestrator" = 8008
    "mcp-gateway"          = 8010
}

$alive = 0
$dead = 0
foreach ($name in $services.Keys) {
    $port = $services[$name]
    try {
        $response = Invoke-WebRequest -Uri "http://localhost:$port/healthz" -TimeoutSec 5 -UseBasicParsing -ErrorAction Stop
        Write-Host "  [OK]   $name (port $port)" -ForegroundColor Green
        $alive++
    } catch {
        Write-Host "  [FAIL] $name (port $port)" -ForegroundColor Red
        $dead++
    }
}

Write-Host ""
Write-Host "=== Celery ===" -ForegroundColor Cyan
$pidsFile = "$PROJECT_ROOT\.running-pids.json"
if (Test-Path $pidsFile) {
    $pids = Get-Content $pidsFile | ConvertFrom-Json
    $celeryPid = $pids.'celery-worker'.Id
    if ($celeryPid -and (Get-Process -Id $celeryPid -ErrorAction SilentlyContinue)) {
        Write-Host "  [OK]   celery-worker (PID=$celeryPid)" -ForegroundColor Green
        $alive++
    } else {
        Write-Host "  [FAIL] celery-worker (PID=$celeryPid not running)" -ForegroundColor Red
        $dead++
    }
    $beatPid = $pids.'celery-beat'.Id
    if ($beatPid -and (Get-Process -Id $beatPid -ErrorAction SilentlyContinue)) {
        Write-Host "  [OK]   celery-beat   (PID=$beatPid)" -ForegroundColor Green
        $alive++
    } else {
        Write-Host "  [FAIL] celery-beat   (PID=$beatPid not running)" -ForegroundColor Red
        $dead++
    }
} else {
    Write-Host "  [SKIP] .running-pids.json not found" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "=== Infrastructure ===" -ForegroundColor Cyan
$redisOk = (Test-NetConnection -ComputerName localhost -Port 6379 -InformationLevel Quiet -WarningAction SilentlyContinue)
if ($redisOk) { Write-Host "  [OK]   Redis (localhost:6379)" -ForegroundColor Green } else { Write-Host "  [FAIL] Redis (localhost:6379)" -ForegroundColor Red }

$mysqlOk = (Test-NetConnection -ComputerName localhost -Port 3306 -InformationLevel Quiet -WarningAction SilentlyContinue)
if ($mysqlOk) { Write-Host "  [OK]   MySQL (localhost:3306)" -ForegroundColor Green } else { Write-Host "  [FAIL] MySQL (localhost:3306)" -ForegroundColor Red }

$minioOk = (Test-NetConnection -ComputerName localhost -Port 9000 -InformationLevel Quiet -WarningAction SilentlyContinue)
if ($minioOk) { Write-Host "  [OK]   MinIO (localhost:9000)" -ForegroundColor Green } else { Write-Host "  [SKIP] MinIO (localhost:9000) - not running, services in lazy mode" -ForegroundColor Yellow }

Write-Host ""
Write-Host "=== Summary ===" -ForegroundColor Cyan
Write-Host "  Alive: $alive  Dead: $dead"
if ($dead -eq 0) {
    Write-Host "  All services healthy!" -ForegroundColor Green
} else {
    Write-Host "  Some services failed." -ForegroundColor Yellow
}
