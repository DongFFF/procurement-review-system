@echo off
chcp 65001 >nul
title 中直高校采购需求审查系统

cd /d "%~dp0"

echo.
echo ============================================================
echo   中直高校采购需求审查系统
echo ============================================================
echo.

REM ---- 检查 Python 虚拟环境 ----
if not exist ".venv\Scripts\python.exe" (
    echo [1/3] 首次运行，正在创建 Python 环境...
    where python >nul 2>nul
    if errorlevel 1 (
        echo.
        echo [错误] 未检测到 Python。
        echo        请先安装 Python 3.10 以上版本：https://www.python.org/downloads/
        echo        安装时请勾选 "Add Python to PATH"。
        echo.
        pause
        exit /b 1
    )
    python -m venv .venv
    if errorlevel 1 (
        echo [错误] 创建虚拟环境失败
        pause
        exit /b 1
    )
    echo       正在安装依赖（约 1-3 分钟）...
    ".venv\Scripts\python.exe" -m pip install --upgrade pip -q
    ".venv\Scripts\python.exe" -m pip install -r backend\requirements.txt -q
    if errorlevel 1 (
        echo [错误] 依赖安装失败，请检查网络
        pause
        exit /b 1
    )
    echo       环境准备完成
)

REM ---- 检查前端构建产物 ----
if not exist "frontend\dist\index.html" (
    echo [2/3] 首次运行，正在构建前端界面...
    where node >nul 2>nul
    if errorlevel 1 (
        echo.
        echo [警告] 未检测到 Node.js，无法构建前端界面。
        echo        请安装 Node.js：https://nodejs.org/
        echo.
        pause
        exit /b 1
    )
    pushd frontend
    if not exist "node_modules" (
        where pnpm >nul 2>nul
        if errorlevel 1 (
            call npm install
        ) else (
            call pnpm install
        )
    )
    where pnpm >nul 2>nul
    if errorlevel 1 (
        call npm run build
    ) else (
        call pnpm run build
    )
    popd
    if not exist "frontend\dist\index.html" (
        echo [错误] 前端构建失败
        pause
        exit /b 1
    )
    echo       前端构建完成
)

REM ---- 启动 ----
echo [3/3] 正在启动服务...
echo.
echo   浏览器将自动打开，如未打开请手动访问：
echo   http://127.0.0.1:8756
echo.
echo   关闭本窗口即可停止程序。
echo ============================================================
echo.

".venv\Scripts\python.exe" backend\run_server.py --auto-port

echo.
echo 程序已停止。
pause
