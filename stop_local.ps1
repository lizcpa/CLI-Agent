<#
.SYNOPSIS
    Stop all ProdVideo local services
#>

$pidsFile = "$PSScriptRoot\.running-pids.json"
if (Test-Path $pidsFile) {
    $processes = Get-Content $pidsFile | ConvertFrom-Json
    foreach ($name in $processes.PSObject.Properties.Name) {
        $procId = $processes.$name.Id
        if ($procId) {
            Write-Host "  Stopping $name (PID=$procId)..." -NoNewline
            try {
                Stop-Process -Id $procId -Force -ErrorAction Stop
                Write-Host " OK" -ForegroundColor Green
            } catch {
                Write-Host " already exited" -ForegroundColor Yellow
            }
        }
    }
    Remove-Item $pidsFile -Force -ErrorAction SilentlyContinue
} else {
    Write-Host "PID file not found, stopping by port..." -ForegroundColor Yellow
    $ports = @(8001, 8002, 8003, 8004, 8005, 8006, 8007, 8008, 8010)
    foreach ($port in $ports) {
        $conns = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue
        foreach ($conn in $conns) {
            Stop-Process -Id $conn.OwningProcess -Force -ErrorAction SilentlyContinue
        }
    }
}

Write-Host "`nAll services stopped." -ForegroundColor Green
