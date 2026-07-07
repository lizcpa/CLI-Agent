<#
.SYNOPSIS
    ProdVideo AI Factory - Local startup script (no Docker)
#>

$ErrorActionPreference = "Continue"
$PROJECT_ROOT = $PSScriptRoot
$PYTHON = "$PROJECT_ROOT\.venv\Scripts\python.exe"

if (-not (Test-Path $PYTHON)) {
    Write-Host "[ERROR] .venv not found" -ForegroundColor Red
    exit 1
}

$services = [ordered]@{
    "crawl-scheduler"     = @("project.backend.crawl_scheduler.main", 8001)
    "product-analyzer"    = @("project.backend.product_analyzer.main", 8002)
    "ai-generation"       = @("project.backend.ai_generation.main", 8003)
    "video-composer"      = @("project.backend.video_composer.main", 8004)
    "publish-dispatcher"  = @("project.backend.publish_dispatcher.main", 8005)
    "asset-manager"       = @("project.backend.asset_manager.main", 8006)
    "web-backend"         = @("project.backend.web_backend.main", 8007)
    "pipeline-orchestrator" = @("project.backend.pipeline_orchestrator.main", 8008)
    "mcp-gateway"         = @("project.backend.mcp_gateway.main", 8010)
}

Write-Host "`n=== Checking Infrastructure ===" -ForegroundColor Cyan

$redisOk = (Test-NetConnection -ComputerName localhost -Port 6379 -InformationLevel Quiet -WarningAction SilentlyContinue)
if ($redisOk) { Write-Host "  [OK] Redis (localhost:6379)" -ForegroundColor Green }
else { Write-Host "  [FAIL] Redis not running" -ForegroundColor Red }

$mysqlOk = (Test-NetConnection -ComputerName localhost -Port 3306 -InformationLevel Quiet -WarningAction SilentlyContinue)
if ($mysqlOk) { Write-Host "  [OK] MySQL (localhost:3306)" -ForegroundColor Green }
else { Write-Host "  [FAIL] MySQL not running" -ForegroundColor Red }

if (-not $redisOk -or -not $mysqlOk) {
    Write-Host "`nInfrastructure not ready. Start Redis and MySQL first." -ForegroundColor Yellow
    exit 1
}

Write-Host "`n=== Starting Backend Services ===" -ForegroundColor Cyan

$processes = @{}
foreach ($name in $services.Keys) {
    $module = $services[$name][0]
    $port = $services[$name][1]
    Write-Host "  Starting $name (port $port)..." -NoNewline
    $proc = Start-Process -FilePath $PYTHON -ArgumentList "-m", "uvicorn", "${module}:app", "--host", "0.0.0.0", "--port", $port -WorkingDirectory $PROJECT_ROOT -WindowStyle Minimized -PassThru
    $processes[$name] = $proc
    Write-Host " PID=$($proc.Id)" -ForegroundColor Green
    Start-Sleep -Milliseconds 500
}

Write-Host "`n=== Starting Celery Worker ===" -ForegroundColor Cyan
Write-Host "  Starting celery-worker..." -NoNewline
$celeryProc = Start-Process -FilePath $PYTHON -ArgumentList "-m", "celery", "-A", "utils.mq_clients.celery_app:celery_app", "worker", "--loglevel=info", "--queues=crawl_queue,analyze_queue,ai_queue,compose_queue,publish_queue,orchestrator_queue", "--pool=solo", "--concurrency=1" -WorkingDirectory $PROJECT_ROOT -WindowStyle Minimized -PassThru
$processes["celery-worker"] = $celeryProc
Write-Host " PID=$($celeryProc.Id)" -ForegroundColor Green

Write-Host "  Starting celery-beat..." -NoNewline
$beatProc = Start-Process -FilePath $PYTHON -ArgumentList "-m", "celery", "-A", "utils.mq_clients.celery_app:celery_app", "beat", "--loglevel=info" -WorkingDirectory $PROJECT_ROOT -WindowStyle Minimized -PassThru
$processes["celery-beat"] = $beatProc
Write-Host " PID=$($beatProc.Id)" -ForegroundColor Green

Write-Host "`n=== Waiting for services (15s) ===" -ForegroundColor Cyan
Start-Sleep -Seconds 15

Write-Host "`n=== Health Check ===" -ForegroundColor Cyan
$allHealthy = $true
foreach ($name in $services.Keys) {
    $port = $services[$name][1]
    try {
        $response = Invoke-WebRequest -Uri "http://localhost:$port/healthz" -TimeoutSec 5 -UseBasicParsing -ErrorAction Stop
        Write-Host "  [OK]   $name (port $port)" -ForegroundColor Green
    } catch {
        Write-Host "  [FAIL] $name (port $port) - no response" -ForegroundColor Red
        $allHealthy = $false
    }
}

Write-Host "`n=== Service URLs ===" -ForegroundColor Cyan
Write-Host "  MCP Gateway:        http://localhost:8010/docs"
Write-Host "  Web Backend (BFF):  http://localhost:8007/docs"
Write-Host "  AI Generation:      http://localhost:8003/docs"
Write-Host "  Crawl Scheduler:    http://localhost:8001/docs"
Write-Host "  Product Analyzer:   http://localhost:8002/docs"
Write-Host "  Video Composer:     http://localhost:8004/docs"
Write-Host "  Publish Dispatcher: http://localhost:8005/docs"
Write-Host "  Asset Manager:      http://localhost:8006/docs"
Write-Host "  Pipeline Orch:      http://localhost:8008/docs"

$processes | ConvertTo-Json | Out-File "$PROJECT_ROOT\.running-pids.json"
Write-Host "`n  PIDs saved to .running-pids.json"
Write-Host "  Stop all: .\stop_local.ps1"

if ($allHealthy) {
    Write-Host "`n=== All services started ===" -ForegroundColor Green
} else {
    Write-Host "`n=== Some services failed, check logs ===" -ForegroundColor Yellow
}
