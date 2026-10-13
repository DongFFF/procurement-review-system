# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller 打包配置：把整个系统打成单个 exe（含前端与知识库）。

产物：
    dist/中直高校采购需求审查系统.exe      单文件，双击即用
    dist/中直高校采购需求审查系统/          目录版，启动更快

打包后运行逻辑：
    exe 启动 → 定位内置的前端与知识库 → 启动 FastAPI → 自动打开浏览器

数据目录：
    exe 同级目录下的 data/（配置、上传、导出），便于绿色迁移。
    用户升级程序时只需替换 exe，data 目录保留。
"""

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH).resolve().parent

# ---------------------------------------------------------------------------
# 数据文件：前端构建产物 + 知识库
# ---------------------------------------------------------------------------
datas = []

frontend_dist = ROOT / "frontend" / "dist"
if frontend_dist.is_dir():
    datas.append((str(frontend_dist), "frontend/dist"))

knowledge = ROOT / "knowledge"
if knowledge.is_dir():
    datas.append((str(knowledge), "knowledge"))

# 后端源码（作为模块被 import，但显式带上便于排查）
datas.append((str(ROOT / "backend" / "app"), "app"))

# ---------------------------------------------------------------------------
# 隐藏导入：FastAPI / uvicorn / APScheduler 大量动态导入
# ---------------------------------------------------------------------------
hiddenimports = [
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.http.httptools_impl",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    "app.main",
    "app.api",
    "app.api.config_api",
    "app.api.kb",
    "app.api.review",
    "app.api.rewrite",
    "app.api.upload",
    "app.core",
    "app.core.config",
    "app.core.llm",
    "app.core.knowledge",
    "app.core.rules",
    "app.core.parser",
    "app.core.reviewer",
    "app.core.rewriter",
    "app.core.fetcher",
    "app.core.scheduler",
]

# 第三方包：动态导入较多，整体收集
for pkg in ("uvicorn", "fastapi", "starlette", "pydantic", "anyio", "httpx", "httpcore"):
    try:
        d, h, b = collect_all(pkg)
        datas += d
        hiddenimports += h
    except Exception:
        pass

# APScheduler 与 jieba 需要显式收集
for pkg in ("apscheduler", "jieba", "docx", "pypdf"):
    try:
        hiddenimports += collect_submodules(pkg)
    except Exception:
        pass

# jieba 的词典是数据文件，必须带上，否则分词失效
try:
    d, h, b = collect_all("jieba")
    datas += d
    hiddenimports += h
except Exception:
    pass

# ---------------------------------------------------------------------------
# 排除无用的大包，减小体积
# ---------------------------------------------------------------------------
excludes = [
    "tkinter", "matplotlib", "scipy", "pandas", "PIL", "PyQt5", "PySide2",
    "IPython", "jupyter", "notebook", "pytest", "setuptools", "pip", "wheel",
    "numpy.f2py", "numpy.distutils",
]

block_cipher = None

a = Analysis(
    [str(ROOT / "backend" / "run_server.py")],
    pathex=[str(ROOT / "backend")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="中直高校采购需求审查系统",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,               # 不用 UPX：容易被杀软误报
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,            # 保留控制台便于查看日志；如需隐藏改为 False
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
