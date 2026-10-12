"""应用入口。FastAPI 服务 + 静态前端托管 + 桌面端启动适配。"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .core.config import APP_ROOT, settings
from .api import router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("prs")

app = FastAPI(
    title="中直高校采购需求审查系统",
    description="知识库驱动的大模型采购需求合规审查与自动修订平台",
    version="0.1.0",
)

# 桌面端本地调用，放开 CORS 以便前端开发态直连
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")


@app.on_event("startup")
async def _startup() -> None:
    """启动时预热知识库与规则库，并启动自动更新调度。"""
    from .core.knowledge import get_kb
    from .core.rules import get_rule_library

    kb = get_kb()
    lib = get_rule_library()
    log.info("知识库：%s 个片段 / %s 个文档", kb.size, len(kb.documents))
    log.info("规则库：%s 条规则", len(lib.rules))

    # 首次启动若无索引，自动从内置法规构建
    if kb.size == 0:
        try:
            from .api.kb import rebuild_index

            stats = await rebuild_index()
            log.info("已自动构建知识库索引：%s", stats)
        except Exception as e:  # noqa: BLE001
            log.warning("自动构建知识库失败：%s", e)

    if settings.kb_auto_update:
        try:
            from .core.scheduler import start_scheduler

            start_scheduler()
        except Exception as e:  # noqa: BLE001
            log.warning("启动自动更新调度失败：%s", e)


@app.on_event("shutdown")
async def _shutdown() -> None:
    try:
        from .core.scheduler import stop_scheduler

        stop_scheduler()
    except Exception:
        pass


@app.get("/api/health")
async def health() -> dict:
    from .core.knowledge import get_kb
    from .core.rules import get_rule_library

    kb = get_kb()
    lib = get_rule_library()
    return {
        "status": "ok",
        "app": "中直高校采购需求审查系统",
        "version": app.version,
        "kb_chunks": kb.size,
        "kb_documents": len(kb.documents),
        "rules": len(lib.rules),
        "active_provider": settings.active_provider_id,
    }


# ---------------------------------------------------------------------------
# 静态前端（打包后由后端直接托管，实现单进程桌面应用）
# ---------------------------------------------------------------------------

def _frontend_dir() -> Path | None:
    candidates = [
        APP_ROOT / "frontend" / "dist",
        APP_ROOT / "web",
        Path(getattr(sys, "_MEIPASS", "")) / "web" if getattr(sys, "_MEIPASS", None) else None,
    ]
    for c in candidates:
        if c and Path(c).is_dir() and (Path(c) / "index.html").exists():
            return Path(c)
    return None


_fdir = _frontend_dir()
if _fdir is not None:
    app.mount("/assets", StaticFiles(directory=str(_fdir / "assets")), name="assets")

    @app.get("/{full_path:path}")
    async def spa(full_path: str):
        """SPA 兜底路由。"""
        if full_path.startswith("api/"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        target = _fdir / full_path
        if full_path and target.is_file():
            return FileResponse(str(target))
        return FileResponse(str(_fdir / "index.html"))
