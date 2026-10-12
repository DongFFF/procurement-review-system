<#
.SYNOPSIS
    打包「中直高校采购需求审查系统」Windows 桌面端安装包。

.DESCRIPTION
    依次完成：环境检查 → 前端构建 → Tauri 打包 → 产物报告。

.PARAMETER SkipChecks
    跳过环境检查（已知环境完备时加速）。

.PARAMETER Debug
    构建 debug 版本（更快，体积更大，用于验证打包流程）。

.EXAMPLE
    .\scripts\build_desktop.ps1
    .\scripts\build_desktop.ps1 -Debug
#>

[CmdletBinding()]
param(
    [switch]$SkipChecks,
    [switch]$Debug
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Write-Step($n, $text) {
    Write-Host ""
    Write-Host ("=" * 66) -ForegroundColor DarkGray
    Write-Host "  [$n] $text" -ForegroundColor Cyan
    Write-Host ("=" * 66) -ForegroundColor DarkGray
}

function Write-Ok($text)   { Write-Host "  [OK]   $text" -ForegroundColor Green }
function Write-Warn2($text){ Write-Host "  [警告] $text" -ForegroundColor Yellow }
function Write-Err($text)  { Write-Host "  [错误] $text" -ForegroundColor Red }

# ---------------------------------------------------------------------------
Write-Step 1 "环境检查"

$missing = @()

if (-not $SkipChecks) {
    # Rust
    $rustc = Get-Command rustc -ErrorAction SilentlyContinue
    if ($rustc) {
        Write-Ok "rustc $((rustc --version) -replace 'rustc ','')"
    } else {
        Write-Err "未找到 rustc（Rust 工具链）"
        $missing += "Rust:  winget install Rustlang.Rustup"
    }

    # Cargo
    if (Get-Command cargo -ErrorAction SilentlyContinue) {
        Write-Ok "cargo 已安装"
    } else {
        $missing += "Cargo: 随 Rust 一起安装"
    }

    # Node
    $node = Get-Command node -ErrorAction SilentlyContinue
    if ($node) {
        Write-Ok "node $(node --version)"
    } else {
        $missing += "Node.js:  winget install OpenJS.NodeJS.LTS"
    }

    # Python
    $venvPy = Join-Path $Root ".venv\Scripts\python.exe"
    if (Test-Path $venvPy) {
        Write-Ok "Python venv 就绪（$venvPy）"
    } elseif (Get-Command python -ErrorAction SilentlyContinue) {
        Write-Warn2 "未找到 .venv，将使用系统 Python"
    } else {
        $missing += "Python:  并创建 .venv 后 pip install -r backend\requirements.txt"
    }

    if ($missing.Count -gt 0) {
        Write-Host ""
        Write-Err "缺少以下依赖，无法继续："
        $missing | ForEach-Object { Write-Host "         - $_" -ForegroundColor Red }
        Write-Host ""
        Write-Host "  详见 docs\打包说明.md" -ForegroundColor Yellow
        exit 1
    }
} else {
    Write-Ok "已跳过环境检查"
}

# ---------------------------------------------------------------------------
Write-Step 2 "构建前端"

$frontend = Join-Path $Root "frontend"
Push-Location $frontend
try {
    if (-not (Test-Path "node_modules")) {
        Write-Host "  安装前端依赖…" -ForegroundColor Gray
        if (Get-Command pnpm -ErrorAction SilentlyContinue) {
            pnpm install
        } else {
            npm install
        }
    }

    Write-Host "  执行 vite build…" -ForegroundColor Gray
    if (Get-Command pnpm -ErrorAction SilentlyContinue) {
        pnpm run build
    } else {
        npm run build
    }

    if (-not (Test-Path "dist\index.html")) {
        throw "前端构建失败：未生成 dist\index.html"
    }
    Write-Ok "前端构建完成 → frontend\dist"
} finally {
    Pop-Location
}

# ---------------------------------------------------------------------------
Write-Step 3 "打包桌面端（Tauri）"

$tauriDir = Join-Path $Root "src-tauri"
if (-not (Test-Path $tauriDir)) {
    Write-Err "未找到 src-tauri 目录"
    exit 1
}

Push-Location $tauriDir
try {
    $cargoArgs = @("tauri", "build")
    if ($Debug) { $cargoArgs += "--debug" }

    Write-Host "  执行 cargo $($cargoArgs -join ' ') …" -ForegroundColor Gray
    Write-Host "  （首次编译需下载依赖，约 5-15 分钟）" -ForegroundColor DarkGray
    Write-Host ""

    # 优先用 npx 调用 tauri CLI（避免依赖全局安装）
    if (Get-Command cargo-tauri -ErrorAction SilentlyContinue) {
        & cargo @cargoArgs
    } else {
        Write-Host "  未找到 cargo-tauri，改用 frontend 中的 @tauri-apps/cli" -ForegroundColor Gray
        $cli = Join-Path $Root "frontend\node_modules\@tauri-apps\cli\tauri.js"
        if (-not (Test-Path $cli)) {
            throw "未找到 Tauri CLI。请先运行：cd frontend; pnpm install"
        }
        $buildArgs = @($cli, "build")
        if ($Debug) { $buildArgs += "--debug" }
        node @buildArgs
    }

    if ($LASTEXITCODE -ne 0) {
        throw "Tauri 打包失败（退出码 $LASTEXITCODE）"
    }
} finally {
    Pop-Location
}

# ---------------------------------------------------------------------------
Write-Step 4 "产物"

$bundleRoot = Join-Path $tauriDir "target\release\bundle"
if ($Debug) { $bundleRoot = Join-Path $tauriDir "target\debug\bundle" }

if (Test-Path $bundleRoot) {
    $artifacts = Get-ChildItem $bundleRoot -Recurse -File |
        Where-Object { $_.Extension -in @(".exe", ".msi") }

    if ($artifacts) {
        Write-Host ""
        foreach ($a in $artifacts) {
            $sizeMb = [math]::Round($a.Length / 1MB, 1)
            Write-Host "  📦 $($a.Name)" -ForegroundColor Green
            Write-Host "     $sizeMb MB" -ForegroundColor Gray
            Write-Host "     $($a.FullName)" -ForegroundColor DarkGray
            Write-Host ""
        }
        Write-Ok "打包完成，共 $($artifacts.Count) 个安装包"
    } else {
        Write-Warn2 "未找到安装包产物，请检查上方编译日志"
    }
} else {
    Write-Warn2 "未找到 bundle 目录：$bundleRoot"
}

Write-Host ""
Write-Host "  提示：若要让用户无需安装 Python，请参考 docs\打包说明.md" -ForegroundColor Yellow
Write-Host "        配置内嵌 Python 运行时（方案 B）" -ForegroundColor Yellow
Write-Host ""
