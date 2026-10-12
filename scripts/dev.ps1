<#
.SYNOPSIS
    开发模式一键启动：同时运行后端与前端开发服务器。

.EXAMPLE
    .\scripts\dev.ps1
#>

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$venvPy = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    Write-Host "未找到 .venv，正在创建…" -ForegroundColor Yellow
    python -m venv .venv
    & $venvPy -m pip install --upgrade pip -q
    & $venvPy -m pip install -r backend\requirements.txt
}

Write-Host ""
Write-Host "=" * 66 -ForegroundColor DarkGray
Write-Host "  开发模式启动" -ForegroundColor Cyan
Write-Host "=" * 66 -ForegroundColor DarkGray
Write-Host "  后端：http://127.0.0.1:8756" -ForegroundColor Gray
Write-Host "  前端：http://localhost:5173  （界面用这个）" -ForegroundColor Gray
Write-Host "  按 Ctrl+C 停止全部" -ForegroundColor Gray
Write-Host ""

# 后端（后台作业）
$backend = Start-Job -ScriptBlock {
    param($py, $root)
    Set-Location $root
    & $py "backend\run_server.py" --reload --no-browser
} -ArgumentList $venvPy, $Root

# 前端（前台）
try {
    Push-Location (Join-Path $Root "frontend")
    if (-not (Test-Path "node_modules")) {
        Write-Host "安装前端依赖…" -ForegroundColor Yellow
        if (Get-Command pnpm -ErrorAction SilentlyContinue) { pnpm install } else { npm install }
    }
    if (Get-Command pnpm -ErrorAction SilentlyContinue) { pnpm run dev } else { npm run dev }
} finally {
    Pop-Location
    Write-Host ""
    Write-Host "正在停止后端…" -ForegroundColor Yellow
    Stop-Job $backend -ErrorAction SilentlyContinue
    Remove-Job $backend -Force -ErrorAction SilentlyContinue
    Write-Host "已停止" -ForegroundColor Green
}
